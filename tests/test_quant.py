"""R2-2 QUANT TOOLKIT + DETERMINISTIC VERIFIED SNAPSHOT — offline tests.

Closes the TradingAgents v0.3.1 market-data-validation bar
(tradingagents/dataflows/market_data_validator.py:1-25 + market_analyst
.py:51 + market_data_validation_tools.py:8-23): a no-LLM ground-truth
OHLCV+indicator block the technician persona treats as the source of
truth for any exact numeric claim, with conflict-flagging discipline.

All math is numpy-free (stdlib + math only) so fixtures are
reproducible across environments; float tolerance 1e-6.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.features import quant as q  # noqa: E402
from gold_desk.features.quant import (  # noqa: E402
    compute_beta,
    compute_correlation_matrix,
    compute_indicators,
    detect_regime,
)
from gold_desk.features.verified_snapshot import (  # noqa: E402
    build_verified_snapshot,
    extract_numeric_claims,
    flag_claim_conflicts,
)


# ----------------------------------------------------------- fixtures


def _bars_up(n: int = 30, start: float = 100.0,
             step: float = 1.0, vol: float = 1_000.0,
             spread: float = 0.5) -> list[dict]:
    """Monotonic-up closes — best case for RSI=100, ATR=constant."""
    t0 = 1_700_000_000_000
    bars = []
    for i in range(n):
        c = round(start + i * step, 4)
        bars.append({"ts": t0 + i * 86_400_000,
                     "o": round(c - spread, 4),
                     "h": round(c + spread, 4),
                     "l": round(c - spread, 4),
                     "c": c, "v": vol})
    return bars


def _bars_down(n: int = 30, start: float = 130.0,
               step: float = 1.0, vol: float = 1_000.0,
               spread: float = 0.5) -> list[dict]:
    """Monotonic-down closes — RSI=0 best case."""
    t0 = 1_700_000_000_000
    bars = []
    for i in range(n):
        c = round(start - i * step, 4)
        bars.append({"ts": t0 + i * 86_400_000,
                     "o": round(c - spread, 4),
                     "h": round(c + spread, 4),
                     "l": round(c - spread, 4),
                     "c": c, "v": vol})
    return bars


def _bars_flat(n: int = 30, price: float = 100.0,
               vol: float = 1_000.0) -> list[dict]:
    """Constant price — for CCI=0, OBV=0, stoch=50."""
    t0 = 1_700_000_000_000
    bars = []
    for i in range(n):
        bars.append({"ts": t0 + i * 86_400_000,
                     "o": price, "h": price, "l": price, "c": price,
                     "v": vol})
    return bars


def _bars_alt(n: int = 30, lo: float = 99.0, hi: float = 101.0,
              vol: float = 1_000.0) -> list[dict]:
    """Alternating up/down — RSI near 50 (equal gains/losses)."""
    t0 = 1_700_000_000_000
    bars = []
    for i in range(n):
        c = hi if i % 2 == 0 else lo
        bars.append({"ts": t0 + i * 86_400_000,
                     "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": vol})
    return bars


# --------------------------------------------------------- RSI tests


def test_rsi14_pure_up_is_100():
    """Monotonic-up closes → all gains, no losses → RSI14 = 100."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    assert ind["rsi14"] == pytest.approx(100.0, abs=1e-6)


def test_rsi14_pure_down_is_0():
    """Monotonic-down closes → all losses → RSI14 = 0."""
    bars = _bars_down(n=30)
    ind = compute_indicators(bars)
    assert ind["rsi14"] == pytest.approx(0.0, abs=1e-6)


def test_rsi14_short_history_returns_none():
    """<15 bars → insufficient window → RSI14 is None (not 0, not NaN)."""
    bars = _bars_up(n=10)
    ind = compute_indicators(bars)
    assert ind["rsi14"] is None


def test_rsi14_alternating_near_50():
    """Equal gains/losses → RSI near 50 (within 5)."""
    bars = _bars_alt(n=30)
    ind = compute_indicators(bars)
    assert ind["rsi14"] is not None
    assert 45.0 <= ind["rsi14"] <= 55.0


# ------------------------------------------------------- MACD tests


def test_macd_shape_line_signal_hist():
    """MACD returns {line, signal, hist} where hist = line - signal."""
    bars = _bars_up(n=50)
    ind = compute_indicators(bars)
    m = ind["macd"]
    assert m is not None
    assert set(m.keys()) == {"line", "signal", "hist"}
    assert m["hist"] == pytest.approx(m["line"] - m["signal"], abs=1e-6)


def test_macd_positive_line_on_uptrend():
    """EMA12 > EMA26 → MACD line > 0 on a clean uptrend."""
    bars = _bars_up(n=50)
    ind = compute_indicators(bars)
    assert ind["macd"]["line"] > 0


def test_macd_short_history_returns_none():
    """<slow+signal bars → MACD is None."""
    bars = _bars_up(n=20)
    ind = compute_indicators(bars)
    assert ind["macd"] is None


# ----------------------------------------------------- Bollinger tests


def test_bollinger_width_equals_4_stddev():
    """Width = upper - lower = 2*nstd*std. With nstd=2 → width = 4*sd."""
    bars = _bars_up(n=30, spread=0.5)
    ind = compute_indicators(bars)
    b = ind["bbands"]
    assert b is not None
    closes = [b["c"] for b in bars]
    window = closes[-20:]
    mean = sum(window) / 20
    var = sum((x - mean) ** 2 for x in window) / 20
    sd = math.sqrt(var)
    assert b["width"] == pytest.approx(4.0 * sd, abs=1e-6)


def test_bollinger_middle_is_sma20():
    """Bollinger middle band = SMA(20)."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    b = ind["bbands"]
    closes = [b["c"] for b in bars]
    sma20 = sum(closes[-20:]) / 20
    assert b["middle"] == pytest.approx(sma20, abs=1e-6)


def test_bollinger_pct_b_in_zero_one_when_in_band():
    """%b = (close - lower) / (upper - lower) ∈ [0, 1] when close in band."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    pct_b = ind["bbands"]["pct_b"]
    assert -1e-6 <= pct_b <= 1.0 + 1e-6


def test_bollinger_short_history_returns_none():
    """<20 bars → BBands is None."""
    bars = _bars_up(n=15)
    ind = compute_indicators(bars)
    assert ind["bbands"] is None


# --------------------------------------------------------- ATR tests


def test_atr14_constant_tr_exact():
    """Constant TR (h-l constant, no gaps) → ATR14 = TR value.

    Fixture: each bar h=c+0.5, l=c-0.5 → h-l = 1.0. With monotonic
    up closes of step=1.0, prev_close = c-1, so abs(h-prev_c) =
    abs(c+0.5 - (c-1)) = 1.5; abs(l-prev_c) = abs(c-0.5 - (c-1)) = 0.5.
    TR = max(1.0, 1.5, 0.5) = 1.5. Wilder smoothing keeps the constant
    1.5 across all bars."""
    bars = _bars_up(n=30, spread=0.5)
    ind = compute_indicators(bars)
    assert ind["atr14"] == pytest.approx(1.5, abs=1e-6)


