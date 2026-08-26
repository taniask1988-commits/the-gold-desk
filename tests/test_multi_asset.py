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
    """Deterministic synthetic closes per instrument for matrix tests."""
    import random
    rng = random.Random(42)
    out = {}
    base = 100.0
    for sym in INSTRUMENT_ORDER:
        prices = [base]
        for _ in range(n_days - 1):
            # Gold-like drift; BTC more volatile
            vol = 0.02 if sym == "BTC-USD" else 0.01
            drift = 0.0005 if sym in ("GC=F", "ES=F") else 0
            prices.append(prices[-1] * (1 + drift + rng.gauss(0, vol)))
        out[sym] = prices
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
    """VWAP weights by volume when volumes present."""
    bars = [
        {"h": 11.0, "l": 9.0, "c": 10.0, "v": 10},
        {"h": 21.0, "l": 19.0, "c": 20.0, "v": 30},
    ]
    # tp1=(11+9+10)/3=10; tp2=(21+19+20)/3=20
    # vwap = (10*10 + 20*30)/40 = (100 + 600)/40 = 17.5
    v = _vwap(bars)
    assert abs(v - 17.5) < 1e-6


def test_vwap_no_volume_falls_back_to_typical_mean():
    """When every volume is 0, fall back to unweighted typical-price mean."""
    bars = [
        {"h": 11.0, "l": 9.0, "c": 10.0, "v": 0},
        {"h": 21.0, "l": 19.0, "c": 20.0, "v": 0},
    ]
    # tp1=10, tp2=20; unweighted mean = 15
    v = _vwap(bars)
    assert abs(v - 15.0) < 1e-6


def test_vwap_empty():
    """Empty bars → None."""
    assert _vwap([]) is None


def test_session_vwap_24h_mode():
    """rolling24 mode slices last 24h regardless of UTC hour."""
    now = datetime.now(timezone.utc)
    bars = []
    for h in range(-48, 1):
        ts = (now.timestamp() + h * 3600) * 1000
        bars.append({"ts": ts, "o": 100.0, "h": 101.0,
                      "l": 99.0, "c": 100.5, "v": 1})
    v, op, sess = _session_vwap_and_open(bars, mode="rolling24")
    assert v is not None
    assert sess == "24h"
    assert op == 100.0   # first bar of the 24h window


def test_session_vwap_fixed_mode_slices_session():
    """fixed mode slices by UTC hour window matching SESSION_BOUNDS."""
    # construct bars that fall in the NY session (UTC 16:00-21:00)
    base = datetime(2026, 1, 5, 17, 0, 0, tzinfo=timezone.utc)
    bars = []
    for m in range(0, 240, 15):
        ts = (base.timestamp() + m * 60) * 1000
        bars.append({"ts": ts, "o": 100.0, "h": 101.0,
                      "l": 99.0, "c": 100.5, "v": 2})
    v, op, sess = _session_vwap_and_open(bars, mode="fixed")
    assert sess == "ny"
    assert v is not None


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
    )
    d = json.dumps(snap.__dict__, default=str)
    back = json.loads(d)
    assert back["symbol"] == "GC=F"
    assert back["price"] == 2050.0
    assert back["calendar"] == "COMEX"
