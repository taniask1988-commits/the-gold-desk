"""R3-1 Build 1 — tests for the multi-asset live monitor.

Covers:
* Yahoo multi-quote parser (mocked chart response shape, _parse_chart_quote)
* Pearson correlation math (vs numpy reference, 3 assets)
* Spearman correlation (vs scipy reference, 3 assets) — skip if scipy missing
* Session-relative % move for each asset (incl. 24/7 BTC rolling-24h path)
* Fail-soft: one asset returning 404 doesn't fail the others
* Snapshot is JSON-serializable (round-trip)
* Correlation matrix symmetric + diagonal = 1.0
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.markets import multi_asset as ma
from gold_desk.markets.multi_asset import (
    AssetSnapshot, MultiAssetMonitor, INSTRUMENTS, INSTRUMENT_ORDER,
    _correlation, _log_returns, _parse_chart_quote, _rank_avg,
    _session_vwap_and_open, _vwap, fetch_multi_quote,
)


# ----------------------------------------------------------- mock chart
def _make_chart(symbol: str, price: float, prev: float,
                 bars: list[dict] | None = None,
                 currency: str = "USD") -> dict:
    """Yahoo v8/chart result[0] shape, minimal."""
    return {
        "meta": {
            "regularMarketPrice": price,
            "chartPreviousClose": prev,
            "currency": currency,
            "regularMarketTime": 1700000000,
        },
        "timestamp": [int(b["ts"] / 1000) for b in (bars or [])],
        "indicators": {
            "quote": [{
                "open":   [b.get("o") for b in (bars or [])],
                "high":   [b.get("h") for b in (bars or [])],
                "low":    [b.get("l") for b in (bars or [])],
                "close":  [b.get("c") for b in (bars or [])],
                "volume": [b.get("v", 0) for b in (bars or [])],
            }],
        },
    }


def test_parse_chart_quote_minimal():
    """Parser pulls price/prev/currency/bars from a minimal chart payload."""
    bars = [
        {"ts": 1699900800000, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.5, "v": 10},
        {"ts": 1699904400000, "o": 100.5, "h": 102.0, "l": 100.0, "c": 101.5, "v": 20},
    ]
    chart = _make_chart("GC=F", 101.5, 100.0, bars=bars)
    out = _parse_chart_quote("GC=F", chart)
    assert out["ok"] is True
    assert out["symbol"] == "GC=F"
    assert out["price"] == 101.5
    assert out["prev_close"] == 100.0
    assert out["change"] == 1.5
    assert abs(out["change_pct"] - 1.5) < 0.01
    assert len(out["bars"]) == 2
    assert out["bars"][0]["c"] == 100.5
    assert out["bars"][1]["v"] == 20


def test_parse_chart_quote_no_closes():
    """When price is None in both meta and closes, raises (caller fail-soft)."""
    chart = {
        "meta": {"currency": "USD"},
        "timestamp": [],
        "indicators": {"quote": [{"open": [], "high": [],
                                   "low": [], "close": [], "volume": []}]},
    }
    with pytest.raises(RuntimeError):
        _parse_chart_quote("DUD", chart)


def test_parse_chart_quote_uses_closes_when_meta_missing():
    """When meta.regularMarketPrice is missing, falls back to last close."""
    bars = [
        {"ts": 1699900800000, "o": 1.0, "h": 1.1, "l": 0.9, "c": 1.05, "v": 5},
        {"ts": 1699904400000, "o": 1.05, "h": 1.2, "l": 1.0, "c": 1.15, "v": 7},
    ]
    chart = {
        "meta": {"chartPreviousClose": 1.0, "currency": "USD"},
        "timestamp": [int(b["ts"] / 1000) for b in bars],
        "indicators": {"quote": [{
            "open": [b["o"] for b in bars],
            "high": [b["h"] for b in bars],
            "low": [b["l"] for b in bars],
            "close": [b["c"] for b in bars],
            "volume": [b["v"] for b in bars],
        }]},
    }
    out = _parse_chart_quote("BTC-USD", chart)
    assert out["price"] == 1.15
    assert out["prev_close"] == 1.0


# ----------------------------------------------------------- mock fetcher
def _mock_fetch_factory(alive: set[str] | None = None) -> callable:
    """Build a fetcher that returns a 2-bar quote per requested symbol.
    `alive` set controls which symbols succeed — those absent return
    `{ok: False}` to exercise the fail-soft path.
    """
    alive = alive or set(INSTRUMENTS.keys())

    def _fetch(symbols: list[str]) -> dict:
        out = {}
        for s in symbols:
            if s not in alive:
                out[s] = {"ok": False, "symbol": s,
                          "error": "HTTPError: 404 Not Found"}
                continue
            bars = [
                {"ts": 1699900800000, "o": 100.0, "h": 101.0,
                 "l": 99.0,  "c": 100.5, "v": 10},
                {"ts": 1699904400000, "o": 100.5, "h": 102.0,
                 "l": 100.0, "c": 101.5, "v": 20},
            ]
            out[s] = {
                "ok": True, "symbol": s, "price": 101.5,
                "prev_close": 100.0, "change": 1.5,
                "change_pct": 1.5, "currency": "USD",
                "market_time": 1700000000, "bars": bars,
                "source": f"mock:{s}",
            }
        return out
    return _fetch


def test_snapshot_eight_instruments():
    """snapshot() returns an entry for all 8 instruments, alive or not."""
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())
    out = mon.snapshot()
    assert out["ok"] is True
    assets = out["assets"]
    for sym in INSTRUMENT_ORDER:
        assert sym in assets, f"missing {sym} in snapshot"
        a = assets[sym]
        assert a["symbol"] == sym
        assert a["calendar"] == INSTRUMENTS[sym]["calendar"]
        assert a["live"] is True
        # sparkline non-empty (24 points or fewer)
        assert isinstance(a["sparkline"], list)


def test_snapshot_fail_soft_one_404():
    """One symbol returning 404 doesn't kill the others."""
    alive = set(INSTRUMENTS.keys()) - {"^VIX"}
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory(alive))
    out = mon.snapshot()
    assert out["ok"] is True
    assert "^VIX" in out["errors"]
    # the other 7 are alive
    assets = out["assets"]
    for sym in INSTRUMENT_ORDER:
        if sym == "^VIX":
            assert assets[sym]["live"] is False
            assert assets[sym]["error"]
        else:
            assert assets[sym]["live"] is True
            assert assets[sym]["price"] == 101.5


