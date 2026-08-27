"""R3-2 Build 4 — tests for risk/metrics.py (VaR / ES / beta / stress /
ratios / portfolio blending).

Reference checks:
* parametric VaR vs scipy.stats.norm.ppf (1e-6) — scipy is a TEST-ONLY
  reference here; production code is stdlib-only.
* historical VaR + MC VaR quantiles vs numpy.percentile (linear method).
* ES hand-computed on a fixed 10-point series.
* beta exact on a synthetic p = alpha + k·b series.
* stress: 50% SPY + 50% CASH under GFC = −19.25% (charter number).
* Sharpe/Sortino/MaxDD/Calmar recomputed inline from first principles.
"""
from __future__ import annotations

import math
import random

import pytest

from gold_desk.risk import metrics as rm


def _ref():
    """scipy + numpy TEST-ONLY references, imported lazily PER TEST so the
    ~25 stdlib-pure tests in this file still run when scipy/numpy are
    absent (R3-2 critic hygiene fix: the module-level importorskip used to
    collapse the whole file to 1 skip in stdlib-only deploys)."""
    scipy_stats = pytest.importorskip("scipy.stats", reason="scipy reference optional")
    np_ref = pytest.importorskip("numpy", reason="numpy reference optional")
    return scipy_stats, np_ref

# fixed 10-point series (the same one the CLI smoke probe uses)
R10 = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012, 0.008, -0.014]
# seeded pseudo-random 250-point series (deterministic across runs)
_rng = random.Random(20260601)
R250 = [_rng.gauss(0.0004, 0.012) for _ in range(250)]


# ---------------------------------------------------------- basic stats
def test_mean_and_sample_stdev_match_scipy():
    _scipy, np = _ref()
    assert rm.mean(R10) == pytest.approx(float(np.mean(R10)), abs=1e-15)
    assert rm.sample_stdev(R10) == pytest.approx(
        float(np.std(R10, ddof=1)), abs=1e-15)


def test_sample_stdev_degenerate():
    assert rm.sample_stdev([]) == 0.0
    assert rm.sample_stdev([0.01]) == 0.0


def test_percentile_matches_numpy_linear():
    _scipy, np = _ref()
    for q in (1, 5, 25, 50, 95, 99):
        assert rm.percentile(R10, q) == pytest.approx(
            float(np.percentile(R10, q)), abs=1e-15), q
        assert rm.percentile(R250, q) == pytest.approx(
            float(np.percentile(R250, q)), abs=1e-15), q


def test_percentile_edge_cases():
    assert rm.percentile([], 50) is None
    assert rm.percentile([0.03], 5) == 0.03
    assert rm.percentile([0.03], 95) == 0.03


# ---------------------------------------------------------- VaR
@pytest.mark.parametrize("conf", [0.95, 0.99])
def test_var_parametric_matches_scipy_ppf(conf):
    """mean − z·σ == scipy.stats.norm.ppf(1−conf, mean, σ_ddof1) to 1e-6."""
    scipy, np = _ref()
    for series in (R10, R250):
        expected = float(scipy.norm.ppf(
            1.0 - conf, loc=float(np.mean(series)),
            scale=float(np.std(series, ddof=1))))
        assert rm.var_parametric(series, conf) == pytest.approx(
            expected, abs=1e-6)


def test_var_parametric_signs_and_degenerate():
    # σ > 0 and mean ≈ 0 → VaR negative (a loss quantile) at both levels;
    # higher confidence → further into the loss tail (more negative)
    assert rm.var_parametric(R10, 0.95) < 0
    assert rm.var_parametric(R10, 0.99) < rm.var_parametric(R10, 0.95)
    assert rm.var_parametric([0.01], 0.95) is None      # n < 2
    with pytest.raises(ValueError):
        rm.var_parametric(R10, 0.975)                  # unsupported conf


@pytest.mark.parametrize("conf", [0.95, 0.99])
def test_var_historical_matches_numpy_percentile(conf):
    _scipy, np = _ref()
    expected = float(np.percentile(R250, (1.0 - conf) * 100.0))
    assert rm.var_historical(R250, conf) == pytest.approx(expected, abs=1e-15)
    assert rm.var_historical([], 0.95) is None


def test_var_historical_hand_computed_ten_point():
    """rank = (10−1)·0.05 = 0.45 → −0.02·0.55 + (−0.014)·0.45 = −0.0173."""
    assert rm.var_historical(R10, 0.95) == pytest.approx(-0.0173, abs=1e-12)


