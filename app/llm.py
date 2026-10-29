"""AI provider clients, built per visitor from the provider and key in their session."""

import hashlib
import threading
from datetime import datetime, timedelta, timezone

import anthropic
import openai

from . import config

IST = timezone(timedelta(hours=5, minutes=30))

# Clients keep HTTP connections open, so reuse them per (provider, key, base_url).
_clients: dict[str, object] = {}
_clients_lock = threading.Lock()
MAX_CLIENTS = 200


class NoAIKey(Exception):
    pass


class AI:
    def __init__(self, provider: str, api_key: str, model: str = "", base_url: str = "") -> None:
        if not provider or not api_key:
            raise NoAIKey()
        preset = config.PROVIDERS.get(provider, config.PROVIDERS["openai"])
        self.provider = provider
        self.api_key = api_key
        self.model = model or preset["model"]
        self.base_url = base_url or preset["base_url"]
        if not self.model:
            raise NoAIKey()

    @property
    def name(self) -> str:
        return {"gemini": "Gemini", "anthropic": "Claude"}.get(self.provider, "AI provider")

    @property
    def use_anthropic(self) -> bool:
        return self.provider == "anthropic"

    def _cached(self, factory):
        key = hashlib.sha256(f"{self.provider}|{self.api_key}|{self.base_url}".encode()).hexdigest()
        with _clients_lock:
            if key not in _clients:
                if len(_clients) >= MAX_CLIENTS:
                    _clients.pop(next(iter(_clients)))
                _clients[key] = factory()
            return _clients[key]

    def anthropic_client(self) -> anthropic.Anthropic:
        return self._cached(lambda: anthropic.Anthropic(api_key=self.api_key))

    def openai_client(self) -> openai.OpenAI:
        if not self.base_url:
            raise openai.OpenAIError("Set the API base URL for your OpenAI-compatible provider.")
        return self._cached(lambda: openai.OpenAI(api_key=self.api_key, base_url=self.base_url))

    def claude_common(self) -> dict:
        # On a safety-classifier decline the API retries on a suitable fallback model.
        return {
            "model": self.model,
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