def test_snapshot_json_serializable():
    """Snapshot round-trips through json.dumps without loss."""
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())
    out = mon.snapshot()
    s = json.dumps(out, default=str, sort_keys=True)
    assert isinstance(s, str)
    back = json.loads(s)
    assert back["ok"] is True
    assert len(back["assets"]) == 8
    # spot-check an AssetSnapshot field survives round-trip
    gc = back["assets"]["GC=F"]
    assert gc["symbol"] == "GC=F"
    assert gc["calendar"] == "COMEX"


def test_snapshot_vwap_and_relative_pct():
    """Session VWAP and session_relative_pct compute on the mock bars."""
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())
    out = mon.snapshot()
    gc = out["assets"]["GC=F"]
    # mock bars: tp1=(101+99+100.5)/3=100.17 v=10; tp2=(102+100+101.5)/3=101.17 v=20
    # vwap = (100.17*10 + 101.17*20)/30 ≈ 100.83
    # GC=F session_mode = "fixed" → UTC hour slice; if both bars fall in
    # same session window, vwap should be ~100.83. Session-relative
    # pct = (101.5 - 100.83)/100.83 * 100 ≈ +0.66
    assert gc["session_vwap"] is not None
    assert isinstance(gc["session_relative_pct"], (int, float))
    assert abs(gc["session_relative_pct"]) < 5.0  # sanity bound


def test_snapshot_btc_rolling_24h():
    """BTC (24/7) gets a rolling-24h VWAP regardless of UTC session."""
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())
    out = mon.snapshot()
    btc = out["assets"]["BTC-USD"]
    # session field for 24/7 = "24h" or "off/24h" (weekend fallback)
    assert btc["session"] in ("24h", "off/24h", "asia", "london",
                              "london_ny_overlap", "ny")
    assert btc["session_vwap"] is not None
    assert isinstance(btc["session_relative_pct"], (int, float))


