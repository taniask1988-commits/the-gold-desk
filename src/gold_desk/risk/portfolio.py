"""R3-3 BUILD 5a — portfolio construction: mean-variance, risk parity (ERC)
and hierarchical risk parity, in pure stdlib.

Three optimizers over the weight simplex {w ≥ 0, Σw = 1}:

* `mean_variance` — seed-pinned random + grid search (~2000 candidates)
  maximizing μᵀw − λ·wᵀΣw (λ default 2.0). Candidates are projected onto
  the simplex intersected with the per-asset box [0, max_weight]
  (max_weight default 0.4), so every returned vector satisfies the cap
  exactly. Deterministic: `random.Random(seed)` with a fixed candidate
  ORDER (equal-weight → grid → cap corners → random draws), first-wins
  ties.

* `risk_parity` — Spinu (2013) convex formulation of Equal Risk
  Contribution: minimize ½xᵀΣx − Σᵢ bᵢ·ln xᵢ (bᵢ = 1/n) by cyclical
  coordinate descent on the exact per-coordinate quadratic, i.e.
  xᵢ ← (−cᵢ + √(cᵢ² + 4Σᵢᵢbᵢ)) / (2Σᵢᵢ) with cᵢ = Σⱼ≠ᵢ Σᵢⱼxⱼ. At the
  optimum xᵢ(Σx)ᵢ = bᵢ — equal risk contributions — then w = x/Σx.
  Convergence tol 1e-6 on maxᵢ|xᵢ(Σx)ᵢ − bᵢ|, max 500 sweeps. For a
  DIAGONAL Σ the fixed point is xᵢ = √(bᵢ/Σᵢᵢ), i.e. wᵢ ∝ 1/σᵢ exactly.

* `hierarchical_risk_parity` — López de Prado (2016): correlation
  distance dᵢⱼ = √(2(1−ρᵢⱼ)), single-linkage agglomerative clustering
  (naive O(n³), deterministic first-found tie-break), quasi-diagonal
  leaf ordering, then top-down recursive bisection splitting each
  cluster list in half and sharing weight between halves by inverse
  cluster variance (α = 1 − var_L/(var_L+var_R)). A perfectly-correlated
  pair next to an uncorrelated asset merges FIRST, so the pair sits in
  one half of the top split and shares ≈ one asset's weight.

All three take {symbol: list[float] returns} and return
{ok, method, symbols, weights, portfolio_vol, risk_contributions
(fraction of portfolio variance per asset — sums to 1),
diversification_ratio = (Σwᵢσᵢ)/σp, expected_returns, volatilities,
n_observations} plus method-specific extras.

Edge cases fail gracefully (never raise): empty asset map, series with
< 2 usable observations, zero-variance assets (RP/HRP — the covariance
is genuinely degenerate for them; MV tolerates a zero-vol asset because
the objective stays well-defined and max_weight bounds it), and
non-convergent ERC iterations (singular covariance, e.g. a perfectly
anti-correlated pair — the Spinu objective is unbounded below along the
null space). Errors come back as {"ok": False, "error": ...}.

Law boundary: research/education tooling like metrics.py — NOT wired
into the orchestrator's decision loop (constitution-gated).
"""
from __future__ import annotations

import math
import random

MV_LAMBDA = 2.0          # default risk-aversion λ in μᵀw − λ·wᵀΣw
MV_CANDIDATES = 2000     # ~2000-candidate random/grid search
MV_SEED = 7              # seed-pinned search: same seed → identical weights
MAX_WEIGHT = 0.4         # default per-asset cap (long-only desk discipline)
RP_TOL = 1e-6            # ERC convergence tolerance
RP_MAX_ITER = 500        # max coordinate-descent sweeps

METHODS = ("mv", "rp", "hrp")
METHOD_ALIASES = {
    "mv": "mv", "mean_variance": "mv", "meanvariance": "mv",
    "rp": "rp", "erc": "rp", "risk_parity": "rp",
    "hrp": "hrp", "hierarchical_risk_parity": "hrp",
}


