import anthropic
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from kiteconnect.exceptions import KiteException
from pydantic import BaseModel, Field

from . import config
from .agent import agent
from .kite_client import NotLoggedIn, session

app = FastAPI(title="Agentic Zerodha")
app.mount("/static", StaticFiles(directory=config.STATIC_DIR), name="static")


@app.exception_handler(NotLoggedIn)
async def not_logged_in(_: Request, __: NotLoggedIn):
    return JSONResponse(status_code=401, content={"detail": "Not logged in to Zerodha. Visit /login."})


@app.get("/")
def index():
    return FileResponse(config.STATIC_DIR / "index.html")


# ---- Kite login flow -------------------------------------------------------


@app.get("/login")
def login():
    if missing := config.missing_settings():
        raise HTTPException(500, f"Set {', '.join(missing)} in .env and restart the server.")
    return RedirectResponse(session.login_url())


@app.get("/callback")
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
    return RedirectResponse("/")


@app.post("/logout")
def logout():
    session.logout()
    agent.reset()
    return {"ok": True}


@app.get("/api/status")
def status():
    return {
        "logged_in": session.is_logged_in(),
        "user": session.user,
        "missing_settings": config.missing_settings(),
        "model": config.CLAUDE_MODEL,
    }


# ---- Read-only portfolio data -------------------------------------------


def _kite(fn, *args):
    try:
        return fn(*args)
    except KiteException as e:
        raise HTTPException(502, f"Kite API error: {e}")


@app.get("/api/holdings")
def holdings():
    return _kite(session.holdings)


@app.get("/api/positions")
def positions():
    return _kite(session.positions)


@app.get("/api/funds")
def funds():
    return _kite(session.funds)


@app.get("/api/quotes")
def quotes(instruments: str):
    items = [i.strip() for i in instruments.split(",") if i.strip()][:50]
    return _kite(session.quotes, items)


# ---- Agent ----------------------------------------------------------------


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@app.post("/api/chat")
def chat(req: ChatRequest):
    if not session.is_logged_in():
        raise NotLoggedIn()
    try:
        return agent.chat(req.message)
    except anthropic.AuthenticationError:
        raise HTTPException(500, "Claude API credentials are missing or invalid. Set ANTHROPIC_API_KEY in .env.")
    except anthropic.RateLimitError:
        raise HTTPException(429, "Claude API rate limit hit. Try again shortly.")
    except anthropic.APIStatusError as e:
        raise HTTPException(502, f"Claude API error ({e.status_code}): {e.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(502, "Could not reach the Claude API.")
    except anthropic.AnthropicError as e:
        # e.g. no credentials found when the client is first created
        raise HTTPException(500, f"Claude client error: {e}")


@app.post("/api/chat/reset")
def chat_reset():
    agent.reset()
    return {"ok": True}
