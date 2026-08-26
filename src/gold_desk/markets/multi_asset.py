"""R3-1 BUILD 1 — Multi-Asset Live Monitor (beats OpenBB Terminal markets panel).

A `MultiAssetMonitor` covers 8 cross-asset instruments — gold (GC=F),
S&P 500 E-mini (ES=F), US 10y Treasury yield (^TNX), US Dollar Index
(DX-Y.NYB), Bitcoin (BTC-USD), VIX (^VIX), WTI crude (CL=F), EUR/USD
(EURUSD=X) — every one keyless via Yahoo's chart endpoint.

Capabilities:

* `snapshot() -> dict[str, AssetSnapshot]` — live quote + session VWAP +
  session-relative % move + sparkline, per asset. One dead symbol never
  poisons the rest (fail-soft per asset — same pattern as
  `markets.board`).
* `compute_correlation(window_days, method)` — symmetric Pearson or
  Spearman correlation matrix over rolling 30/60/90-day daily
  log-returns. Pearson is hand-rolled (cov / σ_x σ_y, bias-corrected);
  Spearman ranks then calls the same Pearson kernel. Validated against
  numpy.corrcoef + scipy.stats.spearmanr in the test suite.

Session calendars (per the R3 charter):

    GC=F   COMEX    Mon-Fri, NY hours
    ES=F   CME      Mon-Fri, NY hours (equity futures nearly 23/5)
    ^TNX   US BOND  Mon-Fri, NY hours
    DX-Y.NYB ICE     Mon-Fri, NY hours
    BTC-USD 24/7    continuous
    ^VIX   CBOE     Mon-Fri, NY hours
    CL=F   NYMEX    Mon-Fri, NY hours
    EURUSD=X 24/5   Sun-Fri, near-24h

We approximate sessions in UTC (matches `clock.py`'s windows):
    asia               00:00 - 07:00
    london             07:00 - 12:00
    london_ny_overlap  12:00 - 16:00
    ny                 16:00 - 21:00
    off                otherwise
24/7 assets (BTC) get a rolling 24h VWAP instead of a UTC-session slice.

Law boundary: display/education telemetry for the gauntlet surface,
NOT wired into the orchestrator's decision loop (constitution-gated).
"""
from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
HTTP_TIMEOUT = 8.0
MULTI_TTL_S = 60            # 1-minute live quote cache
CORR_TTL_S = 15 * 60        # 15-minute correlation-matrix cache
WORKERS = 8                 # threaded fan-out width (one per instrument)

# Session windows in UTC (matches clock.py SESSION_BOUNDS).
SESSION_BOUNDS = [
    ("asia", 0, 7),
    ("london", 7, 12),
    ("london_ny_overlap", 12, 16),
    ("ny", 16, 21),
]

# The 8 instruments the R3 charter mandates. Each entry carries its
# session calendar (used by the UI badge + VWAP slice logic).
INSTRUMENTS: dict[str, dict] = {
    "GC=F":      {"name": "Gold",            "calendar": "COMEX",    "session_mode": "fixed"},
    "ES=F":      {"name": "S&P 500 E-mini",  "calendar": "CME",      "session_mode": "fixed"},
    "^TNX":      {"name": "US 10Y Yield",    "calendar": "US BOND",   "session_mode": "fixed"},
    "DX-Y.NYB":  {"name": "Dollar Index",    "calendar": "ICE",       "session_mode": "fixed"},
    "BTC-USD":   {"name": "Bitcoin",         "calendar": "24/7",     "session_mode": "rolling24"},
    "^VIX":      {"name": "VIX",             "calendar": "CBOE",     "session_mode": "fixed"},
    "CL=F":      {"name": "WTI Crude",       "calendar": "NYMEX",    "session_mode": "fixed"},
    "EURUSD=X":  {"name": "EUR/USD",         "calendar": "24/5",     "session_mode": "fixed"},
}

# Order preserved for stable UI rendering.
INSTRUMENT_ORDER = ["GC=F", "ES=F", "^TNX", "DX-Y.NYB",
                    "BTC-USD", "^VIX", "CL=F", "EURUSD=X"]


def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _cached_fetch(data_root: str | Path, name: str, ttl: int,
                  fetch: Callable[[], dict]) -> dict:
    """Cache-through fetch (same pattern as board._cached_fetch)."""
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


def _is_fx(symbol: str) -> bool:
    return symbol.upper().endswith("=X")


def _publish_price(v: float, symbol: str) -> float:
    """Magnitude-aware publish precision (mirrors board._round)."""
    if v is None:
        return None  # type: ignore[return-value]
    v = float(v)
    if _is_fx(symbol):
        return round(v, 5 if abs(v) < 10 else 3)
    return round(v, 2 if abs(v) >= 1 else 4)