# ------------------------------------------------------ correlation math
def _numpy_corr(x, y):
    """Reference Pearson correlation via numpy (or skip if unavailable)."""
    try:
        import numpy as np
    except ImportError:
        pytest.skip("numpy not installed")
    a = np.array(x, dtype=float)
    b = np.array(y, dtype=float)
    n = min(len(a), len(b))
    a, b = a[-n:], b[-n:]
    return float(np.corrcoef(a, b)[0, 1])


def test_pearson_vs_numpy_three_assets():
    """Hand-rolled Pearson matches numpy.corrcoef for 3 series."""
    # synthetic 3-asset return series with known correlation structure
    a = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, 0.015, -0.005, 0.025, -0.015]
    # b is exactly proportional to a → perfect positive (r=1.0)
    b = [v * 0.5 + 0.001 for v in a]
    # c is the exact negative of a (shifted by a constant) → r=-1.0
    c = [-v for v in a]
    r_ab = _correlation(a, b, method="pearson")
    r_ac = _correlation(a, c, method="pearson")
    r_bc = _correlation(b, c, method="pearson")
    assert r_ab is not None
    assert r_ac is not None
    assert r_bc is not None
    n_ab = _numpy_corr(a, b)
    n_ac = _numpy_corr(a, c)
    n_bc = _numpy_corr(b, c)
    assert abs(r_ab - n_ab) < 1e-6, f"AB: ours={r_ab} numpy={n_ab}"
    assert abs(r_ac - n_ac) < 1e-6, f"AC: ours={r_ac} numpy={n_ac}"
    assert abs(r_bc - n_bc) < 1e-6, f"BC: ours={r_bc} numpy={n_bc}"
    assert r_ab > 0.999       # near-perfect positive (constant shift)
    assert r_ac < -0.999      # near-perfect negative


def test_spearman_vs_scipy_three_assets():
    """Hand-rolled Spearman (avg ranks) matches scipy.stats.spearmanr."""
    try:
        from scipy.stats import spearmanr
    except ImportError:
        pytest.skip("scipy not installed")
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0]
    b = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0, 8.0, 7.0, 10.0, 9.0]
    c = [10.0, 9.0, 8.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.0, 1.0]
    r_ab = _correlation(a, b, method="spearman")
    r_ac = _correlation(a, c, method="spearman")
    r_bc = _correlation(b, c, method="spearman")
    s_ab, _ = spearmanr(a, b)
    s_ac, _ = spearmanr(a, c)
    s_bc, _ = spearmanr(b, c)
    assert r_ab is not None and r_ac is not None and r_bc is not None
    assert abs(r_ab - float(s_ab)) < 1e-6
    assert abs(r_ac - float(s_ac)) < 1e-6
    assert abs(r_bc - float(s_bc)) < 1e-6


def test_spearman_handles_ties():
    """Spearman average-rank for ties — known shape with 3 ties."""
    # values with two tie groups
    a = [1.0, 2.0, 2.0, 3.0, 3.0, 3.0, 4.0]
    b = [10.0, 20.0, 20.0, 30.0, 30.0, 30.0, 40.0]
    ranks = _rank_avg(a)
    # ranks for the tie group at index 1,2 (values 2.0): (2+3)/2 = 2.5
    # ranks for the tie group at index 3,4,5 (values 3.0): (4+5+6)/3 = 5.0
    # rank of index 0 (value 1.0): 1.0
    # rank of index 6 (value 4.0): 7.0
    assert ranks[0] == 1.0
    assert ranks[1] == 2.5
    assert ranks[2] == 2.5
    assert ranks[3] == 5.0
    assert ranks[4] == 5.0
    assert ranks[5] == 5.0
    assert ranks[6] == 7.0
    # full tie → spearman should still produce a result
    r = _correlation(a, b, method="spearman")
    assert r is not None
    assert abs(r - 1.0) < 1e-6  # perfectly monotone


def test_correlation_degenerate_returns_none():
    """Zero variance → None (degenerate input)."""
    flat = [1.0, 1.0, 1.0, 1.0]
    other = [1.0, 2.0, 3.0, 4.0]
    assert _correlation(flat, other, method="pearson") is None
    assert _correlation(other, flat, method="pearson") is None