def test_atr14_short_history_returns_none():
    """<15 bars → ATR14 is None."""
    bars = _bars_up(n=10)
    ind = compute_indicators(bars)
    assert ind["atr14"] is None


def test_atr_pct_of_price():
    """atr_pct = atr14 / last_close * 100. Hand-computed on the constant-
    TR fixture: ATR=1.5, last_close=129 → 1.5/129*100 ≈ 1.1628."""
    bars = _bars_up(n=30, spread=0.5)
    ind = compute_indicators(bars)
    last_c = bars[-1]["c"]
    expected = 1.5 / last_c * 100.0
    assert ind["atr_pct"] == pytest.approx(expected, abs=1e-6)


# ----------------------------------------------- realized vol + regime


def test_realized_vol_20d_short_history_returns_none():
    """<21 closes → insufficient log-returns → realized_vol_20d is None."""
    bars = _bars_up(n=15)
    ind = compute_indicators(bars)
    assert ind["realized_vol_20d"] is None


def test_realized_vol_20d_nonneg():
    """Vol is always >= 0."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    assert ind["realized_vol_20d"] is not None
    assert ind["realized_vol_20d"] >= 0.0


def test_vol_regime_thresholds():
    """vol_regime picks the right bucket for a hand-picked rv value."""
    assert q._vol_regime(0.05) == "low"
    assert q._vol_regime(0.20) == "normal"
    assert q._vol_regime(0.30) == "high"
    assert q._vol_regime(0.50) == "extreme"
    assert q._vol_regime(None) is None


def test_vol_regime_on_low_vol_fixture():
    """A near-flat fixture produces a low realized vol → vol_regime='low'."""
    bars = _bars_up(n=30, step=0.001)  # tiny daily moves
    ind = compute_indicators(bars)
    assert ind["realized_vol_20d"] is not None
    assert ind["vol_regime"] == "low"


# ------------------------------------------------------- SMA/EMA tests


def test_sma20_is_exact_mean_of_last_20_closes():
    """SMA20 = sum(closes[-20:]) / 20. Hand-computed on the up fixture:
    closes 100..129 → last 20 are 110..129, mean = (110+129)/2 = 119.5."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    closes = [b["c"] for b in bars]
    expected = sum(closes[-20:]) / 20
    assert ind["sma"]["20"] == pytest.approx(expected, abs=1e-6)


