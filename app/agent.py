"""Claude copilot with read-only portfolio tools and web search, streamed to the UI."""

import json
import threading
from collections.abc import Iterator

from . import llm, news_feed
from .kite_client import NotLoggedIn

SYSTEM_PROMPT = """You are the AI copilot inside a personal investing dashboard for a single Zerodha (Kite) account owner in India.

You can read the user's holdings, positions, funds and live quotes through tools, and search the web for recent news. Always fetch fresh data with tools rather than guessing numbers. Show amounts in INR using Indian digit grouping (e.g. ₹1,25,000).

Answer in concise Markdown: short paragraphs, bullet points and small tables where they help. Lead with the answer.

You cannot place, modify or cancel orders - this app is read-only. If asked to trade, say order placement isn't enabled and describe what the user could do themselves.

You are not a SEBI-registered investment adviser. Analyse the portfolio (allocation, concentration, P&L, exposure, news impact) and lay out trade-offs and ideas worth researching, but don't present anything as a definitive buy/sell instruction."""

_EMPTY = {"type": "object", "properties": {}, "additionalProperties": False}

TOOLS = [
    {
        "name": "get_profile",
        "description": "Get the logged-in Zerodha user's profile (name, enabled exchanges and products).",
        "input_schema": _EMPTY,
    },
    {
        "name": "get_holdings",
        "description": "Get long-term equity holdings with quantity, average price, last price, invested value, current value, P&L and today's change.",
        "input_schema": _EMPTY,
    },
    {
        "name": "get_positions",
        "description": "Get today's open/net positions (intraday and F&O) with P&L.",
        "input_schema": _EMPTY,
    },
    {
        "name": "get_funds",
        "description": "Get equity segment funds: net balance, cash, live balance, collateral and utilised margin.",
        "input_schema": _EMPTY,
    },
    {
        "name": "get_quotes",
        "description": "Get live quotes for instruments. Use EXCHANGE:SYMBOL format, e.g. NSE:INFY, BSE:SENSEX, NSE:NIFTY 50.",
        "input_schema": {
            "type": "object",
            "properties": {
                "instruments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Up to 50 instruments in EXCHANGE:SYMBOL format.",
                }
            },
            "required": ["instruments"],
            "additionalProperties": False,
        },
    },
]
for _t in TOOLS:
    _t["eager_input_streaming"] = True

# Gemini/OpenAI-compatible providers have no built-in web search; this free news tool stands in for it.
NEWS_TOOL = {
    "name": "search_news",
    "description": "Search recent news headlines (Google News, past 7 days) for a company, stock symbol or market topic. Returns title, source, date and link.",
    "input_schema": {
        "type": "object",
        "properties": {"query": {"type": "string", "description": "e.g. 'Infosys results' or 'RBI repo rate'"}},
        "required": ["query"],
        "additionalProperties": False,
    },
}

OPENAI_TOOLS = [
    {"type": "function", "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]}}
    for t in [*TOOLS, NEWS_TOOL]
]

TOOL_LABELS = {
    "get_profile": "Reading profile",
    "get_holdings": "Reading holdings",
    "get_positions": "Reading positions",
    "get_funds": "Checking funds",
    "get_quotes": "Fetching live quotes",
    "web_search": "Searching the web",
    "search_news": "Searching news",
}

MAX_STEPS = 12
STOPPED = "\n\n_Stopped after too many steps. Try a narrower question._"


def _run_tool(name: str, args, account) -> str:
    if name == "get_profile":
        result = account.profile()
    elif name == "get_holdings":
        result = account.holdings()
    elif name == "get_positions":
        result = account.positions()
    elif name == "get_funds":
        result = account.funds()
    elif name == "get_quotes":
        # Eager input streaming skips server-side validation, so check the shape here.
        instruments = args.get("instruments") if isinstance(args, dict) else None
        if not isinstance(instruments, list) or not all(isinstance(i, str) for i in instruments):
            raise ValueError("instruments must be a list of EXCHANGE:SYMBOL strings")
        result = account.quotes(instruments[:50])
    elif name == "search_news":
        query = args.get("query") if isinstance(args, dict) else None
        if not isinstance(query, str) or not query.strip():
            raise ValueError("query must be a non-empty string")
        result = news_feed.search(query.strip())
    else:
        raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result, default=str)


