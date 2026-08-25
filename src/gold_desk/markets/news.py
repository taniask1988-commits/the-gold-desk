"""MARKET GAUNTLET per-symbol news — keyless Yahoo Finance RSS headlines.

Live-probed 2026-08-25 (GAUNTLET-P8-BUILDER) with the standard verified
UA — all HTTP 200:

    feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US
        → 18 <item>s (title/link/pubDate/description)
    s=BTC-USD   → 18 items      s=GC=F     → 16 items
    s=%5ENSEI   → 6 items       s=EURUSD=X → 19 items
    s=TSLA      → 12 items
    s=RELIANCE.NS / ICICIBANK.NS / USDINR=X → 200 with an EMPTY channel
        (Yahoo's headline RSS simply carries no NSE-listed or minor-FX
        symbols — that state is valid and cached, not an error)

The regex parse mirrors gold_desk.data.feeds._parse_rss (no XML
dependency): <item> blocks, CDATA-stripped fields, Yahoo tracking
wrappers unwrapped from links. Fail-soft like every markets module:
never raises, TTL file cache at <data_root>/cache/markets_news_<slug>.json
(300s — headlines move slower than quotes), stale-serve on network
failure. Max NEWS_MAX_ITEMS items per symbol.

Law boundary: display/education telemetry for the gauntlet surface,
NOT wired into the orchestrator's decision loop.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

# verified keyless UA (probed live — see GAUNTLET-P1 brief)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
YAHOO_HEADLINE_RSS = "https://feeds.finance.yahoo.com/rss/2.0/headline"

NEWS_TTL_S = 300         # 5 minutes — headlines lag quotes
NEWS_MAX_ITEMS = 8       # detail-page card length
HTTP_TIMEOUT = 8.0


def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ parse
def _unescape(text: str) -> str:
    """Minimal XML entity unescape (&amp; LAST so &amp;lt; stays &lt;)."""
    for ent, ch in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                    ("&#39;", "'"), ("&apos;", "'"), ("&amp;", "&")):
        text = text.replace(ent, ch)
    return text


def parse_headline_rss(xml: str, source: str = "Yahoo Finance") -> list[dict]:
    """Regex RSS parse → [{title, link, published, source}].

    Same pattern as gold_desk.data.feeds._parse_rss (which this mirrors
    so the markets plane stays dependency-free): one <item> capture per
    headline, per-tag field extraction with CDATA stripping, Yahoo
    tracking wrappers (?tsrc=rss / url=... redirects) unwrapped from
    links. Items without a title are dropped; empty feeds → [].
    """
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)

    def field(item: str, tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", item, re.DOTALL)
        if not m:
            return ""
        text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", m.group(1),
                      flags=re.DOTALL)
        return _unescape(text).strip()

    out: list[dict] = []
    for it in items:
        title = field(it, "title")
        if not title:
            continue
        link = field(it, "link")
        # unwrap yahoo redirect wrappers: ...?url=https%3A%2F%2Freal...
        m = re.search(r"url=([^&]+)", link)
        if m:
            link = urllib.parse.unquote(m.group(1))
        else:
            link = re.sub(r"\?.*tsrc=rss.*$", "", link)
        out.append({
            "title": title,
            "link": link,
            "published": field(it, "pubDate"),
            "source": source,
        })
    return out


# ------------------------------------------------------------------ cache
def _cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _cache_name(symbol: str) -> str:
    """Filesystem-safe per-symbol cache key. Yahoo symbols carry ^, =,
    ., / ('inr/usd') — slug what's slugable and append a short sha1 of
    the raw symbol so no two symbols ever collide ('GC=F' vs 'GC/F')."""
    slug = re.sub(r"[^A-Za-z0-9_-]+", "_", symbol).strip("_")[:24] or "sym"
    h = hashlib.sha1(symbol.encode()).hexdigest()[:8]
    return f"markets_news_{slug}_{h}"


def _cached_fetch(data_root: str | Path, name: str, ttl: int,
                  fetch) -> dict:
    """Cache-through fetch (feeds.py pattern) under <data_root>/cache/:
    fresh within TTL → fetch → stale-serve on error."""
    path = _cache_path(data_root, name)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < ttl:
        cached["cache_hit"] = True
        return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["cache_hit"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as e:  # noqa: BLE001 — display telemetry fails soft
        if cached:
            cached["cache_hit"] = True
            cached["stale_error"] = f"{type(e).__name__}"
            return cached
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "fetched_at": time.time(), "cache_hit": False}


# ------------------------------------------------------------------ fetch
def fetch_symbol_news(symbol: str, data_root: str | Path = "data") -> dict:
    """Per-symbol RSS headlines, fail-soft.

    Returns {ok, symbol, items: [{title, link, published, source}]}
    with at most NEWS_MAX_ITEMS items. An EMPTY channel is a valid,
    cached state (Yahoo's headline RSS carries no NSE-listed symbols —
    probed live, see module docstring), so it yields {ok: True,
    items: []} rather than an error; only transport/parse failures
    return {ok: False, error}. Never raises.
    """
    sym = str(symbol or "").strip()
    if not sym:
        return {"ok": False, "error": "no symbol", "items": []}
    url = (f"{YAHOO_HEADLINE_RSS}?s={urllib.parse.quote(sym)}"
           "&region=US&lang=en-US")

    def _fetch() -> dict:
        xml = _http_get(url)
        items = parse_headline_rss(xml, source=f"Yahoo Finance · {sym}")
        return {"ok": True, "symbol": sym,
                "items": items[:NEWS_MAX_ITEMS]}

    out = _cached_fetch(data_root, _cache_name(sym), NEWS_TTL_S, _fetch)
    out.setdefault("ok", False)
    out.setdefault("symbol", sym)
    out.setdefault("items", [])
    return out
