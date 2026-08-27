"""R3-3 Build 5a — tests for risk/portfolio.py (MV / RP / HRP).

Deterministic constructions:
* orthogonal Hadamard series — EXACT zero sample covariance with chosen
  variance ratios, so the diagonal-Σ properties (RP wᵢ ∝ 1/σᵢ, equal-σ
  equal weights) are pinned to 1e-9, not to sampling luck
* seeded random series — realistic shapes (HRP's 0.999-correlated pair,
  anti-correlated pairs for the diversification ratio)
* the CLI is exercised through cmd_portfolio with monkeypatched bar
  fetches (epoch-ms shape, like the R3-2 risk regression) — no network.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from gold_desk.risk import portfolio as pf

# Three mutually-orthogonal zero-mean vectors (Hadamard-4 columns minus
# the all-ones column): EXACT zero sample covariance, nonzero variances
_ORTH3 = [[1, 1, -1, -1],
          [1, -1, 1, -1],
          [1, -1, -1, 1]]


def _orthogonal(scales: list[float]) -> dict:
    """{A, B, C} with EXACT zero pairwise covariance and per-asset σ
    proportional to the given scales (Hadamard construction — no
    sampling noise, so the diagonal-Σ properties pin to 1e-9)."""
    return {chr(ord("A") + i): [s * v for v in _ORTH3[i]]
            for i, s in enumerate(scales)}


def _rng_series(n: int, mu: float, sigma: float, seed: int) -> list[float]:
    rng = random.Random(seed)
    return [rng.gauss(mu, sigma) for _ in range(n)]


@pytest.fixture()
def two_asset() -> dict:
    """A: μ=0.002, σ=0.02 (high return, high vol); B: μ=0.001, σ=0.005."""
    return {"A": _rng_series(600, 0.002, 0.02, 21),
            "B": _rng_series(600, 0.001, 0.005, 22)}


@pytest.fixture()
def three_uncorrelated() -> dict:
    """σ ratios 4:2:1 (A wildest, C calmest), population-independent."""
    return {"A": _rng_series(600, 0.0004, 0.02, 31),
            "B": _rng_series(600, 0.0004, 0.01, 32),
            "C": _rng_series(600, 0.0004, 0.005, 33)}


@pytest.fixture()
def hrp_pair() -> dict:
    """A↔B ρ≈0.999 (a tight pair), C uncorrelated with both, equal σ."""
    n = 500
    base = _rng_series(n, 0.0004, 0.01, 41)
    noise_sd = 0.01 * math.sqrt((1.0 - 0.999 ** 2) / 0.999 ** 2)
    rng = random.Random(42)
    pair_noise = [rng.gauss(0.0, noise_sd) for _ in range(n)]
    return {"A": base,
            "B": [a + e for a, e in zip(base, pair_noise)],
            "C": _rng_series(n, 0.0004, 0.01, 43)}


# ------------------------------------------------------------------ MV
def test_mv_lambda_low_favors_high_mu_asset(two_asset):
    """λ=0.1: the variance penalty is negligible → weight concentrates on
    the high-μ asset A."""
    out = pf.mean_variance(two_asset, lambda_risk=0.1, max_weight=1.0)
    assert out["ok"] is True
    assert out["weights"]["A"] > 0.5 > out["weights"]["B"]


def test_mv_lambda_high_favors_low_vol_asset(two_asset):
    """λ=50: the variance penalty dominates → weight concentrates on the
    low-σ asset B (hand math: interior optimum ≈ (0.08, 0.92))."""
    out = pf.mean_variance(two_asset, lambda_risk=50.0, max_weight=1.0)
    assert out["ok"] is True
    assert out["weights"]["B"] > 0.5 > out["weights"]["A"]


def test_mv_lambda_monotone_shift_towards_low_vol(two_asset):
    """Raising λ never increases the high-vol asset's weight (the
    risk-aversion knob points one way)."""
    prev = 1.0
    for lam in (0.5, 2.0, 5.0, 20.0):
        out = pf.mean_variance(two_asset, lambda_risk=lam, max_weight=1.0)
        w_a = out["weights"]["A"]
        assert w_a <= prev + 1e-9
        prev = w_a


def test_mv_weights_sum_to_one_and_nonnegative(two_asset):
    out = pf.mean_variance(two_asset, max_weight=1.0)
    w = out["weights"]
    assert set(w) == {"A", "B"}
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)
    assert all(v >= 0.0 for v in w.values())


def test_mv_max_weight_cap_enforced(three_uncorrelated):
    """Default cap 0.4: no coordinate exceeds it, and the cap actually
    binds somewhere (the uncapped solution is more concentrated)."""
    capped = pf.mean_variance(three_uncorrelated, lambda_risk=2.0,
                              max_weight=0.4)
    assert capped["ok"] is True
    assert max(capped["weights"].values()) <= 0.4 + 1e-12
    assert max(capped["weights"].values()) == pytest.approx(0.4, abs=1e-9)
    uncapped = pf.mean_variance(three_uncorrelated, lambda_risk=2.0,
                                max_weight=1.0)
    assert max(uncapped["weights"].values()) > 0.4


def test_mv_same_seed_identical(two_asset):
    a = pf.mean_variance(two_asset, max_weight=1.0, seed=7)
    b = pf.mean_variance(two_asset, max_weight=1.0, seed=7)
    assert a == b


def test_mv_reported_objective_matches_weights(two_asset):
    """Self-consistency: the reported objective is exactly μᵀw − λ·wᵀΣw
    recomputed from the returned weights and the sample covariance."""
    a = pf.mean_variance(two_asset, max_weight=1.0, seed=7)
    b = pf.mean_variance(two_asset, max_weight=1.0, seed=99)
    for out in (a, b):
        assert out["ok"] is True
        assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)
        syms = out["symbols"]
        mu = [out["expected_returns"][s] for s in syms]
        cov = pf._cov_matrix([two_asset[s] for s in syms])
        w = [out["weights"][s] for s in syms]
        k = len(syms)
        obj = (sum(w[i] * mu[i] for i in range(k))
               - out["lambda_risk"] * sum(w[i] * cov[i][j] * w[j]
                                          for i in range(k)
                                          for j in range(k)))
        assert out["objective"] == pytest.approx(obj, abs=1e-15)


def test_mv_beats_equal_weight(two_asset):
    """The search must find something at least as good as the (evaluated)
    equal-weight candidate."""
    out = pf.mean_variance(two_asset, lambda_risk=2.0, max_weight=1.0)
    syms = out["symbols"]
    mu = [out["expected_returns"][s] for s in syms]
    cov = pf._cov_matrix([two_asset[s] for s in syms])
    w_eq = [0.5, 0.5]
    obj_eq = (sum(w_eq[i] * mu[i] for i in range(2))
              - 2.0 * sum(w_eq[i] * cov[i][j] * w_eq[j]
                          for i in range(2) for j in range(2)))
    assert out["objective"] >= obj_eq - 1e-12


def test_mv_single_asset_weight_one():
    out = pf.mean_variance({"ONE": [0.01, -0.02, 0.03, 0.005]})
    assert out["ok"] is True
    assert out["weights"] == {"ONE": 1.0}
    assert out["diversification_ratio"] == pytest.approx(1.0)


def test_mv_result_shape(three_uncorrelated):
    out = pf.mean_variance(three_uncorrelated)
    assert out["method"] == "mv"
    assert out["symbols"] == ["A", "B", "C"]      # sorted, deterministic
    assert out["n_observations"] == 600
    assert out["portfolio_vol"] > 0
    assert set(out["weights"]) == set(out["risk_contributions"])
    assert sum(out["risk_contributions"].values()) == pytest.approx(
        1.0, abs=1e-9)
    assert out["lambda_risk"] == pf.MV_LAMBDA
    assert out["max_weight"] == pf.MAX_WEIGHT
    assert out["n_candidates"] >= 2000


def test_mv_empty_map_fails_gracefully():
    out = pf.optimize({}, "mv")
    assert out["ok"] is False
    assert "no assets" in out["error"]


def test_mv_one_observation_fails_gracefully():
    out = pf.optimize({"A": [0.01], "B": [0.02, 0.01]}, "mv")
    assert out["ok"] is False
    assert "≥2" in out["error"] and "A" in out["error"]


def test_mv_zero_vol_asset_bounded_by_cap():
    """MV tolerates a zero-volatility asset (objective stays finite): its
    weight is bounded by max_weight, never a crash."""
    data = {"A": [0.01, 0.01, 0.01, 0.01],      # constant → σ = 0
            "B": [0.01, -0.02, 0.03, -0.005],
            "C": [0.004, -0.001, 0.002, 0.003]}
    out = pf.mean_variance(data, max_weight=0.4)
    assert out["ok"] is True
    assert out["weights"]["A"] <= 0.4 + 1e-12


def test_mv_infeasible_cap_fails_gracefully(two_asset):
    out = pf.optimize(two_asset, "mv")            # 2 assets, cap 0.4
    assert out["ok"] is False
    assert "infeasible" in out["error"]
    assert "0.5000" in out["error"]


def test_mv_tail_alignment():
    """Series of different lengths are tail-aligned to the shortest."""
    long_a = [0.01, -0.01, 0.02, -0.02, 0.005, 0.0]
    short_b = [0.004, -0.003, 0.002]
    out = pf.mean_variance({"A": long_a, "B": short_b}, max_weight=1.0)
    assert out["ok"] is True
    assert out["n_observations"] == 3


# ------------------------------------------------------------------ RP
def test_rp_diagonal_exact_inverse_vol():
    """Hadamard construction (exact diagonal Σ): wᵢ ∝ 1/σᵢ EXACTLY.
    σ ratios 4:2:1 → weights (1/7, 2/7, 4/7)."""
    data = _orthogonal([4.0, 2.0, 1.0])
    out = pf.risk_parity(data)
    assert out["ok"] is True
    assert out["weights"]["A"] == pytest.approx(1.0 / 7.0, abs=1e-9)
    assert out["weights"]["B"] == pytest.approx(2.0 / 7.0, abs=1e-9)
    assert out["weights"]["C"] == pytest.approx(4.0 / 7.0, abs=1e-9)


def test_rp_equal_vol_exact_thirds():
    data = _orthogonal([1.0, 1.0, 1.0])
    out = pf.risk_parity(data)
    for w in out["weights"].values():
        assert w == pytest.approx(1.0 / 3.0, abs=1e-9)


def test_rp_risk_contributions_equal(three_uncorrelated):
    """The ERC property itself: every asset contributes exactly 1/n of
    portfolio variance (spec: deviation < 1e-4 — we get ~1e-15)."""
    out = pf.risk_parity(three_uncorrelated)
    assert out["ok"] is True
    for rc in out["risk_contributions"].values():
        assert rc == pytest.approx(1.0 / 3.0, abs=1e-4)


def test_rp_converged_report(three_uncorrelated):
    out = pf.risk_parity(three_uncorrelated)
    assert out["converged"] is True
    assert 1 <= out["iterations"] <= pf.RP_MAX_ITER
    assert out["tol"] == pf.RP_TOL


def test_rp_correlated_erc_holds():
    """ERC holds for a genuinely correlated Σ too (off-diagonals ≠ 0)."""
    rng = random.Random(77)
    n = 400
    market = [rng.gauss(0, 0.01) for _ in range(n)]
    data = {
        "A": [0.8 * m + rng.gauss(0, 0.006) for m in market],
        "B": [0.5 * m + rng.gauss(0, 0.004) for m in market],
        "C": [rng.gauss(0, 0.008) for _ in range(n)],
    }
    out = pf.risk_parity(data)
    assert out["ok"] is True
    for rc in out["risk_contributions"].values():
        assert rc == pytest.approx(1.0 / 3.0, abs=1e-6)


def test_rp_identical_series_converges_equal_weights():
    """ρ = 1 (singular Σ, but PSD with positive diagonal): symmetric ERC
    solution is the equal-weight vector and coordinate descent finds it."""
    z = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02]
    out = pf.risk_parity({"P": list(z), "Q": list(z)})
    assert out["ok"] is True
    # tol 1e-6 on the ERC residual xᵢ(Σx)ᵢ − bᵢ maps to ~1e-7 weight error
    assert out["weights"]["P"] == pytest.approx(0.5, abs=1e-6)
    assert out["weights"]["Q"] == pytest.approx(0.5, abs=1e-6)


def test_rp_anticorrelated_singular_fails_gracefully():
    """ρ = −1: the Spinu objective is unbounded along the null space —
    iteration must hit max_iter and return an honest error, not a crash
    or garbage weights."""
    rng = random.Random(3)
    x = [rng.gauss(0, 0.01) for _ in range(200)]
    out = pf.optimize({"X": x, "Y": [-v for v in x]}, "rp")
    assert out["ok"] is False
    assert "did not converge" in out["error"]


def test_rp_zero_variance_fails_gracefully():
    out = pf.optimize({"A": [0.01, 0.01, 0.01], "B": [0.01, -0.01, 0.02]},
                      "rp")
    assert out["ok"] is False
    assert "zero variance" in out["error"] and "A" in out["error"]


def test_rp_single_asset_weight_one():
    out = pf.risk_parity({"ONE": [0.01, -0.02, 0.03]})
    assert out["ok"] is True
    assert out["weights"] == {"ONE": 1.0}
    assert out["risk_contributions"] == {"ONE": 1.0}


def test_rp_weights_sum_one_nonnegative(three_uncorrelated):
    out = pf.risk_parity(three_uncorrelated)
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)
    assert all(v >= 0 for v in out["weights"].values())


# ------------------------------------------------------------------ HRP
def test_hrp_correlated_pair_shares_one_cluster(hrp_pair):
    """ρ(A,B)=0.999 + uncorrelated C: the pair merges FIRST (one
    cluster), so at the top bisection it shares one asset's weight —
    combined ≈ 0.5, C ≈ 0.5."""
    out = pf.hierarchical_risk_parity(hrp_pair)
    assert out["ok"] is True
    pair_w = out["weights"]["A"] + out["weights"]["B"]
    assert pair_w == pytest.approx(0.5, abs=0.05)
    assert out["weights"]["C"] == pytest.approx(0.5, abs=0.05)
    # the first merge IS the pair (single linkage on correlation distance)
    first = out["merges"][0]
    assert sorted(first["clusters"][0] + first["clusters"][1]) == ["A", "B"]
    assert first["distance"] < 0.1          # √(2(1−0.999)) ≈ 0.045


def test_hrp_pair_splits_half_half_internally(hrp_pair):
    out = pf.hierarchical_risk_parity(hrp_pair)
    assert out["weights"]["A"] == pytest.approx(out["weights"]["B"],
                                               abs=0.02)


def test_hrp_quasi_diagonal_order(hrp_pair):
    """Leaf order puts the pair adjacent; C at one end — deterministic."""
    out = pf.hierarchical_risk_parity(hrp_pair)
    order = out["quasi_diagonal_order"]
    assert sorted(order) == ["A", "B", "C"]
    assert abs(order.index("A") - order.index("B")) == 1
    assert order[0] == "C" or order[-1] == "C"


def test_hrp_four_assets_two_clusters():
    """{A,B} tight, {C,D} tight, cross-correlation ~0: each cluster takes
    ≈ half the book."""
    n = 400
    rng = random.Random(55)
    f1 = [rng.gauss(0, 0.01) for _ in range(n)]
    f2 = [rng.gauss(0, 0.01) for _ in range(n)]
    data = {
        "A": [0.999 * f + rng.gauss(0, 0.0005) for f in f1],
        "B": [f + rng.gauss(0, 0.0005) for f in f1],
        "C": [0.999 * f + rng.gauss(0, 0.0005) for f in f2],
        "D": [f + rng.gauss(0, 0.0005) for f in f2],
    }
    out = pf.hierarchical_risk_parity(data)
    cluster1 = out["weights"]["A"] + out["weights"]["B"]
    cluster2 = out["weights"]["C"] + out["weights"]["D"]
    assert cluster1 == pytest.approx(0.5, abs=0.05)
    assert cluster2 == pytest.approx(0.5, abs=0.05)


def test_hrp_uncorrelated_equal_vol_thirds():
    data = _orthogonal([1.0, 1.0, 1.0])
    out = pf.hierarchical_risk_parity(data)
    for w in out["weights"].values():
        assert w == pytest.approx(1.0 / 3.0, abs=1e-9)


def test_hrp_low_vol_asset_gets_more_weight():
    """Uncorrelated book: the calm asset earns the larger HRP weight
    (inverse-variance flavor of the bisection split)."""
    data = _orthogonal([3.0, 1.0])
    out = pf.hierarchical_risk_parity(data)
    assert out["weights"]["B"] > out["weights"]["A"]


def test_hrp_single_asset_weight_one():
    out = pf.hierarchical_risk_parity({"ONE": [0.01, -0.02, 0.03]})
    assert out["ok"] is True
    assert out["weights"] == {"ONE": 1.0}
    assert out["quasi_diagonal_order"] == ["ONE"]
    assert out["merges"] == []


def test_hrp_weights_sum_one_nonnegative(hrp_pair):
    out = pf.hierarchical_risk_parity(hrp_pair)
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)
    assert all(v >= 0 for v in out["weights"].values())


def test_hrp_merges_logged_and_deterministic(hrp_pair):
    a = pf.hierarchical_risk_parity(hrp_pair)
    b = pf.hierarchical_risk_parity(hrp_pair)
    assert a == b                                  # pure function
    assert len(a["merges"]) == 2                   # k − 1 merges


def test_hrp_zero_variance_fails_gracefully():
    out = pf.optimize({"A": [0.01, 0.01, 0.01], "B": [0.01, -0.01, 0.02]},
                      "hrp")
    assert out["ok"] is False
    assert "zero variance" in out["error"]


# ------------------------------------------------------------------ DR
def test_dr_single_asset_is_one_for_every_method():
    for method in ("mv", "rp", "hrp"):
        out = pf.optimize({"ONE": [0.01, -0.02, 0.03, 0.005]}, method)
        assert out["ok"] is True, method
        assert out["diversification_ratio"] == pytest.approx(1.0), method


def test_dr_anticorrelated_pair_greater_than_one():
    """ρ ≈ −0.95: combining the pair slashes vol far below the weighted
    average volatility — DR is a multiple."""
    rng = random.Random(9)
    n = 400
    x = [rng.gauss(0, 0.01) for _ in range(n)]
    data = {"X": x,
            "Y": [-0.95 * v + rng.gauss(0, 0.003) for v in x]}
    out = pf.hierarchical_risk_parity(data)
    assert out["ok"] is True
    assert out["diversification_ratio"] > 1.5


def test_dr_uncorrelated_greater_than_one(three_uncorrelated):
    for method in ("mv", "rp", "hrp"):
        out = pf.optimize(three_uncorrelated, method)
        assert out["diversification_ratio"] > 1.0, method


def test_dr_zero_variance_portfolio_returns_none():
    """Perfect hedge (ρ = −1, w = 0.5/0.5): vol = 0 → DR undefined → None
    (surfaced as n/a), never a misleading 1.0."""
    x = [0.01, -0.02, 0.03, -0.01, 0.02, -0.005]
    out = pf.hierarchical_risk_parity({"X": x, "Y": [-v for v in x]})
    assert out["ok"] is True
    assert out["portfolio_vol"] == pytest.approx(0.0, abs=1e-12)
    assert out["diversification_ratio"] is None


def test_mv_cap_exactly_one_over_k_forces_equal_weights(three_uncorrelated):
    """cap = 1/3 with 3 assets: the ONLY feasible point is the equal-weight
    vector — the projection must land on it exactly."""
    out = pf.mean_variance(three_uncorrelated, max_weight=1.0 / 3.0)
    assert out["ok"] is True
    for w in out["weights"].values():
        assert w == pytest.approx(1.0 / 3.0, abs=1e-9)


def test_hrp_two_assets_inverse_variance_split():
    """2-asset HRP: the single bisection split gives w_A = var_B /
    (var_A + var_B) — hand-pinned on the exact orthogonal construction."""
    data = _orthogonal([3.0, 1.0])          # σ_A : σ_B = 3 : 1
    out = pf.hierarchical_risk_parity(data)
    assert out["weights"]["A"] == pytest.approx(1.0 / 10.0, abs=1e-9)
    assert out["weights"]["B"] == pytest.approx(9.0 / 10.0, abs=1e-9)


# ------------------------------------------------------------------ dispatch
def test_optimize_dispatch_aliases(two_asset):
    assert pf.optimize(two_asset, "mean_variance")["method"] == "mv"
    assert pf.optimize(two_asset, "erc")["method"] == "rp"
    assert pf.optimize(two_asset, "hierarchical_risk_parity")["method"] \
        == "hrp"


def test_optimize_unknown_method(two_asset):
    out = pf.optimize(two_asset, "black-litterman")
    assert out["ok"] is False
    assert "unknown method" in out["error"]


def test_optimize_never_raises_on_garbage():
    for bad in (None, [], "SPY", {"A": None}, {"A": ["x", "y"]},
                {"A": [], "B": [0.01, 0.02]}, {"A": [0.01]}, {}):
        for method in ("mv", "rp", "hrp"):
            out = pf.optimize(bad, method)
            assert out["ok"] is False and out["error"], (bad, method)


# ------------------------------------------------------------------ CLI
class _PortArgs:
    returns = None
    method = "mv"
    lookback = "90d"
    symbols = "SPY,GC=F,BTC-USD"
    max_weight = 0.4
    lambda_risk = 2.0
    seed = 7
    json = True
    data_root = "/tmp/portfolio_cli"


def test_cli_portfolio_offline_json(capsys):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.returns = json.dumps(
        {"A": [0.01, -0.02, 0.03, 0.005, -0.01, 0.02],
         "B": [0.005, 0.001, -0.003, 0.002, 0.0, 0.004],
         "C": [-0.01, 0.02, -0.005, 0.008, 0.003, -0.002]})
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["method"] == "mv"
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)
    assert out["source"] == "explicit series"
    assert out["n_candidates"] >= 2000


@pytest.mark.parametrize("method", ["rp", "hrp"])
def test_cli_portfolio_rp_hrp_offline(capsys, method):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.method = method
    args.returns = json.dumps(
        {"A": [0.01, -0.02, 0.03, 0.005, -0.01, 0.02],
         "B": [0.005, 0.001, -0.003, 0.002, 0.0, 0.004]})
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["method"] == method
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-9)


def test_cli_portfolio_invalid_returns_json(capsys):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.returns = "{not json"
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert "invalid JSON" in out["error"]


def test_cli_portfolio_returns_not_an_object(capsys):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.returns = "[0.01, 0.02]"
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert "expected a JSON object" in out["error"]


def test_cli_portfolio_live_fetch_fail_closed(monkeypatch, capsys):
    from gold_desk.cli import cmd_portfolio
    monkeypatch.setattr("gold_desk.markets.board.fetch_daily_bars",
                        lambda *a, **k: [])
    rc = cmd_portfolio(_PortArgs())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert "offline" in out["error"]


def test_cli_portfolio_live_epoch_ms_bars_lookback(monkeypatch, capsys):
    """Live path with board.py's EPOCH-MS bar shape + --lookback tail:
    120 aligned days → lookback 30d → exactly 30 return observations."""
    from gold_desk.cli import cmd_portfolio
    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)     # a Monday
    base = {"SPY": 400.0, "GC=F": 2400.0, "BTC-USD": 80000.0}

    def fake_fetch_daily(symbol, range_, data_root="data"):
        bars = []
        price = base[symbol]
        for i in range(120):
            day = t0 + timedelta(days=i)
            if symbol != "BTC-USD" and day.weekday() >= 5:
                continue
            price *= 1.0 + (0.001 if i % 3 else -0.001)
            bars.append({"ts": int(day.timestamp() * 1000),
                         "o": price, "h": price * 1.01,
                         "l": price * 0.99, "c": price, "v": 1000.0})
        return bars

    monkeypatch.setattr("gold_desk.markets.board.fetch_daily_bars",
                        fake_fetch_daily)
    args = _PortArgs()
    args.lookback = "30d"
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["n_observations"] == 30          # 84 aligned → last 30 kept
    assert out["source"].startswith("live SPY")
    assert set(out["weights"]) == {"SPY", "GC=F", "BTC-USD"}


def test_cli_portfolio_pretty(capsys):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.json = False
    args.max_weight = 1.0        # 2-asset book: default cap 0.4 infeasible
    args.returns = json.dumps({"A": [0.01, -0.02, 0.03, 0.005],
                               "B": [0.005, 0.001, -0.003, 0.002]})
    rc = cmd_portfolio(args)
    text = capsys.readouterr().out
    assert rc == 0
    assert "PORTFOLIO" in text and "A" in text and "weight" in text


def test_cli_portfolio_degenerate_returns_fail_honestly(capsys):
    from gold_desk.cli import cmd_portfolio
    args = _PortArgs()
    args.returns = json.dumps({"A": [0.01]})     # 1 observation
    rc = cmd_portfolio(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert "≥2" in out["error"]
