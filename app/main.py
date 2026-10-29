import hmac
import ipaddress
import json
import os
import re
import secrets
import socket
import time
from html import escape
from urllib.parse import urlparse

import anthropic
import openai
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect.exceptions import KiteException
from pydantic import BaseModel, Field

from . import config, kite_client, research, sessions
from .kite_client import DemoAccount, KiteAccount, NotLoggedIn
from .llm import AI, NoAIKey
from .research import ResearchError
from .sessions import UserSession

# `site` holds every route; `app` (bottom of file) mounts it under BASE_PATH, e.g. /equity.
site = FastAPI(title="Agentic Zerodha", docs_url=None, redoc_url=None, openapi_url=None)
site.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

# The page lives at "<BASE_PATH>/" and uses relative URLs, so it works under any prefix.
HOME = f"{config.BASE_PATH}/"


@site.middleware("http")
async def csrf_guard(request: Request, call_next):
    # The session is a cookie, so state-changing calls must come from our own page's fetch(),
    # which sends this header. Cross-site forms can't set it.
    if request.method == "POST" and request.headers.get("x-requested-with") != "fetch":
        return JSONResponse(status_code=403, content={"detail": "Missing X-Requested-With header."})
    return await call_next(request)


@site.exception_handler(NotLoggedIn)
async def not_logged_in(request: Request, _: NotLoggedIn):
    # Kite rejected the token (expired at 6 AM or revoked): forget it so the UI shows the login button.
    sess = sessions.load(request)
    response = JSONResponse(status_code=401, content={"detail": "Not logged in to Zerodha."})
    if sess.access_token:
        sess.access_token, sess.user = "", None
        sessions.save(response, request, sess)
    return response


@site.exception_handler(NoAIKey)
async def no_ai_key(_: Request, __: NoAIKey):
    return JSONResponse(status_code=400, content={"detail": "Add an AI provider key in Settings to use AI features."})


