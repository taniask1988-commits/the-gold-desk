"""R4-2 tests — 24-instrument UNIVERSE + Ledoit-Wolf shrinkage + exact
projected-gradient mean-variance.

Every check is hermetic (no network): MultiAssetMonitor is exercised via
injected fetchers; LW/PGD math verified against analytic properties and
dense-grid references.
"""
import math
import random
import time

import pytest

from gold_desk.markets import registry
from gold_desk.markets.multi_asset import MultiAssetMonitor
from gold_desk.risk import portfolio as pf


# ------------------------------------------------------------------ registry
def test_universe_has_24_unique_symbols():
    syms = [e["symbol"] for e in registry.UNIVERSE]
    assert len(syms) == 24
    assert len(set(syms)) == 24


def test_universe_sectors_cover_all_groups():
    sectors = {e["sector"] for e in registry.UNIVERSE}
    assert sectors == {"metals", "energy", "ag", "indices", "fx",
                       "rates", "crypto", "vol"}


def test_universe_sector_assignments():
    expect = {"GC=F": "metals", "SI=F": "metals", "HG=F": "metals",
              "CL=F": "energy", "NG=F": "energy",
              "ZW=F": "ag", "ZC=F": "ag",
              "ES=F": "indices", "NQ=F": "indices", "YM=F": "indices",
              "RTY=F": "indices",
              "EURUSD=X": "fx", "GBPUSD=X": "fx", "USDJPY=X": "fx",
              "AUDUSD=X": "fx", "USDCAD=X": "fx",
              # DXY grouped with rates by design (macro/rates-adjacent
              # instrument per the registry's documented grouping)
              "DX-Y.NYB": "rates",
              "^TNX": "rates", "^FVX": "rates", "^TYX": "rates",
              "BTC-USD": "crypto", "ETH-USD": "crypto", "SOL-USD": "crypto",
              "^VIX": "vol"}
    for e in registry.UNIVERSE:
        assert e["sector"] == expect[e["symbol"]], e["symbol"]


def test_default_watchlist_is_first_eight():
    assert registry.DEFAULT_WATCHLIST == \
        [e["symbol"] for e in registry.UNIVERSE[:8]]
    assert "GC=F" in registry.DEFAULT_WATCHLIST
    assert "SI=F" not in registry.DEFAULT_WATCHLIST


def test_sector_of_and_universe_entry_helpers():
    assert registry.sector_of("GC=F") == "metals"
    assert registry.sector_of("NOT-A-SYMBOL") in (None, "", "unknown") \
        or registry.sector_of("NOT-A-SYMBOL") is None
    entry = registry.universe_entry("SI=F")
    assert entry and entry["sector"] == "metals"


def test_backward_compat_registry_api_intact():
    # the R3-era surface must keep working
    assert hasattr(registry, "SESSION_CALENDARS")
    assert callable(registry.session_calendar)
    cal = registry.session_calendar("GC=F")
    assert cal and "COMEX" in str(cal.get("calendar", cal))


# ------------------------------------------------------- monitor w/ universe
def _fetch_all_ok(symbols):
    def fetch(syms):
        return {s: {"ok": True, "price": 100.0 + i,
                    "regularMarketPreviousClose": 99.0,
                    "shortName": s, "name": s}
                for i, s in enumerate(syms)}
    return fetch


def test_monitor_all_24(tmp_path):
    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=_fetch_all_ok(None),
                            all=True)
    snap = mon.snapshot()
    assert snap["ok"] is True
    assert len(snap["assets"]) == 24
    assert "SI=F" in snap["assets"] and "SOL-USD" in snap["assets"]


def test_monitor_default_is_watchlist_8(tmp_path):
    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=_fetch_all_ok(None))
    snap = mon.snapshot()
    assert len(snap["assets"]) == 8
    assert "SI=F" not in snap["assets"]


def test_monitor_subset(tmp_path):
    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=_fetch_all_ok(None),
                            symbols=["SI=F", "NQ=F", "ETH-USD"])
    snap = mon.snapshot()
    assert set(snap["assets"]) == {"SI=F", "NQ=F", "ETH-USD"}


def test_monitor_snapshot_rows_carry_sector(tmp_path):
    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=_fetch_all_ok(None),
                            all=True)
    snap = mon.snapshot()
    assert snap["assets"]["GC=F"].get("sector") == "metals"
    assert snap["assets"]["^VIX"].get("sector") == "vol"


