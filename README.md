# Agentic Zerodha

An open-source web app for Zerodha investors: a portfolio dashboard with an AI copilot built in.
Anyone can use it with **their own keys**: a Kite Connect app, plus a Gemini, Claude or other AI API key.

- **Overview:** portfolio value, today's and overall P&L, allocation donut, today's movers, P&L by holding, live index ticker
- **Copilot:** a side panel that streams answers and shows each tool call ("Reading holdings", "Searching news"). Every table row, news story and idea has an **✦ Ask** button that hands it to the copilot
- **News:** the week's news on your largest holdings plus market-wide stories, tagged by sentiment and impact
- **AI Insights:** a portfolio health score, sector allocation, strengths and risks, and ideas on where to add, trim or diversify
- **Demo mode:** try everything on a sample portfolio without a Zerodha account

**Read-only.** The app cannot place, modify or cancel orders. AI insights are not investment advice.

## Using it

1. Create a Kite Connect app at [developers.kite.trade](https://developers.kite.trade) and set its **Redirect URL**
   to the one the app shows you (for example `https://techswitch.in/equity/callback`). Leave the Postback URL blank.
2. Get an AI key. [Gemini](https://aistudio.google.com/apikey) has a free tier. Claude and any OpenAI-compatible
   provider (OpenRouter, Groq, OpenCode...) also work.
3. Open the app, paste your Kite API key and secret and your AI key, then **Log in with Zerodha**.

Kite logs everyone out around 6 AM IST, so you'll click **Log in with Zerodha** once a day. Your keys stay saved.

### Where your keys go

- Your Kite API key and secret, Kite access token and AI key are **encrypted into an HttpOnly cookie in your
  browser** (with the server's `SESSION_SECRET`). The server decrypts it for each of your requests and never
  writes it to disk or logs it. Untick "Remember" to keep them only until you close the browser; **Forget my keys**
  in Settings deletes them.
- Whoever runs the server can technically decrypt the cookies it receives. If that's a concern, self-host it;
  it takes a few minutes (below).
- Chat history and research results are kept in server memory per session and are lost when the server restarts.

## AI providers

| Provider | Model (default) | News source |
|---|---|---|
| Google Gemini (free tier) | `gemini-flash-latest` | Google News RSS (free, no key) |
| Anthropic Claude | `claude-opus-5-5` | Claude's built-in web search |
| OpenAI-compatible | your choice (base URL + model) | Google News RSS |

You can override the model in Settings. A custom base URL must be public `https://`; set `ALLOW_LOCAL_AI_URL=1`
when self-hosting to use something like Ollama at `http://localhost:11434/v1`.

## Run it locally

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env          # optional: set SESSION_SECRET so restarts don't clear saved keys
uvicorn app.main:app --host 127.0.0.1 --port 5000 --reload
```

Open http://127.0.0.1:5000. Your Kite app's Redirect URL for local use is `http://127.0.0.1:5000/callback`.

## Configuration

Only deployment settings; user keys are never configured on the server.

| Variable | Purpose |
|---|---|
| `SESSION_SECRET` | Encrypts session cookies. Set a long random value in production; changing it signs everyone out |
| `BASE_PATH` | URL prefix, e.g. `/equity` for `https://techswitch.in/equity/` |
| `KITE_REDIRECT_URL` | Redirect URL shown to users. Default: derived from the request |
| `ORIGIN_SECRET` | Optional. Only accept requests with this `X-Origin-Auth` header (set the same on the Cloudflare Worker) |
| `ALLOW_LOCAL_AI_URL` | Self-hosting only: allow `http://` and private-network AI base URLs |

## How it works

| Route | Purpose |
|---|---|
| `GET /api/status` | Session state: keys saved (as hints only), logged in, provider, callback URL |
| `POST /api/setup` | Saves the user's keys into their encrypted cookie |
| `POST /api/demo` | Starts a demo session with sample data |
| `GET /login` | Redirects to Kite login with the user's API key and a CSRF `state` |
| `GET /callback` | Verifies `state`, exchanges `request_token` for an access token, stores it in the cookie |
| `POST /logout`, `POST /api/forget` | Ends the Kite session / deletes all saved keys |
| `GET /api/holdings`, `/api/positions`, `/api/funds`, `/api/market`, `/api/quotes` | Read-only portfolio and index data |
| `POST /api/chat` | Streams the copilot's reply as Server-Sent Events (`app/agent.py`) |
| `GET /api/news`, `/api/insights` | Background research jobs the UI polls (`app/research.py`) |

POST requests must carry `X-Requested-With: fetch` (the page's own fetch calls do), so other sites can't use a
visitor's cookie.

## Deploying (example: techswitch.in/equity on Render)

```
techswitch.in/*         → existing static site (unchanged)
techswitch.in/equity/*  → Cloudflare Worker (deploy/) → FastAPI container on Render
```

1. **Render:** dashboard → **New → Blueprint** → this repo. `render.yaml` creates a free Docker web service in
   Singapore with `BASE_PATH=/equity` and a generated `SESSION_SECRET`. No API keys are needed.
2. **Cloudflare Worker:** set `ORIGIN_URL` in `deploy/wrangler.toml` to the Render URL, then from `deploy/`:
   ```bash
   npx wrangler deploy
   ```
   Its cron trigger pings `/healthz` every 10 minutes during market hours so the free service doesn't sleep.
   If techswitch.in is attached to a Worker as a *Custom Domain*, routes don't run before it; add this forwarding
   to the site's own Worker for paths starting with `/equity` instead.
3. Open `https://techswitch.in/equity/`.

Render's free plan sleeps after 15 idle minutes (the next visit takes about a minute) and has no disk. Saved keys
and Kite logins survive that, because they're in the visitor's cookie; open chats don't.

`Dockerfile` runs anywhere (Fly.io, Railway, a VM): one worker, port from `$PORT`. `fly.toml` is an example for Fly.io.

## Security notes

- Treat the server as trusted with users' keys while their requests are in flight; self-host for full control.
- Automating the Zerodha login (storing passwords or TOTP) violates Zerodha's terms. Users log in through Kite's own page.
- If a Kite API secret ever leaks, regenerate it in the Kite developer console.

## Roadmap

- [ ] Order placement with explicit per-order human approval and size/instrument limits
- [x] Streaming chat responses
- [x] Bring-your-own-keys, multi-user
- [ ] Live prices via KiteTicker WebSocket
- [ ] Persist chat history across restarts