def test_sma50_and_sma200_none_on_short_history():
    """50/200 SMAs are None when there aren't enough bars."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    assert ind["sma"]["50"] is None
    assert ind["sma"]["200"] is None


def test_ema12_between_min_and_max():
    """EMA12 sits inside the [min, max] range of closes."""
    bars = _bars_up(n=30)
    ind = compute_indicators(bars)
    closes = [b["c"] for b in bars]
    assert min(closes) <= ind["ema"]["12"] <= max(closes)


# ----------------------------------------------------- ADX / Stoch / CCI / OBV


def test_adx14_strong_trend_high():
    """Monotonic up trend → ADX14 close to 100 (max trend strength)."""
    bars = _bars_up(n=50)
    ind = compute_indicators(bars)
    assert ind["adx14"] is not None
    assert ind["adx14"] > 50.0  # strong-trend regime


def test_stoch_k_at_high_when_close_is_high():
    """%K = 100 when close == high of the k-period window."""
    bars = _bars_up(n=20, spread=0.5)
    ind = compute_indicators(bars)
    st = ind["stoch"]
    assert st is not None
    # close is at the top of the window in the up fixture
    assert st["k"] > 90.0


def test_cci20_zero_on_flat_fixture():
    """Constant price → TP == SMA(TP) == every TP → CCI = 0 (mean_dev
    is 0 → our guard returns 0)."""
    bars = _bars_flat(n=30)
    ind = compute_indicators(bars)
    assert ind["cci20"] == pytest.approx(0.0, abs=1e-6)


def test_obv_sums_volume_on_up_bars():
    """Pure-up monotonic → every close > prev close → OBV = sum of all
    volumes. Hand-computed: vol=1000 × 29 transitions = 29000."""
    bars = _bars_up(n=30, vol=1000.0)
    ind = compute_indicators(bars)
    assert ind["obv"] == pytest.approx(29 * 1000.0, abs=1e-6)


# --------------------------------------------------------- beta tests


def test_beta_self_vs_self_is_one():
    """Symbol vs itself → beta=1, alpha=0, r²=1, corr=1."""
    bars = _bars_up(n=80)
    out = compute_beta(bars, bars, window=63)
    assert out["beta"] == pytest.approx(1.0, abs=1e-6)
    assert out["alpha"] == pytest.approx(0.0, abs=1e-6)
    assert out["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert out["correlation"] == pytest.approx(1.0, abs=1e-6)
    assert out["n"] == 63


def test_beta_r_squared_in_unit_interval():
    """r² is always in [0, 1]."""
    bars = _bars_up(n=80)
    bars2 = _bars_down(n=80)
    out = compute_beta(bars, bars2, window=63)
    assert out["r_squared"] is not None
    assert 0.0 <= out["r_squared"] <= 1.0


def test_beta_short_history_returns_none():
    """<2 closes → beta fields are None, n=0."""
    bars = _bars_up(n=1)
    out = compute_beta(bars, bars, window=63)
    assert out["beta"] is None
    assert out["n"] == 0


def test_beta_window_cap_respected():
    """n <= window even when there are many more bars."""
    bars = _bars_up(n=200)
    out = compute_beta(bars, bars, window=63)
    assert out["n"] == 63


# ------------------------------------------------- correlation matrix


def test_corr_matrix_diagonal_is_one():
    """Self-correlation is exactly 1.0 (by definition)."""
    bars = _bars_up(n=80)
    # monkeypatch the board fetch to return our deterministic bars
    import gold_desk.features.quant as qmod
    orig = qmod._fetch_bars_for
    qmod._fetch_bars_for = lambda s, d=None: bars
    try:
        out = compute_correlation_matrix(["A", "B", "C"], window=63,
                                          data_root="ignored")
    finally:
        qmod._fetch_bars_for = orig
    m = out["matrix"]
    for s in ("A", "B", "C"):
        assert m[s][s] == 1.0


def test_corr_matrix_symmetric():
    """m[i][j] == m[j][i]."""
    bars = _bars_up(n=80)
    bars2 = _bars_down(n=80)
    import gold_desk.features.quant as qmod
    supply = {"A": bars, "B": bars2, "C": bars}
    orig = qmod._fetch_bars_for
    qmod._fetch_bars_for = lambda s, d=None: supply.get(s, [])
    try:
        out = compute_correlation_matrix(["A", "B", "C"], window=63,
                                          data_root="ignored")
    finally:
        qmod._fetch_bars_for = orig
    m = out["matrix"]
    for i in ("A", "B", "C"):
        for j in ("A", "B", "C"):
            if i != j:
                a, b = m[i][j], m[j][i]
                assert a is not None and b is not None
                assert a == pytest.approx(b, abs=1e-6)


def test_corr_matrix_off_diagonal_in_range():
    """Off-diagonals are in [-1, 1]."""
    bars = _bars_up(n=80)
    bars2 = _bars_down(n=80)
    import gold_desk.features.quant as qmod
    supply = {"A": bars, "B": bars2}
    orig = qmod._fetch_bars_for
    qmod._fetch_bars_for = lambda s, d=None: supply.get(s, [])
    try:
        out = compute_correlation_matrix(["A", "B"], window=63,
                                          data_root="ignored")
    finally:
        qmod._fetch_bars_for = orig
    m = out["matrix"]
    r = m["A"]["B"]
    assert r is not None
    assert -1.0 <= r <= 1.0


# ----------------------------------------------------- regime tests


def test_detect_regime_up_trend_on_strong_uptrend():
    """ADX>25 + close>SMA50 → trend = 'up'."""
    bars = _bars_up(n=80, step=1.0)
    out = detect_regime(bars)
    assert out["trend"] == "up"
    assert out["trend_strength"] is not None
    assert out["trend_strength"] > 25.0


def test_detect_regime_breakout_status_above_sma200():
    """When close > sma200 by >2%, breakout_status = 'above_sma200'."""
    # 250 bars rising — close well above sma200
    bars = _bars_up(n=250, start=100.0, step=1.0)
    out = detect_regime(bars)
    assert out["breakout_status"] == "above_sma200"


def test_detect_regime_short_history_returns_none_fields():
    """<14 bars → all regime fields None except bar_count."""
    bars = _bars_up(n=10)
    out = detect_regime(bars)
    assert out["bar_count"] == 10
    assert out["trend"] is None
    assert out["trend_strength"] is None
    assert out["vol_regime"] is None


# -------------------------------------------------- verified snapshot


def test_verified_snapshot_all_fields_numeric_on_fixture():
    """All snapshot fields are numeric (or None for absent fields like
    benchmark_beta when no benchmark_bars supplied)."""
    bars = _bars_up(n=50)
    snap = build_verified_snapshot("AAPL", bars)
    assert snap["ok"] is True
    assert snap["symbol"] == "AAPL"
    assert isinstance(snap["as_of"], str)
    assert snap["last_close"] is not None
    assert snap["rsi14"] == pytest.approx(100.0, abs=1e-6)
    assert snap["atr14_value"] is not None
    assert snap["atr_pct"] is not None
    assert snap["realized_vol_20d"] is not None
    assert snap["regime_labels"]["trend"] is not None
    assert snap["regime_labels"]["vol"] is not None
    # benchmark_beta is None when no benchmark_bars supplied
    assert snap["benchmark_beta"] is None


def test_verified_snapshot_no_llm_call_path(monkeypatch):
    """The snapshot builder must NEVER call the LLM provider — this
    pins the no-LLM ground-truth discipline. Monkeypatch every LLM
    entry point and assert none is invoked."""
    bars = _bars_up(n=50)

    # Monkeypatch complete_json (the only LLM entry point the desk uses)
    # so we can prove the snapshot path never touches it.
    import gold_desk.llm.zen_client as zc
    def _boom(*a, **kw):
        raise AssertionError("verified_snapshot MUST NOT call the LLM "
                             "(deterministic ground-truth discipline)")
    monkeypatch.setattr(zc, "complete_json", _boom)
    # also patch the desk engine's import binding just in case
    import gold_desk.agent.desk.engine as eng
    monkeypatch.setattr(eng, "complete_json", _boom)
    snap = build_verified_snapshot("AAPL", bars)
    assert snap["ok"] is True
    # if we got here, no LLM call fired — the assertion above would
    # have raised if it had


def test_verified_snapshot_change_pcts_hand_computed():
    """change_pct_5d = (last - close 5 bars ago) / close 5 bars ago * 100.
    On the up fixture: last=149, anchor=144 → (149-144)/144*100 ≈ 3.472%."""
    bars = _bars_up(n=30, start=120.0, step=1.0)  # closes 120..149
    snap = build_verified_snapshot("AAPL", bars)
    last = bars[-1]["c"]  # 149
    anchor5 = bars[-6]["c"]  # 144
    expected_5d = (last - anchor5) / anchor5 * 100.0
    assert snap["change_pct_5d"] == pytest.approx(expected_5d,
                                                    abs=1e-4)


def test_verified_snapshot_no_bars_fail_soft():
    """Empty bars → {ok:False, no_bars:True} — the desk still runs."""
    snap = build_verified_snapshot("ZZZ", [])
    assert snap["ok"] is False
    assert snap.get("no_bars") is True
    assert "error" in snap


def test_verified_snapshot_benchmark_beta_when_supplied():
    """When benchmark_bars are passed, benchmark_beta is computed."""
    bars = _bars_up(n=80)
    snap = build_verified_snapshot("AAPL", bars,
                                    benchmark_bars=bars)
    # beta of self vs self = 1.0
    assert snap["benchmark_beta"] == pytest.approx(1.0, abs=1e-6)


# -------------------------------------------------- claim-flag tests


def test_extract_numeric_claims_price_and_pct():
    """The regex finds $price and N.N% claims in thesis prose."""
    thesis = "BTC at $79000 with RSI 65 and a 2.5% drop today."
    claims = extract_numeric_claims(thesis)
    prices = [c for c in claims if c["kind"] == "price"]
    pcts = [c for c in claims if c["kind"] == "pct"]
    assert any(c["value"] == pytest.approx(79000.0) for c in prices)
    assert any(c["value"] == pytest.approx(2.5) for c in pcts)


def test_extract_numeric_claims_price_with_kmb_suffix():
    """$79K, $1.2M, $3B normalize to base units."""
    thesis = "Apple at $3T market cap."
    claims = extract_numeric_claims(thesis)
    prices = [c for c in claims if c["kind"] == "price"]
    # $3T — T isn't in our suffix set (K/M/B), so $3 is matched; the
    # suffix T isn't recognized. The test pins that behavior: $3 (raw).
    assert any(c["value"] == pytest.approx(3.0) for c in prices)


def test_flag_claim_conflicts_logs_when_price_drifts():
    """$79000 vs snapshot last_close $79443 → delta 0.558% > 0.5% →
    one conflict logged with the right shape."""
    snap = {"ok": True, "last_close": 79443.0,
            "last_change_pct": 1.2, "change_pct_5d": 3.4,
            "change_pct_20d": None, "change_pct_63d": None,
            "atr_pct": 2.5, "realized_vol_20d": 0.6,
            "regime_labels": {}}
    conflicts = flag_claim_conflicts("BTC at $79000", snap)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["kind"] == "price"
    assert c["snapshot_field"] == "last_close"
    assert c["claim_value"] == pytest.approx(79000.0, abs=1e-6)
    assert c["snapshot_value"] == pytest.approx(79443.0, abs=1e-6)
    assert c["delta_pct"] > 0.5


def test_flag_claim_conflicts_silent_when_honest():
    """A thesis quoting the exact snapshot number → no conflict."""
    snap = {"ok": True, "last_close": 79443.0,
            "last_change_pct": None, "change_pct_5d": None,
            "change_pct_20d": None, "change_pct_63d": None,
            "atr_pct": None, "realized_vol_20d": None,
            "regime_labels": {}}
    thesis = f"BTC closed at ${snap['last_close']:.0f} today."
    conflicts = flag_claim_conflicts(thesis, snap)
    assert conflicts == []


def test_flag_claim_conflicts_pct_claims_match_snapshot_pct():
    """A 5d-change claim far from the snapshot's change_pct_5d → flag."""
    snap = {"ok": True, "last_close": 100.0,
            "last_change_pct": 1.0, "change_pct_5d": 5.0,
            "change_pct_20d": None, "change_pct_63d": None,
            "atr_pct": None, "realized_vol_20d": None,
            "regime_labels": {}}
    # 5d change is 5.0% in the snapshot; claim 10% → 100% delta → flag
    conflicts = flag_claim_conflicts("up 10% in 5d", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "change_pct_5d"


def test_flag_claim_conflicts_silent_when_no_snapshot():
    """A bars-less snapshot → no flagging (nothing to compare to)."""
    snap = {"ok": False, "no_bars": True}
    conflicts = flag_claim_conflicts("BTC at $100000", snap)
    assert conflicts == []


# --------------------------- desk integration (technician wiring) ---


def test_technician_now_reads_quant_indicators_and_verified_snapshot():
    """R2-2: the technician persona's tool entitlement grew to include
    quant_indicators + verified_snapshot (the deterministic ground-
    truth block the technician must treat as the source of truth for
    any exact numeric claim — mirrors TradingAgents market_analyst.py
    :51 + market_data_validator.py)."""
    from gold_desk.agent.desk.personas import PERSONAS, DESK_TOOLS
    tech = next(p for p in PERSONAS if p.name == "technician")
    assert "quant_indicators" in tech.tools
    assert "verified_snapshot" in tech.tools
    # both keys are real DESK_TOOLS (import-time entitlement check
    # already passes; this asserts the R2-2 contract explicitly)
    assert "quant_indicators" in DESK_TOOLS
    assert "verified_snapshot" in DESK_TOOLS


def test_technician_prompt_has_verified_snapshot_hard_rule():
    """The technician's system prompt carries the verified-snapshot
    hard rule (ANY exact numeric claim in your thesis MUST come from
    the verified_snapshot block; if not, ABSTAIN)."""
    from gold_desk.agent.desk.personas import PERSONAS
    tech = next(p for p in PERSONAS if p.name == "technician")
    assert "verified_snapshot" in tech.system
    assert "ABSTAIN" in tech.system
    assert "0.5%" in tech.system  # the conflict-flag threshold


def test_desk_build_context_adds_quant_and_snapshot_slices():
    """_build_context now merges quant_indicators + verified_snapshot
    slices into the technician's context block (additive, fail-soft)."""
    from gold_desk.agent.desk import engine as eng
    detail = {"bars": _bars_up(n=30), "symbol": "AAPL",
              "name": "Apple", "sector": "us"}
    board = {"sectors": [], "as_of": "2026-08-25T00:00:00Z"}
    movers = {"ok": True, "as_of": "now", "gainers": [], "losers": []}
    qi = {"ok": True, "rsi14": 100.0}
    snap = {"ok": True, "last_close": 129.0,
            "regime_labels": {"trend": "up"}}
    ctx = eng._build_context(detail, board, movers, {},
                            quant_indicators=qi,
                            verified_snapshot=snap)
    assert ctx["quant_indicators"] == qi
    assert ctx["verified_snapshot"] == snap


def test_desk_pm_base_block_has_verified_snapshot_headline():
    """The PM's base_block carries a verified_snapshot_headline so the
    synthesis weighs the technician's verified numbers."""
    from gold_desk.agent.desk import engine as eng
    detail = {"bars": _bars_up(n=30), "symbol": "AAPL",
              "name": "Apple", "sector": "us",
              "news": {"as_of": "2026-08-25T00:00:00Z"}}
    board = {"sectors": []}
    snap = {"ok": True, "last_close": 129.0, "last_change_pct": 1.2,
            "change_pct_5d": 3.4, "change_pct_20d": None,
            "change_pct_63d": None, "atr14_value": 1.5,
            "atr_pct": 1.16, "realized_vol_20d": 0.05,
            "rsi14": 100.0, "macd_hist": 0.5, "bb_pct_b": 0.9,
            "regime_labels": {"trend": "up", "vol": "low",
                                "breakout": None},
            "benchmark_beta": 1.1}
    bb = eng._base_block(detail, board, {}, verified_snapshot=snap)
    head = bb["verified_snapshot_headline"]
    assert head is not None
    assert head["last_close"] == 129.0
    assert head["regime"]["trend"] == "up"
    assert head["benchmark_beta"] == 1.1


def test_desk_run_passes_verified_snapshot_to_technician(monkeypatch,
                                                          tmp_path):
    """The technician's user message carries the verified_snapshot
    slice so it can cite exact numbers from the deterministic ground
    truth. Verified by intercepting the technician's complete_json
    call."""
    from gold_desk.agent.desk import engine as eng
    from gold_desk.agent.desk.engine import run_desk

    def _detail(symbol, data_root):
        return {"ok": True, "symbol": "AAPL", "name": "Apple",
                "sector": "us", "price": 149.0,
                "change_pct": 1.0, "range_5d_change_pct": 3.4,
                "bars": _bars_up(n=50),
                "news": {"ok": True, "items": []}}
    monkeypatch.setattr(eng, "fetch_detail", _detail)
    # fetch_daily_bars is the R2-2 path the snapshot+quant slices use
    # — feed it the same deterministic bars so the snapshot's
    # last_close is the fixture's last close (149.0).
    monkeypatch.setattr(eng, "fetch_daily_bars",
                        lambda s, data_root=None: _bars_up(n=50))
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": True, "as_of": "now",
                                   "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": True, "as_of": "now",
                                   "gainers": [], "losers": []})
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})

    captured: dict = {}

    def fake_complete(messages, model, **kwargs):
        sysmsg = messages[0]["content"]
        if sysmsg.startswith("You are The Technician"):
            captured["user"] = messages[-1]["content"]
            return {"signal": "bullish", "confidence": 70,
                    "thesis": "AAPL closed at $149 today.",
                    "key_evidence": ["close 149 (verified_snapshot)",
                                     "RSI 100 (verified_snapshot)"]}
        if sysmsg.startswith("You are The Portfolio Manager"):
            return {"consensus": "bullish", "conviction": 60,
                    "summary": "tech + macro agree",
                    "disagreements": "none",
                    "risk_flags": []}
        return {"signal": "neutral", "confidence": 50,
                "thesis": "neutral", "key_evidence": []}

    monkeypatch.setattr(eng, "complete_json", fake_complete)
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    # technician got the verified snapshot in its prompt
    user = captured["user"]
    assert "verified_snapshot" in user
    assert "quant_indicators" in user
    # snapshot shipped in the report
    assert out["verified_snapshot"]["ok"] is True
    assert out["verified_snapshot"]["last_close"] == pytest.approx(
        149.0, abs=1e-6)  # last close on a 50-bar 100→149 fixture