def test_monitor_fail_soft_with_3_of_12_down(tmp_path):
    dead = {"GC=F", "CL=F", "BTC-USD"}

    def flaky(syms):
        out = {}
        for s in syms:
            if s in dead:
                out[s] = {"ok": False, "error": "boom"}
            else:
                out[s] = {"ok": True, "price": 10.0,
                          "regularMarketPreviousClose": 9.9, "name": s}
        return out

    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=flaky,
                            symbols="SI=F,ES=F,NQ=F,NG=F,ETH-USD,^TNX,"
                                   "^FVX,^VIX,EURUSD=X,GC=F,CL=F,BTC-USD")
    snap = mon.snapshot()
    assert snap["ok"] is True           # fail-soft: monitor survives
    live = [s for s, a in snap["assets"].items() if a.get("live")]
    assert len(live) == 9


def test_monitor_batches_at_most_6(tmp_path):
    calls = []

    def counting(syms):
        calls.append(list(syms))
        return {s: {"ok": True, "price": 1.0,
                    "regularMarketPreviousClose": 1.0, "name": s}
                for s in syms}

    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=counting, all=True)
    mon.snapshot()
    assert calls and all(len(c) <= 6 for c in calls)
    assert sum(len(c) for c in calls) == 24


def test_correlation_matrix_24x24_symmetric(tmp_path):
    def fetch(syms):
        return {s: {"ok": True, "price": 100.0,
                    "regularMarketPreviousClose": 100.0, "name": s}
                for s in syms}

    mon = MultiAssetMonitor(data_root=tmp_path, fetcher=fetch, all=True)
    # inject deterministic daily closes for every symbol
    rng = random.Random(3)
    closes = {}
    for e in registry.UNIVERSE:
        s = e["symbol"]
        v = 100.0
        series = {}
        for d in range(300):
            v *= 1.0 + rng.gauss(0, 0.01)
            series[f"2026-{(d // 28) + 1:02d}-{(d % 28) + 1:02d}"] = v
        closes[s] = series
    mon._daily_closes = closes        # bypass network on the correlation path
    t0 = time.monotonic()
    out = mon.compute_correlation(window=30)
    dt = time.monotonic() - t0
    m = out["matrix"]
    syms = out["symbols"]
    assert len(syms) == 24
    # matrix is keyed by SYMBOL: matrix[sym_i][sym_j]
    assert len(m) == 24
    for si in syms:
        assert m[si][si] == pytest.approx(1.0, abs=1e-9)
        for sj in syms:
            assert m[si][sj] == pytest.approx(m[sj][si], abs=1e-12)
    assert dt < 15.0                  # mocked data plane completes fast


