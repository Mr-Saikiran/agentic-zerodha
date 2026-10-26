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
        return [_shape_holding(h) for h in self._call(self.kite.holdings)]

    def positions(self) -> list[dict]:
        return [_shape_position(p) for p in self._call(self.kite.positions).get("net", [])]

    def funds(self) -> dict:
        return _shape_funds(self._call(self.kite.margins))

    def quotes(self, instruments: list[str]) -> dict:
        return {key: _shape_quote(q) for key, q in self._call(self.kite.quote, instruments).items()}

    def market(self) -> list[dict]:
        data = self._call(self.kite.quote, [i for _, i in INDICES])
        return [{"name": name, **_shape_quote(data[i])} for name, i in INDICES if i in data]


INDICES = [
    ("NIFTY 50", "NSE:NIFTY 50"),
    ("SENSEX", "BSE:SENSEX"),
    ("BANK NIFTY", "NSE:NIFTY BANK"),
    ("NIFTY IT", "NSE:NIFTY IT"),
    ("INDIA VIX", "NSE:INDIA VIX"),
]


def _shape_holding(h: dict) -> dict:
    qty = (h.get("quantity") or 0) + (h.get("t1_quantity") or 0)
    avg, ltp = h.get("average_price") or 0, h.get("last_price") or 0
    return {
        "symbol": h.get("tradingsymbol"),
        "exchange": h.get("exchange"),
        "quantity": qty,
        "average_price": avg,
        "last_price": ltp,
        "invested": round(qty * avg, 2),
        "current_value": round(qty * ltp, 2),
        "pnl": round(h.get("pnl") or 0, 2),
        "day_change": round(qty * (h.get("day_change") or 0), 2),
        "day_change_pct": round(h.get("day_change_percentage") or 0, 2),
    }


def _shape_position(p: dict) -> dict:
    return {
        "symbol": p.get("tradingsymbol"),
        "exchange": p.get("exchange"),
        "product": p.get("product"),
        "quantity": p.get("quantity"),
        "average_price": p.get("average_price"),
        "last_price": p.get("last_price"),
        "pnl": round(p.get("pnl") or 0, 2),
    }


def _shape_funds(margins: dict) -> dict:
    eq = margins.get("equity", {})
    available = eq.get("available", {})
    return {
        "net": eq.get("net"),
        "cash": available.get("cash"),
        "live_balance": available.get("live_balance"),
        "collateral": available.get("collateral"),
        "utilised": eq.get("utilised", {}).get("debits"),
    }


def _shape_quote(q: dict) -> dict:
    close = (q.get("ohlc") or {}).get("close")
    change = q.get("net_change") or ((q.get("last_price") or 0) - close if close else 0)
    return {
        "last_price": q.get("last_price"),
        "change": round(change, 2),
        "change_pct": round(change / close * 100, 2) if close else None,
        "ohlc": q.get("ohlc"),
        "volume": q.get("volume"),
    }


class DemoSession:
    """Sample data for previewing the UI without a Zerodha login (DEMO_MODE=1)."""

    user = {"user_id": "DEMO01", "user_name": "Demo Investor", "email": None, "broker": "ZERODHA"}

    def login_url(self) -> str:
        return "/"

    def complete_login(self, request_token: str, state: str | None) -> dict:
        return self.user

    def logout(self) -> None:
        pass

    def is_logged_in(self) -> bool:
        return True

    def profile(self) -> dict:
        return {**self.user, "exchanges": ["NSE", "BSE"], "products": ["CNC", "MIS"]}

    def holdings(self) -> list[dict]:
        from .demo import HOLDINGS

        return [_shape_holding(h) for h in HOLDINGS]

    def positions(self) -> list[dict]:
        from .demo import POSITIONS

        return [_shape_position(p) for p in POSITIONS]

    def funds(self) -> dict:
        from .demo import MARGINS

        return _shape_funds(MARGINS)

    def quotes(self, instruments: list[str]) -> dict:
        from .demo import HOLDINGS

        by_symbol = {f"{h['exchange']}:{h['tradingsymbol']}": h for h in HOLDINGS}
        return {
            i: _shape_quote({"last_price": h["last_price"], "ohlc": {"close": h["close_price"]}})
            for i in instruments
            if (h := by_symbol.get(i))
        }

    def market(self) -> list[dict]:
        from .demo import INDEX_QUOTES

        return [{"name": name, **_shape_quote(INDEX_QUOTES[i])} for name, i in INDICES if i in INDEX_QUOTES]


def _last_token_reset() -> datetime:
    now = datetime.now(IST)
    reset = datetime.combine(now.date(), TOKEN_RESET, tzinfo=IST)
    return reset if now >= reset else reset - timedelta(days=1)


session = DemoSession() if config.DEMO_MODE else KiteSession()