# ------------------------------------------------------------------ prepare
def _prepare(returns_by_symbol: dict) -> tuple[list[str], list[list[float]]]:
    """Validate + tail-align. Returns (symbols sorted, columns) where
    columns[i] is the aligned return series of symbols[i]. Raises
    ValueError with an honest message on any degenerate input."""
    if not isinstance(returns_by_symbol, dict) or not returns_by_symbol:
        raise ValueError("no assets: pass {symbol: [returns]}")
    symbols = sorted(returns_by_symbol)
    columns: list[list[float]] = []
    for sym in symbols:
        raw = returns_by_symbol[sym]
        if raw is None:
            raw = []
        series = [float(x) for x in raw]
        if len(series) < 2:
            raise ValueError(f"asset {sym}: needs ≥2 return observations "
                             f"(got {len(series)})")
        columns.append(series)
    n = min(len(col) for col in columns)
    if n < 2:
        raise ValueError("aligned sample < 2 observations across assets")
    return symbols, [col[len(col) - n:] for col in columns]


def _mean_vector(columns: list[list[float]]) -> list[float]:
    return [sum(col) / len(col) for col in columns]


def _cov_matrix(columns: list[list[float]]) -> list[list[float]]:
    """Sample covariance (ddof=1) — the estimator the risk package uses
    everywhere (metrics.sample_stdev is the diagonal)."""
    k = len(columns)
    n = len(columns[0])
    mu = _mean_vector(columns)
    cov = [[0.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i, k):
            s = sum((columns[i][t] - mu[i]) * (columns[j][t] - mu[j])
                    for t in range(n))
            v = s / (n - 1) if n > 1 else 0.0
            cov[i][j] = v
            cov[j][i] = v
    return cov


def _corr_matrix(cov: list[list[float]]) -> list[list[float]]:
    k = len(cov)
    vols = [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]
    corr = [[1.0] * k for _ in range(k)]
    for i in range(k):
        for j in range(i + 1, k):
            denom = vols[i] * vols[j]
            r = 0.0 if denom <= 0.0 else cov[i][j] / denom
            r = max(-1.0, min(1.0, r))
            corr[i][j] = r
            corr[j][i] = r
    return corr, vols


def _portfolio_stats(weights: list[float], cov: list[list[float]]
                     ) -> tuple[float, list[float], float]:
    """(portfolio vol, per-asset risk-contribution FRACTIONS, DR).

    RC_i = wᵢ(Σw)ᵢ is asset i's contribution to portfolio VARIANCE; the
    fraction RC_i/σp² sums to 1 across assets (conservation). The
    diversification ratio DR = (Σwᵢσᵢ)/σp is 1.0 for a single asset and
    > 1 whenever combining assets reduces risk below the weighted
    average volatility (any diversification benefit).
    """
    k = len(weights)
    sigma_w = [sum(cov[i][j] * weights[j] for j in range(k))
               for i in range(k)]
    var_p = sum(weights[i] * sigma_w[i] for i in range(k))
    vol = math.sqrt(max(var_p, 0.0))
    if var_p > 0.0:
        rc = [weights[i] * sigma_w[i] / var_p for i in range(k)]
    else:
        rc = [1.0 / k if k else 0.0] * k
    vols = [math.sqrt(max(cov[i][i], 0.0)) for i in range(k)]
    weighted_vol = sum(weights[i] * vols[i] for i in range(k))
    # zero-variance portfolio (perfect hedge) → DR undefined: None, not a
    # misleading 1.0 — surfaced as n/a by the CLI/web panels
    dr = weighted_vol / vol if vol > 0.0 else None
    return vol, rc, dr


def _result(method: str, symbols: list[str], columns: list[list[float]],
            weights: list[float], cov: list[list[float]],
            mu: list[float], **extra) -> dict:
    vol, rc, dr = _portfolio_stats(weights, cov)
    vols = [math.sqrt(max(cov[i][i], 0.0)) for i in range(len(symbols))]
    out = {
        "ok": True,
        "method": method,
        "symbols": list(symbols),
        "n_observations": len(columns[0]) if columns else 0,
        "weights": {s: w for s, w in zip(symbols, weights)},
        "expected_returns": {s: m for s, m in zip(symbols, mu)},
        "volatilities": {s: v for s, v in zip(symbols, vols)},
        "portfolio_vol": vol,
        "risk_contributions": {s: c for s, c in zip(symbols, rc)},
        "diversification_ratio": dr,
        "expected_return": sum(w * m for w, m in zip(weights, mu)),
    }
    out.update(extra)
    return out


# ------------------------------------------------------------------ helpers
def _project_capped_simplex(w: list[float], cap: float) -> list[float]:
    """Map an arbitrary non-negative vector onto {x : 0 ≤ xᵢ ≤ cap, Σx = 1}
    by bisection on the shift t in xᵢ = clamp(wᵢ − t, 0, cap) — the exact
    Euclidean projection when Σw = 1. Deterministic, no randomness."""
    k = len(w)
    if k == 0:
        return []
    w = [max(0.0, float(x)) for x in w]
    total = sum(w)
    if total <= 0.0:
        w = [1.0] * k
        total = float(k)
    w = [x / total for x in w]                      # now Σw = 1

    def shifted_sum(t: float) -> float:
        return sum(max(0.0, min(cap, x - t)) for x in w)

    if shifted_sum(0.0) <= 1.0 + 1e-15:
        # already inside the box (sum of clamps ≥ 1 means clipping alone
        # cannot reach 1 → need a negative shift; otherwise t ≥ 0)
        pass
    lo, hi = -2.0, 2.0
    # invariant: shifted_sum(lo) ≥ 1 ≥ shifted_sum(hi)
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if shifted_sum(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2.0
    x = [max(0.0, min(cap, v - t)) for v in w]
    # distribute the ≤1e-12 residual on a free coordinate (exact Σ=1)
    resid = 1.0 - sum(x)
    if abs(resid) > 1e-15:
        free = [i for i, v in enumerate(x) if resid > 0 and v < cap - 1e-12]
        if free:
            i = max(free, key=lambda i: cap - x[i])
            x[i] = min(cap, x[i] + resid)
    return x


def _simplex_grid(k: int, step: float) -> list[list[float]]:
    """All weight vectors on the simplex with coordinates that are
    multiples of `step` (compositions). Only used for small k."""
    units = int(round(1.0 / step))
    out: list[list[float]] = []

    def rec(prefix: list[int], remaining: int, slots: int):
        if slots == 1:
            out.append([float(v) / units for v in prefix + [remaining]])
            return
        for take in range(remaining + 1):
            rec(prefix + [take], remaining - take, slots - 1)

    rec([], units, k)
    return out


# ------------------------------------------------------------------ MV
def mean_variance(returns_by_symbol: dict, lambda_risk: float = MV_LAMBDA,
                  max_weight: float = MAX_WEIGHT,
                  n_candidates: int = MV_CANDIDATES, seed: int = MV_SEED
                  ) -> dict:
    """Mean-variance: seed-pinned random/grid search over the capped
    weight simplex maximizing μᵀw − λ·wᵀΣw."""
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    if k == 1:                      # a single asset IS the portfolio
        cov = _cov_matrix(columns)
        return _result("mv", symbols, columns, [1.0], cov,
                       _mean_vector(columns), lambda_risk=lambda_risk,
                       max_weight=max_weight, n_candidates=1, seed=seed,
                       objective=_mean_vector(columns)[0])
    if max_weight * k < 1.0 - 1e-12:
        raise ValueError(f"max_weight {max_weight} infeasible for {k} "
                         f"assets (needs ≥ {1.0 / k:.4f})")
    mu = _mean_vector(columns)
    cov = _cov_matrix(columns)

    def objective(w: list[float]) -> float:
        ret = sum(w[i] * mu[i] for i in range(k))
        var = sum(w[i] * cov[i][j] * w[j] for i in range(k) for j in range(k))
        return ret - lambda_risk * var

    candidates: list[list[float]] = []
    # 1) equal weight
    candidates.append([1.0 / k] * k)
    # 2) deterministic grid (small k only)
    if k <= 3:
        candidates.extend(_simplex_grid(k, 0.05))
    elif k <= 6:
        candidates.extend(_simplex_grid(k, 0.1))
    # 3) cap corners: one asset at the cap, the rest spread equally
    if max_weight < 1.0:
        for i in range(k):
            corner = [(1.0 - max_weight) / (k - 1)] * k if k > 1 else [1.0]
            corner[i] = max_weight
            candidates.append(corner)
    # 4) seed-pinned random draws (Dirichlet(1) via exponentials)
    rng = random.Random(seed)
    while len(candidates) < n_candidates:
        draws = [rng.expovariate(1.0) for _ in range(k)]
        candidates.append(draws)
    # project every candidate onto the capped simplex and evaluate
    best_w: list[float] | None = None
    best_obj = -math.inf
    for cand in candidates:
        w = cand if (max_weight >= 1.0 and abs(sum(cand) - 1.0) < 1e-12
                     and min(cand) >= 0.0) \
            else _project_capped_simplex(cand, max_weight)
        obj = objective(w)
        if obj > best_obj + 1e-15:                    # strict: first wins ties
            best_obj, best_w = obj, w
    assert best_w is not None
    return _result("mv", symbols, columns, best_w, cov, mu,
                   lambda_risk=lambda_risk, max_weight=max_weight,
                   n_candidates=len(candidates), seed=seed,
                   objective=best_obj)


# ------------------------------------------------------------------ RP
def risk_parity(returns_by_symbol: dict, tol: float = RP_TOL,
                max_iter: int = RP_MAX_ITER) -> dict:
    """Risk parity / ERC via Spinu's convex formulation solved by cyclical
    coordinate descent. Converges to equal risk contributions; for
    diagonal Σ the answer is exactly wᵢ ∝ 1/σᵢ."""
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    cov = _cov_matrix(columns)
    mu = _mean_vector(columns)
    if k == 1:                      # trivially equal risk contribution
        return _result("rp", symbols, columns, [1.0], cov, mu,
                       iterations=0, converged=True, tol=tol)
    for i, sym in enumerate(symbols):
        if cov[i][i] <= 0.0:
            raise ValueError(f"asset {sym}: zero variance — equal risk "
                             "contribution is undefined")
    b = [1.0 / k] * k
    # diagonal fixed point start: xᵢ = √(bᵢ/Σᵢᵢ) (exact for diagonal Σ)
    x = [math.sqrt(b[i] / cov[i][i]) for i in range(k)]
    converged = False
    iterations = 0
    for it in range(1, max_iter + 1):
        iterations = it
        for i in range(k):
            c = sum(cov[i][j] * x[j] for j in range(k) if j != i)
            a = cov[i][i]
            # ∂/∂xᵢ [½xᵀΣx − bᵢ ln xᵢ] = 0 → a·xᵢ² + c·xᵢ − bᵢ = 0
            x[i] = (-c + math.sqrt(c * c + 4.0 * a * b[i])) / (2.0 * a)
        err = 0.0
        for i in range(k):
            sx = sum(cov[i][j] * x[j] for j in range(k))
            err = max(err, abs(x[i] * sx - b[i]))
        if err < tol:
            converged = True
            break
    if not converged:
        raise ValueError(f"risk parity did not converge in {max_iter} "
                         "iterations (covariance singular/degenerate)")
    total = sum(x)
    weights = [xi / total for xi in x]
    return _result("rp", symbols, columns, weights, cov, mu,
                   iterations=iterations, converged=True, tol=tol)


# ------------------------------------------------------------------ HRP
def _single_linkage_order(corr: list[list[float]]) -> tuple[list[int], list[dict]]:
    """Agglomerative single-linkage clustering on the correlation distance
    dᵢⱼ = √(2(1−ρᵢⱼ)). Returns (quasi-diagonal leaf order, merge log).
    Deterministic: the first-found closest pair wins ties; a merge always
    concatenates left-cluster + right-cluster (lower list index first)."""
    k = len(corr)
    dist = [[math.sqrt(max(0.0, 2.0 * (1.0 - corr[i][j])))
             for j in range(k)] for i in range(k)]
    clusters: list[list[int]] = [[i] for i in range(k)]
    merges: list[dict] = []
    while len(clusters) > 1:
        best: tuple[float, int, int] | None = None
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = min(dist[a][b] for a in clusters[i] for b in clusters[j])
                if best is None or d < best[0] - 1e-15:
                    best = (d, i, j)
        d, i, j = best  # type: ignore[misc]
        merges.append({
            "clusters": [list(clusters[i]), list(clusters[j])],
            "distance": d,
        })
        merged = clusters[i] + clusters[j]
        clusters = [c for idx, c in enumerate(clusters) if idx not in (i, j)]
        clusters.append(merged)
    return clusters[0], merges


def _cluster_variance(idx: list[int], cov: list[list[float]]) -> float:
    """Variance of a cluster weighted by inverse-variance weights inside
    it (López de Prado's clCov helper). Zero-variance cluster → 0.0."""
    if not idx:
        return 0.0
    inv = []
    for i in idx:
        v = cov[i][i]
        if v <= 0.0:
            return 0.0
        inv.append(1.0 / v)
    total = sum(inv)
    w = [v / total for v in inv]
    return sum(w[a] * cov[idx[a]][idx[b]] * w[b]
               for a in range(len(idx)) for b in range(len(idx)))


def hierarchical_risk_parity(returns_by_symbol: dict) -> dict:
    """HRP: correlation-distance single linkage → quasi-diagonalization →
    recursive bisection with inverse cluster-variance splits."""
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    cov = _cov_matrix(columns)
    mu = _mean_vector(columns)
    if k == 1:                      # one leaf: weight 1.0, no bisection
        return _result("hrp", symbols, columns, [1.0], cov, mu,
                       quasi_diagonal_order=list(symbols), merges=[])
    for i, sym in enumerate(symbols):
        if cov[i][i] <= 0.0:
            raise ValueError(f"asset {sym}: zero variance — correlation "
                             "distance is undefined")
    corr, _ = _corr_matrix(cov)
    order, merges = _single_linkage_order(corr)
    weights = [1.0] * k
    stack: list[list[int]] = [list(order)]
    while stack:
        cluster = stack.pop()
        if len(cluster) <= 1:
            continue
        half = len(cluster) // 2
        left, right = cluster[:half], cluster[half:]
        var_l = _cluster_variance(left, cov)
        var_r = _cluster_variance(right, cov)
        if var_l + var_r <= 0.0:
            alpha = 0.5
        else:
            alpha = 1.0 - var_l / (var_l + var_r)
        for i in left:
            weights[i] *= alpha
        for i in right:
            weights[i] *= 1.0 - alpha
        stack.append(left)
        stack.append(right)
    return _result("hrp", symbols, columns, weights, cov, mu,
                   quasi_diagonal_order=[symbols[i] for i in order],
                   merges=[{"clusters": [[symbols[i] for i in c]
                                         for c in m["clusters"]],
                            "distance": m["distance"]} for m in merges])


# ------------------------------------------------------------------ dispatch
def optimize(returns_by_symbol: dict, method: str = "mv", **kwargs) -> dict:
    """Dispatch to one of the three optimizers. Never raises on data
    problems — degenerate inputs come back as {"ok": False, "error": ...}
    with the method echoed for honest CLI/web surfacing."""
    canonical = METHOD_ALIASES.get(str(method).lower())
    if canonical is None:
        return {"ok": False, "method": method,
                "error": f"unknown method {method!r} "
                         f"(choose from {list(METHODS)})"}
    try:
        if canonical == "mv":
            return mean_variance(returns_by_symbol, **kwargs)
        if canonical == "rp":
            return risk_parity(returns_by_symbol, **kwargs)
        return hierarchical_risk_parity(returns_by_symbol, **kwargs)
    except ValueError as exc:
        return {"ok": False, "method": canonical, "error": str(exc)}