def test_correlation_too_short_returns_none():
    """<2 points → None."""
    assert _correlation([1.0], [2.0]) is None
    assert _correlation([], []) is None


def test_correlation_clamp_to_pm1():
    """Output never exceeds [-1, 1] even under fp drift."""
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [1.0, 2.0, 3.0, 4.0, 5.0]
    r = _correlation(a, b, method="pearson")
    assert -1.0 <= r <= 1.0


# ----------------------------------------------------- correlation matrix
def _mock_daily_closes(n_days: int = 60) -> dict:
    """Deterministic synthetic closes per instrument for matrix tests.

    Date-keyed (D2): each symbol maps {"YYYY-MM-DD": close} over
    n_days consecutive calendar days (all 8 share the same calendar
    here — mixed-calendar coverage lives in the D2 regression tests
    below)."""
    import random
    from datetime import timedelta
    rng = random.Random(42)
    out: dict[str, dict[str, float]] = {}
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    dates = [(start + timedelta(days=i)).strftime("%Y-%m-%d")
             for i in range(n_days)]
    for sym in INSTRUMENT_ORDER:
        base = 100.0
        prices = [base]
        for _ in range(n_days - 1):
            # Gold-like drift; BTC more volatile
            vol = 0.02 if sym == "BTC-USD" else 0.01
            drift = 0.0005 if sym in ("GC=F", "ES=F") else 0
            prices.append(prices[-1] * (1 + drift + rng.gauss(0, vol)))
        out[sym] = dict(zip(dates, prices))
    return out


def test_correlation_matrix_returns_dict():
    """Matrix is a dict[symbol][symbol] -> float|None."""
    ma._TEST_DAILY_CLOSES = _mock_daily_closes(60)
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        assert out["ok"] is True
        assert out["window"] == 30
        assert out["method"] == "pearson"
        # D3: clean fetch → not degraded, empty errors
        assert out["degraded"] is False
        assert out["errors"] == []
        syms = out["symbols"]
        assert "GC=F" in syms
        matrix = out["matrix"]
        # diagonal = 1.0
        for s in syms:
            assert matrix[s][s] == 1.0
        # symmetric
        for s1 in syms:
            for s2 in syms:
                if matrix[s1][s2] is not None and matrix[s2][s1] is not None:
                    assert matrix[s1][s2] == matrix[s2][s1]
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_correlation_matrix_method_unknown_rejected():
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())
    out = mon.compute_correlation(window=30, method="kendall")
    assert out["ok"] is False


def test_correlation_matrix_spearman_runs():
    """Spearman path runs end-to-end without error."""
    ma._TEST_DAILY_CLOSES = _mock_daily_closes(40)
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=20, method="spearman")
        assert out["ok"] is True
        assert out["method"] == "spearman"
        # diagonal = 1.0
        for s in out["symbols"]:
            assert out["matrix"][s][s] == 1.0
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_log_returns_basic():
    """_log_returns computes ln(b/a) skipping non-positive closes.

    A 0.0 close breaks the chain on BOTH sides (k=i-1 when prev is 0
    AND k=i when current is 0) — so a 4-element series with one 0 in
    the middle produces 1 valid return (before the 0).
    """
    closes = [100.0, 110.0, 0.0, 121.0]   # 0.0 breaks the chain both sides
    rets = _log_returns({"X": closes})["X"]
    assert len(rets) == 1
    assert abs(rets[0] - math.log(110 / 100)) < 1e-9


def test_vwap_volume_weighted():
    """VWAP weights by volume when volumes present (label: vwap)."""
    bars = [
        {"h": 11.0, "l": 9.0, "c": 10.0, "v": 10},
        {"h": 21.0, "l": 19.0, "c": 20.0, "v": 30},
    ]
    # tp1=(11+9+10)/3=10; tp2=(21+19+20)/3=20
    # vwap = (10*10 + 20*30)/40 = (100 + 600)/40 = 17.5
    v, method = _vwap(bars)
    assert abs(v - 17.5) < 1e-6
    assert method == "vwap"


