"""One-shot Claude research tasks (news, portfolio insights) that return structured JSON.

Each task gives Claude web search plus a strict `submit_*` tool; the tool's input is the result.
Results are cached in memory so page reloads don't re-run (and re-bill) the research.
Call `get("news")` / `get("insights")`; the work runs in a background thread.
"""

import json
import threading
import time

import openai

from . import llm, news_feed

CACHE_TTL = 30 * 60
MAX_STEPS = 8

class ResearchError(Exception):
    pass


def _obj(properties: dict) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


_str = {"type": "string"}
_strs = {"type": "array", "items": _str}
_level = {"type": "string", "enum": ["high", "medium", "low"]}

NEWS_TOOL = {
    "name": "submit_news",
    "description": "Submit the final news digest. Call exactly once when research is done.",
    "strict": True,
    "input_schema": _obj(
        {
            "market_summary": {**_str, "description": "2-3 sentence summary of the Indian market mood this week."},
            "items": {
                "type": "array",
                "description": "8-15 most relevant recent stories, most important first.",
                "items": _obj(
                    {
                        "title": _str,
                        "summary": {**_str, "description": "1-2 sentences: what happened and why it matters to this investor."},
                        "source": {**_str, "description": "Publisher name, e.g. Economic Times."},
                        "url": _str,
                        "published": {**_str, "description": "Publish date as YYYY-MM-DD if known, else empty."},
                        "symbols": {**_strs, "description": "Affected holdings' trading symbols; empty for market-wide news."},
                        "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                        "impact": _level,
                    }
                ),
            },
        }
    ),
}

INSIGHTS_TOOL = {
    "name": "submit_insights",
    "description": "Submit the final portfolio review. Call exactly once when analysis is done.",
    "strict": True,
    "input_schema": _obj(
        {
            "summary": {**_str, "description": "3-4 sentence overview of the portfolio's shape and health."},
            "health_score": {"type": "integer", "description": "0-100 diversification and risk-balance score."},
            "sectors": {
                "type": "array",
                "items": _obj({"name": _str, "weight_pct": {"type": "number"}, "symbols": _strs}),
            },
            "strengths": _strs,
            "risks": {"type": "array", "items": _obj({"title": _str, "detail": _str, "severity": _level})},
            "ideas": {
                "type": "array",
                "description": "4-8 ideas, most compelling first.",
                "items": _obj(
                    {
                        "title": _str,
                        "type": {"type": "string", "enum": ["add_more", "trim", "diversify", "rebalance", "research"]},
                        "symbols": {**_strs, "description": "Existing holdings or new candidates this idea concerns."},
                        "rationale": {**_str, "description": "2-3 sentences grounded in the portfolio data and recent news."},
                        "confidence": _level,
                    }
                ),
            },
        }
    ),
}

NEWS_PROMPT = """Today is {today} (IST). Research the most important news from the past 7 days for this investor's Indian equity portfolio.

Holdings (largest first):
{holdings}

Cover company-specific news for the largest holdings (results, guidance, management, regulation, deals, rating changes) plus 2-3 market-wide stories (RBI, FII flows, Nifty/Sensex moves, budget/policy). Prefer reputable Indian financial publishers. Only include stories you found in search results, with their real URLs.

When done, call submit_news."""

INSIGHTS_PROMPT = """Today is {today} (IST). Review this Indian equity portfolio and suggest where the investor could consider deploying more capital, what to trim, and how to improve diversification.

Holdings:
{holdings}

Funds available: {funds}

Steps:
1. Classify each holding into a sector and compute sector weights by current value.
2. Identify concentration, sector and single-stock risks, and losers with weak fundamentals.
3. Use web search sparingly to check recent developments for the largest positions and any idea you propose.
4. Propose ideas: which existing holdings look worth adding to (and why), what to trim, and which under-represented sectors or broad-market instruments (e.g. index ETFs) could improve balance. Ground every idea in the data.

This is analysis for the investor's own research, not a SEBI-registered recommendation - be balanced and mention the key risk in each rationale.

When done, call submit_insights."""


