"""Kite Connect session handling and read-only portfolio helpers."""

import json
import secrets
import threading
from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

from . import config

IST = timezone(timedelta(hours=5, minutes=30))
# Kite access tokens are invalidated every morning around 6 AM IST.
TOKEN_RESET = time(6, 0)


class NotLoggedIn(Exception):
    pass


class KiteSession:
    def __init__(self) -> None:
        self.kite = KiteConnect(api_key=config.KITE_API_KEY)
        self._lock = threading.Lock()
        self._pending_states: set[str] = set()
        self.user: dict | None = None
        self._load()

    # ---- login flow -------------------------------------------------------

    def login_url(self) -> str:
        # Kite echoes `redirect_params` back to the redirect URL; we use it as a CSRF state.
        state = secrets.token_urlsafe(16)
        with self._lock:
            self._pending_states.add(state)
        return f"{self.kite.login_url()}&redirect_params={quote(f'state={state}')}"

    def complete_login(self, request_token: str, state: str | None) -> dict:
        with self._lock:
            if not state or state not in self._pending_states:
                raise ValueError("Invalid or missing login state; start again from /login.")
            self._pending_states.discard(state)
        data = self.kite.generate_session(request_token, api_secret=config.KITE_API_SECRET)
        self.kite.set_access_token(data["access_token"])
        self.user = {k: data.get(k) for k in ("user_id", "user_name", "email", "broker")}
        self._save(data["access_token"])
        return self.user

    def logout(self) -> None:
        try:
            if self.is_logged_in():
                self.kite.invalidate_access_token()
        except Exception:
            pass
        self.kite.set_access_token(None)
        self.user = None
        config.SESSION_FILE.unlink(missing_ok=True)

    def is_logged_in(self) -> bool:
        return self.kite.access_token is not None

    # ---- persistence ------------------------------------------------------

    def _save(self, access_token: str) -> None:
        config.SESSION_FILE.write_text(
            json.dumps(
                {
                    "access_token": access_token,
                    "user": self.user,
                    "created_at": datetime.now(IST).isoformat(),
                }
            )
        )

    def _load(self) -> None:
        if not config.SESSION_FILE.exists():
            return
        try:
            data = json.loads(config.SESSION_FILE.read_text())
            created = datetime.fromisoformat(data["created_at"])
        except (ValueError, KeyError):
            config.SESSION_FILE.unlink(missing_ok=True)
            return
        if created < _last_token_reset():
            config.SESSION_FILE.unlink(missing_ok=True)
            return
        self.kite.set_access_token(data["access_token"])
        self.user = data.get("user")

    # ---- read-only API wrappers ---------------------------------------------

    def _call(self, fn, *args, **kwargs):
        if not self.is_logged_in():
            raise NotLoggedIn()
        try:
            return fn(*args, **kwargs)
        except TokenException:
            # Token expired or revoked on Zerodha's side.
            self.kite.set_access_token(None)
            self.user = None
            config.SESSION_FILE.unlink(missing_ok=True)
            raise NotLoggedIn()

    def profile(self) -> dict:
        p = self._call(self.kite.profile)
        return {k: p.get(k) for k in ("user_id", "user_name", "email", "broker", "exchanges", "products")}

    def holdings(self) -> list[dict]:
        rows = self._call(self.kite.holdings)
        out = []
        for h in rows:
            qty = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
            avg, ltp = h.get("average_price") or 0, h.get("last_price") or 0
            out.append(
                {
                    "symbol": h.get("tradingsymbol"),
                    "exchange": h.get("exchange"),
                    "quantity": qty,
                    "average_price": avg,
                    "last_price": ltp,
                    "invested": round(qty * avg, 2),
                    "current_value": round(qty * ltp, 2),
                    "pnl": round(h.get("pnl") or 0, 2),
                    "day_change_pct": round(h.get("day_change_percentage") or 0, 2),
                }
            )
        return out

    def positions(self) -> list[dict]:
        net = self._call(self.kite.positions).get("net", [])
        return [
            {
                "symbol": p.get("tradingsymbol"),
                "exchange": p.get("exchange"),
                "product": p.get("product"),
                "quantity": p.get("quantity"),
                "average_price": p.get("average_price"),
                "last_price": p.get("last_price"),
                "pnl": round(p.get("pnl") or 0, 2),
            }
            for p in net
        ]

    def funds(self) -> dict:
        eq = self._call(self.kite.margins).get("equity", {})
        available = eq.get("available", {})
        utilised = eq.get("utilised", {})
        return {
            "net": eq.get("net"),
            "cash": available.get("cash"),
            "live_balance": available.get("live_balance"),
            "collateral": available.get("collateral"),
            "utilised": utilised.get("debits"),
        }

    def quotes(self, instruments: list[str]) -> dict:
        data = self._call(self.kite.quote, instruments)
        return {
            key: {
                "last_price": q.get("last_price"),
                "change_pct": round(q.get("net_change") / q["ohlc"]["close"] * 100, 2)
                if q.get("ohlc", {}).get("close")
                else None,
                "ohlc": q.get("ohlc"),
                "volume": q.get("volume"),
            }
            for key, q in data.items()
        }


def _last_token_reset() -> datetime:
    now = datetime.now(IST)
    reset = datetime.combine(now.date(), TOKEN_RESET, tzinfo=IST)
    return reset if now >= reset else reset - timedelta(days=1)


session = KiteSession()