# ------------------------------------------------------------------ fetch
def fetch_multi_quote(symbols: list[str], timeout: float = HTTP_TIMEOUT) -> dict:
    """Fetch live quotes for a list of Yahoo symbols.

    Yahoo's documented v7/finance/quote multi-symbol endpoint has been
    rate-limited/broken intermittently since 2023 (HTTP 401/429 for the
    keyless UA). The robust path is the v8/finance/chart endpoint that
    `markets.board` already uses — one call per symbol, fan out via
    ThreadPoolExecutor. This function returns a dict keyed by symbol:

        {symbol: {ok, price, prev_close, currency, market_time, source}}

    A symbol whose fetch fails lands in the dict with `ok: False` and an
    `error` string — `snapshot()` filters those out (fail-soft per asset,
    never raises).

    A `mock_response` hook is exposed for tests: when set on the module
    (`multi_asset._TEST_QUOTES`), it short-circuits all HTTP and returns
    the canned payload instead. This is the seam the test suite uses to
    pin the parser shape.
    """
    canned = _TEST_QUOTES
    if canned is not None:
        # test seam — never touches the network
        return {sym: canned.get(sym, {"ok": False, "error": "not in mock"})
                for sym in symbols}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(WORKERS, len(symbols) or 1)) as ex:
        futures = {ex.submit(_fetch_chart_quote, sym, timeout): sym
                   for sym in symbols}
        for fut in as_completed(futures):
            sym = futures[fut]
            try:
                out[sym] = fut.result()
            except Exception as e:  # noqa: BLE001 — fail-soft per symbol
                out[sym] = {"ok": False, "symbol": sym,
                            "error": f"{type(e).__name__}: {e}"}
    return out


# Test seam — set by tests to a dict keyed by canonical Yahoo symbol
# (each value shaped like the inner of `_parse_chart_quote`). Never set
# in production code paths.
_TEST_QUOTES: dict | None = None


def _fetch_chart_quote(symbol: str, timeout: float = HTTP_TIMEOUT) -> dict:
    """One Yahoo v8/chart call → live quote for a single symbol."""
    url = (f"{YAHOO_CHART_URL}{urllib.parse.quote(symbol, safe='')}"
           f"?range=2d&interval=15m")
    data = json.loads(_http_get(url, timeout=timeout))
    results = (data.get("chart") or {}).get("result") or []
    if not results or not results[0]:
        raise RuntimeError(f"no chart result for {symbol}")
    return _parse_chart_quote(symbol, results[0])


def _parse_chart_quote(symbol: str, r: dict) -> dict:
    """Yahoo v8/chart result[0] → live quote + intraday bars.

    Returns:
        {ok, symbol, price, prev_close, change, change_pct, currency,
         market_time, bars: [{ts, o, h, l, c, v}, ...], source}
    The `bars` field carries the last ~96 15-minute OHLCV bars (24h) so
    the monitor can compute session VWAP without a second fetch.
    """
    meta = r.get("meta") or {}
    quote = ((r.get("indicators") or {}).get("quote") or [{}])
    quote = quote[0] if quote else {}
    ts_arr = r.get("timestamp") or []
    opens = quote.get("open") or []
    highs = quote.get("high") or []
    lows = quote.get("low") or []
    closes = quote.get("close") or []
    vols = quote.get("volume") or []
    bars: list[dict] = []
    for i, t in enumerate(ts_arr):
        def _at(arr, idx):
            return arr[idx] if idx < len(arr) else None
        o, h, l, c = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
        v = _at(vols, i)
        if None in (o, h, l, c):
            continue
        bars.append({"ts": int(t) * 1000,
                     "o": float(o), "h": float(h),
                     "l": float(l), "c": float(c),
                     "v": float(v) if isinstance(v, (int, float)) else 0.0})
    price = meta.get("regularMarketPrice")
    if price is None and closes:
        price = closes[-1]
    if price is None:
        raise RuntimeError(f"no price in payload for {symbol}")
    prev = meta.get("chartPreviousClose", meta.get("previousClose"))
    if prev is None and len(closes) >= 2:
        prev = closes[-2]
    price_pub = _publish_price(price, symbol)
    prev_pub = _publish_price(prev, symbol) if prev else None
    if prev_pub:
        change_pub = round(price_pub - prev_pub, 6)
        change_pct = round((price_pub - prev_pub) / prev_pub * 100.0, 4)
    else:
        change_pub = change_pct = None
    return {
        "ok": True,
        "symbol": symbol,
        "price": price_pub,
        "prev_close": prev_pub,
        "change": change_pub,
        "change_pct": change_pct,
        "currency": meta.get("currency", "USD"),
        "market_time": int(meta.get("regularMarketTime") or 0),
        "bars": bars,
        "source": "yahoo:" + symbol,
    }