def _run(prompt: str, tool: dict, web_uses: int, effort: str, ai: llm.AI) -> dict:
    messages: list = [{"role": "user", "content": prompt}]
    for _ in range(MAX_STEPS):
        with ai.anthropic_client().beta.messages.stream(
            **ai.claude_common(),
            max_tokens=32000,
            tools=[llm.web_search_tool(web_uses), tool],
            messages=messages,
            output_config={"effort": effort},
        ) as stream:
            response = stream.get_final_message()

        for block in response.content:
            if block.type == "tool_use" and block.name == tool["name"]:
                return block.input

        if response.stop_reason == "refusal":
            raise ResearchError("The AI declined this request.")
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        # Finished without calling the submit tool - nudge it once more.
        messages.append({"role": "user", "content": f"Please call {tool['name']} with your results now."})
    raise ResearchError("Research did not complete. Try again.")


def _holdings_table(holdings: list[dict]) -> str:
    rows = sorted(holdings, key=lambda h: h["current_value"], reverse=True)
    return json.dumps(rows, default=str)


def _top_symbols(holdings: list[dict], n: int) -> list[str]:
    return [h["symbol"] for h in sorted(holdings, key=lambda h: h["current_value"], reverse=True)[:n]]


# ---- Gemini / OpenAI-compatible path: headlines come from Google News RSS, not web search ----

FEED_NEWS_TOOL_SCHEMA = _obj(
    {
        "market_summary": _str,
        "items": {
            "type": "array",
            "items": _obj(
                {
                    "article_id": {"type": "integer", "description": "id of the headline this item is about"},
                    "summary": _str,
                    "symbols": _strs,
                    "sentiment": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                    "impact": _level,
                }
            ),
        },
    }
)

