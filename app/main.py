import hmac
import json

import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect.exceptions import KiteException
from pydantic import BaseModel, Field

from . import config, research
from .agent import agent
from .kite_client import NotLoggedIn, session
from .research import ResearchError

# `site` holds every route; `app` (bottom of file) mounts it under BASE_PATH, e.g. /equity.
site = FastAPI(title="Agentic Zerodha", docs_url=None, redoc_url=None, openapi_url=None)
site.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")

# The page lives at "<BASE_PATH>/" and uses relative URLs, so it works under any prefix.
HOME = f"{config.BASE_PATH}/"


@site.exception_handler(NotLoggedIn)
async def not_logged_in(_: Request, __: NotLoggedIn):
    return JSONResponse(status_code=401, content={"detail": "Not logged in to Zerodha."})


@site.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


# ---- Kite login flow -------------------------------------------------------


@site.get("/login")
def login():
    if missing := config.missing_settings():
        raise HTTPException(500, f"Set {', '.join(missing)} in .env and restart the server.")
    return RedirectResponse(session.login_url())


@site.get("/callback")
def callback(status: str | None = None, request_token: str | None = None, state: str | None = None):
    if status != "success" or not request_token:
        raise HTTPException(400, "Zerodha login was cancelled or failed.")
    try:
        session.complete_login(request_token, state)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except KiteException as e:
        raise HTTPException(400, f"Could not create Kite session: {e}")
    agent.reset()
    return RedirectResponse(HOME)


@site.post("/logout")
def logout():
    session.logout()
    agent.reset()
    research.reset()
    return {"ok": True}


@site.get("/api/status")
def status():
    return {
        "logged_in": session.is_logged_in(),
        "user": session.user,
        "missing_settings": config.missing_settings(),
        "model": config.CLAUDE_MODEL,
        "demo": config.DEMO_MODE,
    }


# ---- Read-only portfolio data -------------------------------------------


def _kite(fn, *args):
    try:
        return fn(*args)
    except KiteException as e:
        raise HTTPException(502, f"Kite API error: {e}")


@site.get("/api/holdings")
def holdings():
    return _kite(session.holdings)


@site.get("/api/positions")
def positions():
    return _kite(session.positions)


@site.get("/api/funds")
def funds():
    return _kite(session.funds)


@site.get("/api/quotes")
def quotes(instruments: str):
    items = [i.strip() for i in instruments.split(",") if i.strip()][:50]
    return _kite(session.quotes, items)


@site.get("/api/market")
def market():
    return _kite(session.market)


# ---- AI: copilot, news, insights ---------------------------------------------


def _claude_error(e: Exception) -> tuple[int, str]:
    if isinstance(e, anthropic.AuthenticationError):
        return 500, "Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY in .env."
    if isinstance(e, anthropic.RateLimitError):
        return 429, "Claude API rate limit hit. Try again shortly."
    if isinstance(e, anthropic.APIStatusError):
        return 502, f"Claude API error ({e.status_code}): {e.message}"
    if isinstance(e, anthropic.APIConnectionError):
        return 502, "Could not reach the Claude API."
    if isinstance(e, anthropic.AnthropicError):
        # e.g. no credentials found when the client is first created
        return 500, f"Claude client error: {e}"
    if isinstance(e, ResearchError):
        return 502, str(e)
    if isinstance(e, KiteException):
        return 502, f"Kite API error: {e}"
    raise e


def _research(key: str, refresh: bool):
    """Starts or polls a background research job: {"status": "running"} until {"status": "ready", ...}."""
    if not session.is_logged_in():
        raise NotLoggedIn()
    try:
        return research.get(key, refresh)
    except NotLoggedIn:
        raise
    except Exception as e:
        status, detail = _claude_error(e)
        raise HTTPException(status, detail)


@site.get("/api/news")
def news(refresh: bool = False):
    return _research("news", refresh)


@site.get("/api/insights")
def insights(refresh: bool = False):
    return _research("insights", refresh)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@site.post("/api/chat")
def chat(req: ChatRequest):
    """Streams copilot events as Server-Sent Events."""
    if not session.is_logged_in():
        raise NotLoggedIn()

    def events():
        try:
            for event in agent.chat_stream(req.message):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            try:
                _, detail = _claude_error(e)
            except Exception:
                detail = f"Unexpected error: {e}"
            yield f"data: {json.dumps({'type': 'error', 'message': detail})}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@site.post("/api/chat/reset")
def chat_reset():
    agent.reset()
    return {"ok": True}


# ---- Entry point: mount under BASE_PATH ------------------------------------------

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)


@app.middleware("http")
async def require_origin_secret(request: Request, call_next):
    # Only requests forwarded by the Cloudflare Worker carry the secret; direct hits to the origin are refused.
    if config.ORIGIN_SECRET and request.url.path != "/healthz":
        supplied = request.headers.get("x-origin-auth", "")
        if not hmac.compare_digest(supplied, config.ORIGIN_SECRET):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"ok": True}


if config.BASE_PATH:

    @app.get(config.BASE_PATH, include_in_schema=False)
    def base_redirect():
        # Relative URLs in the page need the trailing slash.
        return RedirectResponse(HOME)

app.mount(config.BASE_PATH or "/", site)