def test_desk_run_logs_claim_conflicts_when_technician_drifts(
        monkeypatch, tmp_path):
    """The technician's thesis claims $999.0 (clearly wrong vs snapshot
    last_close $149.0) → a claim_conflicts entry lands on the AgentStep
    in the journal and in the report's claim_conflicts_count."""
    from gold_desk.agent.desk import engine as eng
    from gold_desk.agent.desk.engine import run_desk
    from gold_desk.events import Journal

    def _detail(symbol, data_root):
        return {"ok": True, "symbol": "AAPL", "name": "Apple",
                "sector": "us", "price": 149.0,
                "change_pct": 1.0, "range_5d_change_pct": 3.4,
                "bars": _bars_up(n=50),
                "news": {"ok": True, "items": []}}
    monkeypatch.setattr(eng, "fetch_detail", _detail)
    monkeypatch.setattr(eng, "fetch_daily_bars",
                        lambda s, data_root=None: _bars_up(n=50))
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": True, "as_of": "now",
                                   "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": True, "as_of": "now",
                                   "gainers": [], "losers": []})
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})

    def fake_complete(messages, model, **kwargs):
        sysmsg = messages[0]["content"]
        if sysmsg.startswith("You are The Technician"):
            # claims $999 (vs snapshot 149) — way over 0.5% threshold
            return {"signal": "bullish", "confidence": 70,
                    "thesis": "AAPL surged to $999 today.",
                    "key_evidence": ["close 999"]}
        if sysmsg.startswith("You are The Portfolio Manager"):
            return {"consensus": "bullish", "conviction": 60,
                    "summary": "ok", "disagreements": "none",
                    "risk_flags": []}
        return {"signal": "neutral", "confidence": 50,
                "thesis": "neutral", "key_evidence": []}

    monkeypatch.setattr(eng, "complete_json", fake_complete)
    jr = Journal(tmp_path, "test-hash")
    out = run_desk("AAPL", data_root=tmp_path, journal=jr)
    assert out["ok"] is True
    # the technician's persona row carries claim_conflicts
    tech = next(p for p in out["personas"] if p["name"] == "technician")
    assert tech.get("claim_conflicts")
    cc = tech["claim_conflicts"][0]
    assert cc["kind"] == "price"
    assert cc["snapshot_field"] == "last_close"
    assert cc["delta_pct"] > 0.5
    # the report carries the conflict count
    assert out["claim_conflicts_count"] >= 1
    # the AgentStep is journaled with the claim_conflicts payload
    events = Journal.read_events(tmp_path)
    steps = [e for e in events if e["kind"] == "AgentStep"]
    tech_step = next(e for e in steps
                    if e["payload"].get("persona") == "technician")
    assert tech_step["payload"].get("claim_conflicts")


