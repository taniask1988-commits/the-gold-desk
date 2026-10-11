"""GAUNTLET 7 Piece 1 — pro-charting data plane (display/telemetry layer).

Keyless Yahoo v8/chart bars at three chart timeframes (15m/1h/1d) plus
rolling indicator series (SMA/EMA/RSI/Bollinger/ATR) computed in pure
stdlib and aligned bar-by-bar so the web deck can overlay them as line
series on lightweight-charts.

Law boundary: display/education telemetry only — a read-only consumer,
NOT wired into the orchestrator's decision loop. Every fetch is
fail-soft + stale-serve via the house helper `_cached_fetch` and every
response stamps source + fetched_at so the UI can badge LIVE/DELAYED
honestly (never fabricate).
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

from . import board as board_mod
from .board import _quote, _round

# timeframe → (default yahoo range, cache TTL seconds)
TIMEFRAMES: dict[str, dict] = {
    "15m": {"range": "1mo",   "interval": "15m", "ttl": 300,
            "label": "15 minutes"},
    "1h":  {"range": "3mo",   "interval": "1h",  "ttl": 600,
            "label": "1 hour"},
    "1d":  {"range": "1y",    "interval": "1d",  "ttl": 1800,
            "label": "1 day"},
}

DEFAULT_INDICATORS = ["sma20", "sma50", "rsi14"]
ALL_INDICATORS = [
    "sma20", "sma50", "ema20", "ema50", "rsi14", "bb20", "atr14",
]


def _http_cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root)
    (d / "cache").mkdir(parents=True, exist_ok=True)
    return d / "cache" / name


def _cached_fetch(data_root: str | Path, name: str, ttl: int, fetch) -> dict:
    """House cache-through fetch: fresh → fetch → STALE-SERVE on error.

    Mirrors data/feeds.py semantics: a stale answer beats an error, and
    failures surface in the payload (`stale_error`, `errors`) — never
    silently.
    """
    path = _http_cache_path(data_root, name)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < ttl:
        cached["cached"] = True
        return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["stale_error"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as exc:  # noqa: BLE001 — stale-serve, never raise
        if cached:
            out = dict(cached)
            out["cached"] = False
            out["stale_error"] = True
            out["stale_reason"] = f"{type(exc).__name__}: {exc}"
            return out
        return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"],
                "fetched_at": time.time(), "stale_error": False}


def _bars_from_result(r: dict, symbol: str) -> list[dict]:
    """chart.result[0] → [{t, o, h, l, c, v}] (ms timestamps, rounded)."""
    ts_arr = r.get("timestamp") or []
    q = _quote(r)
    o_arr, h_arr = q.get("open") or [], q.get("high") or []
    l_arr, c_arr = q.get("low") or [], q.get("close") or []
    v_arr = q.get("volume") or []
    bars: list[dict] = []
    for i, t in enumerate(ts_arr):
        def _at(arr, j):
            return arr[j] if j < len(arr) else None
        o, h, l, c = _at(o_arr, i), _at(h_arr, i), _at(l_arr, i), \
            _at(c_arr, i)
        if None in (o, h, l, c):
            continue
        v = _at(v_arr, i)
        bars.append({
            "t": int(t) * 1000,
            "o": _round(o, symbol), "h": _round(h, symbol),
            "l": _round(l, symbol), "c": _round(c, symbol),
            "v": float(v) if isinstance(v, (int, float)) else 0.0,
        })
    return bars


# ------------------------------------------------------------- indicators
def _sma_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    run = 0.0
    for i, v in enumerate(values):
        run += v
        if i >= period:
            run -= values[i - period]
        if i >= period - 1:
            out[i] = run / period
    return out


def _ema_series(values: list[float], period: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    if len(values) < period:
        return out
    k = 2.0 / (period + 1.0)
    prev = sum(values[:period]) / period
    out[period - 1] = prev
    for i in range(period, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def _rsi_series(closes: list[float], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(closes)
    if len(closes) < period + 1:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_g, avg_l = gains / period, losses / period
    out[period] = 100.0 if avg_l == 0 else \
        100.0 - 100.0 / (1.0 + avg_g / avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0.0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0.0)) / period
        out[i] = 100.0 if avg_l == 0 else \
            100.0 - 100.0 / (1.0 + avg_g / avg_l)
    return out


def _bb_series(closes: list[float], period: int = 20, ndev: float = 2.0
               ) -> tuple[list, list, list]:
    mid = _sma_series(closes, period)
    upper: list[float | None] = [None] * len(closes)
    lower: list[float | None] = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        m = mid[i]
        if m is None:
            continue
        window = closes[i - period + 1:i + 1]
        var = sum((x - m) ** 2 for x in window) / period
        sd = math.sqrt(var)
        upper[i] = m + ndev * sd
        lower[i] = m - ndev * sd
    return upper, mid, lower


def _atr_series(bars: list[dict], period: int = 14) -> list[float | None]:
    out: list[float | None] = [None] * len(bars)
    if len(bars) < period + 1:
        return out
    trs: list[float] = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    prev_atr = sum(trs[1:period + 1]) / period
    out[period] = prev_atr
    for i in range(period + 1, len(bars)):
        prev_atr = (prev_atr * (period - 1) + trs[i]) / period
        out[i] = prev_atr
    return out


def indicator_series(bars: list[dict], indicators: list[str]) -> list[dict]:
    """Rolling indicator series aligned bar-by-bar.

    Returns [{name, kind: line|band, points: [{t, v} | {t, hi, lo}]}].
    Points omit undefined leading values (charts connect the rest).
    """
    closes = [b["c"] for b in bars]
    out: list[dict] = []
    for name in indicators:
        if name.startswith("sma"):
            period = int(name[3:] or 20)
            ser = _sma_series(closes, period)
            out.append({"name": f"SMA{period}", "kind": "line",
                        "points": [{"t": bars[i]["t"], "v": round(v, 4)}
                                   for i, v in enumerate(ser) if v is not None]})
        elif name.startswith("ema"):
            period = int(name[3:] or 20)
            ser = _ema_series(closes, period)
            out.append({"name": f"EMA{period}", "kind": "line",
                        "points": [{"t": bars[i]["t"], "v": round(v, 4)}
                                   for i, v in enumerate(ser) if v is not None]})
        elif name.startswith("rsi"):
            period = int(name[3:] or 14)
            ser = _rsi_series(closes, period)
            out.append({"name": f"RSI{period}", "kind": "line",
                        "points": [{"t": bars[i]["t"], "v": round(v, 2)}
                                   for i, v in enumerate(ser) if v is not None]})
        elif name == "bb20":
            up, mid, lo = _bb_series(closes, 20, 2.0)
            out.append({"name": "BB20", "kind": "band",
                        "points": [{"t": bars[i]["t"], "hi": round(up[i], 4),
                                    "lo": round(lo[i], 4)}
                                   for i in range(len(bars))
                                   if up[i] is not None and lo[i] is not None]})
        elif name == "atr14":
            ser = _atr_series(bars, 14)
            out.append({"name": "ATR14", "kind": "line",
                        "points": [{"t": bars[i]["t"], "v": round(v, 4)}
                                   for i, v in enumerate(ser) if v is not None]})
    return out


# ----------------------------------------------------------------- public
def fetch_chart(symbol: str, timeframe: str = "1h",
                data_root: str | Path = "data",
                indicators: list[str] | None = None) -> dict:
    """Bars + indicator series for one symbol at one chart timeframe.

    Fail-soft: a dead symbol returns ok=False with errors[] populated.
    The registry normalizes human aliases (gold → GC=F) when it can.
    """
    tf = TIMEFRAMES.get(timeframe)
    if tf is None:
        return {"ok": False, "errors": [f"unsupported timeframe: {timeframe}"],
                "supported": sorted(TIMEFRAMES)}
    if not symbol or not str(symbol).strip():
        return {"ok": False, "errors": ["empty symbol"], "bars": []}
    indicators = [i for i in (indicators or DEFAULT_INDICATORS)
                  if i in ALL_INDICATORS]

    def _build() -> dict:
        r = board_mod._fetch_chart(symbol, tf["range"], tf["interval"])
        bars = _bars_from_result(r, symbol)
        if not bars:
            raise RuntimeError(f"no {timeframe} bars for {symbol}")
        meta = r.get("meta") or {}
        return {"ok": True, "symbol": symbol,
                "timeframe": timeframe, "interval": tf["interval"],
                "range": tf["range"], "bars": bars,
                "meta": {"currency": meta.get("currency", "USD"),
                         "exchange": meta.get("exchangeName", ""),
                         "regular_market_price": meta.get(
                             "regularMarketPrice"),
                         "symbol": meta.get("symbol", symbol)}}

    out = _cached_fetch(data_root, f"chart_{symbol.upper()}_{timeframe}.json",
                        tf["ttl"], _build)
    if out.get("ok") and out.get("bars"):
        out["indicator_series"] = indicator_series(out["bars"], indicators)
        out["indicators"] = indicators
    out.setdefault("source", "yahoo_v8_chart")
    # D-015 (GAUNTLET 8) — the old intraday label was a vague hedge, not
    # a data label: Yahoo intraday bars are delayed/unverified, daily
    # bars are EOD. Both are honest "delayed" variants; the UI badges
    # them verbatim.
    out.setdefault("data_delay",
                   "delayed" if timeframe == "1d" else "delayed (intraday)")
    return out
