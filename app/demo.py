"""Sample Kite-shaped data for DEMO_MODE. Not real market data."""


def _holding(symbol: str, qty: int, avg: float, ltp: float, close: float, exchange: str = "NSE") -> dict:
    return {
        "tradingsymbol": symbol,
        "exchange": exchange,
        "quantity": qty,
        "t1_quantity": 0,
        "average_price": avg,
        "last_price": ltp,
        "close_price": close,
        "pnl": round((ltp - avg) * qty, 2),
        "day_change": round(ltp - close, 2),
        "day_change_percentage": round((ltp - close) / close * 100, 2),
    }


HOLDINGS = [
    _holding("RELIANCE", 40, 2410.50, 2948.20, 2921.75),
    _holding("HDFCBANK", 60, 1585.00, 1712.40, 1724.10),
    _holding("INFY", 55, 1490.20, 1865.35, 1842.90),
    _holding("TCS", 18, 3620.00, 4105.80, 4112.25),
    _holding("ICICIBANK", 70, 905.40, 1268.15, 1255.60),
    _holding("ITC", 150, 412.30, 468.90, 471.05),
    _holding("TATAMOTORS", 85, 795.60, 742.30, 756.85),
    _holding("ASIANPAINT", 20, 3180.00, 2875.40, 2861.20),
    _holding("BHARTIARTL", 45, 1120.00, 1642.75, 1618.30),
    _holding("NIFTYBEES", 300, 228.40, 271.85, 270.10),
]

POSITIONS = [
    {"tradingsymbol": "SBIN", "exchange": "NSE", "product": "MIS", "quantity": 100,
     "average_price": 812.40, "last_price": 818.95, "pnl": 655.0},
]

MARGINS = {
    "equity": {
        "net": 184250.75,
        "available": {"cash": 192500.00, "live_balance": 184250.75, "collateral": 0},
        "utilised": {"debits": 8249.25},
    }
}

INDEX_QUOTES = {
    "NSE:NIFTY 50": {"last_price": 24812.35, "ohlc": {"close": 24698.10}},
    "BSE:SENSEX": {"last_price": 81455.60, "ohlc": {"close": 81120.45}},
    "NSE:NIFTY BANK": {"last_price": 53240.80, "ohlc": {"close": 53398.25}},
    "NSE:NIFTY IT": {"last_price": 41205.15, "ohlc": {"close": 40880.70}},
    "NSE:INDIA VIX": {"last_price": 13.42, "ohlc": {"close": 13.95}},
}