def test_vwap_no_volume_falls_back_to_typical_mean():
    """When every volume is 0, fall back to the unweighted
    typical-price mean — LABELED typical_unweighted (D5, no longer
    silent)."""
    bars = [
        {"h": 11.0, "l": 9.0, "c": 10.0, "v": 0},
        {"h": 21.0, "l": 19.0, "c": 20.0, "v": 0},
    ]
    # tp1=10, tp2=20; unweighted mean = 15
    v, method = _vwap(bars)
    assert abs(v - 15.0) < 1e-6
    assert method == "typical_unweighted"


def test_vwap_single_bar_label():
    """Exactly 1 bar in the session → vwap_method single_bar (D5)."""
    bars = [{"h": 12.0, "l": 6.0, "c": 9.0, "v": 0}]
    v, method = _vwap(bars)
    assert abs(v - 9.0) < 1e-9   # (12+6+9)/3
    assert method == "single_bar"
    # a single bar WITH volume still labels single_bar
    v2, m2 = _vwap([{"h": 12.0, "l": 6.0, "c": 9.0, "v": 100}])
    assert m2 == "single_bar"
    assert abs(v2 - 9.0) < 1e-9


def test_vwap_empty():
    """Empty bars → (None, "none")."""
    assert _vwap([]) == (None, "none")


def test_session_vwap_24h_mode():
    """rolling24 mode slices last 24h regardless of UTC hour."""
    now = datetime.now(timezone.utc)
    bars = []
    for h in range(-48, 1):
        ts = (now.timestamp() + h * 3600) * 1000
        bars.append({"ts": ts, "o": 100.0, "h": 101.0,
                      "l": 99.0, "c": 100.5, "v": 1})
    v, op, sess, method = _session_vwap_and_open(bars, mode="rolling24")
    assert v is not None
    assert sess == "24h"
    assert op == 100.0   # first bar of the 24h window
    assert method == "vwap"   # volumes present → volume-weighted


def test_session_vwap_fixed_mode_slices_session():
    """fixed mode slices by UTC hour window matching SESSION_BOUNDS."""
    # construct bars that fall in the NY session (UTC 16:00-21:00)
    base = datetime(2026, 1, 5, 17, 0, 0, tzinfo=timezone.utc)
    bars = []
    for m in range(0, 240, 15):
        ts = (base.timestamp() + m * 60) * 1000
        bars.append({"ts": ts, "o": 100.0, "h": 101.0,
                      "l": 99.0, "c": 100.5, "v": 2})
    v, op, sess, method = _session_vwap_and_open(bars, mode="fixed")
    assert sess == "ny"
    assert v is not None
    assert method == "vwap"


# --------------------------------------------------------------- helpers
def _tmp_data_root() -> Path:
    import tempfile
    return tempfile.mkdtemp(prefix="multi_asset_test_")


def test_fetch_multi_quote_test_seam():
    """The _TEST_QUOTES module-level seam short-circuits HTTP."""
    ma._TEST_QUOTES = {
        "GC=F": {"ok": True, "symbol": "GC=F", "price": 2050.0,
                  "prev_close": 2040.0, "change_pct": 0.49,
                  "currency": "USD", "market_time": 1700000000,
                  "bars": [], "source": "mock"},
    }
    try:
        out = fetch_multi_quote(["GC=F", "ES=F"])
        assert out["GC=F"]["ok"] is True
        assert out["GC=F"]["price"] == 2050.0
        assert out["ES=F"]["ok"] is False  # not in mock
    finally:
        ma._TEST_QUOTES = None


def test_instrument_meta():
    """Public instrument_meta() returns the calendar metadata."""
    m = ma.instrument_meta("GC=F")
    assert m["calendar"] == "COMEX"
    m = ma.instrument_meta("BTC-USD")
    assert m["calendar"] == "24/7"
    assert m["session_mode"] == "rolling24"
    assert ma.instrument_meta("UNKNOWN") == {}


def test_registry_session_calendar_lookup():
    """registry.session_calendar() wires up the 8-instrument calendar map."""
    from gold_desk.markets.registry import session_calendar
    assert session_calendar("GC=F")["calendar"] == "COMEX"
    assert session_calendar("BTC-USD")["session_mode"] == "rolling24"
    assert session_calendar("EURUSD=X")["calendar"] == "24/5"
    assert session_calendar("UNKNOWN") is None