def test_var_monte_carlo_deterministic():
    a = rm.var_monte_carlo(R250, 0.95)
    b = rm.var_monte_carlo(R250, 0.95)
    assert a == b                                    # bit-for-bit
    c = rm.var_monte_carlo(R250, 0.95, seed=99)
    assert a != c                                    # seed is load-bearing
    # path lists are reproducible too
    assert rm.monte_carlo_returns(R10) == rm.monte_carlo_returns(R10)


def test_var_monte_carlo_matches_numpy_quantile_of_paths():
    _scipy, np = _ref()
    paths = rm.monte_carlo_returns(R250, n_paths=1000, seed=42)
    assert len(paths) == 1000
    expected = float(np.percentile(paths, 5.0))
    assert rm.var_monte_carlo(R250, 0.95, n_paths=1000, seed=42) == (
        pytest.approx(expected, abs=1e-15))


def test_monte_carlo_distribution_shape():
    """GBM paths: mean ± 2σ of the SIMULATED paths must contain ~95%
    (empirical 68/95 rule on the simulated sample itself)."""
    _scipy, np = _ref()
    paths = rm.monte_carlo_returns(R250, n_paths=2000, seed=7)
    mu = sum(paths) / len(paths)
    sd = math.sqrt(sum((p - mu) ** 2 for p in paths) / (len(paths) - 1))
    inside = sum(1 for p in paths if mu - 2 * sd <= p <= mu + 2 * sd)
    frac = inside / len(paths)
    assert 0.93 <= frac <= 0.97, frac
    # the simulation reproduces the input moments (log-space GBM)
    assert mu == pytest.approx(float(np.mean(R250)), abs=3e-4)


def test_monte_carlo_no_loss_of_principal_guard():
    """A −100% single-period return is excluded from log-space; sim values
    stay > −1 (exp()−1 floor)."""
    bad = [0.01, -1.0, -0.5, 0.02]                  # contains −100%
    paths = rm.monte_carlo_returns(bad, n_paths=200, seed=3)
    assert all(p > -1.0 for p in paths)


# ---------------------------------------------------------- ES
def test_expected_shortfall_hand_computed():
    """95% hist VaR = −0.0173 → tail = {−0.02} → ES = −0.02."""
    v = rm.var_historical(R10, 0.95)
    assert v == pytest.approx(-0.0173, abs=1e-12)
    es = rm.expected_shortfall(R10, 0.95)
    assert es == pytest.approx(-0.02, abs=1e-12)


def test_expected_shortfall_beyond_var():
    """ES is at least as pessimistic as VaR on the same tail."""
    for conf in (0.95, 0.99):
        v = rm.var_historical(R250, conf)
        e = rm.expected_shortfall(R250, conf)
        assert e <= v + 1e-15
    assert rm.expected_shortfall([], 0.95) is None


def test_expected_shortfall_injected_parametric_var():
    """Pass a parametric VaR below the sample min → degenerate tail = VaR."""
    v = rm.var_parametric(R10, 0.99)                 # −0.0293 < min(−0.02)
    assert v < min(R10)
    assert rm.expected_shortfall(R10, 0.99, var_value=v) == pytest.approx(v)


def test_es_matches_scipy_tail_mean_on_random_series():
    v = rm.var_historical(R250, 0.95)
    tail = [r for r in R250 if r <= v]
    assert rm.expected_shortfall(R250, 0.95) == pytest.approx(
        sum(tail) / len(tail), abs=1e-15)
    assert len(tail) >= 1


# ---------------------------------------------------------- beta
def test_beta_exact_known_portfolio():
    """p = 0.001 + 1.5·b → beta 1.5, alpha 0.001, r² 1."""
    rng = random.Random(5)
    b = [rng.gauss(0.0, 0.01) for _ in range(200)]
    p = [0.001 + 1.5 * x for x in b]
    out = rm.beta_vs_benchmark(p, b)
    assert out["beta"] == pytest.approx(1.5, abs=1e-12)
    assert out["alpha"] == pytest.approx(0.001, abs=1e-12)
    assert out["correlation"] == pytest.approx(1.0, abs=1e-12)
    assert out["r_squared"] == pytest.approx(1.0, abs=1e-12)
    assert out["n"] == 200


