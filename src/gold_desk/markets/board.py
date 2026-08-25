"""MARKET GAUNTLET market board — threaded multi-market snapshot.

One threaded fan-out (12 workers) over the keyless Yahoo v8/chart
endpoint fills all 67 registry symbols in well under a second: last
price, previous close, change, sparkline points. Fail-soft per symbol
(a dead symbol lands in `errors`, the board never raises), TTL file
cache at <data_root>/cache/markets_board.json (120s), stale-serve on
network failure — same pattern as gold_desk.data.feeds._cached_fetch.

Round-2 fixes (GAUNTLET-P2-BUILDER, after the round-1 critic picked
TradingView on data quality):

* FX pip precision: `_round()` is symbol-aware — "=X" pairs publish at
  5dp below 10 / 3dp at-or-above 10 (tenth-of-a-pip both regimes), so
  EURUSD=X keeps 1.16591 instead of the round-1 "1.17" that destroyed
  41 pips and made USDCAD rows internally inconsistent. Published
  change/change_pct derive from the published price/prev so a row
  never contradicts itself.
* Sparkline fallback: when the 1d/15m fetch yields fewer than 8 points
  (GC=F/SI=F/CL=F gave 3, ^NSEI 5, ags 0) the row refetches that symbol
  at range=5d&interval=60m and sparks off those closes, labeled
  `points_source: "1d" | "5d"` — fail-soft.
* Movers: top-5 gainers/losers by daily change_pct across the whole
  board, computed locally from board rows (no extra fetches).

fetch_detail(symbol) uses TWO chart calls (round-1 defect: a single
5d call mislabeled the 5-day change as daily — gold printed +3.75%
against the board's −0.21%):
    range=1d & interval=15m → daily fields: price, prev_close, change,
                              change_pct (1d meta.chartPreviousClose is
                              the previous session close)
    range=5d & interval=30m → OHLC bars + range_5d_change_pct

Round-3 fixes (GAUNTLET-P4-BUILDER, after the round-2 critic picked
TradingView narrowly on coverage):

* range_5d_change_pct is BAR-DERIVED: (last_close − first_close) /
  first_close over the served 5d bars. Yahoo's 5d meta
  chartPreviousClose anchors near YESTERDAY for 24/7 assets (BTC's 5d
  cp ≈ its 1d cp), so the old meta-anchored math printed +2.74% while
  the bars themselves ran 73,699 → 80,484 (+9.2%). The 1d change
  fields are untouched (they were correct).
* Whole-market movers: fetch_market_movers() reads the keyless Yahoo
  predefined screeners (day_gainers / day_losers, live-probed HTTP
  200; the combined comma form 400s — "Can only have 1 scrId"),
  cached 120s, fail-soft. The board now carries market_movers (whole
  market) AND watchlist_movers (the registry's own top-5, the
  round-2 "movers" renamed; "movers" stays as a back-compat alias).
* Inverse FX pairs: "inr/usd" resolves to the reciprocal registry
  pair USDINR=X and is served INVERTED (price=1/price,
  change_pct=(1/p−1/q)/(1/q)·100, labeled "INR/USD (derived)").
  Pairs outside the registry ("jpy/eur") resolve ad-hoc against
  Yahoo, anchoring on whichever side Yahoo quotes at higher precision
  (inverse pairs publish ~4dp: JPYEUR=X 0.0054 vs EURJPY=X 185.687).

Law boundary: display/education telemetry for the gauntlet surface,
NOT wired into the orchestrator's decision loop.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from .registry import SECTORS, find, normalize, resolve_pair

# verified keyless UA (probed live — see GAUNTLET-P1 brief)
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
YAHOO_SCREENER_URL = ("https://query1.finance.yahoo.com/v1/finance/"
                      "screener/predefined/saved")

BOARD_TTL_S = 120          # 2 minutes — snapshot-fresh, API-polite
BOARD_WORKERS = 12         # threaded fan-out width
SPARK_POINTS = 24          # last ~24 closes for the sparkline
MIN_SPARK_POINTS = 8       # below this a 1d sparkline is degenerate
MOVERS_TOP_N = 5           # watchlist movers strip length
MOVERS_SCREENER_COUNT = 12  # whole-market movers strip length
HTTP_TIMEOUT = 8.0

# P10 defect 1 (ad-hoc raw symbols): guard for treating arbitrary raw
# input as a Yahoo symbol — short and charset-restricted so a mover
# card's ticker (TOP, GENB, EZPW, ETSY…) can be probed directly while
# spaces / %-sequences / query junk never reach the URL builder.
ADHOC_MAX_LEN = 24
_ADHOC_SYMBOL_RE = re.compile(r"^[A-Za-z0-9^.=:/-]+$")


# ------------------------------------------------------------------ fetch
def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _adhoc_candidate(symbol: str) -> str | None:
    """Raw user input → candidate Yahoo symbol for the ad-hoc fallback
    (P10 defect 1). Guards: ≤ ADHOC_MAX_LEN chars and the restricted
    charset ^[A-Za-z0-9^.=:/-]+$ (letters, digits, caret, equals, dot,
    colon, slash, dash — no spaces or %-encoding junk, so there is no
    injection surface in the chart URL). Upper-cased: Yahoo symbols
    are canonically upper-case. Returns None when the input is not a
    safe candidate (then fetch_detail serves the not-found path
    WITHOUT touching the network)."""
    s = str(symbol or "").strip()
    if not s or len(s) > ADHOC_MAX_LEN:
        return None
    if not _ADHOC_SYMBOL_RE.match(s):
        return None
    return s.upper()


def _fetch_chart(symbol: str, range_: str = "1d",
                 interval: str = "15m") -> dict:
    """One Yahoo v8/chart call → chart.result[0]. Raises on any failure
    (callers fail-soft). The v7 quote/spark endpoints are degraded —
    v8/chart is the only shape that still carries the series."""
    url = f"{YAHOO_CHART_URL}{symbol}?range={range_}&interval={interval}"
    data = json.loads(_http_get(url))
    result = (data.get("chart") or {}).get("result") or []
    if not result or not result[0]:
        raise RuntimeError(f"no chart result for {symbol}")
    return result[0]


# ------------------------------------------------------------------ math
def _is_fx(symbol: str | None) -> bool:
    return bool(symbol) and str(symbol).upper().endswith("=X")


def _round(v: float, symbol: str | None = None) -> float | None:
    """Publish precision per instrument class.

    FX pairs (Yahoo "=X" suffix) keep pip resolution — the round-1
    defect published EURUSD=X at 2dp and destroyed 41 pips:
      |v|  < 10 → 5 decimals  (tenth of a pip, e.g. 1.16591)
      |v| >= 10 → 3 decimals  (JPY/INR-style pip, e.g. 159.321)
    Everything else: magnitude-aware (>=1 → 2dp, <1 → 4dp).
    """
    if v is None:
        return None
    v = float(v)
    if symbol and _is_fx(symbol):
        return round(v, 5 if abs(v) < 10 else 3)
    return round(v, 2 if abs(v) >= 1 else 4)


def _round_derived(v: float) -> float | None:
    """Precision for a DERIVED reciprocal FX quote (1/x of a fetched
    pair). One digit finer than the fetched pair keeps the reciprocal
    meaningful: 1/95.717 publishes as 0.010447 (6dp) — Yahoo's own
    native inverse INRUSD=X carries only 0.0104 (4dp)."""
    if v is None:
        return None
    v = float(v)
    return round(v, 6 if abs(v) < 1 else 5)


def _pair_label(symbol: str) -> str:
    """Reciprocal-direction label for a Yahoo FX symbol:
    USDINR=X → "INR/USD" (what the user asked for, derived)."""
    core = str(symbol)[:6].upper()
    return f"{core[3:]}/{core[:3]}"


def _direct_pair_label(symbol: str) -> str:
    """Direct-direction label: EURJPY=X → "EUR/JPY"."""
    core = str(symbol)[:6].upper()
    return f"{core[:3]}/{core[3:]}"


def _swap_sides(symbol: str) -> str:
    """JPYEUR=X → EURJPY=X (keep the =X suffix)."""
    core = str(symbol)[:6].upper()
    return f"{core[3:]}{core[:3]}=X"


def _sig_figs(v) -> int:
    """Significant digits of a published price: 0.0054 → 2,
    185.687 → 6, 95.715 → 5. Used to pick which side of an ad-hoc FX
    pair Yahoo quotes at higher precision."""
    if not isinstance(v, (int, float)) or v == 0:
        return 0
    s = f"{float(v):.12g}".replace("-", "").split("e")[0]
    digits = s.replace(".", "").lstrip("0")
    return len(digits.rstrip("0")) if digits else 1


def _price_of(r: dict) -> float | None:
    """Last price of a chart.result[0] (meta first, closes fallback)."""
    m = (r or {}).get("meta") or {}
    p = m.get("regularMarketPrice")
    if p is None:
        closes = _closes(r or {})
        p = closes[-1] if closes else None
    return p


def _quote(r: dict) -> dict:
    q = ((r.get("indicators") or {}).get("quote") or [{}])
    return q[0] if q else {}


def _closes(r: dict) -> list[float]:
    return [c for c in (_quote(r).get("close") or []) if c is not None]


def fetch_daily_bars(symbol: str, range_: str = "1y",
                     data_root: str | Path = "data") -> list[dict]:
    """R2-2: fetch daily OHLCV bars for a symbol via the v8/chart
    endpoint at range=1y&interval=1d (fail-soft, returns [] on error).

    Used by the quant toolkit's compute_beta path — daily log-returns
    over a 63-day window need 64+ daily closes, which the 5d/30m bars
    from fetch_detail don't carry. This helper is the canonical
    keyless path for daily-resolution bars; cached under
    <data_root>/cache/daily_<SYMBOL>.json with a 30-min TTL (the
    brief's mandated TTL).
    """
    def _build() -> dict:
        r = _fetch_chart(symbol, range_, "1d")
        ts_arr = r.get("timestamp") or []
        q = _quote(r)
        opens = q.get("open") or []
        highs = q.get("high") or []
        lows = q.get("low") or []
        closes = q.get("close") or []
        vols = q.get("volume") or []
        bars: list[dict] = []
        for i, t in enumerate(ts_arr):
            def _at(arr, i):
                return arr[i] if i < len(arr) else None
            o, h, l, c = _at(opens, i), _at(highs, i), _at(lows, i), \
                _at(closes, i)
            v = _at(vols, i)
            if None in (o, h, l, c):
                continue
            bars.append({"ts": int(t) * 1000,
                         "o": _round(o, symbol),
                         "h": _round(h, symbol),
                         "l": _round(l, symbol),
                         "c": _round(c, symbol),
                         "v": float(v) if isinstance(v, (int, float))
                         else 0.0})
        if not bars:
            raise RuntimeError(f"no daily bars for {symbol}")
        return {"ok": True, "symbol": symbol, "range": range_,
                "interval": "1d", "bars": bars}

    cached = _cached_fetch(data_root, f"daily_{symbol.upper()}", 1800,
                            _build)
    if not cached.get("ok"):
        return []
    return cached.get("bars") or []


def _row_from_chart(entry: dict, r: dict) -> dict:
    """Registry entry + chart.result[0] → one board row.

    Published rows are self-consistent: change and change_pct derive
    from the PUBLISHED price/prev_close (each already rounded at the
    symbol's precision) so a row never contradicts itself — the round-1
    defect published USDCAD price=1.39 / prev=1.38 / change=0.002.
    """
    meta = r.get("meta") or {}
    closes = _closes(r)
    # last traded price: final non-null close, else the meta fallback
    price = closes[-1] if closes else meta.get("regularMarketPrice")
    if price is None:
        raise RuntimeError("no price in payload")
    prev = meta.get("chartPreviousClose", meta.get("previousClose"))
    if prev is None and len(closes) >= 2:
        prev = closes[-2]
    ts_arr = r.get("timestamp") or []
    last_ts = ts_arr[-1] if ts_arr else meta.get("regularMarketTime")
    # curated registry name first (clean board labels, TradingView-style);
    # Yahoo's shortName/longName stay in fetch_detail where contract
    # months ("Gold Dec 26") are useful
    name = entry["name"] or meta.get("shortName") or meta.get("longName")
    sym = entry["symbol"]
    price_pub = _round(price, sym)
    prev_pub = _round(prev, sym) if prev else None
    if prev_pub:
        change_pub = _round(price_pub - prev_pub, sym)
        change_pct = round((price_pub - prev_pub) / prev_pub * 100.0, 2)
    else:
        change_pub = change_pct = None
    return {
        "symbol": sym,
        "name": name,
        "sector": entry["sector"],
        "price": price_pub,
        "prev_close": prev_pub,
        "change": change_pub,
        "change_pct": change_pct,
        "currency": meta.get("currency", "USD"),
        "points": [_round(c, sym) for c in closes[-SPARK_POINTS:]],
        "ts": int(last_ts) * 1000 if last_ts else None,
    }


def _row_for_entry(entry: dict) -> dict:
    """One board row: 1d/15m chart for the quote + sparkline.

    Sparkline fallback (round-1 defect): several symbols return only a
    handful of 15m bars in the 1d window (GC=F/SI=F/CL=F gave 3, ^NSEI
    5, KC=F/SB=F zero). When the 1d fetch yields < MIN_SPARK_POINTS
    points we refetch the same symbol at range=5d&interval=60m and
    spark off those closes, labeling the row `points_source` ("1d" or
    "5d"). Fail-soft: a failed/empty fallback keeps the 1d points.
    """
    r = _fetch_chart(entry["symbol"], "1d", "15m")
    row = _row_from_chart(entry, r)
    source = "1d"
    if len(row["points"]) < MIN_SPARK_POINTS:
        try:
            r5 = _fetch_chart(entry["symbol"], "5d", "60m")
            alt = _closes(r5)
            if len(alt) >= MIN_SPARK_POINTS:
                row["points"] = [_round(c, entry["symbol"])
                                 for c in alt[-SPARK_POINTS:]]
                source = "5d"
        except Exception:  # noqa: BLE001 — fallback is best-effort
            pass
    row["points_source"] = source
    return row


# ------------------------------------------------------------------ cache
def _cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


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


def _detail_news(canon: str, data_root: str | Path = "data") -> dict:
    """Per-symbol RSS headlines for fetch_detail (fail-soft seam).

    Live-probed 2026-08-25: feeds.finance.yahoo.com/rss/2.0/headline is
    keyless and serves US equities, crypto, futures, indices and major
    FX (AAPL 18 items, BTC-USD 18, GC=F 16, ^NSEI 6, EURUSD=X 19) but
    carries NO NSE-listed symbols (RELIANCE.NS → empty channel — a
    valid cached state, not an error). Runs off the canon Yahoo symbol
    (an inverted pair like inr/usd → USDINR=X simply gets empty news).
    Never raises — see markets/news.py.
    """
    try:
        from .news import fetch_symbol_news
        return fetch_symbol_news(canon, data_root)
    except Exception:  # noqa: BLE001 — news never breaks the detail
        return {"ok": False, "items": []}


# ------------------------------------------------------- market movers
def _fetch_screener(scr_id: str,
                    count: int = MOVERS_SCREENER_COUNT) -> list[dict]:
    """One keyless Yahoo predefined-screener call → its raw quotes
    list (raises on any failure — callers fail-soft).

    Live-probed 2026-08-25 with the standard UA:
      query1 & query2 …/screener/predefined/saved?scrIds=day_gainers
      &count=12 → HTTP 200, 12 quotes (same for day_losers); the
      combined comma form (scrIds=day_gainers,day_losers) → 400
      "Can only have 1 scrId currently", so gainers and losers are
      two separate calls.
    """
    url = f"{YAHOO_SCREENER_URL}?scrIds={scr_id}&count={count}"
    data = json.loads(_http_get(url))
    results = (data.get("finance") or {}).get("result") or []
    quotes: list[dict] = []
    for r in results:
        if isinstance(r, dict):
            quotes.extend(r.get("quotes") or [])
    if not quotes:
        raise RuntimeError(f"empty screener result: {scr_id}")
    return quotes


def _slim_screener_quote(q: dict) -> dict | None:
    """Screener quote → {symbol, name, price, change_pct}; None when
    there is no numeric change_pct (nothing to rank on)."""
    pct = q.get("regularMarketChangePercent")
    if not isinstance(pct, (int, float)):
        return None
    return {
        "symbol": q.get("symbol"),
        "name": q.get("shortName") or q.get("longName"),
        "price": _round(q.get("regularMarketPrice")),
        "change_pct": round(float(pct), 2),
    }


def fetch_market_movers(data_root: str | Path = "data") -> dict:
    """Whole-market daily gainers/losers from the keyless Yahoo
    predefined screeners (round-3 defect 2: the old movers strip
    ranked only our 67 registry symbols, so a top mover outside the
    curated list could never appear).

    Returns {ok, as_of, gainers, losers} with up to 12 slim quotes per
    side, or {ok: False, error} — never raises (fail-soft, stale-serve
    from cache on network failure). Cached at
    <data_root>/cache/markets_movers.json, TTL 120s.
    """
    def _build() -> dict:
        def _side(scr_id: str) -> list[dict]:
            try:
                return [s for s in (_slim_screener_quote(q)
                                    for q in _fetch_screener(scr_id))
                        if s is not None]
            except Exception:  # noqa: BLE001 — per-side fail-soft
                return []
        with ThreadPoolExecutor(max_workers=2) as ex:
            fg = ex.submit(_side, "day_gainers")
            fl = ex.submit(_side, "day_losers")
            gainers, losers = fg.result(), fl.result()
        if not gainers and not losers:
            raise RuntimeError("screener unreachable (day_gainers and "
                               "day_losers both empty)")
        return {
            "ok": True,
            "as_of": datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "gainers": gainers,
            "losers": losers,
        }

    out = _cached_fetch(data_root, "markets_movers", BOARD_TTL_S, _build)
    out["kind"] = "markets_movers"
    return out


# ------------------------------------------------------------------ board
def _resolve_sectors(sectors) -> list[tuple[str, dict]]:
    """None → all; else valid keys (case-insensitive, order-preserving).
    An empty/unknown filter falls back to all sectors."""
    keys = list(SECTORS.keys())
    if not sectors:
        return [(k, SECTORS[k]) for k in keys]
    wanted: list[str] = []
    for s in sectors:
        k = str(s).strip().lower()
        if k in SECTORS and k not in wanted:
            wanted.append(k)
    if not wanted:
        return [(k, SECTORS[k]) for k in keys]
    return [(k, SECTORS[k]) for k in keys if k in wanted]


def _movers(rows: list[dict], top_n: int = MOVERS_TOP_N) -> dict:
    """Top gainers/losers by daily change_pct across the given rows —
    the WATCHLIST movers (our registry universe). Computed locally
    from board rows (no extra fetches). Rows without a change_pct are
    skipped. Round-2 called this simply "movers"; round-3 renames it
    watchlist_movers (whole-market movers now come from the Yahoo
    screener — fetch_market_movers)."""
    ranked = [r for r in rows
              if isinstance(r.get("change_pct"), (int, float))]

    def slim(r: dict) -> dict:
        return {"symbol": r["symbol"], "name": r["name"],
                "sector": r["sector"], "change_pct": r["change_pct"],
                "price": r["price"]}

    gainers = sorted(ranked, key=lambda r: r["change_pct"],
                     reverse=True)[:top_n]
    losers = sorted(ranked, key=lambda r: r["change_pct"])[:top_n]
    return {"gainers": [slim(r) for r in gainers],
            "losers": [slim(r) for r in losers]}


def fetch_board(data_root: str | Path = "data", sectors=None) -> dict:
    """Threaded multi-market snapshot for the registry (fail-soft).

    Returns {ok, as_of, sectors: [{key, label, rows: [...]}],
    watchlist_movers: {gainers, losers}, movers (back-compat alias of
    watchlist_movers), market_movers?: {gainers, losers}, errors} — a
    symbol that fails lands in `errors` and never raises.

    watchlist_movers ranks the fetched rows (top-5 daily gainers/
    losers across the whole — or filtered — board). market_movers
    (round-3) is the WHOLE-MARKET strip from the Yahoo predefined
    screeners via fetch_market_movers() — merged post-cache so the
    screener keeps its own 120s cache and an outage never poisons the
    board cache; the key is simply absent when the screener is
    unreachable (fail-soft). Cached at
    <data_root>/cache/markets_board.json, TTL 120s; sector-filtered
    boards get their own cache file.
    """
    wanted = _resolve_sectors(sectors)
    if len(wanted) == len(SECTORS):
        cache_name = "markets_board"
    else:
        cache_name = "markets_board_" + "-".join(k for k, _ in wanted)

    def _build() -> dict:
        entries = [
            {"symbol": s["symbol"], "name": s["name"], "sector": key}
            for key, sec in wanted for s in sec["symbols"]
        ]
        rows: dict[str, dict] = {}
        errors: list[str] = []
        with ThreadPoolExecutor(max_workers=BOARD_WORKERS) as ex:
            futures = {ex.submit(_row_for_entry, e): e for e in entries}
            for fut in as_completed(futures):
                entry = futures[fut]
                try:
                    rows[entry["symbol"]] = fut.result()
                except Exception:  # noqa: BLE001 — fail-soft per symbol
                    errors.append(entry["symbol"])
        if not rows:
            raise RuntimeError("all symbol fetches failed")
        out_sectors = []
        for key, sec in wanted:
            sec_rows = [rows[s["symbol"]] for s in sec["symbols"]
                        if s["symbol"] in rows]
            out_sectors.append({"key": key, "label": sec["label"],
                                "rows": sec_rows})
        all_rows = [r for sec in out_sectors for r in sec["rows"]]
        watchlist = _movers(all_rows)
        return {
            "ok": True,
            "as_of": datetime.now(timezone.utc).isoformat(
                timespec="seconds").replace("+00:00", "Z"),
            "sectors": out_sectors,
            "watchlist_movers": watchlist,
            "movers": watchlist,   # round-2 key, kept as back-compat alias
            "errors": sorted(errors),
        }

    out = _cached_fetch(data_root, cache_name, BOARD_TTL_S, _build)
    out["kind"] = "markets_board"
    # stale round-2 cache files: backfill the renamed key from the alias
    if "watchlist_movers" not in out and out.get("movers"):
        out["watchlist_movers"] = out["movers"]
    # whole-market movers (round-3 defect 2) — own 120s cache, fail-soft:
    # the key is absent when the screener is unreachable
    try:
        mm = fetch_market_movers(data_root)
    except Exception:  # noqa: BLE001 — display telemetry fails soft
        mm = {}
    if mm.get("ok"):
        out["market_movers"] = {"gainers": mm.get("gainers") or [],
                                "losers": mm.get("losers") or []}
    return out


# ----------------------------------------------------------------- detail
def _resolve_for_detail(symbol: str):
    """Input → (canon, inverted, in_registry) for fetch_detail.

    normalize() wins first so aliases keep beating pair heuristics
    ("xauusd" stays GC=F, "silver" stays SI=F even though both are
    6-alpha pair-shaped). When the pair resolver says the registry
    symbol is the RECIPROCAL of what the user asked (inr/usd →
    USDINR=X) the inverted flag is set and the quote is served as
    1/price. Pair inputs with no registry hit on either side (jpy/eur)
    return the ad-hoc Yahoo pair, in_registry=False.

    Resolution order (P10 defect 1, whole-market movers dead-end):
    registry alias → registry symbol → FX pair/direct → reciprocal
    pair → AD-HOC raw Yahoo attempt (in fetch_detail, guarded by
    _adhoc_candidate) → not found. Before P10 anything the registry
    and pair resolvers didn't know (screener movers like TOP/GENB/
    EZPW, or any arbitrary ticker) dead-ended on "Symbol not found".
    """
    norm = normalize(symbol)
    pr = resolve_pair(symbol)
    if norm is not None:
        inverted = bool(pr and pr[2] and pr[1] and pr[0] == norm)
        return norm, inverted, True
    if pr:
        return pr
    return None, False, False


def fetch_detail(symbol: str, data_root: str | Path = "data") -> dict:
    """Drill-down for any registry symbol — two chart calls.

    Round-1 defect fixed: the old single range=5d fetch used
    meta.chartPreviousClose (the 5-day-ago close) as the daily previous
    close, so gold's detail printed +3.75% while the board said −0.21%.
    Now:
      range=1d & interval=15m → price, prev_close, change, change_pct
      (daily change, same chain as the board) and
      range=5d & interval=30m → OHLC bars + range_5d_change_pct
      (the 5-day change, labeled as such).

    Round-3 fixes:
      * range_5d_change_pct is BAR-DERIVED — (last_close −
        first_close)/first_close over the served 5d bars. The old
        meta.chartPreviousClose anchor sits near YESTERDAY for 24/7
        assets (BTC 5d cp ≈ 1d cp), printing +2.74% against a
        73,699→80,484 (+9.2%) bar series.
      * inverse FX pairs: "inr/usd" resolves to the reciprocal registry
        pair USDINR=X and is served INVERTED — price=1/price,
        prev=1/prev, change recomputed, change_pct =
        (1/p−1/q)/(1/q)·100 exactly, named "INR/USD (derived)", with
        derived/derived_from flags and inverted OHLC bars (high/low
        swap under 1/x). Pairs outside the registry ("jpy/eur") are
        fetched ad-hoc from Yahoo — BOTH directions in parallel,
        anchoring on whichever side Yahoo quotes at higher precision
        (inverse pairs publish ~4dp: JPYEUR=X 0.0054 vs EURJPY=X
        185.687), deriving the other side by inversion.

    Input goes through registry.normalize() — "btc", "gold",
    "reliance", "vix", "10y", "inr/usd" all resolve. Fail-soft:
    never raises, {ok: False, error} instead. If only one of the two
    fetches works, the other block degrades (None / empty bars)
    without killing the response.

    Round-4 (GAUNTLET-P8-BUILDER): the detail now carries `news` —
    per-symbol keyless Yahoo RSS headlines (max 8, 300s cache, fail-
    soft; fetched in parallel with the chart calls for registry
    symbols). Empty news (Yahoo's RSS carries no NSE-listed symbols)
    is {ok: True, items: []} — the drill-down page simply hides the
    card.

    Round-5 (GAUNTLET-P10-BUILDER, defect 1): AD-HOC raw symbols.
    When registry AND pair resolution both fail, the guarded raw
    input (see _adhoc_candidate) is tried as a Yahoo symbol directly
    — validated by the 1d chart fetch itself: chart.result[0] with a
    price (meta.regularMarketPrice or closes) → served with
    sector="adhoc", name from meta.shortName/longName (raw symbol
    fallback) and every normal field; anything else → the same clean
    not-found as before. This is what makes the ~24 whole-market
    mover cards (screener symbols like TOP/GENB/EZPW outside the
    67-symbol registry) one-click drill-downs instead of dead ends —
    and any arbitrary ticker (ETSY, …) too. A 6-letter all-alpha
    ticker reads as an FX pair to resolve_pair; when neither pair
    side serves, the raw input still gets its ad-hoc attempt
    (slash-free only — a "/" is pair intent, never a Yahoo symbol).
    """
    try:
        canon, inverted, in_reg = _resolve_for_detail(symbol)
        adhoc = False
        if not canon:
            # P10 defect 1: registry + pair resolution both missed —
            # the raw input itself is the last candidate
            cand = _adhoc_candidate(symbol)
            if cand is None:
                return {"ok": False, "symbol": str(symbol),
                        "error": f"unknown symbol: {symbol!r}"}
            canon, in_reg, adhoc = cand, False, True
        daily: dict | None = None
        weekly: dict | None = None
        daily_err = weekly_err = None
        news: dict = {"ok": False, "items": []}
        if in_reg:
            # registry symbol (or its reciprocal): 1d ∥ 5d ∥ news
            with ThreadPoolExecutor(max_workers=3) as ex:
                fd = ex.submit(_fetch_chart, canon, "1d", "15m")
                fw = ex.submit(_fetch_chart, canon, "5d", "30m")
                fn = ex.submit(_detail_news, canon, data_root)
                try:
                    daily = fd.result()
                except Exception as e:  # noqa: BLE001 — per-block fail-soft
                    daily_err = f"{type(e).__name__}: {e}"
                try:
                    weekly = fw.result()
                except Exception as e:  # noqa: BLE001
                    weekly_err = f"{type(e).__name__}: {e}"
                try:
                    news = fn.result()
                except Exception:  # noqa: BLE001 — news fails soft
                    pass
        elif adhoc:
            # P10 defect 1: ad-hoc raw symbol — the 1d chart fetch IS
            # the validation (Yahoo serving chart.result[0] with a
            # price means the symbol exists); a failed/priceless
            # fetch is the not-found path, never a half-quote
            try:
                daily = _fetch_chart(canon, "1d", "15m")
                if _price_of(daily) is None:
                    raise RuntimeError(f"no price in payload for {canon}")
            except Exception:
                return {"ok": False, "symbol": str(symbol),
                        "error": f"unknown symbol: {symbol!r}"}
            with ThreadPoolExecutor(max_workers=2) as ex:
                fw = ex.submit(_fetch_chart, canon, "5d", "30m")
                fn = ex.submit(_detail_news, canon, data_root)
                try:
                    weekly = fw.result()
                except Exception as e:  # noqa: BLE001
                    weekly_err = f"{type(e).__name__}: {e}"
                try:
                    news = fn.result()
                except Exception:  # noqa: BLE001 — news fails soft
                    pass
        else:
            # ad-hoc Yahoo pair (neither side in the registry): fetch
            # BOTH directions, anchor on the better-quoted side
            direct, recip = canon, _swap_sides(canon)
            d_daily = r_daily = None
            with ThreadPoolExecutor(max_workers=2) as ex:
                fd = ex.submit(_fetch_chart, direct, "1d", "15m")
                fr = ex.submit(_fetch_chart, recip, "1d", "15m")
                try:
                    d_daily = fd.result()
                except Exception:  # noqa: BLE001 — candidate probing
                    pass
                try:
                    r_daily = fr.result()
                except Exception:  # noqa: BLE001
                    pass
            if d_daily is not None or r_daily is not None:
                if r_daily is not None and (d_daily is None or
                                            _sig_figs(_price_of(r_daily)) >
                                            _sig_figs(_price_of(d_daily))):
                    canon, daily, inverted = recip, r_daily, True
                else:
                    daily = d_daily
                try:
                    weekly = _fetch_chart(canon, "5d", "30m")
                except Exception as e:  # noqa: BLE001
                    weekly_err = f"{type(e).__name__}: {e}"
            else:
                # P10 defect 1: a pair-shaped input may be a plain
                # 6-letter ticker ("XXXXXX" reads as a pair to
                # resolve_pair) — give the raw input its ad-hoc Yahoo
                # attempt before giving up. Slash-free only: a "/" is
                # pair intent and never a valid Yahoo symbol path.
                rescued = None
                cand = _adhoc_candidate(symbol)
                if cand and "/" not in cand:
                    try:
                        c_daily = _fetch_chart(cand, "1d", "15m")
                        if _price_of(c_daily) is not None:
                            rescued = c_daily
                    except Exception:  # noqa: BLE001 — last-resort probe
                        pass
                if rescued is not None:
                    canon, daily, adhoc = cand, rescued, True
                    try:
                        weekly = _fetch_chart(canon, "5d", "30m")
                    except Exception as e:  # noqa: BLE001
                        weekly_err = f"{type(e).__name__}: {e}"
                else:
                    daily_err = (f"no chart result for {direct} or {recip}")
            # canon is final after the ad-hoc probe — news for that side
            news = _detail_news(canon, data_root)
        if daily is None and weekly is None:
            raise RuntimeError(f"daily: {daily_err}; 5d: {weekly_err}")

        # ---- daily fields (range=1d — chartPreviousClose here IS the
        # previous session close, same chain the board uses). Published
        # values are self-consistent: change/change_pct derive from the
        # rounded price/prev (board scheme). For an inverted pair the
        # quote is served as the reciprocal: price=1/p, prev=1/q,
        # change recomputed from the inverted values, change_pct =
        # (1/p−1/q)/(1/q)·100 exactly.
        dmeta = (daily or {}).get("meta") or {}
        d_closes = _closes(daily or {})
        price = prev = change = change_pct = None
        raw_price = raw_prev = None
        if daily is not None:
            raw_price = d_closes[-1] if d_closes else \
                dmeta.get("regularMarketPrice")
            raw_prev = dmeta.get("chartPreviousClose",
                                 dmeta.get("previousClose"))
            if raw_prev is None and len(d_closes) >= 2:
                raw_prev = d_closes[-2]
        if inverted and raw_price and raw_prev:
            ip, iq = 1.0 / raw_price, 1.0 / raw_prev
            price = _round_derived(ip)
            prev = _round_derived(iq)
            change = _round_derived(ip - iq)
            change_pct = round((ip - iq) / iq * 100.0, 2)
        elif raw_price is not None:
            price = _round(raw_price, canon)
            if raw_prev:
                prev = _round(raw_prev, canon)
                if price is not None:
                    change = _round(price - prev, canon)
                    change_pct = round((price - prev) / prev * 100.0, 2)

        # ---- 5d fields (range=5d — bars + honestly-labeled 5d change)
        wmeta = (weekly or {}).get("meta") or {}
        bars: list[dict] = []
        range_5d_change_pct = None
        if weekly is not None:
            ts_arr = weekly.get("timestamp") or []
            q = _quote(weekly)
            opens = q.get("open") or []
            highs = q.get("high") or []
            lows = q.get("low") or []
            closes = q.get("close") or []

            def _at(arr, i):
                return arr[i] if i < len(arr) else None

            for i, t in enumerate(ts_arr):
                o, h = _at(opens, i), _at(highs, i)
                l, c = _at(lows, i), _at(closes, i)
                if None in (o, h, l, c):
                    continue
                if inverted:
                    # reciprocal bar: 1/x reverses order, so high/low swap
                    o, h, l, c = 1.0 / o, 1.0 / l, 1.0 / h, 1.0 / c
                    bars.append({"ts": int(t) * 1000,
                                 "o": _round_derived(o),
                                 "h": _round_derived(h),
                                 "l": _round_derived(l),
                                 "c": _round_derived(c)})
                else:
                    bars.append({"ts": int(t) * 1000,
                                 "o": _round(o, canon),
                                 "h": _round(h, canon),
                                 "l": _round(l, canon),
                                 "c": _round(c, canon)})

            # round-3 defect 1: range_5d_change_pct from the BARS
            # themselves — the CLOSE of the FIRST bar in the window
            # (not the open, not meta.chartPreviousClose, which for
            # 24/7 assets anchors near yesterday). Falls back to the
            # raw closes when no bar carries full OHLC.
            if bars:
                anchor_c, last_c = bars[0]["c"], bars[-1]["c"]
            else:
                w_closes = _closes(weekly)
                anchor_c = w_closes[0] if w_closes else None
                last_c = w_closes[-1] if w_closes else None
            if anchor_c:
                range_5d_change_pct = round(
                    (last_c - anchor_c) / anchor_c * 100.0, 2)

        entry = find(canon)
        meta = dmeta or wmeta
        if inverted:
            name = f"{_pair_label(canon)} (derived)"
        else:
            name = (meta.get("shortName") or meta.get("longName")
                    or (entry["name"] if entry else None)
                    or (canon if adhoc else None)   # raw symbol, P10
                    or _direct_pair_label(canon))
        out = {
            "ok": True,
            "symbol": canon,
            "name": name,
            "sector": (entry["sector"] if entry
                       else ("adhoc" if adhoc else "forex")),
            "currency": (canon[:3] if inverted
                         else meta.get("currency", "USD")),
            "price": price,
            "prev_close": prev,
            "change": change,
            "change_pct": change_pct,
            "range_5d_change_pct": range_5d_change_pct,
            "bars": bars,
            "meta": meta,
        }
        if inverted:
            out["derived"] = True
            out["derived_from"] = canon
        out["news"] = news
        return out
    except Exception as e:  # noqa: BLE001 — display telemetry fails soft
        return {"ok": False, "symbol": str(symbol),
                "error": f"{type(e).__name__}: {e}"}