def test_instrument_count_is_eight():
    """The R3 charter mandates exactly 8 instruments."""
    assert len(INSTRUMENTS) == 8
    assert len(INSTRUMENT_ORDER) == 8


def test_asset_snapshot_dataclass_json():
    """AssetSnapshot round-trips through asdict + json.dumps."""
    snap = AssetSnapshot(
        symbol="GC=F", name="Gold", calendar="COMEX",
        price=2050.0, prev_close=2040.0, change_pct=0.49,
        session="ny", session_vwap=2045.0,
        session_relative_pct=0.24, session_open_pct=0.49,
        sparkline=[2040.0, 2050.0], live=True,
        source="yahoo:GC=F", fetched_at=1700000000,
        cache_hit=False, error=None,
        vwap_method="vwap",
    )
    d = json.dumps(snap.__dict__, default=str)
    back = json.loads(d)
    assert back["symbol"] == "GC=F"
    assert back["price"] == 2050.0
    assert back["calendar"] == "COMEX"
    assert back["vwap_method"] == "vwap"
    # default label when not provided (D5)
    default_snap = AssetSnapshot(
        symbol="X", name="X", calendar="X", price=None,
        prev_close=None, change_pct=None, session="off",
        session_vwap=None, session_relative_pct=None,
        session_open_pct=None)
    assert default_snap.vwap_method == "none"


# =========================================================================
# D1-D6 FIX REGRESSION TESTS (GAUNTLET3-R1-FIX)
# =========================================================================
# ------------------------------------------------------ D2: date alignment
def _mixed_calendar_closes(sign: float = 1.0, n_days: int = 120,
                           seed: int = 11) -> dict:
    """Two synthetic assets on DIFFERENT calendars driven by one signal.

    BTC-USD trades every day (7-day calendar); GC=F trades weekdays
    only (5-day calendar). Both assets' same-date returns are driven
    by the SAME gaussian shock (GC's scaled by `sign`), so the TRUE
    same-day correlation is strongly signed. The old position-based
    tail pairing misaligned the tails and destroyed the sign."""
    import random
    from datetime import timedelta
    rng = random.Random(seed)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    days = [start + timedelta(days=i) for i in range(n_days)]
    shock = {d.strftime("%Y-%m-%d"): rng.gauss(0, 0.02) for d in days}
    btc: dict[str, float] = {}
    p = 50000.0
    for d in days:
        ds = d.strftime("%Y-%m-%d")
        p *= math.exp(shock[ds])
        btc[ds] = p
    gc: dict[str, float] = {}
    p = 2000.0
    for d in days:
        if d.weekday() >= 5:      # GC=F: weekdays only
            continue
        ds = d.strftime("%Y-%m-%d")
        p *= math.exp(sign * shock[ds])
        gc[ds] = p
    return {"BTC-USD": btc, "GC=F": gc}