def test_beta_negative_and_zero_variance():
    rng = random.Random(6)
    b = [rng.gauss(0.0, 0.01) for _ in range(100)]
    p = [-2.0 * x for x in b]                        # perfect inverse
    out = rm.beta_vs_benchmark(p, b)
    assert out["beta"] == pytest.approx(-2.0, abs=1e-12)
    assert out["correlation"] == pytest.approx(-1.0, abs=1e-12)
    flat = rm.beta_vs_benchmark([0.01, 0.02, 0.03], [0.0, 0.0, 0.0])
    assert flat["beta"] is None                      # degenerate benchmark
    tiny = rm.beta_vs_benchmark([0.01], [0.01])
    assert tiny["beta"] is None and tiny["n"] == 1


def test_beta_tail_alignment():
    """Longer portfolio series is tail-aligned to the benchmark."""
    b = [0.01, 0.02] * 25                            # varying benchmark
    p = [9.99] * 30 + list(b)                        # junk prefix dropped
    out = rm.beta_vs_benchmark(p, b)
    assert out["n"] == 50
    assert out["beta"] == pytest.approx(1.0, abs=1e-12)
    assert out["alpha"] == pytest.approx(0.0, abs=1e-12)


# ---------------------------------------------------------- stress
def test_stress_gfc_half_spy_half_cash():
    """Charter number: 50% SPY + 50% CASH under 2008 GFC → −19.25%."""
    out = rm.stress_test([{"symbol": "SPY", "weight": 0.5},
                          {"symbol": "CASH", "weight": 0.5}])
    shocks = out["portfolio_shocks"]
    assert shocks["gfc_2008"] == pytest.approx(-0.1925, abs=1e-12)
    assert shocks["covid_2020"] == pytest.approx(-0.1695, abs=1e-12)
    assert shocks["rate_shock_2022"] == pytest.approx(-0.097, abs=1e-12)


def test_stress_scenario_metadata_and_unshocked():
    """R3-3 gap-fix: gold is now SHOCKED in all 3 scenarios (was unshocked
    pre-fix) — a gold-only book reads −20%/−12%/−5%, and `unshocked` only
    lists genuinely-unmodeled assets (CASH)."""
    out = rm.stress_test([{"symbol": "GC=F", "weight": 1.0}])
    shocks = out["portfolio_shocks"]
    assert shocks["gfc_2008"] == pytest.approx(-0.20, abs=1e-12)
    assert shocks["covid_2020"] == pytest.approx(-0.12, abs=1e-12)
    assert shocks["rate_shock_2022"] == pytest.approx(-0.05, abs=1e-12)
    gfc = {s["name"]: s for s in out["scenarios"]}["gfc_2008"]
    assert gfc["label"] == "2008 Global Financial Crisis"
    assert gfc["shocked"] == ["GC=F"]
    assert gfc["unshocked"] == []                  # gold modeled everywhere
    rate = {s["name"]: s for s in out["scenarios"]}["rate_shock_2022"]
    assert rate["yield_change_pp"] == 2.36
    cash = rm.stress_test([{"symbol": "CASH", "weight": 1.0}])
    assert cash["portfolio_shocks"]["gfc_2008"] == 0.0
    assert cash["scenarios"][0]["unshocked"] == ["CASH"]   # truly unmodeled


def test_stress_gold_btc_positions_shocked():
    """R3-3 gap-fix headline: the default book's gold+BTC legs are shocked.
    30% GC=F + 15% BTC-USD hand math per scenario."""
    out = rm.stress_test([{"symbol": "GC=F", "weight": 0.30},
                          {"symbol": "BTC-USD", "weight": 0.15}])
    s = out["portfolio_shocks"]
    # GFC: 0.30×(−0.20) + 0.15×(−0.45) = −0.1275
    assert s["gfc_2008"] == pytest.approx(-0.1275, abs=1e-12)
    # COVID: 0.30×(−0.12) + 0.15×(−0.50) = −0.111
    assert s["covid_2020"] == pytest.approx(-0.111, abs=1e-12)
    # 2022: 0.30×(−0.05) + 0.15×(−0.65) = −0.1125
    assert s["rate_shock_2022"] == pytest.approx(-0.1125, abs=1e-12)
    for scen in out["scenarios"]:
        assert set(scen["shocked"]) == {"GC=F", "BTC-USD"}
        assert scen["unshocked"] == []