@site.get("/")
def index():
    # Revalidate on every load so a new deploy's page (and its asset links) is picked up straight away.
    return FileResponse(config.STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@site.middleware("http")
async def revalidate_static(request: Request, call_next):
    response = await call_next(request)
    if "/static/" in request.url.path:
        response.headers.setdefault("Cache-Control", "no-cache")
    return response


# ---- session helpers ------------------------------------------------------------


def _logged_in(sess: UserSession) -> bool:
    return sess.demo or bool(sess.access_token and kite_client.token_valid(sess.token_at))


def _account(sess: UserSession):
    if sess.demo:
        return DemoAccount()
    if not _logged_in(sess):
        raise NotLoggedIn()
    return KiteAccount(sess.kite_api_key, sess.access_token)


def _ai(sess: UserSession) -> AI:
    return AI(sess.provider, sess.ai_key, sess.model, sess.base_url)


def _hint(value: str) -> str:
    return f"…{value[-4:]}" if value else ""


def _callback_url(request: Request) -> str:
    return config.KITE_REDIRECT_URL or f"{sessions.public_base(request)}/callback"


def _json_with_cookie(request: Request, sess: UserSession, body: dict) -> JSONResponse:
    response = JSONResponse(body)
    sessions.save(response, request, sess)
    return response


@site.get("/api/status")
def status(request: Request):
    sess = sessions.load(request)
    return {
        "configured": sess.configured,
        "logged_in": _logged_in(sess),
        "demo": sess.demo,
        "user": DemoAccount.user if sess.demo else sess.user,
        "has_kite_keys": sess.has_kite_keys,
        "kite_key_hint": _hint(sess.kite_api_key),
        "has_ai": sess.has_ai,
        "provider": sess.provider,
        "ai_key_hint": _hint(sess.ai_key),
        "model": sess.model or config.PROVIDERS.get(sess.provider, {}).get("model", ""),
        "base_url": sess.base_url,
        "remember": sess.remember,
        "callback_url": _callback_url(request),
        "providers": config.PROVIDERS,
    }


# ---- setup: visitor's own keys ------------------------------------------------------

KEY_RE = re.compile(r"^[A-Za-z0-9]{6,64}$")
SECRET_RE = re.compile(r"^[A-Za-z0-9]{6,128}$")
AI_KEY_RE = re.compile(r"^\S{8,400}$")
MODEL_RE = re.compile(r"^[\w.:/@-]{1,100}$")
ALLOW_LOCAL_AI = os.getenv("ALLOW_LOCAL_AI_URL", "").lower() in ("1", "true", "yes")


def _check_base_url(url: str) -> str:
    """Custom AI endpoints must be public HTTPS, so visitors can't make the server call internal addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.hostname:
        raise HTTPException(400, "The API base URL must start with https://")
    if ALLOW_LOCAL_AI:  # self-hosting, e.g. Ollama at http://localhost:11434/v1
        return url
    if parsed.scheme != "https":
        raise HTTPException(400, "The API base URL must use https://")
    try:
        addrs = {ai[4][0] for ai in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror:
        raise HTTPException(400, "Couldn't resolve the API base URL's host.")
    for addr in addrs:
        ip = ipaddress.ip_address(addr)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise HTTPException(400, "The API base URL must be a public address.")
    return url


class SetupRequest(BaseModel):
    # Blank fields keep what's already saved.
    kite_api_key: str = Field("", max_length=64)
    kite_api_secret: str = Field("", max_length=128)
    provider: str = Field("", max_length=20)
    ai_key: str = Field("", max_length=400)
    model: str = Field("", max_length=100)
    base_url: str = Field("", max_length=300)
    remember: bool = True


@site.post("/api/setup")
def setup(req: SetupRequest, request: Request):
    sess = sessions.load(request)
    key, secret = req.kite_api_key.strip(), req.kite_api_secret.strip()
    if key or secret:
        if not KEY_RE.match(key) or not SECRET_RE.match(secret):
            raise HTTPException(400, "Enter both the Kite API key and API secret (letters and numbers only).")
        if key != sess.kite_api_key:
            sess.access_token, sess.user = "", None
        sess.kite_api_key, sess.kite_api_secret, sess.demo = key, secret, False

    provider = req.provider.strip().lower()
    if provider:
        if provider not in config.PROVIDERS:
            raise HTTPException(400, "Unknown AI provider.")
        ai_key = req.ai_key.strip()
        if ai_key and not AI_KEY_RE.match(ai_key):
            raise HTTPException(400, "That AI API key doesn't look right.")
        if provider != sess.provider and not ai_key:
            raise HTTPException(400, "Enter an API key for the selected AI provider.")
        model = req.model.strip()
        if model and not MODEL_RE.match(model):
            raise HTTPException(400, "That model name doesn't look right.")
        base_url = req.base_url.strip()
        if provider == "openai":
            if not base_url or not model:
                raise HTTPException(400, "OpenAI-compatible providers need an API base URL and a model name.")
            base_url = _check_base_url(base_url)
        else:
            base_url = ""
        sess.provider, sess.model, sess.base_url = provider, model, base_url
        if ai_key:
            sess.ai_key = ai_key

    sess.remember = req.remember
    if not sess.configured:
        raise HTTPException(400, "Enter your Kite API key and secret, or try the demo.")
    return _json_with_cookie(request, sess, {"ok": True})


@site.post("/api/demo")
def start_demo(request: Request):
    sess = sessions.load(request)
    sess.demo = True
    sessions.state(sess.sid).agent.reset()
    return _json_with_cookie(request, sess, {"ok": True})


@site.post("/api/forget")
def forget(request: Request):
    """Deletes every key and token this browser has saved, and the server-side chat."""
    sess = sessions.load(request)
    if sess.access_token:
        kite_client.invalidate(sess.kite_api_key, sess.access_token)
    sessions.drop_state(sess.sid)
    response = JSONResponse({"ok": True})
    sessions.clear(response)
    return response


# ---- Kite login flow (visitor's own Kite Connect app) ------------------------------


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">
        <title>Login problem</title><body style="font-family:system-ui;max-width:520px;margin:15vh auto;padding:0 16px">
        <h2>Couldn't log in to Zerodha</h2><p>{escape(message)}</p><p><a href="{HOME}">Back to the app</a></p>""",
        status_code=status_code,
    )


@site.get("/login")
def login(request: Request):
    sess = sessions.load(request)
    if not sess.has_kite_keys:
        return RedirectResponse(HOME)
    sess.login_state = secrets.token_urlsafe(16)
    response = RedirectResponse(kite_client.login_url(sess.kite_api_key, sess.login_state))
    sessions.save(response, request, sess)
    return response


@site.get("/callback")
def callback(request: Request, status: str | None = None, request_token: str | None = None, state: str | None = None):
    sess = sessions.load(request)
    if status != "success" or not request_token:
        return _error_page("The Zerodha login was cancelled or failed.")
    if not sess.login_state or not state or not hmac.compare_digest(state, sess.login_state):
        return _error_page("This login link has expired or wasn't started from this browser. Start again from the app.")
    try:
        token, user = kite_client.create_session(sess.kite_api_key, sess.kite_api_secret, request_token)
    except KiteException as e:
        return _error_page(f"Zerodha rejected the login: {e}. Check your Kite API secret.")
    sess.access_token, sess.token_at, sess.user, sess.login_state, sess.demo = token, time.time(), user, "", False
    sessions.state(sess.sid).agent.reset()
    response = RedirectResponse(HOME)
    sessions.save(response, request, sess)
    return response


@site.post("/logout")
def logout(request: Request):
    """Ends the Zerodha session (and demo); keeps saved keys unless the user chose not to remember them."""
    sess = sessions.load(request)
    if sess.access_token:
        kite_client.invalidate(sess.kite_api_key, sess.access_token)
    sess.access_token, sess.user, sess.demo = "", None, False
    sessions.drop_state(sess.sid)
    response = JSONResponse({"ok": True})
    if sess.remember:
        sessions.save(response, request, sess)
    else:
        sessions.clear(response)
    return response