# ------------------------------------------------------------------ sessions
def session_of(dt: datetime) -> str:
    """UTC hour → session name (matches clock.py SESSION_BOUNDS)."""
    hour = dt.astimezone(timezone.utc).hour
    for name, start, end in SESSION_BOUNDS:
        if start <= hour < end:
            return name
    return "off"


def _session_vwap_and_open(bars: list[dict],
                           mode: str = "fixed") -> tuple[float | None,
                                                          float | None,
                                                          str]:
    """Compute VWAP + open for the asset's current session.

    For 24/7 assets (BTC) the session is the last 24h of bars (`mode=
    "rolling24"`); for everything else we slice by UTC session window.
    Returns (vwap, session_open_price, session_name). Any of those can
    be None when bars are absent — the snapshot then reports a None
    session_relative_pct.
    """
    if not bars:
        return None, None, "off"
    last_ts = bars[-1]["ts"]
    now = datetime.fromtimestamp(last_ts / 1000.0, tz=timezone.utc)
    if mode == "rolling24":
        cutoff = now.timestamp() * 1000 - 24 * 3600 * 1000
        session_bars = [b for b in bars if b["ts"] >= cutoff]
        session_name = "24h"
    else:
        session_name = session_of(now)
        # session UTC hour window for today
        for name, start, end in SESSION_BOUNDS:
            if name == session_name:
                start_of_day = now.replace(hour=0, minute=0, second=0,
                                           microsecond=0)
                lo = (start_of_day + _hours(start)).timestamp() * 1000
                hi = (start_of_day + _hours(end)).timestamp() * 1000
                session_bars = [b for b in bars if lo <= b["ts"] < hi]
                break
        else:
            session_bars = []
    if not session_bars:
        # weekends / off-hours: fall back to the last bar's session via
        # the trailing 24h bucket so the snapshot still has a number
        cutoff = now.timestamp() * 1000 - 24 * 3600 * 1000
        session_bars = [b for b in bars if b["ts"] >= cutoff]
        session_name = session_name + "/24h"
    vwap = _vwap(session_bars)
    open_price = session_bars[0]["o"] if session_bars else None
    return vwap, open_price, session_name


def _hours(n: int):
    from datetime import timedelta
    return timedelta(hours=n)


def _vwap(bars: list[dict]) -> float | None:
    """Volume-weighted average price from OHLCV bars.

    Typical price (h+l+c)/3 weighted by volume. When volumes are zero
    or absent for every bar (some Yahoo chart calls strip volume), we
    fall back to the unweighted mean of typical prices so the snapshot
    still reports a number — labeled as `vwap_method: "typical"` by the
    caller; the rolling 24h VWAP for BTC and the session VWAP for
    fixed-calendar assets use the same kernel.
    """
    if not bars:
        return None
    pv = 0.0
    vol = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        v = b.get("v") or 0.0
        pv += tp * v
        vol += v
    if vol > 0:
        return pv / vol
    # fallback: unweighted typical-price mean
    tps = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bars]
    return sum(tps) / len(tps) if tps else None


# ------------------------------------------------------------------ snapshot
@dataclass
class AssetSnapshot:
    """One row in the multi-asset monitor.

    Plain dataclass — JSON-serializable via `asdict` and `json.dumps
    (test: snapshot_json_serializable pins the round-trip).
    """
    symbol: str
    name: str
    calendar: str
    price: float | None
    prev_close: float | None
    change_pct: float | None
    session: str
    session_vwap: float | None
    session_relative_pct: float | None
    session_open_pct: float | None
    sparkline: list[float] = field(default_factory=list)
    live: bool = False
    source: str = ""
    fetched_at: int = 0
    cache_hit: bool = False
    error: str | None = None


