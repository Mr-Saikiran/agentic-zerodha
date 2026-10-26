"""Shared Claude client and request settings."""

from datetime import datetime, timedelta, timezone

import anthropic

from . import config

IST = timezone(timedelta(hours=5, minutes=30))

_client: anthropic.Anthropic | None = None


def client() -> anthropic.Anthropic:
    # Created lazily so the dashboard works even before Claude credentials are configured.
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


# Applied to every request: on a safety-classifier decline the API retries on a suitable fallback model.
COMMON = {
    "model": config.CLAUDE_MODEL,
    "thinking": {"type": "adaptive"},
    "betas": ["server-side-fallback-2026-07-01"],
    "fallbacks": "default",
}


def web_search_tool(max_uses: int) -> dict:
    return {
        "type": "web_search_20260209",
        "name": "web_search",
        "max_uses": max_uses,
        "user_location": {"type": "approximate", "country": "IN", "timezone": "Asia/Kolkata"},
    }


def today() -> str:
    return datetime.now(IST).strftime("%A, %d %B %Y")