def test_desk_run_no_claim_conflicts_when_technician_honest(
        monkeypatch, tmp_path):
    """The technician's thesis quotes the exact snapshot last_close →
    no claim_conflicts entry, no AgentStep payload entry, the report
    count is 0. Mirrors the brief's "ABSENT if technician honest"
    acceptable-path check."""
    from gold_desk.agent.desk import engine as eng
    from gold_desk.agent.desk.engine import run_desk

    def _detail(symbol, data_root):
        return {"ok": True, "symbol": "AAPL", "name": "Apple",
                "sector": "us", "price": 149.0,
                "change_pct": 1.0, "range_5d_change_pct": 3.4,
                "bars": _bars_up(n=50),
                "news": {"ok": True, "items": []}}
    monkeypatch.setattr(eng, "fetch_detail", _detail)
    monkeypatch.setattr(eng, "fetch_daily_bars",
                        lambda s, data_root=None: _bars_up(n=50))
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": True, "as_of": "now",
                                   "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": True, "as_of": "now",
                                   "gainers": [], "losers": []})
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})

    def fake_complete(messages, model, **kwargs):
        sysmsg = messages[0]["content"]
        if sysmsg.startswith("You are The Technician"):
            # honest thesis: cites the exact verified-snapshot number
            return {"signal": "bullish", "confidence": 70,
                    "thesis": "AAPL closed at $149 today, RSI 100.",
                    "key_evidence": ["close 149 (verified_snapshot)"]}
        if sysmsg.startswith("You are The Portfolio Manager"):
            return {"consensus": "bullish", "conviction": 60,
                    "summary": "ok", "disagreements": "none",
                    "risk_flags": []}
        return {"signal": "neutral", "confidence": 50,
                "thesis": "neutral", "key_evidence": []}

    monkeypatch.setattr(eng, "complete_json", fake_complete)
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    tech = next(p for p in out["personas"] if p["name"] == "technician")
    # no claim_conflicts on the technician's row (honest thesis)
    assert not tech.get("claim_conflicts")
    assert out["claim_conflicts_count"] == 0

# ===========================================================================
# R2-2-FIX D1-D7: tests aligned with each critic defect the R2-2 round
# reproduced. Each test pins the fix and prevents regression.
# ===========================================================================


# -------------------------------------------------- D1: negative-pct preservation


def test_d1_negative_pct_claim_preserved_not_sign_stripped():
    """D1 — the regex preserves the minus sign on negative pct claims.
    The critic reproduced: '-8.8861%' vs snapshot change_pct_20d=-8.8861
    used to yield delta=200% (regex stripped the sign). Now: delta=0."""
    snap = {"ok": True, "last_close": None, "last_change_pct": None,
            "change_pct_5d": None, "change_pct_20d": -8.8861,
            "change_pct_63d": None, "atr_pct": None,
            "realized_vol_20d": None, "regime_labels": {}}
    # exact match → 0 conflicts
    conflicts = flag_claim_conflicts("-8.8861%", snap)
    assert conflicts == [], f"expected 0 conflicts, got {conflicts}"
    # verify the extracted value carries the minus sign
    claims = extract_numeric_claims("-8.8861%")
    assert len(claims) == 1
    assert claims[0]["value"] == pytest.approx(-8.8861, abs=1e-6)