class PortfolioAgent:
    def __init__(self) -> None:
        self.messages: list = []
        self.provider = ""
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.messages = []

    def chat_stream(self, user_text: str, account, ai: llm.AI) -> Iterator[dict]:
        """Yields UI events: {"type": "text"|"tool"|"done", ...}. `account` is the visitor's Kite account."""
        with self._lock:
            if self.provider != ai.provider:
                # History formats differ between providers; start over if the user switched.
                self.messages, self.provider = [], ai.provider
            self.messages.append({"role": "user", "content": user_text})
            system = f"{SYSTEM_PROMPT}\n\nToday is {llm.today()} (IST)."
            if ai.use_anthropic:
                yield from self._claude_turn(system, account, ai)
            else:
                yield from self._openai_turn(system, account, ai)
        yield {"type": "done"}

    def _claude_turn(self, system: str, account, ai: llm.AI) -> Iterator[dict]:
        for _ in range(MAX_STEPS):
            try:
                with ai.anthropic_client().beta.messages.stream(
                    **ai.claude_common(),
                    max_tokens=32000,
                    system=system,
                    tools=[*TOOLS, llm.web_search_tool(5)],
                    messages=self.messages,
                    output_config={"effort": "medium"},
                ) as stream:
                    for event in stream:
                        if event.type == "text":
                            yield {"type": "text", "text": event.text}
                        elif event.type == "content_block_start" and event.content_block.type in (
                            "tool_use",
                            "server_tool_use",
                        ):
                            name = event.content_block.name
                            yield {"type": "tool", "name": name, "label": TOOL_LABELS.get(name, name)}
                    response = stream.get_final_message()
            except ValueError:
                # Tool input JSON the SDK couldn't parse; re-issue the turn.
                continue

            # Append the full content (thinking, tool and search blocks) unchanged.
            self.messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "pause_turn":
                continue

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason in ("refusal", "max_tokens"):
                # Don't run a possibly cut-off tool call, but answer it so the history stays valid.
                if tool_uses:
                    self.messages.append({"role": "user", "content": [
                        {"type": "tool_result", "tool_use_id": b.id, "content": "Not run.", "is_error": True}
                        for b in tool_uses
                    ]})
                if response.stop_reason == "refusal":
                    yield {"type": "text", "text": "\n\nSorry, I can't help with that request."}
                return
            if not tool_uses:
                return

            results = []
            for block in tool_uses:
                try:
                    content, is_error = _run_tool(block.name, block.input, account), False
                except NotLoggedIn:
                    content, is_error = "Zerodha session expired. Ask the user to log in again.", True
                except Exception as e:
                    content, is_error = f"Tool failed: {e}", True
                results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                )
            # All tool results go back in a single user message.
            self.messages.append({"role": "user", "content": results})
        yield {"type": "text", "text": STOPPED}

    def _openai_turn(self, system: str, account, ai: llm.AI) -> Iterator[dict]:
        """Same loop for Gemini / OpenAI-compatible chat completions (history kept in OpenAI format)."""
        client = ai.openai_client()
        for _ in range(MAX_STEPS):
            stream = client.chat.completions.create(
                model=ai.model,
                messages=[{"role": "system", "content": system}, *self.messages],
                tools=OPENAI_TOOLS,
                stream=True,
            )
            text, calls, finish = "", {}, None
            for chunk in stream:
                if not chunk.choices:
                    continue
                choice = chunk.choices[0]
                delta = choice.delta
                if delta.content:
                    text += delta.content
                    yield {"type": "text", "text": delta.content}
                for i, tc in enumerate(delta.tool_calls or []):
                    # Some providers omit `index`; fall back to the position in this chunk.
                    slot = calls.setdefault(tc.index if tc.index is not None else i, {"id": None, "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name and not slot["name"]:
                        slot["name"] = tc.function.name
                        yield {"type": "tool", "name": slot["name"], "label": TOOL_LABELS.get(slot["name"], slot["name"])}
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments
                finish = choice.finish_reason or finish

            if not calls:
                self.messages.append({"role": "assistant", "content": text})
                if finish == "content_filter":
                    yield {"type": "text", "text": "\n\nSorry, I can't help with that request."}
                return

            tool_calls = [
                {"id": c["id"] or f"call_{n}", "type": "function",
                 "function": {"name": c["name"], "arguments": c["args"] or "{}"}}
                for n, c in sorted(calls.items())
            ]
            self.messages.append({"role": "assistant", "content": text or None, "tool_calls": tool_calls})
            for tc in tool_calls:
                try:
                    content = _run_tool(tc["function"]["name"], json.loads(tc["function"]["arguments"]), account)
                except NotLoggedIn:
                    content = "Error: Zerodha session expired. Ask the user to log in again."
                except Exception as e:
                    content = f"Error: tool failed: {e}"
                self.messages.append({"role": "tool", "tool_call_id": tc["id"], "content": content})
        yield {"type": "text", "text": STOPPED}