def test_d2_mixed_calendar_positive_sign_preserved():
    """7-day vs 5-day calendar, same-day signal → correlation is
    strongly POSITIVE (D2: position pairing sign-flipped it)."""
    ma._TEST_DAILY_CLOSES = _mixed_calendar_closes(sign=1.0)
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        r = out["matrix"]["BTC-USD"]["GC=F"]
        assert r is not None, "cell must compute (both symbols landed)"
        assert r > 0.3, f"sign flipped / destroyed: r={r}"
        # symmetric cell agrees
        assert out["matrix"]["GC=F"]["BTC-USD"] == r
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_d2_mixed_calendar_negative_sign_preserved():
    """7-day vs 5-day calendar, OPPOSITE same-day signal → correlation
    is strongly NEGATIVE (D2 regression)."""
    ma._TEST_DAILY_CLOSES = _mixed_calendar_closes(sign=-1.0)
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        r = out["matrix"]["BTC-USD"]["GC=F"]
        assert r is not None
        assert r < -0.3, f"sign flipped / destroyed: r={r}"
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_d2_live_shape_btc365_gc260_no_sign_flip():
    """Reproduces the live-case shape: 365 closes for BTC vs ~260 for
    GC=F (COMEX ~5/week), same underlying signal → the matrix must NOT
    sign-flip the flagship gold↔bitcoin cell (critic live-quantified
    true +0.55 vs reported −0.04)."""
    import random
    from datetime import timedelta
    rng = random.Random(7)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    days = [start + timedelta(days=i) for i in range(365)]
    shock = {d.strftime("%Y-%m-%d"): rng.gauss(0, 0.01) for d in days}
    btc: dict[str, float] = {}
    p = 50000.0
    for d in days:
        ds = d.strftime("%Y-%m-%d")
        p *= math.exp(shock[ds])
        btc[ds] = p
    gc: dict[str, float] = {}
    p = 2000.0
    for d in days:
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y-%m-%d")
        p *= math.exp(shock[ds])
        gc[ds] = p
    # live shape: 365 vs ~261 weekday closes
    assert len(btc) == 365
    assert 240 <= len(gc) <= 270
    ma._TEST_DAILY_CLOSES = {"BTC-USD": btc, "GC=F": gc}
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        r = out["matrix"]["BTC-USD"]["GC=F"]
        assert r is not None
        assert r > 0.3, f"sign flipped: r={r} (was +0.55 live)"
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_d2_aligned_log_returns_kernel():
    """_aligned_log_returns pairs by date INTERSECTION: an extra
    weekend close for one asset never shifts the pairing."""
    a = {"2025-01-01": 100.0, "2025-01-02": 110.0,
         "2025-01-03": 120.0, "2025-01-04": 130.0}
    b = {"2025-01-02": 200.0, "2025-01-03": 220.0, "2025-01-04": 240.0}
    ra, rb = ma._aligned_log_returns(a, b)
    # common dates: 01-02, 01-03, 01-04 → 2 paired returns
    assert len(ra) == 2 and len(rb) == 2
    assert abs(ra[0] - math.log(120 / 110)) < 1e-12
    assert abs(rb[0] - math.log(220 / 200)) < 1e-12


# ------------------------------------------------------ D3: error surfacing
def test_d3_fetch_failure_surfaces_errors_and_degraded(monkeypatch):
    """One symbol's daily fetch failing lands in errors[] with reason
    daily_closes_fetch_failed + degraded=True (never silently
    dropped), via a monkeypatched _fetch_daily_one raise (the other
    7 symbols are served from canned data — no network)."""
    canned = _mock_daily_closes(60)
    ma._TEST_DAILY_CLOSES = None
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory())

    def _fetch_one(symbol):
        if symbol == "ES=F":
            raise RuntimeError("HTTPError: 404 Not Found")
        return dict(canned[symbol])
    monkeypatch.setattr(mon, "_fetch_daily_one", _fetch_one)
    out = mon.compute_correlation(window=30, method="pearson")
    assert out["ok"] is True               # documented choice: matrix served
    assert out["degraded"] is True
    err = [e for e in out["errors"]
           if e.get("symbol") == "ES=F"]
    assert err and err[0]["reason"] == "daily_closes_fetch_failed"
    assert "ES=F" not in out["symbols"]   # dropped from the matrix
    assert "GC=F" in out["symbols"]        # the other 7 survive


def test_d3_seam_dropped_symbol_degraded(monkeypatch):
    """Same via the test seam (symbol absent from canned closes)."""
    canned = _mock_daily_closes(60)
    canned.pop("ES=F")
    ma._TEST_DAILY_CLOSES = canned
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        assert out["degraded"] is True
        assert {"symbol": "ES=F",
                "reason": "daily_closes_fetch_failed"} in out["errors"]
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_d3_insufficient_common_dates_returns_none_cell():
    """< window+2 common dates → None cell + insufficient_common_dates
    error entry (D2 rule 3 / D3 surfacing)."""
    canned = _mock_daily_closes(60)
    # CL=F only has 20 dates → 20 < 30+2 → all its cells None
    canned["CL=F"] = dict(list(canned["CL=F"].items())[:20])
    ma._TEST_DAILY_CLOSES = canned
    try:
        mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                                fetcher=_mock_fetch_factory())
        out = mon.compute_correlation(window=30, method="pearson")
        assert out["matrix"]["CL=F"]["GC=F"] is None
        assert out["matrix"]["GC=F"]["CL=F"] is None
        insuff = [e for e in out["errors"]
                  if e.get("reason") == "insufficient_common_dates"]
        assert insuff, "insufficient overlap must be surfaced"
        assert out["degraded"] is True
        # the healthy pairs still compute
        assert out["matrix"]["GC=F"]["ES=F"] is not None
    finally:
        ma._TEST_DAILY_CLOSES = None


