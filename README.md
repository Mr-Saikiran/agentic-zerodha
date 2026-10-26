# Agentic Zerodha

A local web app for your Zerodha account: a portfolio dashboard with an AI copilot built in.

- **Overview:** portfolio value, today's and overall P&L, allocation donut, today's movers, P&L by holding, live index ticker
- **Copilot:** a side panel that streams answers and shows each tool call ("Reading holdings", "Searching the web"). Every table row, news story and idea has an **✦ Ask** button that hands it to the copilot
- **News:** Claude searches the web for the past week's news on your largest holdings plus market-wide stories, tagged by sentiment and impact
- **AI Insights:** a portfolio health score, sector allocation, strengths and risks, and ideas on where to add, trim or diversify

**Read-only for now.** The app cannot place, modify or cancel orders. AI insights are not investment advice.

News and Insights each run a Claude request with web search, so they cost a few cents per run. Results are cached for 30 minutes; use **Refresh** to re-run.

**Preview without logging in:** set `DEMO_MODE=1` in `.env` to load sample portfolio data. The AI features still need `ANTHROPIC_API_KEY`.

## Stack

- **Backend:** FastAPI on `http://127.0.0.1:5000`
- **Broker:** [Kite Connect](https://kite.trade/docs/connect/v3/) via the `kiteconnect` SDK
- **Agent:** Claude (`claude-opus-5-5`) through the Anthropic SDK, using read-only tools
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
   `KITE_API_SECRET` and `ANTHROPIC_API_KEY`. `.env` is git-ignored. Never commit it.
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

## Security notes

- The server binds to `127.0.0.1` only. Don't expose it publicly without adding authentication.
- `KITE_API_SECRET` stays server-side. If it ever leaks, regenerate it in the Kite console.
- Automating the Zerodha login (storing your password or TOTP) violates Zerodha's terms. Log in through the browser.

## Roadmap

- [ ] Order placement with explicit per-order human approval and size/instrument limits
- [x] Streaming chat responses
- [ ] Live prices via KiteTicker WebSocket
- [ ] Persist chat history and insights across restarts