def test_stress_gapfix_40_30_30_gfc_exact():
    """R3-3 gap-fix headline regression (hermetic — pure vector math, no
    data): 40% SPY + 30% gold + 30% BTC under GFC =
    0.4×(−0.385) + 0.3×(−0.20) + 0.3×(−0.45) = −0.349 EXACT, with all
    three legs shocked and nothing unshocked."""
    out = rm.stress_test([{"symbol": "SPY", "weight": 0.40},
                          {"symbol": "GC=F", "weight": 0.30},
                          {"symbol": "BTC-USD", "weight": 0.30}])
    assert out["portfolio_shocks"]["gfc_2008"] == pytest.approx(
        0.40 * -0.385 + 0.30 * -0.20 + 0.30 * -0.45, abs=1e-12)
    assert out["portfolio_shocks"]["gfc_2008"] == pytest.approx(-0.349,
                                                                abs=1e-12)
    gfc = {s["name"]: s for s in out["scenarios"]}["gfc_2008"]
    assert gfc["shocked"] == ["BTC-USD", "GC=F", "SPY"]
    assert gfc["unshocked"] == []


def test_stress_gold_aliases():
    """XAU / XAUUSD / GOLD aliases carry the same shock as GC=F; BTC
    alias carries the BTC vector."""
    for alias in ("XAU", "XAUUSD", "GOLD"):
        out = rm.stress_test([{"symbol": alias, "weight": 1.0}])
        assert out["portfolio_shocks"]["covid_2020"] == pytest.approx(-0.12)
    out = rm.stress_test([{"symbol": "BTC", "weight": 1.0}])
    assert out["portfolio_shocks"]["rate_shock_2022"] == pytest.approx(-0.65)


def test_stress_tnx_yield_shock_and_es_futures_alias():
    out = rm.stress_test([{"symbol": "ES=F", "weight": 1.0},
                          {"symbol": "^TNX", "weight": 1.0}])
    shocks = out["portfolio_shocks"]
    # GFC: ES=F alias carries the same −38.5%
    assert shocks["gfc_2008"] == pytest.approx(-0.385, abs=1e-12)
    # 2022: ES=F −19.4% AND the 10y yield +2.36pp in its own units
    assert shocks["rate_shock_2022"] == pytest.approx(-0.194 + 0.0236,
                                                      abs=1e-12)


def test_stress_leverage_and_custom_scenarios():
    doubled = rm.stress_test([{"symbol": "SPY", "weight": 2.0}])
    assert doubled["portfolio_shocks"]["gfc_2008"] == pytest.approx(-0.77)
    custom = rm.stress_test(
        [{"symbol": "XAU", "weight": 1.0}],
        scenarios={"mine": {"label": "custom", "shocks": {"XAU": -0.10}}})
    assert custom["portfolio_shocks"]["mine"] == pytest.approx(-0.10)


# ---------------------------------------------------------- ratios
def test_sharpe_hand_computed():
    """Sharpe (rf 5% annual, 252): explicit first-principles recomputation."""
    r = [0.01, 0.02, -0.005, 0.015]
    mu = sum(r) / len(r)
    sd = math.sqrt(sum((x - mu) ** 2 for x in r) / (len(r) - 1))
    expected = (mu - 0.05 / 252) / sd * math.sqrt(252)
    assert rm.sharpe_ratio(r) == pytest.approx(expected, abs=1e-12)
    # annualization matters: rf must exceed the daily mean to go negative
    assert rm.sharpe_ratio(r) > 0


def test_sortino_hand_computed():
    """Full-sample downside deviation (÷n, not ÷n_downside)."""
    r = [0.01, 0.02, -0.005, 0.015]
    target = 0.05 / 252
    mu = sum(r) / len(r)
    dd = math.sqrt(sum(min(x - target, 0.0) ** 2 for x in r) / len(r))
    expected = (mu - target) / dd * math.sqrt(252)
    assert rm.sortino_ratio(r) == pytest.approx(expected, abs=1e-12)
    # no downside at all → None (not +inf)
    assert rm.sortino_ratio([0.01, 0.02]) is None
    # Sortino ≥ Sharpe whenever both exist (downside σ ≤ total σ)
    both = rm.sortino_ratio(R10)
    assert both >= rm.sharpe_ratio(R10) - 1e-12


def test_max_drawdown_hand_computed():
    """Equity 100 → 120 → 90 → 110: peak 120, trough 90 → 25%."""
    assert rm.max_drawdown([100.0, 120.0, 90.0, 110.0]) == pytest.approx(0.25)
    assert rm.max_drawdown([100.0, 110.0, 120.0]) == 0.0
    assert rm.max_drawdown([]) is None
    assert rm.max_drawdown([100.0]) == 0.0