def test_d3_cli_renders_na_and_warning(capsys):
    """CLI renders null cells as 'n/a' (never 0.0000) and prints a
    warning line listing dropped symbols (D3)."""
    from gold_desk.cli import cmd_markets_multi_corr
    canned = _mock_daily_closes(60)
    canned.pop("ES=F")                       # fetch-failed symbol
    canned["CL=F"] = dict(list(canned["CL=F"].items())[:20])  # n/a cells
    ma._TEST_DAILY_CLOSES = canned
    try:
        class _Args:
            window = 30
            method = "pearson"
            json = False
            data_root = str(_tmp_data_root())
        rc = cmd_markets_multi_corr(_Args())
        out = capsys.readouterr().out
        assert rc == 0
        assert "n/a" in out                 # null cell rendered, not 0.0000
        assert "0.0000" not in out           # no fake zeros anywhere
        assert "WARNING" in out
        assert "ES=F" in out                 # dropped symbol listed
        assert "CL=F" in out                 # insufficient overlap listed
    finally:
        ma._TEST_DAILY_CLOSES = None


# ------------------------------------------------------ D5: vwap labels
def test_d5_snapshot_vwap_method_labels():
    """Snapshot surfaces vwap_method per asset: normal bars → vwap,
    zero-volume bars → typical_unweighted, 1 bar → single_bar (D5)."""
    def _bars(v: float, n: int = 2) -> list[dict]:
        return [{"ts": 1699900800000 + i * 3600000,
                 "o": 100.0 + i, "h": 101.0 + i,
                 "l": 99.0 + i, "c": 100.5 + i, "v": v}
                for i in range(n)]

    def _fetch(symbols: list[str]) -> dict:
        out = {}
        for s in symbols:
            if s == "GC=F":
                bars = _bars(10)             # normal volume-weighted
            elif s == "ES=F":
                bars = _bars(0)          # zero volume → fallback label
            elif s == "^VIX":
                bars = _bars(5, n=1)     # single bar
            else:
                bars = _bars(10)
            out[s] = {"ok": True, "symbol": s, "price": 101.5,
                      "prev_close": 100.0, "change": 1.5,
                      "change_pct": 1.5, "currency": "USD",
                      "market_time": 1700000000, "bars": bars,
                      "source": f"mock:{s}"}
        return out

    mon = MultiAssetMonitor(data_root=_tmp_data_root(), fetcher=_fetch)
    out = mon.snapshot()
    assert out["assets"]["GC=F"]["vwap_method"] == "vwap"
    assert out["assets"]["ES=F"]["vwap_method"] == "typical_unweighted"
    assert out["assets"]["^VIX"]["vwap_method"] == "single_bar"
    # the 24/7 asset's rolling-24h bucket also labels volume-weighted
    assert out["assets"]["BTC-USD"]["vwap_method"] == "vwap"


def test_d5_snapshot_dead_symbol_vwap_method_none():
    """A failed asset snapshot carries vwap_method "none"."""
    alive = set(INSTRUMENTS.keys()) - {"^VIX"}
    mon = MultiAssetMonitor(data_root=_tmp_data_root(),
                            fetcher=_mock_fetch_factory(alive))
    out = mon.snapshot()
    assert out["assets"]["^VIX"]["vwap_method"] == "none"


# ------------------------------------------------------ D6: kernel precision
def test_d6_correlation_kernel_full_precision():
    """D6: the kernel keeps FULL float precision — no 6dp rounding.

    For a=[1..6], b=[2,1,4,3,6,5] the exact Pearson r is 29/35 =
    0.828571428571… (repeating — not representable at 6dp); a 6dp
    round would leave a ~4.3e-7 residue."""
    a = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    b = [2.0, 1.0, 4.0, 3.0, 6.0, 5.0]
    r = _correlation(a, b, method="pearson")
    expected = 29.0 / 35.0
    assert r is not None
    assert abs(r - expected) < 1e-12, \
        f"kernel rounded: r={r} vs {expected}"
    # clamp still holds
    assert -1.0 <= r <= 1.0
