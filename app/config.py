"""Deployment settings. User credentials are NOT configured here: each visitor enters their own
Kite and AI keys in the app, and they're kept in an encrypted cookie (see sessions.py)."""

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
STATIC_DIR = ROOT / "static"

# URL prefix the app is served under, e.g. "/equity" for https://techswitch.in/equity/. Empty = site root.
BASE_PATH = "/" + os.getenv("BASE_PATH", "").strip("/") if os.getenv("BASE_PATH", "").strip("/") else ""

# Encrypts the session cookie. Set a long random value in production; if it changes, everyone has to
# enter their keys again. Without it, a random one is used and sessions reset on every restart.
SESSION_SECRET = os.getenv("SESSION_SECRET", "")
if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_urlsafe(32)
    logging.getLogger("uvicorn.error").warning(
        "SESSION_SECRET is not set: using a random one, so sessions won't survive a restart."
    )

# Optional. Public callback URL to show users for their Kite app's Redirect URL. By default it's derived
# from the request, e.g. https://techswitch.in/equity/callback.
KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "")

# Optional. When set, every request must carry this value in the X-Origin-Auth header (the Cloudflare
# Worker adds it), so the origin can only be reached through the Worker.
ORIGIN_SECRET = os.getenv("ORIGIN_SECRET", "")

# ---- AI providers users can pick -------------------------------------------------
PROVIDERS = {
    "gemini": {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "model": "gemini-flash-latest",
        "key_url": "https://aistudio.google.com/apikey",
    },
    "anthropic": {
        "label": "Anthropic Claude",
        "base_url": "",
        "model": "claude-opus-5-5",
        "key_url": "https://console.anthropic.com/settings/keys",
    },
    "openai": {
        "label": "OpenAI-compatible (OpenRouter, Groq, OpenCode, Ollama...)",
        "base_url": "",
        "model": "",
        "key_url": "",
    },
}