def test_calmar_hand_computed():
    """total +50% over 252 periods, mdd 25% → calmar = 0.5/0.25 = 2."""
    assert rm.calmar_ratio(0.5, 0.25, 252) == pytest.approx(2.0)
    # shorter run annualizes: +10% over 63 periods (¼ yr) → ann ≈ +46.4%
    ann = (1.10) ** (252 / 63) - 1.0
    assert rm.calmar_ratio(0.10, 0.10, 63) == pytest.approx(ann / 0.10)
    assert rm.calmar_ratio(0.5, 0.0, 252) is None
    assert rm.calmar_ratio(-1.0, 0.25, 252) is None  # wiped out
    assert rm.calmar_ratio(0.5, 0.25, 0) is None


# ---------------------------------------------------------- portfolio
def test_date_aligned_returns_intersection():
    """BTC trades weekends; SPY doesn't — pairing must use the intersection."""
    btc = {"2026-01-02": 100.0, "2026-01-03": 110.0, "2026-01-04": 121.0,
           "2026-01-05": 108.9}
    spy = {"2026-01-02": 400.0, "2026-01-05": 404.0}
    out = rm.date_aligned_returns({"BTC-USD": btc, "SPY": spy})
    # common dates: 01-02, 01-05 → ONE return each (01-03/04 dropped)
    assert out["BTC-USD"] == [pytest.approx(108.9 / 100.0 - 1.0)]
    assert out["SPY"] == [pytest.approx(404.0 / 400.0 - 1.0)]


def test_portfolio_returns_blending():
    """60/40 blend: r_p = 0.6·r_a + 0.4·r_b element-wise."""
    a = [0.01, 0.02, -0.01]
    b = [0.005, -0.01, 0.005]
    out = rm.portfolio_returns([
        {"symbol": "A", "weight": 0.6, "returns": a},
        {"symbol": "B", "weight": 0.4, "returns": b},
    ])
    expected = [0.6 * x + 0.4 * y for x, y in zip(a, b)]
    assert out == pytest.approx(expected, abs=1e-15)


def test_portfolio_returns_tail_aligned_and_empty():
    out = rm.portfolio_returns([
        {"symbol": "A", "weight": 1.0, "returns": [0.01, 0.02, 0.03, 0.04]},
        {"symbol": "B", "weight": 1.0, "returns": [0.02, 0.01]},
    ])
    assert out == pytest.approx([0.03, 0.03])       # shortest series wins
    assert rm.portfolio_returns([]) == []
    assert rm.portfolio_returns([{"symbol": "C", "weight": 1.0,
                                  "returns": []}]) == []


# ---------------------------------------------------------- risk_report
def test_risk_report_shape_and_determinism():
    _scipy, np = _ref()
    r1 = rm.risk_report(R250, benchmark=R250[:200],
                        positions=[{"symbol": "SPY", "weight": 0.5},
                                   {"symbol": "CASH", "weight": 0.5}])
    r2 = rm.risk_report(R250, benchmark=R250[:200],
                        positions=[{"symbol": "SPY", "weight": 0.5},
                                   {"symbol": "CASH", "weight": 0.5}])
    assert r1 == r2                                  # MC seed-pinned
    assert r1["ok"] is True and r1["n_observations"] == 250
    for method in ("parametric", "historical", "monte_carlo"):
        # 99% VaR sits deeper in the loss tail than 95% (more negative)
        assert r1["var"][method]["99"] < r1["var"][method]["95"] < 0
    assert r1["expected_shortfall"]["historical_95"] <= r1["var"]["historical"]["95"]
    assert r1["beta"]["n"] == 200                    # tail-aligned benchmark
    assert r1["stress"]["portfolio_shocks"]["gfc_2008"] == pytest.approx(
        -0.1925)
    assert r1["stdev"] == pytest.approx(
        float(np.std(R250, ddof=1)), abs=1e-15)


def test_risk_report_degenerate_series():
    out = rm.risk_report([0.01])
    assert out["ok"] is False
    assert out["var"]["parametric"]["95"] is None
    assert out["mean"] == 0.01
    # beta/stress still computed when supplied
    with_b = rm.risk_report([0.01], benchmark=[0.01, 0.02],
                            positions=[{"symbol": "SPY", "weight": 1.0}])
    assert with_b["beta"]["n"] == 1
    assert with_b["stress"]["portfolio_shocks"]["gfc_2008"] == pytest.approx(
        -0.385)


# ============================================================== R4-3
# Historical stress replay — REAL daily-return paths on the current book
import json as _json                                     # noqa: E402
from datetime import timedelta as _td                    # noqa: E402

