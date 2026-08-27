"""R3-1 BUILD 1 — Multi-Asset Live Monitor (beats OpenBB Terminal markets panel).

A `MultiAssetMonitor` covers cross-asset instruments — by default the
R3 charter's 8 (gold GC=F, S&P 500 E-mini ES=F, US 10y Treasury yield
^TNX, US Dollar Index DX-Y.NYB, Bitcoin BTC-USD, VIX ^VIX, WTI crude
CL=F, EUR/USD EURUSD=X) — every one keyless via Yahoo's chart endpoint.

R4-2 (GAUNTLET4-R2-BUILDER): the monitor generalizes to the
24-instrument UNIVERSE (`markets.registry.UNIVERSE`): silver, copper,
natgas, wheat, corn, Nasdaq/Dow/Russell futures, 4 more FX majors,
5y/30y yields, ETH and SOL — `MultiAssetMonitor(symbols=[...])` takes
any subset, `MultiAssetMonitor(all=True)` takes all 24, and the
default constructor still takes the original 8 (backward compatible).
Fan-out runs in batches of 6 with a small inter-batch pause
(rate-limit friendly), snapshot rows carry their UNIVERSE `sector`,
and daily closes are cached per symbol for an hour so a 24×24
correlation matrix completes in seconds.

Capabilities:

* `snapshot() -> dict[str, AssetSnapshot]` — live quote + session VWAP +
  session-relative % move + sparkline, per asset. One dead symbol never
  poisons the rest (fail-soft per asset — same pattern as
  `markets.board`).
* `compute_correlation(window_days, method)` — symmetric Pearson or
  Spearman correlation matrix over rolling 30/60/90-day daily
  log-returns. Returns are DATE-ALIGNED: daily closes are fetched
  with their timestamps and paired on the INTERSECTION of trading
  dates, so a 24/7 asset (BTC-USD, ~365 closes/yr) never misaligns
  against a ~5-day/week asset (GC=F, ~260 closes/yr) — position-
  based tail pairing sign-flipped every BTC pair (critic defect D2).
  Pearson is hand-rolled (cov / σ_x σ_y, bias-corrected); Spearman
  ranks then calls the same Pearson kernel (full float precision —
  no kernel rounding; rendering rounds at display time). Validated
  against numpy.corrcoef + scipy.stats.spearmanr in the test suite.
  Per-symbol fetch failures and insufficient-overlap pairs are
  surfaced in `errors[]` with a `degraded: True` flag (D3) — never
  silently dropped.

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
DAILY_TTL_S = 60 * 60       # R4-2: 1-hour per-symbol daily-closes cache
WORKERS = 8                 # threaded fan-out width (one per instrument)
BATCH_SIZE = 6              # R4-2: fan-out batch width (rate-limit friendly)
BATCH_PAUSE_S = 0.15        # R4-2: pause between fan-out batches (seconds)

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
                                                          str, str]:
    """Compute VWAP + open for the asset's current session.

    For 24/7 assets (BTC) the session is the last 24h of bars (`mode=
    "rolling24"`); for everything else we slice by UTC session window.
    Returns (vwap, session_open_price, session_name, vwap_method).
    `vwap_method` documents how the VWAP was computed (D5 — the
    zero-volume fallback is no longer silent):
      "vwap"                — volume-weighted (normal path)
      "single_bar"          — session had exactly 1 bar
      "typical_unweighted"  — all-zero volumes → unweighted typical
                               mean (documented fallback)
      "none"                — no bars at all
    Any of the numeric fields can be None when bars are absent — the
    snapshot then reports a None session_relative_pct.
    """
    if not bars:
        return None, None, "off", "none"
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
    vwap, vwap_method = _vwap(session_bars)
    open_price = session_bars[0]["o"] if session_bars else None
    return vwap, open_price, session_name, vwap_method


def _hours(n: int):
    from datetime import timedelta
    return timedelta(hours=n)


def _vwap(bars: list[dict]) -> tuple[float | None, str]:
    """Volume-weighted average price from OHLCV bars + method label.

    Typical price (h+l+c)/3 weighted by volume. Returns (vwap, method)
    where method documents exactly how the number was derived (D5 —
    fallbacks are labeled, never silent):
      * "vwap" — volume-weighted (normal path)
      * "single_bar" — exactly 1 bar in the session (VWAP = that
        bar's typical price; volume weighting is a no-op)
      * "typical_unweighted" — every bar had zero volume (some Yahoo
        chart calls strip volume), so we fell back to the unweighted
        mean of typical prices
      * "none" — no bars
    The rolling 24h VWAP for BTC and the session VWAP for fixed-
    calendar assets use the same kernel.
    """
    if not bars:
        return None, "none"
    if len(bars) == 1:
        b = bars[0]
        return (b["h"] + b["l"] + b["c"]) / 3.0, "single_bar"
    pv = 0.0
    vol = 0.0
    for b in bars:
        tp = (b["h"] + b["l"] + b["c"]) / 3.0
        v = b.get("v") or 0.0
        pv += tp * v
        vol += v
    if vol > 0:
        return pv / vol, "vwap"
    # fallback: unweighted typical-price mean (labeled, not silent)
    tps = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bars]
    return (sum(tps) / len(tps) if tps else None), "typical_unweighted"


# ------------------------------------------------------------------ snapshot
@dataclass
class AssetSnapshot:
    """One row in the multi-asset monitor.

    Plain dataclass — JSON-serializable via `asdict` and `json.dumps
    (test: snapshot_json_serializable pins the round-trip).
    R4-2 adds `sector` (UNIVERSE sector group, "" for unknown symbols).
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
    # D5: how the session_vwap was derived — "vwap" (volume-weighted),
    # "single_bar", "typical_unweighted" (zero-volume fallback),
    # "none" (no bars).
    vwap_method: str = "none"
    # R4-2: UNIVERSE sector group (metals/energy/ag/indices/fx/rates/
    # crypto/vol) — "" for symbols outside the 24-instrument universe.
    sector: str = ""


class MultiAssetMonitor:
    """Live monitor with cross-asset correlation over the UNIVERSE.

    R3-1: 8-instrument monitor (the DEFAULT_WATCHLIST — gold, S&P
    e-mini, 10y yield, DXY, BTC, VIX, WTI, EUR/USD).

    R4-2 (GAUNTLET4-R2-BUILDER): the constructor generalizes to the
    24-instrument UNIVERSE —

        MultiAssetMonitor()                    → the original 8 (compat)
        MultiAssetMonitor(symbols=["SI=F", …]) → any subset (UNIVERSE
                                                  order, unknowns last)
        MultiAssetMonitor(all=True)            → all 24
        MultiAssetMonitor(symbols="all")       → all 24 (CLI passthrough)
        MultiAssetMonitor(symbols="SI=F,NQ=F") → comma-list passthrough

    Fan-out runs in batches of BATCH_SIZE (6) with a small pause
    between batches (rate-limit friendly), fail-soft per asset, and
    snapshot rows carry their UNIVERSE `sector`. Correlation works on
    any subset with a 1-hour per-symbol daily-closes cache so a 24×24
    matrix completes comfortably (the 15-minute matrix cache is
    namespaced per symbol-set — a subset monitor never poisons the
    default monitor's cache, and vice versa).

    Lifecycle: instantiate once, call `snapshot()` for the live row
    dict and `compute_correlation(window, method)` for the matrix. Both
    go through the cache-through fetch helper so a 1-min-old snapshot
    is reused and a network outage degrades to stale-serve (mirrors
    `markets.board.fetch_board`).

    Fail-soft per asset: a 404 on one symbol lands in `errors` (and the
    returned snapshot dict simply omits that symbol — never raises).
    """

    def __init__(self, data_root: str | Path = "data",
                 fetcher: Callable[[list[str]], dict] | None = None,
                 symbols=None, all: bool = False):
        from .registry import resolve_symbols
        self.data_root = data_root
        # R4-2: resolve the symbol request (None → default 8, list/str
        # → subset, all=True → 24). Ordered by UNIVERSE position.
        self._symbols: list[str] = resolve_symbols(symbols, all=all)
        # injectable fetcher for tests (mocked Yahoo response)
        self._fetcher = fetcher or fetch_multi_quote

    # ----------------------------------------------------------- symbols
    @property
    def symbols(self) -> list[str]:
        """The monitor's instrument list (ordered, as resolved)."""
        return list(self._symbols)

    def _meta(self, symbol: str) -> dict:
        """name/calendar/session_mode/sector for a symbol (UNIVERSE
        metadata; unknown symbols get a placeholder that fail-softs)."""
        from .registry import universe_entry, SESSION_MODES
        entry = universe_entry(symbol)
        if entry:
            return {
                "name": entry["name"],
                "calendar": entry["calendar"],
                "session_mode": SESSION_MODES.get(symbol, "fixed"),
                "sector": entry["sector"],
            }
        return {"name": symbol, "calendar": "unknown",
                "session_mode": "fixed", "sector": ""}

    def _cache_slug(self) -> str:
        """Cache namespace for this symbol set ("" for the default 8 —
        legacy cache files keep working)."""
        import hashlib
        from .registry import DEFAULT_WATCHLIST
        if self._symbols == list(DEFAULT_WATCHLIST):
            return ""
        raw = "|".join(self._symbols)
        return "_" + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]

    # ----------------------------------------------------------- snapshot
    def snapshot(self) -> dict:
        """Live snapshot for the monitor's instruments.

        Returns {ok, as_of, instruments, assets: {symbol:
        AssetSnapshot-as-dict}, errors: [symbol, ...]} — never raises.
        One asset's failure lands in `errors`; the others are served
        normally.
        """
        def _build() -> dict:
            quotes = self._fetch_batched(list(self._symbols))
            assets: dict[str, dict] = {}
            errors: list[str] = []
            for sym in self._symbols:
                meta = self._meta(sym)
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
                        sector=meta["sector"],
                    )
                    assets[sym] = asdict(snap)
                    continue
                vwap, session_open, session_name, vwap_method = \
                    _session_vwap_and_open(
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
                    vwap_method=vwap_method,
                    sector=meta["sector"],
                )
                assets[sym] = asdict(snap)
            return {
                "ok": True,
                "as_of": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
                "instruments": list(self._symbols),
                "assets": assets,
                "errors": sorted(errors),
            }
        out = _cached_fetch(self.data_root,
                            f"markets_multi{self._cache_slug()}",
                            MULTI_TTL_S, _build)
        out["kind"] = "markets_multi"
        return out

    # ---------------------------------------------------- batched fan-out
    def _fetch_batched(self, symbols: list[str]) -> dict:
        """Fan out the fetcher in batches of BATCH_SIZE with a small
        pause between batches (R4-2 — rate-limit friendly). Results are
        merged; per-symbol fail-soft is the fetcher's job."""
        out: dict = {}
        for i in range(0, len(symbols), BATCH_SIZE):
            batch = symbols[i:i + BATCH_SIZE]
            out.update(self._fetcher(batch) or {})
            if i + BATCH_SIZE < len(symbols) and BATCH_PAUSE_S > 0:
                time.sleep(BATCH_PAUSE_S)
        return out

    # ------------------------------------------------------ correlation
    def compute_correlation(self, window: int = 30,
                           method: str = "pearson") -> dict:
        """Symmetric correlation matrix across the monitor's instruments
        (any subset of the 24-instrument UNIVERSE; 24×24 completes in
        seconds thanks to the per-symbol daily-closes cache).

        Returns {ok, degraded, window, method, symbols, matrix,
        n_points, errors} where `matrix[sym_i][sym_j]` is a float in
        [-1, 1] (full precision — rendering rounds at display time)
        or None when there isn't enough overlap.

        D2 fix — DATE ALIGNMENT: per-symbol daily closes are keyed by
        date (YYYY-MM-DD) and paired on the INTERSECTION of dates, so
        mixed calendars (BTC 24/7 vs GC=F ~5d/week) correlate same-day
        returns instead of misaligned tail positions.

        D3 — ERROR SURFACING (documented choice: `ok` stays True while
        the matrix could be computed; `degraded` is True when ANY
        symbol or pair failed):
          * errors[] carries {"symbol", "reason":
            "daily_closes_fetch_failed"} per dropped symbol, and
            {"symbol", "pair", "reason": "insufficient_common_dates",
            "common_dates"} per pair with < window+2 common dates
            (those cells are None, rendered "n/a" by the CLI).
        Cached 15 minutes per (window, method, symbol-set) under
        <data_root>/cache/markets_corr_{w}_{m}{set-slug}.json (the
        default watchlist keeps the legacy un-suffixed name).
        """
        method = (method or "pearson").lower()
        if method not in ("pearson", "spearman"):
            return {"ok": False, "error": f"unknown method: {method}"}
        cache_name = f"markets_corr_{window}_{method}{self._cache_slug()}"

        def _build() -> dict:
            closes_map = self._fetch_daily_closes_for_all()
            errors: list[dict] = []
            for sym in self._symbols:
                if not closes_map.get(sym):
                    errors.append({"symbol": sym,
                                   "reason": "daily_closes_fetch_failed"})
            syms = [s for s in self._symbols if closes_map.get(s)]
            matrix: dict[str, dict[str, float | None]] = \
                {s: {} for s in syms}
            n_points: dict[str, int] = {}
            for i, si in enumerate(syms):
                for sj in syms[i:]:
                    if si == sj:
                        matrix[si][sj] = 1.0
                        matrix[sj][si] = 1.0
                        continue
                    # D2: pair on the INTERSECTION of trading dates
                    ra, rb = _aligned_log_returns(closes_map[si],
                                                  closes_map[sj])
                    common_n = len(set(closes_map[si])
                                   & set(closes_map[sj]))
                    if common_n < window + 2 or min(len(ra), len(rb)) < 2:
                        matrix[si][sj] = None
                        matrix[sj][si] = None
                        errors.append(
                            {"symbol": si, "pair": sj,
                             "reason": "insufficient_common_dates",
                             "common_dates": common_n})
                        continue
                    sr = ra[-window:]
                    br = rb[-window:]
                    r = _correlation(sr, br, method=method)
                    matrix[si][sj] = r
                    matrix[sj][si] = r
                    n_points[f"{si}|{sj}"] = len(sr)
            return {
                "ok": True,
                "degraded": bool(errors),
                "window": window,
                "method": method,
                "symbols": syms,
                "matrix": matrix,
                "n_points": n_points,
                "errors": errors,
                "as_of": datetime.now(timezone.utc).isoformat(
                    timespec="seconds").replace("+00:00", "Z"),
            }
        out = _cached_fetch(self.data_root, cache_name, CORR_TTL_S, _build)
        out["kind"] = "markets_correlation"
        return out

    # ------------------------------------------------- daily closes fetch
    def _fetch_daily_closes_for_all(self) -> dict[str, dict[str, float]]:
        """Daily close history per instrument, DATE-KEYED (D2 fix).

        One Yahoo v8/chart call per symbol at range=1y&interval=1d for
        each of the monitor's symbols (any subset of the 24-instrument
        UNIVERSE), threaded to 8 workers and CACHED PER SYMBOL for one
        hour (R4-2 — a 24×24 matrix reuses yesterday's-subset closes;
        the same monitor in another panel doesn't refetch 24 charts).
        Each value maps "YYYY-MM-DD" → close so
        `compute_correlation` can pair returns on the intersection of
        trading dates (a 24/7 calendar never misaligns against a
        5-day/week one). Symbols whose fetch fails are simply absent
        from the returned dict — `compute_correlation` records them in
        `errors[]` (D3) and assembles the matrix from whatever landed.
        """
        canned = _TEST_DAILY_CLOSES
        if canned is not None:
            return {sym: dict(canned.get(sym, {}))
                    for sym in self._symbols}

        out: dict[str, dict[str, float]] = {}
        with ThreadPoolExecutor(max_workers=min(WORKERS,
                                                len(self._symbols) or 1)) as ex:
            futures = {ex.submit(self._fetch_daily_one_cached, sym): sym
                       for sym in self._symbols}
            for fut in as_completed(futures):
                sym = futures[fut]
                try:
                    out[sym] = fut.result()
                except Exception:  # noqa: BLE001 — fail-soft per symbol
                    pass
        return out

    def _fetch_daily_one_cached(self, symbol: str) -> dict[str, float]:
        """Cache-through wrapper around `_fetch_daily_one` (R4-2).

        One-hour TTL per symbol under <data_root>/cache/
        daily_{urlsafe-symbol}.json — daily bars barely move intraday,
        so a 24-instrument correlation run that follows a subset run
        (or a repeat within the hour) costs 0 HTTP calls. Failures are
        never cached: the exception propagates to the per-symbol
        fail-soft in `_fetch_daily_closes_for_all`.
        """
        urlsafe = "".join(ch if (ch.isalnum() or ch in ".-") else "_"
                         for ch in symbol)
        name = f"daily_{urlsafe}"

        def _fetch() -> dict:
            return {"closes": self._fetch_daily_one(symbol)}

        out = _cached_fetch(self.data_root, name, DAILY_TTL_S, _fetch)
        closes = out.get("closes") or {}
        if not closes:
            raise RuntimeError(f"no daily closes for {symbol}")
        return {d: float(v) for d, v in closes.items()}

    def _fetch_daily_one(self, symbol: str) -> dict[str, float]:
        """One Yahoo v8/chart daily call → {"YYYY-MM-DD": close}.

        The chart response carries a `timestamp` array (epoch seconds
        per daily bar) parallel to the closes — we key each close by
        its UTC calendar date so correlation pairing is date-aligned.
        """
        url = (f"{YAHOO_CHART_URL}{urllib.parse.quote(symbol, safe='')}"
               f"?range=1y&interval=1d")
        data = json.loads(_http_get(url))
        results = (data.get("chart") or {}).get("result") or []
        if not results or not results[0]:
            raise RuntimeError(f"no daily chart for {symbol}")
        r = results[0]
        quote = ((r.get("indicators") or {}).get("quote") or [{}])
        quote = quote[0] if quote else {}
        ts_arr = r.get("timestamp") or []
        closes = quote.get("close") or []
        out: dict[str, float] = {}
        for t, c in zip(ts_arr, closes):
            if t is None or c is None:
                continue
            d = datetime.fromtimestamp(int(t), tz=timezone.utc).strftime(
                "%Y-%m-%d")
            out[d] = float(c)
        if not out:
            raise RuntimeError(f"no closes in daily chart for {symbol}")
        return out


# Test seam for `_fetch_daily_closes_for_all`
# (dict[symbol -> {"YYYY-MM-DD": close}]).
_TEST_DAILY_CLOSES: dict | None = None


# ------------------------------------------------------------------ math
def _aligned_log_returns(closes_a: dict[str, float],
                         closes_b: dict[str, float]
                         ) -> tuple[list[float], list[float]]:
    """DATE-ALIGNED paired log-returns for two symbols (D2 fix).

    Pairs by the INTERSECTION of trading dates: returns are computed
    on consecutive COMMON dates only, so a 24/7 calendar (BTC-USD,
    ~365 closes/yr) never misaligns against a ~5-day/week calendar
    (GC=F, ~260 closes/yr). Position-based tail pairing sign-flipped
    every BTC pair — this kernel is the fix. Returns (rets_a, rets_b),
    index-aligned.
    """
    common = sorted(set(closes_a) & set(closes_b))
    ra: list[float] = []
    rb: list[float] = []
    for k in range(1, len(common)):
        d0, d1 = common[k - 1], common[k]
        a0, a1 = closes_a[d0], closes_a[d1]
        b0, b1 = closes_b[d0], closes_b[d1]
        if a0 > 0 and a1 > 0 and b0 > 0 and b1 > 0:
            ra.append(math.log(a1 / a0))
            rb.append(math.log(b1 / b0))
    return ra, rb


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
    # clamp fp drift; keep FULL float precision — callers round at
    # display time (D6 fix: no 6dp kernel rounding).
    return max(-1.0, min(1.0, r))


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
    """Public accessor for instrument metadata.

    R3-1: the original 8 instruments (INSTRUMENTS map — name, calendar,
    session_mode). R4-2: falls through to the 24-instrument UNIVERSE
    for the 16 added symbols ({name, calendar, session_mode, sector}).
    Returns {} for unknown symbols.
    """
    if symbol in INSTRUMENTS:
        return INSTRUMENTS[symbol]
    from .registry import universe_entry, SESSION_MODES
    entry = universe_entry(symbol)
    if entry:
        return {"name": entry["name"], "calendar": entry["calendar"],
                "session_mode": SESSION_MODES.get(symbol, "fixed"),
                "sector": entry["sector"]}
    return {}