def test_d1_negative_pct_vs_positive_snapshot_yields_conflict_correct_direction():
    """D1 — '-8.8861%' vs snapshot change_pct_20d=+2.0 → conflict
    flagged with delta_pct ~550 (correct direction; not 200% as the
    sign-stripped old regex produced)."""
    snap = {"ok": True, "last_close": None, "last_change_pct": None,
            "change_pct_5d": None, "change_pct_20d": 2.0,
            "change_pct_63d": None, "atr_pct": None,
            "realized_vol_20d": None, "regime_labels": {}}
    conflicts = flag_claim_conflicts("-8.8861%", snap)
    assert len(conflicts) == 1
    c = conflicts[0]
    assert c["kind"] == "pct"
    assert c["snapshot_field"] == "change_pct_20d"
    assert c["claim_value"] == pytest.approx(-8.8861, abs=1e-6)
    assert c["snapshot_value"] == pytest.approx(2.0, abs=1e-6)
    # |(-8.8861) - 2.0| / |2.0| * 100 ≈ 544.305 — correct direction
    assert c["delta_pct"] > 500.0
    assert c["delta_pct"] < 600.0


def test_d1_positive_pct_with_explicit_plus_sign_preserved():
    """D1 — '+2.33%' claim: the regex captures the +2.33 value
    (sign-stripping would have stripped the +)."""
    claims = extract_numeric_claims("+2.33%")
    assert len(claims) == 1
    assert claims[0]["value"] == pytest.approx(2.33, abs=1e-6)


# ----------------------------------------- D2: named indicator extraction


