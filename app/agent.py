"""Claude copilot with read-only portfolio tools and web search, streamed to the UI."""

import json
import threading
from collections.abc import Iterator

from . import llm
from .kite_client import NotLoggedIn, session

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

TOOL_LABELS = {
    "get_profile": "Reading profile",
    "get_holdings": "Reading holdings",
    "get_positions": "Reading positions",
    "get_funds": "Checking funds",
    "get_quotes": "Fetching live quotes",
    "web_search": "Searching the web",
}

MAX_STEPS = 12


def _run_tool(name: str, args) -> str:
    if name == "get_profile":
        result = session.profile()
    elif name == "get_holdings":
        result = session.holdings()
    elif name == "get_positions":
        result = session.positions()
    elif name == "get_funds":
        result = session.funds()
    elif name == "get_quotes":
        # Eager input streaming skips server-side validation, so check the shape here.
        instruments = args.get("instruments") if isinstance(args, dict) else None
        if not isinstance(instruments, list) or not all(isinstance(i, str) for i in instruments):
            raise ValueError("instruments must be a list of EXCHANGE:SYMBOL strings")
        result = session.quotes(instruments[:50])
    else:
        raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result, default=str)


class PortfolioAgent:
    def __init__(self) -> None:
        self.messages: list = []
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.messages = []

    def chat_stream(self, user_text: str) -> Iterator[dict]:
        """Yields UI events: {"type": "text"|"tool"|"done", ...}."""
        with self._lock:
            self.messages.append({"role": "user", "content": user_text})
            system = f"{SYSTEM_PROMPT}\n\nToday is {llm.today()} (IST)."

            for _ in range(MAX_STEPS):
                try:
                    with llm.client().beta.messages.stream(
                        **llm.COMMON,
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
                    break
                if not tool_uses:
                    break

                results = []
                for block in tool_uses:
                    try:
                        content, is_error = _run_tool(block.name, block.input), False
                    except NotLoggedIn:
                        content, is_error = "Zerodha session expired. Ask the user to log in again.", True
                    except Exception as e:
                        content, is_error = f"Tool failed: {e}", True
                    results.append(
                        {"type": "tool_result", "tool_use_id": block.id, "content": content, "is_error": is_error}
                    )
                # All tool results go back in a single user message.
                self.messages.append({"role": "user", "content": results})
            else:
                yield {"type": "text", "text": "\n\n_Stopped after too many steps. Try a narrower question._"}

        yield {"type": "done"}


agent = PortfolioAgent()