# ------------------------------------------------------------ Ledoit–Wolf
def _factor_series(n, k, blocks=None, seed=11):
    """Columns from common/blocked factor structure (constant-corr-ish
    when blocks=None: one common factor; block world otherwise)."""
    rng = random.Random(seed)
    z = [rng.gauss(0, 1) for _ in range(n)]
    zs = {}
    nb = blocks or 1
    for b in range(nb):
        zs[b] = [rng.gauss(0, 1) for _ in range(n)]
    cols = []
    for i in range(k):
        base = z if blocks is None else zs[i * nb // k]
        beta = 0.9
        cols.append([beta * base[t] + 0.05 * rng.gauss(0, 1)
                     for t in range(n)])
    return cols


def test_lw_constant_correlation_world_shrinks_hard():
    cols = _factor_series(3000, 5)            # one common factor
    lw = pf.ledoit_wolf_shrinkage(cols)
    assert lw["intensity"] > 0.9              # target explains the world


def test_lw_block_world_shrinks_barely():
    cols = _factor_series(3000, 6, blocks=2)  # 2 independent blocks
    lw = pf.ledoit_wolf_shrinkage(cols)
    assert lw["intensity"] < 0.05             # target misses the structure


def test_lw_deterministic():
    cols = _factor_series(500, 4)
    assert pf.ledoit_wolf_shrinkage(cols) == pf.ledoit_wolf_shrinkage(cols)


def test_lw_convex_combination_is_exact():
    cols = _factor_series(400, 4)
    lw = pf.ledoit_wolf_shrinkage(cols)
    a = lw["intensity"]
    S = pf._cov_matrix(cols)
    F = lw["target"]
    for i in range(4):
        for j in range(4):
            want = a * F[i][j] + (1 - a) * S[i][j]
            assert lw["cov"][i][j] == pytest.approx(want, abs=1e-15)


def test_lw_target_is_symmetric_and_keeps_variances():
    cols = _factor_series(200, 4)
    lw = pf.ledoit_wolf_shrinkage(cols)
    S = pf._cov_matrix(cols)
    F = lw["target"]
    for i in range(4):
        assert F[i][i] == pytest.approx(S[i][i], abs=1e-18)
        for j in range(4):
            assert F[i][j] == pytest.approx(F[j][i], abs=1e-18)


def test_lw_intensity_bounded():
    cols = _factor_series(100, 8)
    lw = pf.ledoit_wolf_shrinkage(cols)
    assert 0.0 <= lw["intensity"] <= 1.0


def test_lw_single_asset_degenerate():
    lw = pf.ledoit_wolf_shrinkage([[0.01, 0.02, 0.01]])
    assert lw["intensity"] == 0.0
    assert lw["cov"][0][0] == pytest.approx(
        pf._cov_matrix([[0.01, 0.02, 0.01]])[0][0])


# ----------------------------------------------------- exact MV (PGD)
def _mv_data():
    return {'A': [0.012, -0.008, 0.015, 0.003, -0.011, 0.019, -0.004,
                  0.009, 0.006, -0.013] * 8,
            'B': [0.004, 0.002, -0.001, 0.005, 0.003, -0.002, 0.001,
                  0.004, 0.002, 0.0] * 8,
            'C': [-0.006, 0.009, 0.011, -0.003, 0.007, -0.009, 0.013,
                  0.002, -0.008, 0.005] * 8}


def _dense_grid_best(ret, cap, lam=2.0, steps=40):
    out = pf.mean_variance(ret, max_weight=cap, lambda_risk=lam)
    syms = out["symbols"]
    mu = [out["expected_returns"][s] for s in syms]
    _, cols = pf._prepare(ret)
    cov = pf._cov_matrix(cols)
    k = len(syms)

    def obj(w):
        return (sum(w[i] * mu[i] for i in range(k))
                - lam * sum(w[i] * cov[i][j] * w[j]
                            for i in range(k) for j in range(k)))

    best = -math.inf
    grids = [x / steps for x in range(0, int(cap * steps) + 1)]
    for a in grids:
        for b in grids:
            c = 1.0 - a - b
            if c < -1e-12 or c > cap + 1e-12:
                continue
            best = max(best, obj([a, b, c]))
    return best, out


def test_mv_exact_matches_dense_grid_3_assets():
    best, out = _dense_grid_best(_mv_data(), cap=0.4)
    assert out["objective"] >= best - 1e-6      # PGD ≥ grid optimum
    assert out["objective"] <= best + 1e-4      # ...and within tolerance


def test_mv_exact_deterministic_without_seed():
    a = pf.mean_variance(_mv_data(), max_weight=0.4, seed=1)
    b = pf.mean_variance(_mv_data(), max_weight=0.4, seed=99)
    # seed is accepted for signature compat and echoed, but the algorithm
    # is seed-free — weights/objective must be identical
    assert a["weights"] == b["weights"]
    assert a["objective"] == b["objective"]


def test_mv_exact_constraints_hold():
    out = pf.mean_variance(_mv_data(), max_weight=0.4)
    w = list(out["weights"].values())
    assert sum(w) == pytest.approx(1.0, abs=1e-12)
    assert all(x >= -1e-15 for x in w)
    assert max(w) <= 0.4 + 1e-12


def test_mv_exact_convergence_reported():
    out = pf.mean_variance(_mv_data(), max_weight=0.4)
    assert out["converged"] is True
    assert out["algorithm"] == "projected_gradient"
    assert out["n_iterations"] >= 1


def test_mv_exact_scales_to_12_assets():
    rng = random.Random(5)
    ret = {}
    for i in range(12):
        ret[f"S{i}"] = [rng.gauss(0.0005 * (12 - i), 0.002)
                        for _ in range(300)]
    # truly dominant asset: highest mu AND lowest vol by construction
    ret["S0"] = [0.006 + rng.gauss(0, 0.0002) for _ in range(300)]
    out = pf.mean_variance(ret, max_weight=0.4, lambda_risk=2.0)
    assert out["converged"] is True
    w = out["weights"]
    assert w["S0"] == pytest.approx(0.4, abs=1e-6)   # dominant hits the cap
    assert sum(w.values()) == pytest.approx(1.0, abs=1e-12)


def test_mv_with_ledoit_wolf_cov():
    out = pf.mean_variance(_mv_data(), max_weight=0.4,
                           cov_method="ledoit_wolf")
    assert out["ok"] is True
    assert out["cov_method"] == "ledoit_wolf"
    assert 0.0 <= out["shrink_intensity"] <= 1.0
    assert sum(out["weights"].values()) == pytest.approx(1.0, abs=1e-12)


def test_rp_with_ledoit_wolf_cov():
    out = pf.risk_parity(_mv_data(), cov_method="ledoit_wolf")
    assert out["ok"] is True
    assert out["cov_method"] == "ledoit_wolf"
    rc = list(out["risk_contributions"].values())
    assert sum(rc) == pytest.approx(1.0, abs=1e-9)


def test_hrp_with_ledoit_wolf_cov():
    out = pf.hierarchical_risk_parity(_mv_data(), cov_method="lw")
    assert out["ok"] is True
    assert out["cov_method"] == "ledoit_wolf"


def test_unknown_cov_method_rejected():
    with pytest.raises(ValueError):
        pf.mean_variance(_mv_data(), cov_method="bogus")


def test_optimize_passes_cov_method_through():
    out = pf.optimize(_mv_data(), "mv", cov_method="ledoit_wolf",
                      max_weight=0.4)
    assert out["ok"] is True and out["cov_method"] == "ledoit_wolf"


def test_lw_vs_sample_changes_solution():
    """Shrinkage actually changes the covariance the optimizer sees — on
    this pinned instance (seed 10, heterogeneous vols, n=35: the sample
    Σ is noisy enough that full LW shrinkage re-ranks assets) the LW
    optimum sits at a different point than the sample optimum."""
    rng = random.Random(10)
    ret = {}
    for i in range(6):
        ret[f"A{i}"] = [rng.gauss(0.001 * (i + 1), 0.005 + 0.004 * ((i * 7) % 6))
                        for _ in range(35)]
    s = pf.mean_variance(ret, max_weight=0.4)
    lw = pf.mean_variance(ret, max_weight=0.4, cov_method="ledoit_wolf")
    assert lw["shrink_intensity"] > 0.3        # it actually shrinks
    l1 = sum(abs(a - b) for a, b in
             zip(s["weights"].values(), lw["weights"].values()))
    assert l1 > 0.01                           # ...and the optimum moves


# ------------------------- R4 EXIT-critic D1/D2 regressions ----------------
def test_stress_replay_cash_sleeve_is_flat_never_fetched():
    """D1: 'CASH' must never resolve to the NASDAQ ticker CASH — it is a
    flat sleeve: 0%/day, listed in `flat`, absent from fetch calls."""
    from gold_desk.risk import metrics as rm
    fetched = []

    def fetch(sym, start, end, **kw):
        fetched.append(sym)
        return {"2020-02-19": 100.0, "2020-02-20": 90.0}   # −10% if used

    positions = [{"symbol": "SPY", "weight": 0.5},
                 {"symbol": "CASH", "weight": 0.5}]
    out = rm.stress_replay(positions, "covid_2020", fetch=fetch)
    assert out["ok"] is True
    assert "CASH" not in fetched                     # never hit Yahoo
    assert out["flat"] == ["CASH"]                   # surfaced as flat
    # SPY −10% on half the book → −5% cumulative; CASH contributes 0
    assert out["cumulative"] == pytest.approx(-0.05, abs=1e-12)


def test_stress_replay_flat_assets_listed_not_unshocked():
    from gold_desk.risk import metrics as rm
    out = rm.stress_replay(
        [{"symbol": "SPY", "weight": 0.4},
         {"symbol": "USDT", "weight": 0.3},
         {"symbol": "MISSING-1", "weight": 0.3}],
        "covid_2020",
        bars_by_symbol={"SPY": {"2020-02-19": 100.0, "2020-02-20": 80.0},
                        "MISSING-1": {}})
    assert "USDT" in out["flat"]                     # modeled flat
    assert "USDT" not in out["unshocked"]            # NOT "unmodeled"
    assert "MISSING-1" in out["unshocked"]           # genuinely unmodeled


def test_mv_fista_exact_vs_slsqp_at_k24():
    """D2: the exit critic measured plain PGD stalling ≤1.83% relative
    below SLSQP at k=24. FISTA + adaptive restart + 20000-iter cap must
    match SLSQP to ~machine precision on the same kind of instance.
    Skipped when scipy is absent (stdlib-only deploys)."""
    scipy_minimize = pytest.importorskip(
        "scipy.optimize", reason="scipy SLSQP reference").minimize
    rng = random.Random(7)
    ret = {}
    for i in range(24):
        ret[f"S{i:02d}"] = [rng.gauss(0.0004 * (24 - i), 0.012)
                            for _ in range(250)]
    out = pf.mean_variance(ret, max_weight=0.4, lambda_risk=2.0)
    assert out["converged"] is True
    syms = out["symbols"]
    mu = [out["expected_returns"][s] for s in syms]
    _, cols = pf._prepare(ret)
    cov = pf._cov_matrix(cols)
    k = 24
    lam = 2.0

    def negobj(w):
        return -(sum(w[i] * mu[i] for i in range(k))
                 - lam * sum(w[i] * cov[i][j] * w[j]
                             for i in range(k) for j in range(k)))

    res = scipy_minimize(negobj, [1.0 / k] * k, method="SLSQP",
                         bounds=[(0.0, 0.4)] * k,
                         constraints=[{"type": "eq",
                                       "fun": lambda w: sum(w) - 1.0}],
                         options={"maxiter": 500, "ftol": 1e-14})
    rel = (res.fun * -1 - out["objective"]) / max(abs(res.fun), 1e-18)
    assert rel < 1e-6                                # SLSQP-level exact
