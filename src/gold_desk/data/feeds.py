"""Free real-market feeds for DISPLAY-ONLY telemetry (no key, no account).

Sources (researched 2026-08, all keyless):
  PRICE primary : Yahoo Finance GC=F (COMEX gold futures) — live quote + H1 bars
  PRICE fallback: Binance PAXGUSDT (tokenized gold, 24/7 spot) — covers weekends
  NEWS          : Yahoo Finance RSS for GC=F

Law boundary: these feeds are display/education telemetry for the web deck.
They are NOT wired into the orchestrator's decision loop — the live data
plane stays constitution-gated (owner BLOCKED fields) exactly as before.

Every fetch: short timeout, cached (TTL), fail-soft (returns error field,
never raises), and stamps the source + fetched_at so the UI can badge
LIVE vs DEMO honestly.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gold-desk/1.0"
YAHOO_CHART_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    "?interval=1h&range=5d"
)
YAHOO_DAILY_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart/GC=F"
    "?interval=1d&range=5d"
)
PAXG_TICKER_URL = (
    "https://api.binance.com/api/v3/ticker/24hr?symbol=PAXGUSDT"
)
YAHOO_NEWS_RSS = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline"
    "?s=GC=F&region=US&lang=en-US"
)

PRICE_TTL_S = 60          # live quote cache
NEWS_TTL_S = 15 * 60      # news cache
BARS_TTL_S = 5 * 60       # hourly bars cache


def _http_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root)
    d.mkdir(parents=True, exist_ok=True)
    return d / f"feed_{name}.json"


def _cached_fetch(data_root: str | Path, name: str, ttl: int,
                  fetch) -> dict:
    """Cache-through fetch: return cached when fresh, else fetch, else stale."""
    path = _cache_path(data_root, name)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < ttl:
        cached["cached"] = False
        cached["cache_hit"] = True
        return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["cached"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as e:  # noqa: BLE001 — display telemetry fails soft
        if cached:
            cached["cached"] = True
            cached["stale_error"] = f"{type(e).__name__}"
            return cached
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "fetched_at": time.time()}


# --------------------------------------------------------------------- price
def _yahoo_price() -> dict:
    data = json.loads(_http_get(YAHOO_DAILY_URL))
    result = (data.get("chart") or {}).get("result") or [None]
    if not result or not result[0]:
        raise RuntimeError("yahoo returned no chart result")
    r = result[0]
    meta = r.get("meta") or {}
    price = meta.get("regularMarketPrice")
    if price is None:
        raise RuntimeError("no regularMarketPrice in meta")
    # true previous session close = last completed daily bar's close
    ts = r.get("timestamp") or []
    q = (r.get("indicators") or {}).get("quote", [{}])[0]
    prev_close = meta.get("chartPreviousClose")
    for i in range(len(ts) - 1, -1, -1):
        closes = q.get("close") or []
        if i < len(closes) and closes[i] is not None:
            # the final bar may be the forming session; use the one before
            if i >= 1 and closes[i - 1] is not None:
                prev_close = closes[i - 1]
            else:
                prev_close = closes[i]
            break
    return {
        "ok": True,
        "source": "yahoo:GC=F (COMEX gold futures)",
        "symbol": "GC=F",
        "price": float(price),
        "prev_close": round(float(prev_close), 2) if prev_close else None,
        "currency": meta.get("currency", "USD"),
        "market_time": meta.get("regularMarketTime"),
        "unit": "USD/oz",
    }


def _paxg_price() -> dict:
    data = json.loads(_http_get(PAXG_TICKER_URL))
    price = float(data["lastPrice"])
    return {
        "ok": True,
        "source": "binance:PAXGUSDT (tokenized gold spot)",
        "symbol": "PAXGUSDT",
        "price": price,
        "prev_close": float(data.get("prevClosePrice") or 0) or None,
        "change_pct": float(data.get("priceChangePercent") or 0),
        "currency": "USD",
        "market_time": int(time.time() * 1000) // 1000,
        "unit": "USD/oz (1 PAXG = 1 troy oz)",
    }


def fetch_spot(data_root: str | Path = "data") -> dict:
    """Live gold spot: Yahoo futures, PAXG fallback — and when futures are
    stale (weekend), PAXG provides the fresher 24/7 price with futures kept
    as reference."""
    def _fetch() -> dict:
        try:
            out = _yahoo_price()
            mt = out.get("market_time") or 0
            age_h = (time.time() - mt) / 3600.0
            if age_h > 8:  # futures closed (weekend/holiday) → 24/7 PAXG
                try:
                    paxg = _paxg_price()
                    out["reference"] = {
                        "source": out["source"], "price": out["price"],
                        "as_of": mt,
                    }
                    out.update({
                        "price": paxg["price"],
                        "source": paxg["source"] + " (futures closed)",
                        "market_time": paxg["market_time"],
                        "prev_close": paxg.get("prev_close") or out.get("prev_close"),
                    })
                except Exception:
                    pass
            return out
        except Exception:
            out = _paxg_price()
            out["fallback_from"] = "yahoo"
            return out
    out = _cached_fetch(data_root, "price", PRICE_TTL_S, _fetch)
    out["kind"] = "spot"
    return out


def fetch_bars(data_root: str | Path = "data", limit: int = 120) -> dict:
    """Hourly OHLC bars from Yahoo (chart), PAXG klines fallback."""
    def _fetch() -> dict:
        data = json.loads(_http_get(YAHOO_CHART_URL))
        r = data["chart"]["result"][0]
        ts = r.get("timestamp") or []
        q = (r.get("indicators") or {}).get("quote", [{}])[0]
        bars = []
        for i, t in enumerate(ts):
            o, h = q.get("open", [None]*len(ts))[i], q.get("high", [None]*len(ts))[i]
            l, c = q.get("low", [None]*len(ts))[i], q.get("close", [None]*len(ts))[i]
            if None in (o, h, l, c):
                continue
            bars.append({"ts": t * 1000, "o": o, "h": h, "l": l, "c": c})
        if not bars:
            raise RuntimeError("no yahoo bars")
        return {"ok": True, "source": "yahoo:GC=F", "interval": "1h",
                "bars": bars[-limit:]}
    def _paxg_bars() -> dict:
        raw = json.loads(_http_get(
            "https://api.binance.com/api/v3/klines?symbol=PAXGUSDT"
            "&interval=1h&limit=" + str(limit)))
        bars = [{"ts": k[0], "o": float(k[1]), "h": float(k[2]),
                 "l": float(k[3]), "c": float(k[4])} for k in raw]
        return {"ok": True, "source": "binance:PAXGUSDT", "interval": "1h",
                "bars": bars}
    def _chain() -> dict:
        try:
            return _fetch()
        except Exception:
            return _paxg_bars()
    out = _cached_fetch(data_root, "bars", BARS_TTL_S, _chain)
    out["kind"] = "bars"
    return out


# ---------------------------------------------------------------------- news
def _parse_rss(xml: str) -> list[dict]:
    items = re.findall(r"<item>(.*?)</item>", xml, re.DOTALL)

    def field(item: str, tag: str) -> str:
        m = re.search(rf"<{tag}>(.*?)</{tag}>", item, re.DOTALL)
        if not m:
            return ""
        text = m.group(1)
        text = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", text, flags=re.DOTALL)
        return text.strip()

    out = []
    for it in items:
        title = field(it, "title")
        if not title:
            continue
        # strip yahoo tracking wrappers from links
        link = field(it, "link")
        m = re.search(r"url=([^&]+)", link)
        if m:
            link = urllib.parse.unquote(m.group(1))
        out.append({
            "title": title,
            "link": link,
            "published": field(it, "pubDate"),
            "source": "Yahoo Finance · Gold",
        })
    return out


def fetch_news(data_root: str | Path = "data", limit: int = 12) -> dict:
    def _fetch() -> dict:
        xml = _http_get(YAHOO_NEWS_RSS)
        items = _parse_rss(xml)
        if not items:
            raise RuntimeError("no rss items parsed")
        return {"ok": True, "source": "yahoo:GC=F rss", "items": items[:limit]}
    out = _cached_fetch(data_root, "news", NEWS_TTL_S, _fetch)
    out["kind"] = "news"
    return out
