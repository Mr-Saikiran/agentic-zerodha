"""Free news headlines from Google News RSS (no API key).

Used instead of Claude's built-in web search when the AI provider is Gemini or another
OpenAI-compatible model.
"""

import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime

FEED = "https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
CACHE_TTL = 15 * 60
MARKET_QUERIES = ["Sensex Nifty stock market today", "RBI policy FII flows India markets"]

_cache: dict[str, tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def search(query: str, limit: int = 8, days: int = 7) -> list[dict]:
    """Recent headlines for a query: [{title, url, source, published}], newest first."""
    key = f"{query}|{limit}|{days}"
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]

    q = urllib.parse.quote(f"{query} when:{days}d")
    req = urllib.request.Request(FEED.format(q=q), headers={"User-Agent": "Mozilla/5.0 agentic-zerodha"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        root = ET.fromstring(resp.read())

    items = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        source = (item.findtext("source") or "").strip()
        # Google appends " - Publisher" to titles; the publisher is in <source> already.
        if source and title.endswith(f" - {source}"):
            title = title[: -len(source) - 3]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            published = ""
        items.append({"title": title, "url": item.findtext("link") or "", "source": source, "published": published})
    items.sort(key=lambda i: i["published"], reverse=True)
    items = items[:limit]

    with _lock:
        _cache[key] = (time.time(), items)
    return items


def portfolio_news(symbols: list[str], per_symbol: int = 4) -> list[dict]:
    """Headlines for each symbol plus market-wide stories, de-duplicated, each with an `id` and `symbol`."""
    queries = [(s, f"{s} share") for s in symbols] + [(None, q) for q in MARKET_QUERIES]

    def fetch(pair):
        symbol, query = pair
        try:
            return [{**a, "symbol": symbol} for a in search(query, limit=per_symbol)]
        except Exception:
            return []  # one failed feed shouldn't sink the digest

    with ThreadPoolExecutor(max_workers=6) as pool:
        batches = list(pool.map(fetch, queries))

    seen, out = set(), []
    for batch in batches:
        for a in batch:
            if a["title"].lower() in seen:
                continue
            seen.add(a["title"].lower())
            out.append({**a, "id": len(out)})
    return out
