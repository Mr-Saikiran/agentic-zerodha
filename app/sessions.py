"""Per-visitor sessions for a multi-user, bring-your-own-keys deployment.

Each visitor's credentials (Kite API key and secret, Kite access token, AI provider key) live only in
an encrypted, HttpOnly cookie in their own browser. The server decrypts it per request and never writes
it to disk. Chat history and research jobs are kept in server memory per session id, so they're lost on
restart (the credentials aren't).
"""

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field, fields

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request, Response

from . import config

COOKIE = "az_session"
REMEMBER_SECONDS = 30 * 24 * 3600


def _fernet() -> Fernet:
    # Any string works as SESSION_SECRET; derive a valid 32-byte Fernet key from it.
    digest = hashlib.sha256(config.SESSION_SECRET.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


_FERNET = _fernet()


@dataclass
class UserSession:
    sid: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    # Kite Connect app (each visitor registers their own at developers.kite.trade)
    kite_api_key: str = ""
    kite_api_secret: str = ""
    access_token: str = ""
    token_at: float = 0.0
    user: dict | None = None
    # AI provider: "gemini" | "anthropic" | "openai"
    provider: str = ""
    ai_key: str = ""
    model: str = ""
    base_url: str = ""
    demo: bool = False
    remember: bool = True
    # CSRF state for the in-flight Kite login
    login_state: str = ""

    @property
    def has_kite_keys(self) -> bool:
        return bool(self.kite_api_key and self.kite_api_secret)

    @property
    def has_ai(self) -> bool:
        return bool(self.provider and self.ai_key)

    @property
    def configured(self) -> bool:
        return self.demo or self.has_kite_keys


def load(request: Request) -> UserSession:
    raw = request.cookies.get(COOKIE)
    if raw:
        try:
            data = json.loads(_FERNET.decrypt(raw.encode()))
            known = {f.name for f in fields(UserSession)}
            return UserSession(**{k: v for k, v in data.items() if k in known})
        except (InvalidToken, ValueError, TypeError):
            pass  # tampered, or encrypted with an old SESSION_SECRET: start fresh
    return UserSession()


def save(response: Response, request: Request, sess: UserSession) -> None:
    token = _FERNET.encrypt(json.dumps(asdict(sess)).encode()).decode()
    response.set_cookie(
        COOKIE,
        token,
        max_age=REMEMBER_SECONDS if sess.remember else None,
        path=f"{config.BASE_PATH}/",
        httponly=True,
        secure=is_https(request),
        samesite="lax",
    )


def clear(response: Response) -> None:
    response.delete_cookie(COOKIE, path=f"{config.BASE_PATH}/")


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme) == "https"


def public_base(request: Request) -> str:
    """The URL visitors use, e.g. https://techswitch.in/equity, even when proxied by the Cloudflare Worker."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    proto = "https" if is_https(request) else "http"
    return f"{proto}://{host}{config.BASE_PATH}"


# ---- server-side, in-memory state per session (chat history, research jobs) ----

MAX_SESSIONS = 500
IDLE_TTL = 12 * 3600


class _State:
    def __init__(self) -> None:
        from .agent import PortfolioAgent

        self.agent = PortfolioAgent()
        self.jobs: dict[str, dict] = {}
        self.jobs_lock = threading.Lock()
        self.last_used = time.time()


_states: dict[str, _State] = {}
_states_lock = threading.Lock()


def state(sid: str) -> _State:
    now = time.time()
    with _states_lock:
        st = _states.get(sid)
        if st is None:
            # Drop idle sessions, then the least recently used if still over the cap.
            for k in [k for k, v in _states.items() if now - v.last_used > IDLE_TTL]:
                del _states[k]
            while len(_states) >= MAX_SESSIONS:
                del _states[min(_states, key=lambda k: _states[k].last_used)]
            st = _states[sid] = _State()
        st.last_used = now
        return st


def drop_state(sid: str) -> None:
    with _states_lock:
        _states.pop(sid, None)