# hand-built 5-day path inside the covid_2020 window
# (pad close 2020-02-18 so day 1 of the window has a return basis)
REPLAY_BARS = {
    "SPY": {"2020-02-18": 100.0, "2020-02-19": 101.0, "2020-02-20": 99.0,
            "2020-02-21": 95.0, "2020-02-24": 90.0, "2020-02-25": 92.0,
            "2020-02-26": 91.0},
    "GOLD": {"2020-02-18": 1600.0, "2020-02-19": 1620.0,
             "2020-02-20": 1610.0, "2020-02-21": 1630.0,
             "2020-02-24": 1600.0, "2020-02-25": 1610.0,
             "2020-02-26": 1620.0},
}
REPLAY_POS = [{"symbol": "SPY", "weight": 0.5},
              {"symbol": "GOLD", "weight": 0.5}]


def test_r43_replay_windows_pinned():
    """The three charter windows, exact dates."""
    assert rm.REPLAY_WINDOWS["gfc_2008"]["start"] == "2008-09-01"
    assert rm.REPLAY_WINDOWS["gfc_2008"]["end"] == "2009-03-09"
    assert rm.REPLAY_WINDOWS["covid_2020"]["start"] == "2020-02-19"
    assert rm.REPLAY_WINDOWS["covid_2020"]["end"] == "2020-03-23"
    assert rm.REPLAY_WINDOWS["rate_shock_2022"]["start"] == "2022-01-03"
    assert rm.REPLAY_WINDOWS["rate_shock_2022"]["end"] == "2022-10-12"


def test_r43_replay_five_day_hand_path_exact():
    """Hand-computed: day returns (SPY|GOLD, 50/50 book):
      02-20: .5·(99/101−1)  + .5·(1610/1620−1)  = −0.012987
      02-21: .5·(95/99−1)   + .5·(1630/1610−1)  = −0.013991
      02-24: .5·(90/95−1)   + .5·(1600/1630−1)  = −0.035518  ← worst
      02-25: .5·(92/90−1)   + .5·(1610/1600−1)  = +0.014236
      02-26: .5·(91/92−1)   + .5·(1620/1610−1)  = −0.002329
    equity 1.0 → .987013 → .973203 → .938637 → .952 → .949782
    cumulative −0.050218 · worst day −0.035518 (02-24) · MaxDD 0.061363
    """
    out = rm.stress_replay(REPLAY_POS, "covid_2020",
                           bars_by_symbol=REPLAY_BARS)
    assert out["ok"] is True and out["mode"] == "historical"
    assert out["n_days"] == 5
    assert out["cumulative"] == pytest.approx(-0.050218, abs=1e-6)
    assert out["worst_day"] == pytest.approx(-0.035518, abs=1e-6)
    assert out["worst_day_date"] == "2020-02-24"
    assert out["max_drawdown"] == pytest.approx(0.061363, abs=1e-6)
    assert out["path"][0]["date"] == "2020-02-20"
    assert out["path"][0]["equity"] == pytest.approx(0.987013, abs=1e-6)
    assert out["path"][-1]["equity"] == pytest.approx(0.949782, abs=1e-6)
    assert len(out["path"]) == 5
    assert out["static"]["portfolio_shock"] == pytest.approx(
        0.5 * -0.339 + 0.5 * -0.12)                    # −0.2295


def test_r43_replay_window_boundaries():
    """Returns are keyed for dates in (start, end]: the start day itself
    is the entry basis (no return), the pad day never enters the path."""
    rets = rm._window_returns(REPLAY_BARS["SPY"], "2020-02-19",
                              "2020-03-23")
    assert "2020-02-19" not in rets          # start = basis day
    assert "2020-02-20" in rets              # first replayed day
    assert rets["2020-02-20"] == pytest.approx(99.0 / 101.0 - 1.0)
    assert set(rets) == {"2020-02-20", "2020-02-21", "2020-02-24",
                         "2020-02-25", "2020-02-26"}


def test_r43_replay_missing_symbol_zero_and_unshocked():
    """A symbol with no bars contributes 0 and lands in unshocked —
    never a crash, never a silent re-weight."""
    pos = REPLAY_POS + [{"symbol": "CASH", "weight": 0.3},
                        {"symbol": "ZZZZ", "weight": 0.2}]
    out = rm.stress_replay(pos, "covid_2020", bars_by_symbol=REPLAY_BARS)
    assert out["ok"] is True
    assert out["unshocked"] == ["CASH", "ZZZZ"]
    assert out["shocked"] == ["GOLD", "SPY"]
    # identical numbers to the 2-symbol book (the dead legs added 0)
    base = rm.stress_replay(REPLAY_POS, "covid_2020",
                            bars_by_symbol=REPLAY_BARS)
    assert out["cumulative"] == base["cumulative"]
    assert out["max_drawdown"] == base["max_drawdown"]