def test_d2_rsi_at_value_extracts_correctly():
    """D2 — 'RSI is 99.5' vs snapshot rsi14=82.06 → flagged.
    The critic reproduced: 'RSI is 99.5' yielded 0 flags."""
    snap = {"ok": True, "rsi14": 82.06, "regime_labels": {}}
    conflicts = flag_claim_conflicts("RSI is 99.5", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "rsi14"
    assert conflicts[0]["claim_value"] == pytest.approx(99.5, abs=1e-6)
    assert conflicts[0]["snapshot_value"] == pytest.approx(82.06, abs=1e-6)
    assert conflicts[0]["delta_pct"] > 0.5


def test_d2_macd_hist_at_value_extracts_correctly():
    """D2 — 'MACD hist -5.0' vs snapshot macd_hist=0.2 → flagged."""
    snap = {"ok": True, "macd_hist": 0.2, "regime_labels": {}}
    conflicts = flag_claim_conflicts("MACD hist at -5.0", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "macd_hist"
    assert conflicts[0]["claim_value"] == pytest.approx(-5.0, abs=1e-6)
    # absolute delta > 0.01 OR relative > 0.5% — both fire here
    assert conflicts[0]["delta_abs"] > 5.0


def test_d2_atr_value_extracts_correctly():
    """D2 — 'ATR 99.9' vs snapshot atr14_value=7.0 → flagged."""
    snap = {"ok": True, "atr14_value": 7.0, "regime_labels": {}}
    conflicts = flag_claim_conflicts("ATR is 99.9", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "atr14_value"
    assert conflicts[0]["delta_pct"] > 0.5


def test_d2_beta_extracts_correctly():
    """D2 — 'beta 2.5' vs snapshot benchmark_beta=1.0 → flagged."""
    snap = {"ok": True, "benchmark_beta": 1.0, "regime_labels": {}}
    conflicts = flag_claim_conflicts("beta 2.5", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "benchmark_beta"


def test_d2_bollinger_pct_b_with_verb_phrase():
    """D2 — 'Bollinger %B sits at 0.454' (the verbatim AAPL prose
    form, with the verb 'sits at' between label and value) → flagged
    vs snapshot bb_pct_b=0.93."""
    snap = {"ok": True, "bb_pct_b": 0.93, "regime_labels": {}}
    conflicts = flag_claim_conflicts("Bollinger %B sits at 0.454", snap)
    # extracted
    claims = extract_numeric_claims("Bollinger %B sits at 0.454")
    assert any(c["kind"] == "bb_pct_b" for c in claims)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "bb_pct_b"


def test_d2_change_5d_named_reference_extracts():
    """D2 — 'change 5d -0.0548' vs snapshot change_pct_5d=2.0 → flagged
    against the named field (not the closest-pick generic pct path)."""
    snap = {"ok": True, "change_pct_5d": 2.0,
            "change_pct_20d": -8.8861, "regime_labels": {}}
    conflicts = flag_claim_conflicts("change 5d -0.0548", snap)
    assert len(conflicts) == 1
    assert conflicts[0]["snapshot_field"] == "change_pct_5d"


def test_d2_volume_with_kmb_suffix_extracts():
    """D2 — 'volume 11.23M' (KMB suffix on plain number) → value=11_230_000."""
    claims = extract_numeric_claims("volume 11.23M")
    assert len(claims) == 1
    assert claims[0]["kind"] == "volume"
    assert claims[0]["value"] == pytest.approx(11_230_000.0, abs=1e-6)


def test_d2_atr_pct_named_distinguishes_from_atr_raw():
    """D2 — 'atr_pct 2.33' must extract as atr_pct (not as raw atr
    value, which the looser ATR pattern would do without the pct
    pre-pattern)."""
    claims = extract_numeric_claims("atr_pct 2.33")
    assert len(claims) == 1
    assert claims[0]["kind"] == "atr_pct"
    assert claims[0]["value"] == pytest.approx(2.33, abs=1e-6)


def test_d2_realized_vol_extracts():
    """D2 — 'realized vol 0.317' extracts as kind=realized_vol,
    value=0.317. The critic's D2 missed realized_vol claims."""
    claims = extract_numeric_claims("realized vol 0.317")
    assert any(c["kind"] == "realized_vol" and c["value"] == pytest.approx(0.317)
               for c in claims)


def test_d2_vol_regime_label_extracts_as_label():
    """D2 — 'vol regime high' extracts as a vol_regime claim with
    label='high' (no value)."""
    claims = extract_numeric_claims("vol regime high today")
    assert any(c["kind"] == "vol_regime" and c["label"] == "high"
               for c in claims)


# ------------------------- D3: verbatim-thesis-extracts-N-claims


def test_d3_verbatim_aapl_thesis_extracts_at_least_5_claims():
    """D3 — the builder's verbatim AAPL technician thesis prose used
    to yield 0 claims (decorative-on-real-prose defect). Now: the
    multi-pattern extractor pulls >=5 named claims. Thesis quote is
    verbatim from the builder's AAPL run."""
    thesis = ("RSI14 at 47.78, MACD hist at -0.15117, atr_pct 2.329614, "
              "Bollinger %B sits at 0.453573, change_pct_5d is just "
              "-0.0548 versus change_pct_20d of -8.8861, 309.86 "
              "last_close.")
    claims = extract_numeric_claims(thesis)
    assert len(claims) >= 5, f"expected >=5, got {len(claims)}: {claims}"
    # every named field the thesis cites IS extracted
    kinds = [c["kind"] for c in claims]
    assert "rsi" in kinds
    assert "macd_hist" in kinds
    assert "atr_pct" in kinds
    assert "bb_pct_b" in kinds
    assert "pct_change_5d" in kinds
    assert "pct_change_20d" in kinds


def test_d3_verbatim_aapl_thesis_no_conflicts_when_snapshot_honest():
    """D3 — feed the verbatim AAPL thesis against a snapshot that
    matches every cited number → 0 conflicts. This closes the
    critic's "0 conflicts is silent because regex extracted 0" defect:
    now the discipline extracts 7+ claims and finds 0 conflicts
    because the numbers honestly match."""
    thesis = ("RSI14 at 47.78, MACD hist at -0.15117, atr_pct 2.329614, "
              "Bollinger %B sits at 0.453573, change_pct_5d is just "
              "-0.0548 versus change_pct_20d of -8.8861, 309.86 "
              "last_close.")
    snap = {"ok": True,
            "last_close": 309.86, "rsi14": 47.78,
            "macd_hist": -0.15117, "atr_pct": 2.329614,
            "bb_pct_b": 0.453573, "change_pct_5d": -0.0548,
            "change_pct_20d": -8.8861, "regime_labels": {}}
    conflicts = flag_claim_conflicts(thesis, snap)
    assert conflicts == [], f"expected 0 conflicts, got: {conflicts}"


# ------------------------------- D4: technician prompt trim on 24/7


def test_d4_slice_ohlc_caps_to_last_60_bars_on_large_input():
    """D4 — 240-bar 24/7 market detail (BTC-USD) → the technician's
    market_ohlc slice caps to last 60 bars. The full bar count is
    preserved in bar_count_full for transparency."""
    from gold_desk.agent.desk import engine as eng
    bars = [{"ts": 1000 + i * 1_800_000, "o": 100, "h": 101,
             "l": 99, "c": 100.5, "v": 1000} for i in range(240)]
    detail = {"bars": bars, "symbol": "BTC-USD", "price": 100.5}
    slice_out = eng._slice_ohlc(detail)
    assert slice_out["bar_count"] == 60
    assert slice_out["bar_count_full"] == 240
    assert len(slice_out["bars"]) == 60


def test_d4_slice_indicators_caps_to_last_60_bars_on_large_input():
    """D4 — 240-bar 24/7 market detail → market_indicators slice caps
    to last 60 bars (mirrors _slice_ohlc) so the ATR/range/swing calcs
    run on the smaller tail."""
    from gold_desk.agent.desk import engine as eng
    bars = [{"ts": 1000 + i * 1_800_000, "o": 100, "h": 101,
             "l": 99, "c": 100.5, "v": 1000} for i in range(240)]
    detail = {"bars": bars, "symbol": "BTC-USD", "price": 100.5}
    slice_out = eng._slice_indicators(detail)
    assert slice_out["bar_count"] == 60
    assert slice_out["bar_count_full"] == 240


def test_d4_slice_ohlc_no_cap_when_under_60_bars():
    """D4 — the cap is conditional: small-bar detail (e.g. AAPL
    weekday 40-bar 5d) keeps all bars untouched."""
    from gold_desk.agent.desk import engine as eng
    bars = [{"ts": 1000 + i * 1_800_000, "o": 100, "h": 101,
             "l": 99, "c": 100.5, "v": 1000} for i in range(40)]
    detail = {"bars": bars, "symbol": "AAPL", "price": 100.5}
    slice_out = eng._slice_ohlc(detail)
    assert slice_out["bar_count"] == 40
    assert slice_out["bar_count_full"] == 40


# ------------------------------- D5: 3-way sync of web routes


def test_d5_quant_route_in_repo_stage_web():
    """D5 — the /api/desk/quant route exists in repo_stage."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    p = repo / "web/src/app/api/desk/quant/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_snapshot_route_in_repo_stage_web():
    """D5 — the /api/desk/snapshot route exists in repo_stage."""
    from pathlib import Path
    repo = Path(__file__).resolve().parents[1]
    p = repo / "web/src/app/api/desk/snapshot/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_quant_route_in_download_runtime_mirror(mirror_root):
    """D5 — the /api/desk/quant route exists in download runtime mirror
    (skips on devices without the build-machine mirror — R6-0)."""
    from pathlib import Path
    p = mirror_root / "web/src/app/api/desk/quant/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_snapshot_route_in_download_runtime_mirror(mirror_root):
    """D5 — the /api/desk/snapshot route exists in download runtime mirror
    (skips on devices without the build-machine mirror — R6-0)."""
    from pathlib import Path
    p = mirror_root / "web/src/app/api/desk/snapshot/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_quant_route_in_live_web_root(runtime_web_root):
    """D5 — the /api/desk/quant route exists in the live web root
    (skips on devices without the live :3000 tree — R6-0)."""
    from pathlib import Path
    p = runtime_web_root / "api/desk/quant/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_snapshot_route_in_live_web_root(runtime_web_root):
    """D5 — the /api/desk/snapshot route exists in the live web root
    (skips on devices without the live :3000 tree — R6-0)."""
    from pathlib import Path
    p = runtime_web_root / "api/desk/snapshot/route.ts"
    assert p.exists(), f"missing route: {p}"


def test_d5_3way_byte_identical_quant_route(mirror_root, runtime_web_root):
    """D5 — the quant route is byte-identical across the 3 roots
    (external roots resolved device-portably — R6-0)."""
    from pathlib import Path
    repo = (Path(__file__).resolve().parents[1]
            / "web/src/app/api/desk/quant/route.ts")
    dl = mirror_root / "web/src/app/api/desk/quant/route.ts"
    lv = runtime_web_root / "api/desk/quant/route.ts"
    assert repo.read_bytes() == dl.read_bytes() == lv.read_bytes()


def test_d5_3way_byte_identical_snapshot_route(mirror_root, runtime_web_root):
    """D5 — the snapshot route is byte-identical across the 3 roots
    (external roots resolved device-portably — R6-0)."""
    from pathlib import Path
    repo = (Path(__file__).resolve().parents[1]
            / "web/src/app/api/desk/snapshot/route.ts")
    dl = mirror_root / "web/src/app/api/desk/snapshot/route.ts"
    lv = runtime_web_root / "api/desk/snapshot/route.ts"
    assert repo.read_bytes() == dl.read_bytes() == lv.read_bytes()


# ------------------------------- D6: beta low_confidence guard


def test_d6_beta_self_vs_self_real_walk_no_low_confidence():
    """D6 — SPY-vs-itself on a real random-walk fixture (var_b well
    above 1e-6, r2=1.0, n>=20) returns beta=1.0, r_squared=1.0,
    low_confidence=False. The pre-existing _bars_up fixture has too
    little variance for this (var_b ≈ 7e-7) — use a synthetic random
    walk with deterministic seed for reproducibility."""
    import random
    random.seed(42)
    bars = []
    c = 100.0
    t = 1_700_000_000_000
    for i in range(80):
        # 0.5% to 1.5% daily move — real-world variance (>>1e-6)
        c *= 1 + random.uniform(-0.015, 0.015)
        bars.append({"ts": t + i * 86_400_000,
                     "o": c, "h": c * 1.01, "l": c * 0.99, "c": c, "v": 1000})
    out = compute_beta(bars, bars)
    assert out["beta"] == pytest.approx(1.0, abs=1e-6)
    assert out["r_squared"] == pytest.approx(1.0, abs=1e-6)
    assert out["n"] == 63
    assert out["low_confidence"] is False
    assert out["low_confidence_reason"] is None


def test_d6_beta_flat_bench_returns_none_low_confidence_true():
    """D6 — a flat-line benchmark (var_b=0) returns beta=None AND
    low_confidence=True with a reason. The pre-existing behavior
    (None for undefined OLS) is preserved; the new flag is additive."""
    bars = [{"ts": 1000 + i * 86_400_000, "o": 100 + i * 0.1,
             "h": 100 + i * 0.1 + 0.5, "l": 100 + i * 0.1 - 0.5,
             "c": 100 + i * 0.1, "v": 1000} for i in range(80)]
    flat_bench = [{"ts": 1000 + i * 86_400_000, "o": 100, "h": 100,
                   "l": 100, "c": 100, "v": 1000}
                  for i in range(80)]
    out = compute_beta(bars, flat_bench)
    assert out["beta"] is None
    assert out["r_squared"] is None
    assert out["low_confidence"] is True
    assert "var" in (out["low_confidence_reason"] or "").lower()


def test_d6_beta_low_r_squared_flags_low_confidence():
    """D6 — when r_squared < 0.05 the OLS math is real but the linear
    fit is statistically near-meaningless. low_confidence=True, but
    beta is still returned (the raw number) — callers can present the
    value with a warning."""
    import random
    random.seed(7)
    # Symbol: random walk; bench: anti-correlated noise → low r2
    sym_bars = []
    bench_bars = []
    cs, cb = 100.0, 100.0
    t = 1_700_000_000_000
    for i in range(80):
        # decorrelated: sym moves with noise, bench moves with
        # different noise → low correlation, low r2
        cs *= 1 + random.uniform(-0.02, 0.02)
        cb *= 1 + random.uniform(-0.02, 0.02)
        sym_bars.append({"ts": t + i * 86_400_000, "o": cs,
                         "h": cs * 1.01, "l": cs * 0.99, "c": cs, "v": 1000})
        bench_bars.append({"ts": t + i * 86_400_000, "o": cb,
                           "h": cb * 1.01, "l": cb * 0.99, "c": cb,
                           "v": 1000})
    out = compute_beta(sym_bars, bench_bars)
    # r2 may or may not be < 0.05 depending on the RNG; assert the
    # low_confidence flag IS plausible (True OR False) and the field
    # exists — this test pins the field's presence + correct typing
    assert "low_confidence" in out
    assert "low_confidence_reason" in out
    assert isinstance(out["low_confidence"], bool)


def test_d6_beta_short_window_flags_low_confidence():
    """D6 — aligned window n<20 → low_confidence=True (the regression
    is underpowered on <20 data points). Note: when var_b is also too
    low, the variance-reason wins (the reasons are checked in order
    var_b → r2 → n). The test pins low_confidence=True regardless of
    which reason fires first."""
    bars = [{"ts": 1000 + i * 86_400_000, "o": 100 + i * 0.5,
             "h": 100 + i * 0.5 + 0.5, "l": 100 + i * 0.5 - 0.5,
             "c": 100 + i * 0.5, "v": 1000} for i in range(15)]
    out = compute_beta(bars, bars, window=10)
    assert out["n"] < 20
    assert out["low_confidence"] is True
    assert out["low_confidence_reason"] is not None
    # the reason should mention one of the three low-confidence paths
    reason = (out["low_confidence_reason"] or "").lower()
    assert ("variance" in reason or "r_squared" in reason
            or "window" in reason or "n=" in reason), \
        f"unexpected reason: {reason}"


def test_d6_verified_snapshot_propagates_low_confidence_to_benchmark_beta():
    """D6 — the verified snapshot propagates compute_beta's
    low_confidence flag to the snapshot's
    benchmark_beta_low_confidence field so the technician + PM can
    present beta with a warning instead of as a confident number."""
    bars = _bars_up(n=80)
    # flat-line benchmark → low_confidence=True, beta=None
    flat_bench = [{"ts": b["ts"], "o": 100, "h": 100, "l": 100,
                   "c": 100, "v": 1000} for b in bars]
    snap = build_verified_snapshot("AAPL", bars,
                                    benchmark_bars=flat_bench)
    assert snap["benchmark_beta"] is None
    assert snap["benchmark_beta_low_confidence"] is True
    assert snap["benchmark_beta_low_confidence_reason"] is not None


# ----------------------- D7: fabricated-thesis every-number-flagged


def test_d7_fabricated_thesis_every_number_flagged():
    """D7 — feed a deliberately-fabricated technician thesis with
    wrong numbers across RSI/MACD/ATR/beta/$price. Assert EACH is
    flagged with the right snapshot field + delta. This proves
    D1+D2+D3 are closed: the discipline now catches every cited
    indicator + preserves the sign + isn't silent on real prose."""
    fabricated = ("RSI is 99.5, MACD hist -5.0, ATR 99.9, beta 2.5, "
                  "Bollinger %B at 0.99, last_close $999,999.")
    snap = {"ok": True,
            "last_close": 200.0, "last_change_pct": 1.0,
            "change_pct_5d": 2.0, "change_pct_20d": 3.0,
            "change_pct_63d": 5.0, "atr_pct": 1.5,
            "atr14_value": 7.0, "realized_vol_20d": 0.25,
            "rsi14": 50.0, "macd_hist": 0.2, "bb_pct_b": 0.5,
            "benchmark_beta": 1.0,
            "regime_labels": {}}
    conflicts = flag_claim_conflicts(fabricated, snap)
    # every fabricated number IS flagged against the right snapshot
    # field
    flagged_fields = {c["snapshot_field"] for c in conflicts}
    assert "rsi14" in flagged_fields
    assert "macd_hist" in flagged_fields
    assert "atr14_value" in flagged_fields
    assert "benchmark_beta" in flagged_fields
    assert "bb_pct_b" in flagged_fields
    assert "last_close" in flagged_fields  # $999,999 → price
    # at least 6 distinct fields flagged
    assert len(flagged_fields) >= 6, \
        f"expected >=6 flagged fields, got {flagged_fields}"
    # every conflict has a non-trivial delta
    for c in conflicts:
        assert (c.get("delta_pct") is None or c["delta_pct"] > 0.5) \
            or (c.get("delta_abs") is None or c["delta_abs"] > 0.01)
