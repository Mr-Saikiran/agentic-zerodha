# Agentic Zerodha

A local web app for your Zerodha account: a portfolio dashboard with an AI copilot built in.

- **Overview:** portfolio value, today's and overall P&L, allocation donut, today's movers, P&L by holding, live index ticker
- **Copilot:** a side panel that streams answers and shows each tool call ("Reading holdings", "Searching the web"). Every table row, news story and idea has an **✦ Ask** button that hands it to the copilot
- **News:** Claude searches the web for the past week's news on your largest holdings plus market-wide stories, tagged by sentiment and impact
- **AI Insights:** a portfolio health score, sector allocation, strengths and risks, and ideas on where to add, trim or diversify

**Read-only for now.** The app cannot place, modify or cancel orders. AI insights are not investment advice.

Results of News and Insights are cached for 30 minutes; use **Refresh** to re-run.

**Preview without logging in:** set `DEMO_MODE=1` in `.env` to load sample portfolio data. The AI features still need an AI provider key.

## Stack

- **Backend:** FastAPI on `http://127.0.0.1:5000`
- **Broker:** [Kite Connect](https://kite.trade/docs/connect/v3/) via the `kiteconnect` SDK
- **AI:** pick a provider with one key:
  - **Gemini** (`GEMINI_API_KEY`, free tier): `gemini-flash-latest` via Gemini's OpenAI-compatible API. News comes from Google News RSS (free, no key).
  - **Claude** (`ANTHROPIC_API_KEY`, pay-per-use): `claude-opus-5-5` with Claude's built-in web search.
  - **Any OpenAI-compatible API** (`LLM_PROVIDER=openai`, `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `COPILOT_MODEL`): OpenRouter, Groq, OpenCode, Ollama...
- **Frontend:** plain HTML/CSS/JS in `static/`

## Setup

1. **Kite developer console** (https://developers.kite.trade). Set your app's:
   - Redirect URL: `http://127.0.0.1:5000/callback`
   - Postback URL: leave blank
2. **Install:**
   ```bash
   python -m venv .venv
   .venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
   pip install -r requirements.txt
   ```
3. **Configure secrets:** copy `.env.example` to `.env` and fill in `KITE_API_KEY`,
   `KITE_API_SECRET` and `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`). `.env` is git-ignored. Never commit it.
4. **Run:**
   ```bash
   uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
   ```
5. Open http://127.0.0.1:5000 and click **Log in with Zerodha**.

Kite access tokens expire every morning around 6 AM IST, so you'll log in once a day.
The token is cached in `.kite_session.json` (git-ignored) so restarting the server
doesn't log you out.

## How it works

| Route | Purpose |
|---|---|
| `GET /login` | Redirects to Kite login with a CSRF `state` passed via `redirect_params` |
| `GET /callback` | Verifies `state`, exchanges `request_token` for an access token |
| `GET /api/holdings`, `/api/positions`, `/api/funds`, `/api/market`, `/api/quotes?instruments=NSE:INFY` | Read-only portfolio and index data |
| `POST /api/chat` | Streams the copilot's reply as Server-Sent Events (tool-use loop in `app/agent.py`) |
| `POST /api/chat/reset` | Clears the conversation |
| `GET /api/news?refresh=true` | News digest from Claude + web search (`app/research.py`) |
| `GET /api/insights?refresh=true` | AI portfolio review (`app/research.py`) |

The copilot's tools are `get_profile`, `get_holdings`, `get_positions`, `get_funds`, `get_quotes` and Claude's server-side `web_search`.
News and Insights return structured JSON by having Claude call a strict `submit_news` / `submit_insights` tool.

## Deploying to techswitch.in/equity

```
techswitch.in/*         → existing static site (unchanged)
techswitch.in/equity/*  → Cloudflare Worker (deploy/) → FastAPI container
```

### On Fly.io

`fly.toml` is set up for one always-on machine in Singapore (`sin`) with a 1 GB volume at `/data`.

```bash
fly auth login
fly apps create agentic-zerodha            # pick another name if taken, and update `app` in fly.toml
fly volumes create equity_data --region sin --size 1
fly secrets set KITE_API_KEY=... KITE_API_SECRET=... GEMINI_API_KEY=... ORIGIN_SECRET=...
fly deploy --ha=false                       # exactly one machine
```

Check it: `curl https://agentic-zerodha.fly.dev/healthz` returns `{"ok":true}`, and
`https://agentic-zerodha.fly.dev/equity/` returns 403 (no secret), as it should. Then set
`ORIGIN_URL = "https://agentic-zerodha.fly.dev"` in `deploy/wrangler.toml` and continue from step 2 below.
Logs: `fly logs`.

### Any host

1. **Run the container** on an always-on host (Render, Railway, Fly.io or a VM). Run exactly **one instance**:
   the Kite session, chat history and research jobs live in memory. Mount a volume at `/data` to keep the
   Kite token across restarts. Set these environment variables:

   | Variable | Value |
   |---|---|
   | `BASE_PATH` | `/equity` |
   | `KITE_API_KEY`, `KITE_API_SECRET` | from your **production** Kite app |
   | `KITE_REDIRECT_URL` | `https://techswitch.in/equity/callback` |
   | `GEMINI_API_KEY` (or `ANTHROPIC_API_KEY`) | your AI provider key |
   | `ORIGIN_SECRET` | a long random string, e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

   Health check path: `/healthz` (it doesn't need the secret).
2. **Kite developer console:** create a second Kite app for production with Redirect URL
   `https://techswitch.in/equity/callback`. Keep the first one for local development at `127.0.0.1:5000`.
3. **Cloudflare Worker:** edit `ORIGIN_URL` in `deploy/wrangler.toml`, then from `deploy/`:
   ```bash
   npx wrangler secret put ORIGIN_SECRET
   npx wrangler deploy
   ```
   If techswitch.in is attached to a Worker as a *Custom Domain*, routes don't run before it. In that case add
   this forwarding logic to the site's own Worker for paths starting with `/equity` instead.
4. **Cloudflare Access (required):** Zero Trust → Access → Applications → Self-hosted, domain `techswitch.in`,
   path `equity`, policy *Allow* → *Emails* → your email only. Without this, anyone who opens the URL sees your
   portfolio and spends your Claude credits.

To test the path prefix locally: `BASE_PATH=/equity uvicorn app.main:app --port 5000`, then open
http://127.0.0.1:5000/equity/.

## Security notes

- The app has no login of its own. When it's on the internet, Cloudflare Access is what keeps it private.
- With `ORIGIN_SECRET` set, the container refuses any request that didn't come through the Worker.

- The server binds to `127.0.0.1` only. Don't expose it publicly without adding authentication.
- `KITE_API_SECRET` stays server-side. If it ever leaks, regenerate it in the Kite console.
- Automating the Zerodha login (storing your password or TOTP) violates Zerodha's terms. Log in through the browser.

## Roadmap

- [ ] Order placement with explicit per-order human approval and size/instrument limits
- [x] Streaming chat responses
- [ ] Live prices via KiteTicker WebSocket
- [ ] Persist chat history and insights across restarts