def test_r43_replay_fetch_injection_seam():
    def fetch(symbol, start, end, data_root="data"):
        return dict(REPLAY_BARS[symbol])

    out = rm.stress_replay(REPLAY_POS, "covid_2020", fetch=fetch)
    hand = rm.stress_replay(REPLAY_POS, "covid_2020",
                            bars_by_symbol=REPLAY_BARS)
    assert out["cumulative"] == hand["cumulative"]
    assert out["worst_day"] == hand["worst_day"]
    assert out["n_days"] == hand["n_days"]


def test_r43_replay_total_network_failure_falls_back_static():
    """Yahoo down: {ok: False, fallback: static} + the static vector."""
    def boom(symbol, start, end):
        raise RuntimeError("network down")

    out = rm.stress_replay(REPLAY_POS, "covid_2020", fetch=boom)
    assert out["ok"] is False
    assert out["fallback"] == "static"
    assert out["mode"] == "fallback"
    assert "network down" in out["error"]
    assert out["static"]["portfolio_shock"] == pytest.approx(-0.2295)
    assert out["unshocked"] == ["GOLD", "SPY"]


def test_r43_replay_partial_failure_is_fail_soft():
    """One symbol dead, one alive → replay proceeds with the survivor."""
    def fetch(symbol, start, end):
        if symbol == "GOLD":
            raise RuntimeError("no gold bars")
        return dict(REPLAY_BARS["SPY"])

    out = rm.stress_replay(REPLAY_POS, "covid_2020", fetch=fetch)
    assert out["ok"] is True
    assert out["unshocked"] == ["GOLD"]
    assert out["shocked"] == ["SPY"]
    # the GOLD weight is NOT re-weighted: its 50% sits in dead cash, so
    # cumulative = Π(1 + 0.5·r_spy,t) − 1 (hand-compounded below)
    rets = rm._window_returns(REPLAY_BARS["SPY"], "2020-02-19",
                              "2020-03-23")
    hand = 1.0
    for d in sorted(rets):
        hand *= (1.0 + 0.5 * rets[d])
    assert out["cumulative"] == pytest.approx(hand - 1.0, abs=1e-6)


def test_r43_replay_fast_mode_serves_static_vector():
    out = rm.stress_replay(REPLAY_POS, "gfc_2008", fast=True)
    assert out["ok"] is True and out["mode"] == "static"
    assert out["portfolio_shock"] == pytest.approx(
        0.5 * -0.385 + 0.5 * -0.20)                    # GOLD alias shock
    assert out["window"]["start"] == "2008-09-01"


def test_r43_replay_empty_book_serves_static_vector():
    out = rm.stress_replay([], "covid_2020")
    assert out["ok"] is True and out["mode"] == "static"
    assert out["portfolio_shock"] == 0.0


def test_r43_replay_unknown_scenario():
    out = rm.stress_replay(REPLAY_POS, "dotcom_2000")
    assert out["ok"] is False
    assert "unknown scenario" in out["error"]


def test_r43_replay_path_capped_at_400(monkeypatch):
    """A 730-day window → n_path_points 730 but only the last 400 in
    the output path (the charter's cap)."""
    bars = {"SPY": {}, "GOLD": {}}
    d = rm.datetime(2020, 1, 1)
    px = {"SPY": 100.0, "GOLD": 1600.0}
    for i in range(731):
        day = (d + _td(days=i)).strftime("%Y-%m-%d")
        for sym in bars:
            px[sym] *= 1.001
            bars[sym][day] = round(px[sym], 4)
    monkeypatch.setitem(rm.REPLAY_WINDOWS, "long_window",
                        {"label": "synthetic long window",
                         "start": "2020-01-01", "end": "2021-12-31"})
    out = rm.stress_replay(REPLAY_POS, "long_window",
                           bars_by_symbol=bars)
    assert out["n_path_points"] == 730
    assert len(out["path"]) == rm.REPLAY_PATH_MAX
    assert out["path"][0]["date"] == "2020-11-27"     # 730 − 400 + 1