# ---- Read-only portfolio data -------------------------------------------


def _kite(request: Request, method: str, *args):
    account = _account(sessions.load(request))
    try:
        return getattr(account, method)(*args)
    except KiteException as e:
        raise HTTPException(502, f"Kite API error: {e}")


@site.get("/api/holdings")
def holdings(request: Request):
    return _kite(request, "holdings")


@site.get("/api/positions")
def positions(request: Request):
    return _kite(request, "positions")


@site.get("/api/funds")
def funds(request: Request):
    return _kite(request, "funds")


@site.get("/api/quotes")
def quotes(request: Request, instruments: str):
    items = [i.strip() for i in instruments.split(",") if i.strip()][:50]
    return _kite(request, "quotes", items)


@site.get("/api/market")
def market(request: Request):
    return _kite(request, "market")


# ---- AI: copilot, news, insights ---------------------------------------------


def _ai_error(e: Exception, ai_name: str) -> tuple[int, str]:
    if isinstance(e, anthropic.AuthenticationError):
        return 400, "Your Claude API key is invalid. Update it in Settings."
    if isinstance(e, anthropic.RateLimitError):
        return 429, "Claude API rate limit hit. Try again shortly."
    if isinstance(e, anthropic.APIStatusError):
        return 502, f"Claude API error ({e.status_code}): {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return 502, "Could not reach the Claude API."
    if isinstance(e, anthropic.AnthropicError):
        return 500, f"Claude client error: {e}"
    if isinstance(e, openai.AuthenticationError | openai.PermissionDeniedError):
        return 400, f"Your {ai_name} API key is invalid. Update it in Settings."
    if isinstance(e, openai.RateLimitError):
        return 429, f"{ai_name} rate limit or free-tier quota hit. Try again shortly."
    if isinstance(e, openai.NotFoundError):
        return 400, f"{ai_name} couldn't find that model. Check the model name in Settings."
    if isinstance(e, openai.APIStatusError):
        return 502, f"{ai_name} API error ({e.status_code}): {e.message}"
    if isinstance(e, openai.APIConnectionError):
        return 502, f"Could not reach the {ai_name} API."
    if isinstance(e, openai.OpenAIError):
        return 500, f"{ai_name} client error: {e}"
    if isinstance(e, ResearchError):
        return 502, str(e)
    if isinstance(e, KiteException):
        return 502, f"Kite API error: {e}"
    raise e


def _research(request: Request, key: str, refresh: bool):
    """Starts or polls a background research job: {"status": "running"} until {"status": "ready", ...}."""
    sess = sessions.load(request)
    account, ai = _account(sess), _ai(sess)
    try:
        return research.get(sessions.state(sess.sid), key, refresh, account, ai)
    except (NotLoggedIn, NoAIKey):
        raise
    except Exception as e:
        status_code, detail = _ai_error(e, ai.name)
        raise HTTPException(status_code, detail)


@site.get("/api/news")
def news(request: Request, refresh: bool = False):
    return _research(request, "news", refresh)


@site.get("/api/insights")
def insights(request: Request, refresh: bool = False):
    return _research(request, "insights", refresh)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@site.post("/api/chat")
def chat(req: ChatRequest, request: Request):
    """Streams copilot events as Server-Sent Events."""
    sess = sessions.load(request)
    account, ai = _account(sess), _ai(sess)
    agent = sessions.state(sess.sid).agent

    def events():
        try:
            for event in agent.chat_stream(req.message, account, ai):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            try:
                _, detail = _ai_error(e, ai.name)
            except Exception:
                detail = f"Unexpected error: {e}"
            yield f"data: {json.dumps({'type': 'error', 'message': detail})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@site.post("/api/chat/reset")
def chat_reset(request: Request):
    sessions.state(sessions.load(request).sid).agent.reset()
    return {"ok": True}


# ---- Entry point: mount under BASE_PATH ------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

# Reachable without the origin secret: host health checks (Fly/Render probe "/"). They return no data.
PUBLIC_PATHS = {"/healthz", "/"} if config.BASE_PATH else {"/healthz"}


@app.middleware("http")
async def require_origin_secret(request: Request, call_next):
    # Optional: only requests forwarded by the Cloudflare Worker carry the secret.
    if config.ORIGIN_SECRET and request.url.path not in PUBLIC_PATHS:
        supplied = request.headers.get("x-origin-auth", "")
        if not hmac.compare_digest(supplied, config.ORIGIN_SECRET):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


if config.BASE_PATH:

    @app.get("/", include_in_schema=False)
    def root():
        # The app itself lives under BASE_PATH; this only answers uptime checks on the bare host.
        return {"ok": True, "app": f"{config.BASE_PATH}/"}

    @app.get(config.BASE_PATH, include_in_schema=False)
    def base_redirect():
        # Relative URLs in the page need the trailing slash.
        return RedirectResponse(HOME)

app.mount(config.BASE_PATH or "/", site)
