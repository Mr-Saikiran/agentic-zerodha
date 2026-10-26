"""Claude agent with read-only tools over the Zerodha portfolio."""

import json
import threading

import anthropic

from . import config
from .kite_client import NotLoggedIn, session

SYSTEM_PROMPT = """You are a portfolio assistant for a single Zerodha (Kite) account owner.

You can read the user's holdings, positions, funds and live quotes through tools. Always fetch
fresh data with tools rather than guessing numbers. Show amounts in INR.

You cannot place, modify or cancel orders - this app is read-only. If asked to trade, explain
that order placement isn't enabled yet and describe what the user could do themselves.

You are not a licensed investment adviser. You can analyse the portfolio (allocation,
concentration, P&L, exposure) and explain trade-offs, but don't give personalised buy/sell
recommendations; frame anything forward-looking as considerations, not advice."""

TOOLS = [
    {
        "name": "get_profile",
        "description": "Get the logged-in Zerodha user's profile (name, enabled exchanges and products).",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "get_holdings",
        "description": "Get long-term equity holdings with quantity, average price, last price, invested value, current value, P&L and today's change.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "get_positions",
        "description": "Get today's open/net positions (intraday and F&O) with P&L.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "get_funds",
        "description": "Get equity segment funds: net balance, cash, live balance, collateral and utilised margin.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
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
        "strict": True,
    },
]

MAX_STEPS = 10


def _run_tool(name: str, args: dict) -> str:
    if name == "get_profile":
        result = session.profile()
    elif name == "get_holdings":
        result = session.holdings()
    elif name == "get_positions":
        result = session.positions()
    elif name == "get_funds":
        result = session.funds()
    elif name == "get_quotes":
        result = session.quotes(args["instruments"][:50])
    else:
        raise ValueError(f"Unknown tool: {name}")
    return json.dumps(result, default=str)


class PortfolioAgent:
    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None
        self.messages: list = []
        self._lock = threading.Lock()

    @property
    def client(self) -> anthropic.Anthropic:
        # Created lazily so the dashboard works even before Claude credentials are configured.
        if self._client is None:
            self._client = anthropic.Anthropic()
        return self._client

    def reset(self) -> None:
        with self._lock:
            self.messages = []

    def chat(self, user_text: str) -> dict:
        with self._lock:
            self.messages.append({"role": "user", "content": user_text})
            tool_calls: list[str] = []

            for _ in range(MAX_STEPS):
                response = self.client.beta.messages.create(
                    model=config.CLAUDE_MODEL,
                    max_tokens=16000,
                    system=SYSTEM_PROMPT,
                    tools=TOOLS,
                    messages=self.messages,
                    thinking={"type": "adaptive"},
                    output_config={"effort": "medium"},
                    # On a safety-classifier decline, the API retries on a suitable fallback model.
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                # Append the full content (including thinking blocks) unchanged.
                self.messages.append({"role": "assistant", "content": response.content})

                if response.stop_reason == "refusal":
                    return {"reply": "Sorry, I can't help with that request.", "tool_calls": tool_calls}

                if response.stop_reason == "pause_turn":
                    continue

                if response.stop_reason != "tool_use":
                    text = "\n".join(b.text for b in response.content if b.type == "text")
                    return {"reply": text, "tool_calls": tool_calls}

                results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    tool_calls.append(block.name)
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

            return {"reply": "Stopped after too many tool steps. Try a narrower question.", "tool_calls": tool_calls}


agent = PortfolioAgent()
