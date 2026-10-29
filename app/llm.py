"""Shared AI clients and request settings (Claude, or Gemini/other OpenAI-compatible APIs)."""

from datetime import datetime, timedelta, timezone

import anthropic
import openai

from . import config

IST = timezone(timedelta(hours=5, minutes=30))

_client: anthropic.Anthropic | None = None
_openai_client: openai.OpenAI | None = None


def client() -> anthropic.Anthropic:
    # Created lazily so the dashboard works even before Claude credentials are configured.
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def openai_client() -> openai.OpenAI:
    """Client for Gemini or any OpenAI-compatible provider (LLM_PROVIDER != "anthropic")."""
    global _openai_client
    if _openai_client is None:
        if not config.OPENAI_API_KEY:
            key = "GEMINI_API_KEY" if config.LLM_PROVIDER == "gemini" else "OPENAI_API_KEY"
            raise openai.OpenAIError(f"{key} is not set.")
        _openai_client = openai.OpenAI(api_key=config.OPENAI_API_KEY, base_url=config.OPENAI_BASE_URL)
    return _openai_client


def use_anthropic() -> bool:
    return config.LLM_PROVIDER == "anthropic"


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
