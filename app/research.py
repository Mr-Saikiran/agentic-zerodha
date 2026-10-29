"""One-shot Claude research tasks (news, portfolio insights) that return structured JSON.

Each task gives Claude web search plus a strict `submit_*` tool; the tool's input is the result.
Results are cached in memory so page reloads don't re-run (and re-bill) the research.
Call `get("news")` / `get("insights")`; the work runs in a background thread.
"""

import json
import threading
import time

from . import llm
from .kite_client import session

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


def _run(prompt: str, tool: dict, web_uses: int, effort: str) -> dict:
    messages: list = [{"role": "user", "content": prompt}]
    for _ in range(MAX_STEPS):
        with llm.client().beta.messages.stream(
            **llm.COMMON,
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
            raise ResearchError("Claude declined this request.")
        messages.append({"role": "assistant", "content": response.content})
        if response.stop_reason == "pause_turn":
            continue
        # Finished without calling the submit tool - nudge it once more.
        messages.append({"role": "user", "content": f"Please call {tool['name']} with your results now."})
    raise ResearchError("Research did not complete. Try again.")


def _holdings_table(holdings: list[dict]) -> str:
    rows = sorted(holdings, key=lambda h: h["current_value"], reverse=True)
    return json.dumps(rows, default=str)


def _build_news() -> dict:
    holdings = session.holdings()
    top = sorted(holdings, key=lambda h: h["current_value"], reverse=True)[:12]
    brief = "\n".join(f"- {h['symbol']} ({h['exchange']})" for h in top) or "- (no holdings)"
    return _run(NEWS_PROMPT.format(today=llm.today(), holdings=brief), NEWS_TOOL, web_uses=8, effort="low")


def _build_insights() -> dict:
    prompt = INSIGHTS_PROMPT.format(
        today=llm.today(),
        holdings=_holdings_table(session.holdings()),
        funds=json.dumps(session.funds(), default=str),
    )
    return _run(prompt, INSIGHTS_TOOL, web_uses=5, effort="high")


BUILDERS = {"news": _build_news, "insights": _build_insights}

# Research runs in a background thread and the UI polls, so no single HTTP request outlives
# a proxy timeout (Cloudflare drops requests idle for ~100s).
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _work(key: str) -> None:
    try:
        result = BUILDERS[key]()
        job = {"status": "ready", "result": {**result, "generated_at": time.time()}, "finished_at": time.time()}
    except Exception as e:
        job = {"status": "error", "error": e}
    with _jobs_lock:
        _jobs[key] = job


def get(key: str, refresh: bool = False) -> dict:
    """Returns {"status": "ready", ...result} or {"status": "running"}; re-raises a failed job's error once."""
    with _jobs_lock:
        job = _jobs.get(key)
        if job and job["status"] == "running":
            return {"status": "running", "started_at": job["started_at"]}
        if job and job["status"] == "error" and not refresh:
            del _jobs[key]
            raise job["error"]
        if job and job["status"] == "ready" and not refresh and time.time() - job["finished_at"] < CACHE_TTL:
            return {"status": "ready", **job["result"]}
        started_at = time.time()
        _jobs[key] = {"status": "running", "started_at": started_at}
    threading.Thread(target=_work, args=(key,), daemon=True).start()
    return {"status": "running", "started_at": started_at}


def reset() -> None:
    with _jobs_lock:
        _jobs.clear()
