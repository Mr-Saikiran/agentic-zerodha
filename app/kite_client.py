"""Kite Connect accounts (one per visitor, from their own API key and access token) and
read-only portfolio helpers."""

from datetime import datetime, time, timedelta, timezone
from urllib.parse import quote

from kiteconnect import KiteConnect
from kiteconnect.exceptions import TokenException

IST = timezone(timedelta(hours=5, minutes=30))
# Kite access tokens are invalidated every morning around 6 AM IST.
TOKEN_RESET = time(6, 0)


class NotLoggedIn(Exception):
    pass


def login_url(api_key: str, state: str) -> str:
    # Kite echoes `redirect_params` back to the redirect URL; we use it as a CSRF state.
    return f"{KiteConnect(api_key=api_key).login_url()}&redirect_params={quote(f'state={state}')}"


def create_session(api_key: str, api_secret: str, request_token: str) -> tuple[str, dict]:
    """Exchanges the request_token from Kite's redirect for an access token. Returns (token, user)."""
    data = KiteConnect(api_key=api_key).generate_session(request_token, api_secret=api_secret)
    user = {k: data.get(k) for k in ("user_id", "user_name", "email", "broker")}
    return data["access_token"], user


def token_valid(token_at: float) -> bool:
    return token_at >= _last_token_reset().timestamp()


def invalidate(api_key: str, access_token: str) -> None:
    try:
        KiteConnect(api_key=api_key, access_token=access_token).invalidate_access_token()
    except Exception:
        pass


class KiteAccount:
    def __init__(self, api_key: str, access_token: str) -> None:
        if not api_key or not access_token:
            raise NotLoggedIn()
        self.kite = KiteConnect(api_key=api_key, access_token=access_token)

    def _call(self, fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except TokenException:
            # Token expired or revoked on Zerodha's side.
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


class DemoAccount:
    """Sample portfolio for the "Try with demo data" option. Not real market data."""

    user = {"user_id": "DEMO01", "user_name": "Demo Investor", "email": None, "broker": "ZERODHA"}

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