class MultiAssetMonitor:
    """8-instrument live monitor with cross-asset correlation.

    Lifecycle: instantiate once, call `snapshot()` for the live row
    dict and `compute_correlation(window, method)` for the matrix. Both
    go through the cache-through fetch helper so a 1-min-old snapshot
    is reused and a network outage degrades to stale-serve (mirrors
    `markets.board.fetch_board`).

    Fail-soft per asset: a 404 on one symbol lands in `errors` (and the
    returned snapshot dict simply omits that symbol — never raises).
    """

    def __init__(self, data_root: str | Path = "data",
                 fetcher: Callable[[list[str]], dict] | None = None):
        self.data_root = data_root
        # injectable fetcher for tests (mocked Yahoo response)
        self._fetcher = fetcher or fetch_multi_quote

    # ----------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """Live snapshot for all 8 instruments.

        Returns {ok, as_of, assets: {symbol: AssetSnapshot-as-dict},
        errors: [symbol, ...]} — never raises. One asset's failure
        lands in `errors`; the others are served normally.
        """
        def _build() -> dict:
            quotes = self._fetcher(list(INSTRUMENTS.keys()))
            assets: dict[str, dict] = {}
            errors: list[str] = []
            for sym in INSTRUMENT_ORDER:
                meta = INSTRUMENTS[sym]
                q = quotes.get(sym) or {}
                if not q.get("ok"):
                    errors.append(sym)
                    snap = AssetSnapshot(
                        symbol=sym, name=meta["name"],
                        calendar=meta["calendar"], price=None,
                        prev_close=None, change_pct=None,
                        session="error", session_vwap=None,
                        session_relative_pct=None,
                        session_open_pct=None, sparkline=[],
                        live=False, source="", fetched_at=0,
                        cache_hit=False,
                        error=q.get("error", "fetch failed"),
                    )
                    assets[sym] = asdict(snap)
                    continue
                vwap, session_open, session_name = _session_vwap_and_open(
                    q.get("bars") or [], meta["session_mode"])
                price = q.get("price")
                rel_pct: float | None = None
                if vwap and price:
                    rel_pct = round((price - vwap) / vwap * 100.0, 4)
                open_pct: float | None = None
                if session_open and price:
                    open_pct = round((price - session_open) / session_open
                                     * 100.0, 4)
                spark = [_publish_price(c, sym)
                         for c in (b["c"] for b in (q.get("bars") or [])[-24:])]
                snap = AssetSnapshot(
                    symbol=sym, name=meta["name"],
                    calendar=meta["calendar"],
                    price=price,
                    prev_close=q.get("prev_close"),
                    change_pct=q.get("change_pct"),
                    session=session_name,
                    session_vwap=_publish_price(vwap, sym)
                                 if vwap is not None else None,
                    session_relative_pct=rel_pct,
                    session_open_pct=open_pct,
                    sparkline=spark,
                    live=True,
                    source=q.get("source", ""),
                    fetched_at=int(q.get("market_time") or 0),
                    cache_hit=False,
                    error=None,
                )
                assets[sym] = asdict(snap)
            return {
                "ok": True,
                "as_of": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
                "instruments": list(INSTRUMENTS.keys()),
                "assets": assets,
                "errors": sorted(errors),
            }
        out = _cached_fetch(self.data_root, "markets_multi", MULTI_TTL_S,
                            _build)
        out["kind"] = "markets_multi"
        return out

    # ------------------------------------------------------ correlation
    def compute_correlation(self, window: int = 30,
                           method: str = "pearson") -> dict:
        """Symmetric correlation matrix across the 8 instruments.

        Returns {ok, window, method, symbols, matrix, n_points} where
        `matrix[sym_i][sym_j]` is a float in [-1, 1] or None when there
        isn't enough overlap. Cached 15 minutes per (window, method)
        under <data_root>/cache/markets_corr_{w}_{m}.json.
        """
        method = (method or "pearson").lower()
        if method not in ("pearson", "spearman"):
            return {"ok": False, "error": f"unknown method: {method}"}
        cache_name = f"markets_corr_{window}_{method}"

        def _build() -> dict:
            closes_map = self._fetch_daily_closes_for_all()
            rets_map = _log_returns(closes_map)
            syms = [s for s in INSTRUMENT_ORDER if s in rets_map]
            matrix: dict[str, dict[str, float | None]] = \
                {s: {} for s in syms}
            n_points: dict[str, int] = {}
            for i, si in enumerate(syms):
                for sj in syms[i:]:
                    if si == sj:
                        matrix[si][sj] = 1.0
                        matrix[sj][si] = 1.0
                        continue
                    sr = rets_map[si][-window:]
                    br = rets_map[sj][-window:]
                    n = min(len(sr), len(br))
                    sr, br = sr[-n:], br[-n:]
                    if n < 2:
                        matrix[si][sj] = None
                        matrix[sj][si] = None
                        continue
                    r = _correlation(sr, br, method=method)
                    matrix[si][sj] = r
                    matrix[sj][si] = r
                    n_points[f"{si}|{sj}"] = n
            return {
                "ok": True,
                "window": window,
                "method": method,
                "symbols": syms,
                "matrix": matrix,
                "n_points": n_points,
                "as_of": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
            }
        out = _cached_fetch(self.data_root, cache_name, CORR_TTL_S, _build)
        out["kind"] = "markets_correlation"
        return out

    # ------------------------------------------------- daily closes fetch
    def _fetch_daily_closes_for_all(self) -> dict[str, list[float]]:
        """Daily close history per instrument (fail-soft per symbol).

        One Yahoo v8/chart call per symbol at range=1y&interval=1d,
        threaded to 8 workers. Symbols whose fetch fails are simply
        absent from the returned dict — `compute_correlation` skips
        them and assembles the matrix from whatever landed.
        """
        canned = _TEST_DAILY_CLOSES
        if canned is not None:
            return {sym: list(canned.get(sym, []))
                    for sym in INSTRUMENTS}

        out: dict[str, list[float]] = {}
        with ThreadPoolExecutor(max_workers=WORKERS) as ex:
            futures = {ex.submit(self._fetch_daily_one, sym): sym
                       for sym in INSTRUMENTS}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    out[sym] = fut.result()
                except Exception:  # noqa: BLE001 — fail-soft per symbol
                    pass
        return out

    def _fetch_daily_one(self, symbol: str) -> list[float]:
        url = (f"{YAHOO_CHART_URL}{urllib.parse.quote(symbol, safe='')}"
               f"?range=1y&interval=1d")
        data = json.loads(_http_get(url))
        results = (data.get("chart") or {}).get("result") or []
        if not results or not results[0]:
            raise RuntimeError(f"no daily chart for {symbol}")
        r = results[0]
        quote = ((r.get("indicators") or {}).get("quote") or [{}])
        quote = quote[0] if quote else {}
        closes = [c for c in (quote.get("close") or []) if c is not None]
        if not closes:
            raise RuntimeError(f"no closes in daily chart for {symbol}")
        return closes


# Test seam for `_fetch_daily_closes_for_all` (dict[symbol -> list[float]]).
_TEST_DAILY_CLOSES: dict | None = None


# ------------------------------------------------------------------ math
def _log_returns(closes_map: dict[str, list[float]]) -> dict[str, list[float]]:
    """Daily log-returns per symbol: ln(c[i]/c[i-1]) skipping ≤0 closes."""
    out: dict[str, list[float]] = {}
    for sym, closes in closes_map.items():
        rets: list[float] = []
        for k in range(1, len(closes)):
            a, b = closes[k - 1], closes[k]
            if a > 0 and b > 0:
                rets.append(math.log(b / a))
        out[sym] = rets
    return out


def _correlation(x: list[float], y: list[float],
                 method: str = "pearson") -> float | None:
    """Pearson or Spearman correlation (hand-rolled, stdlib only).

    * Pearson: cov(x, y) / (σ_x · σ_y), bias-corrected (n-1).
    * Spearman: rank-then-pearson (avg ranks for ties).
    Returns None on degenerate inputs; clamped to [-1, 1].
    """
    n = min(len(x), len(y))
    if n < 2:
        return None
    x, y = x[-n:], y[-n:]
    if method == "spearman":
        x = _rank_avg(x)
        y = _rank_avg(y)
    sx = sum(x) / n
    sy = sum(y) / n
    cov = sum((x[i] - sx) * (y[i] - sy) for i in range(n)) / (n - 1)
    vx = sum((x[i] - sx) ** 2 for i in range(n)) / (n - 1)
    vy = sum((y[i] - sy) ** 2 for i in range(n)) / (n - 1)
    sdx = math.sqrt(vx) if vx > 0 else 0.0
    sdy = math.sqrt(vy) if vy > 0 else 0.0
    if sdx == 0 or sdy == 0:
        return None
    r = cov / (sdx * sdy)
    # clamp fp drift
    return round(max(-1.0, min(1.0, r)), 6)


def _rank_avg(values: list[float]) -> list[float]:
    """Average ranks (resolves ties the scipy way)."""
    n = len(values)
    indexed = sorted(range(n), key=lambda i: values[i])
    ranks = [0.0] * n
    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[indexed[j + 1]] == values[indexed[i]]:
            j += 1
        # average rank of the tie group is (i+1 + j+1) / 2 = (i+j+2)/2
        avg = (i + j + 2) / 2.0
        for k in range(i, j + 1):
            ranks[indexed[k]] = avg
        i = j + 1
    return ranks


# ------------------------------------------------------------------ helpers
def instrument_meta(symbol: str) -> dict:
    """Public accessor for the 8-instrument metadata map."""
    return INSTRUMENTS.get(symbol, {})
