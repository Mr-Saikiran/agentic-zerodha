import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:5000/callback")
# ---- AI provider -------------------------------------------------------------
# "anthropic" (Claude, with built-in web search), "gemini", or "openai" (any OpenAI-compatible API:
# OpenRouter, Groq, OpenCode, Ollama...). Defaults to gemini when only GEMINI_API_KEY is set.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
LLM_PROVIDER = (
    os.getenv("LLM_PROVIDER")
    or ("gemini" if GEMINI_API_KEY and not os.getenv("ANTHROPIC_API_KEY") else "anthropic")
).lower()

# App-specific name so it doesn't pick up a CLAUDE_MODEL set by other tools in the shell.
CLAUDE_MODEL = os.getenv("COPILOT_MODEL", "claude-opus-5-5")

if LLM_PROVIDER == "gemini":
    OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
    OPENAI_API_KEY = GEMINI_API_KEY
    OPENAI_MODEL = os.getenv("COPILOT_MODEL", "gemini-flash-latest")
else:
    OPENAI_BASE_URL = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
    OPENAI_MODEL = os.getenv("COPILOT_MODEL", "gpt-5-mini")

MODEL_NAME = CLAUDE_MODEL if LLM_PROVIDER == "anthropic" else OPENAI_MODEL
# Serve sample portfolio data instead of calling Kite - for previewing the UI without logging in.
DEMO_MODE = os.getenv("DEMO_MODE", "").lower() in ("1", "true", "yes")

# URL prefix the app is served under, e.g. "/equity" for https://techswitch.in/equity/. Empty = site root.
BASE_PATH = "/" + os.getenv("BASE_PATH", "").strip("/") if os.getenv("BASE_PATH", "").strip("/") else ""
# When set, every request must carry this value in the X-Origin-Auth header. The Cloudflare Worker adds it,
# so the origin can't be reached directly and bypass Cloudflare Access.
ORIGIN_SECRET = os.getenv("ORIGIN_SECRET", "")

# Access token is cached here (git-ignored) so a server restart doesn't force a re-login.
# Point DATA_DIR at a persistent volume when running in a container.
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT))
SESSION_FILE = DATA_DIR / ".kite_session.json"
STATIC_DIR = ROOT / "static"


def missing_settings() -> list[str]:
    if DEMO_MODE:
        return []
    return [
        name
        for name, value in (("KITE_API_KEY", KITE_API_KEY), ("KITE_API_SECRET", KITE_API_SECRET))
        if not value or value.startswith("your_")
    ]