FEED_NEWS_PROMPT = """Today is {today} (IST). These are recent Google News headlines about this investor's Indian equity holdings and the market.

Holdings (largest first): {symbols}

Headlines (JSON; each has an id):
{articles}

Choose the 8-15 stories that matter most to this investor, most important first. Skip duplicates, ads and low-quality items.
For each, give its article_id, a 1-2 sentence summary of what happened and why it matters to this portfolio,
the affected holdings' symbols (empty for market-wide news), sentiment and impact.
Also write market_summary: 2-3 sentences on the Indian market mood this week.

Reply with JSON only, matching this JSON schema:
{schema}"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    try:
        return json.loads(text[start : end + 1])
    except ValueError:
        raise ResearchError("The model didn't return valid JSON. Try again.")


def _openai_json(prompt: str, name: str, schema: dict, ai: llm.AI) -> dict:
    client = ai.openai_client()
    messages = [{"role": "user", "content": prompt}]
    try:
        response = client.chat.completions.create(
            model=ai.model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": name, "schema": schema, "strict": True}},
        )
    except openai.BadRequestError:
        # Some providers don't accept json_schema; the schema is also in the prompt.
        response = client.chat.completions.create(
            model=ai.model, messages=messages, response_format={"type": "json_object"}
        )
    return _parse_json(response.choices[0].message.content or "")


def _feed_news(holdings: list[dict], ai: llm.AI) -> dict:
    symbols = _top_symbols(holdings, 10)
    articles = news_feed.portfolio_news(symbols)
    if not articles:
        raise ResearchError("Couldn't fetch news headlines right now. Try again shortly.")
    brief = [{k: a[k] for k in ("id", "title", "source", "published", "symbol")} for a in articles]
    prompt = FEED_NEWS_PROMPT.format(
        today=llm.today(),
        symbols=", ".join(symbols) or "(no holdings)",
        articles=json.dumps(brief, ensure_ascii=False),
        schema=json.dumps(FEED_NEWS_TOOL_SCHEMA),
    )
    result = _openai_json(prompt, "news_digest", FEED_NEWS_TOOL_SCHEMA, ai)
    by_id = {a["id"]: a for a in articles}
    items = []
    for item in result.get("items", []):
        # Title, link, source and date come from the feed, so the model can't invent a URL.
        a = by_id.get(item.get("article_id"))
        if a:
            items.append({
                "title": a["title"], "url": a["url"], "source": a["source"], "published": a["published"],
                "summary": item.get("summary", ""), "symbols": item.get("symbols") or ([a["symbol"]] if a["symbol"] else []),
                "sentiment": item.get("sentiment", "neutral"), "impact": item.get("impact", "low"),
            })
    return {"market_summary": result.get("market_summary", ""), "items": items}


def _feed_insights(prompt: str, holdings: list[dict], ai: llm.AI) -> dict:
    headlines = [
        {k: a[k] for k in ("title", "source", "published", "symbol")}
        for a in news_feed.portfolio_news(_top_symbols(holdings, 6), per_symbol=3)
    ]
    prompt = (
        prompt.replace(
            "3. Use web search sparingly to check recent developments for the largest positions and any idea you propose.",
            "3. Use the recent headlines below to check developments for the largest positions.",
        ).replace("When done, call submit_insights.", "")
        + f"\nRecent headlines:\n{json.dumps(headlines, ensure_ascii=False)}\n\n"
        + f"Reply with JSON only, matching this JSON schema:\n{json.dumps(INSIGHTS_TOOL['input_schema'])}"
    )
    return _openai_json(prompt, "portfolio_review", INSIGHTS_TOOL["input_schema"], ai)


def _build_news(account, ai: llm.AI) -> dict:
    holdings = account.holdings()
    if not ai.use_anthropic:
        return _feed_news(holdings, ai)
    top = sorted(holdings, key=lambda h: h["current_value"], reverse=True)[:12]
    brief = "\n".join(f"- {h['symbol']} ({h['exchange']})" for h in top) or "- (no holdings)"
    return _run(NEWS_PROMPT.format(today=llm.today(), holdings=brief), NEWS_TOOL, 8, "low", ai)


def _build_insights(account, ai: llm.AI) -> dict:
    holdings = account.holdings()
    prompt = INSIGHTS_PROMPT.format(
        today=llm.today(),
        holdings=_holdings_table(holdings),
        funds=json.dumps(account.funds(), default=str),
    )
    if not ai.use_anthropic:
        return _feed_insights(prompt, holdings, ai)
    return _run(prompt, INSIGHTS_TOOL, 5, "high", ai)


BUILDERS = {"news": _build_news, "insights": _build_insights}

# Research runs in a background thread and the UI polls, so no single HTTP request outlives
# a proxy timeout (Cloudflare drops requests idle for ~100s). Jobs live in the visitor's session state.


def _work(st, key: str, account, ai: llm.AI) -> None:
    try:
        result = BUILDERS[key](account, ai)
        job = {"status": "ready", "result": {**result, "generated_at": time.time()}, "finished_at": time.time()}
    except Exception as e:
        job = {"status": "error", "error": e}
    with st.jobs_lock:
        st.jobs[key] = job


def get(st, key: str, refresh: bool, account, ai: llm.AI) -> dict:
    """Returns {"status": "ready", ...result} or {"status": "running"}; re-raises a failed job's error once.
    `st` is the visitor's session state (sessions.state)."""
    with st.jobs_lock:
        job = st.jobs.get(key)
        if job and job["status"] == "running":
            return {"status": "running", "started_at": job["started_at"]}
        if job and job["status"] == "error" and not refresh:
            del st.jobs[key]
            raise job["error"]
        if job and job["status"] == "ready" and not refresh and time.time() - job["finished_at"] < CACHE_TTL:
            return {"status": "ready", **job["result"]}
        started_at = time.time()
        st.jobs[key] = {"status": "running", "started_at": started_at}
    threading.Thread(target=_work, args=(st, key, account, ai), daemon=True).start()
    return {"status": "running", "started_at": started_at}