def test_r43_replay_all_three_scenarios():
    out = rm.stress_replay_all(REPLAY_POS, fast=True)
    assert out["ok"] is True and out["n_ok"] == 3
    assert list(out["replays"]) == ["gfc_2008", "covid_2020",
                                    "rate_shock_2022"]
    assert all(s["mode"] == "static" for s in out["scenarios"])


class _FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_r43_fetch_window_closes_parses_and_caches(monkeypatch, tmp_path):
    """One Yahoo v8/chart call with period1/period2 epoch bounds →
    {date: close}; the second call is served from the file cache (the
    HTTP seam stays cold)."""
    calls = []

    def fake_urlopen(req, timeout=10.0):
        calls.append(req.full_url)
        t19 = int(rm.datetime(2020, 2, 19, tzinfo=rm.timezone.utc).timestamp())
        chart = {"chart": {"result": [{
            "timestamp": [t19, t19 + 86400],       # 2020-02-19/20 UTC
            "indicators": {"quote": [{"close": [100.0, 99.0]}]},
        }]}}
        return _FakeResponse(_json.dumps(chart).encode())

    monkeypatch.setattr(rm.urllib.request, "urlopen", fake_urlopen)
    closes = rm.fetch_window_closes("SPY", "2020-02-19", "2020-02-20",
                                    data_root=tmp_path)
    assert closes == {"2020-02-19": 100.0, "2020-02-20": 99.0}
    assert len(calls) == 1
    url = calls[0]
    assert "period1=" in url and "period2=" in url and "interval=1d" in url
    # p1 = 2020-02-19 minus the 10-day pad (UTC epoch)
    p1 = int(url.split("period1=")[1].split("&")[0])
    assert p1 == int(rm.datetime(2020, 2, 19, tzinfo=rm.timezone.utc)
                     .timestamp()) - rm.REPLAY_PAD_DAYS * 86400
    # cached second call: no extra HTTP
    again = rm.fetch_window_closes("SPY", "2020-02-19", "2020-02-20",
                                   data_root=tmp_path)
    assert again == closes
    assert len(calls) == 1


def test_r43_fetch_window_closes_raises_on_empty(monkeypatch, tmp_path):
    def fake_urlopen(req, timeout=10.0):
        return _FakeResponse(b'{"chart": {"result": []}}')

    monkeypatch.setattr(rm.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError):
        rm.fetch_window_closes("SPY", "2020-02-19", "2020-02-20",
                               data_root=tmp_path)


# ------------------------------------------------------ R4-3 CLI wiring
class _RiskArgs:
    returns = '[0.01,-0.02,0.015,0.005,-0.01,0.02,-0.005,0.012,0.008,-0.014]'
    benchmark_returns = None
    positions = '[{"symbol":"SPY","weight":0.5},{"symbol":"GOLD","weight":0.5}]'
    stress_replay = True
    fast = True
    json = True
    data_root = ""


def test_r43_cli_risk_stress_replay_json(capsys, tmp_path):
    from gold_desk.cli import cmd_risk
    args = _RiskArgs()
    args.data_root = str(tmp_path)
    rc = cmd_risk(args)
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    replay = out["stress_replay"]
    assert replay["ok"] is True and replay["n_ok"] == 3
    gfc = replay["replays"]["gfc_2008"]
    assert gfc["mode"] == "static"
    assert gfc["portfolio_shock"] == pytest.approx(-0.2925)
    assert gfc["window"] == {"start": "2008-09-01", "end": "2009-03-09"}


def test_r43_cli_risk_stress_replay_pretty(capsys, tmp_path):
    from gold_desk.cli import cmd_risk
    args = _RiskArgs()
    args.json = False
    args.data_root = str(tmp_path)
    rc = cmd_risk(args)
    text = capsys.readouterr().out
    assert rc == 0
    assert "STRESS REPLAY" in text
    assert "2008-09-01" in text and "static vector (--fast)" in text


def test_r43_cli_risk_replay_only_when_default_fetch_fails(
        capsys, tmp_path, monkeypatch):
    """1y default-portfolio fetch down + --stress-replay --fast → the
    replay-only report still ships (static vectors), rc 0."""
    import gold_desk.cli as cli
    from gold_desk.cli import cmd_risk
    monkeypatch.setattr(cli, "_risk_default_portfolio",
                        lambda data_root: None)
    args = _RiskArgs()
    args.returns = None
    args.positions = None
    args.data_root = str(tmp_path)
    rc = cmd_risk(args)
    out = _json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["stress_replay"]["n_ok"] == 3
    assert "replay-only" in out["portfolio"]
