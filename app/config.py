import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KITE_API_KEY = os.getenv("KITE_API_KEY", "")
KITE_API_SECRET = os.getenv("KITE_API_SECRET", "")
KITE_REDIRECT_URL = os.getenv("KITE_REDIRECT_URL", "http://127.0.0.1:5000/callback")
# App-specific name so it doesn't pick up a CLAUDE_MODEL set by other tools in the shell.
CLAUDE_MODEL = os.getenv("COPILOT_MODEL", "claude-opus-5-5")
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
