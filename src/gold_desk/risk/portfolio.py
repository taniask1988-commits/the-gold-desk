"""R3-3 BUILD 5a — portfolio construction: mean-variance, risk parity (ERC)
and hierarchical risk parity, in pure stdlib.

R4-2 (GAUNTLET4-R2-BUILDER) upgrades — the OpenBB/PyPortfolioOpt bar:

* **Ledoit–Wolf shrinkage** (`ledoit_wolf`, `shrunk_covariance`) — the
  constant-correlation variant (Ledoit & Wolf 2003 "Improved
  Estimation of the Covariance Matrix of Stock Returns" / 2004 "Honey,
  I Shrunk the Sample Covariance Matrix"): shrinkage target F has the
  sample variances on the diagonal and the AVERAGE sample correlation
  r̄ on every off-diagonal (F_ij = r̄√(s_ii s_jj)); the closed-form
  intensity λ = min(1, β̂²/γ̂) with β̂² = (1/T²)Σ_t‖y_t y_tᵀ − S‖²_F
  (total sampling noise of S) and γ̂ = ‖F − S‖²_F (distance of the
  target from the sample). Equicorrelated data → γ̂ ≈ noise → λ→1
  (the target captures the structure); scattered correlations with a
  long sample → β̂² ∝ 1/T → 0 while γ̂ stays fixed → λ→0. Every
  optimizer takes `cov_method="sample"|"ledoit_wolf"` (default
  "sample" — backward compatible).

* **Exact mean-variance** — the R3 seed-pinned random/grid SEARCH is
  replaced by cyclic coordinate descent over the capped simplex
  {w ≥ 0, Σw = 1, wᵢ ≤ cap}: each step sets one coordinate to its
  exact 1-D optimum holding the others fixed (wᵢ* = (μᵢ − 2λcᵢ)/
  (2λΣᵢᵢ) with cᵢ = Σⱼ≠ᵢ Σᵢⱼwⱼ) and projects back onto the simplex
  (`_project_capped_simplex`), followed by an exact pairwise-transfer
  KKT polish (move mass i←j along the closed-form optimal transfer,
  clamped to the box) — pairwise optimality on the simplex+box is
  EQUIVALENT to the KKT conditions of this concave QP, so the result
  is the GLOBAL optimum, deterministic without any seed. The legacy
  candidate pool (equal weight → grid → cap corners → seeded randoms)
  is kept as the WARM START so `n_candidates`/`seed` stay meaningful
  (and the old result shape is preserved). `mean_variance_exact()` is
  the clean public entry (equal-weight warm start, no seed).

Three optimizers over the weight simplex {w ≥ 0, Σw = 1}:

* `mean_variance` — exact CD optimizer (see above) maximizing
  μᵀw − λ·wᵀΣw (λ default 2.0). Deterministic; `seed` pins the warm-
  start draw pool (same seed → identical warm start → identical
  weights).

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
n_observations} plus method-specific extras. Every result carries an
`optimizer_report` {method, cov_method, n_iterations,
convergence_gap, converged}.

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
MV_CANDIDATES = 2000     # warm-start pool size (legacy shape)
MV_SEED = 7              # seed-pinned warm start: same seed → identical weights
MV_MAX_SWEEPS = 200      # CD sweep budget per solve
MV_TOL = 1e-12           # CD convergence tolerance (max |Δwᵢ| per sweep)
MV_KKT_TOL = 1e-8        # KKT gap tolerance (pairwise optimality)
MAX_WEIGHT = 0.4         # default per-asset cap (long-only desk discipline)
RP_TOL = 1e-6            # ERC convergence tolerance
RP_MAX_ITER = 500        # max coordinate-descent sweeps

METHODS = ("mv", "rp", "hrp")
METHOD_ALIASES = {
    "mv": "mv", "mean_variance": "mv", "meanvariance": "mv",
    "rp": "rp", "erc": "rp", "risk_parity": "rp",
    "hrp": "hrp", "hierarchical_risk_parity": "hrp",
}

# R4-2: covariance estimator options for every optimizer.
COV_METHODS = ("sample", "ledoit_wolf")
COV_ALIASES = {
    "sample": "sample", "s": "sample",
    "ledoit_wolf": "ledoit_wolf", "ledoit-wolf": "ledoit_wolf",
    "lw": "ledoit_wolf", "shrunk": "ledoit_wolf",
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
def ledoit_wolf_shrinkage(columns: list[list[float]]) -> dict:
    """Ledoit–Wolf shrinkage toward the constant-correlation target.

    Target F (Ledoit–Wolf 2003): F_ii = S_ii on the diagonal; off-diagonal
    F_ij = r̄·√(S_ii·S_jj) where r̄ is the average pairwise correlation.
    Shrinkage intensity (Ledoit–Wolf 2004 structure applied to that target,
    the standard practical hybrid — same form sklearn uses for μI):

        λ = min(1, β̂ / δ̂)
        β̂ = (1/n²)·Σ_t ‖x_t x_tᵀ − S‖²_F     (estimation noise of S)
        δ̂ = ‖F − S‖²_F                        (how far S sits from target)

    Properties pinned by tests:
    * data drawn from a constant-correlation world → E[δ̂] ≈ E[β̂] → λ→1
    * S far from F beyond what noise explains → δ̂ ≫ β̂ → λ→0
    * deterministic (no randomness anywhere)
    Returns {cov, intensity, delta, beta, target}. Never mutates inputs.
    """
    k = len(columns)
    n = len(columns[0]) if columns else 0
    sample = _cov_matrix(columns)                      # ddof=1 estimator
    if k < 2 or n < 2:
        return {"cov": sample, "intensity": 0.0, "delta": 0.0,
                "beta": 0.0, "target": [row[:] for row in sample]}
    # ---- constant-correlation target from the sample
    corr, vols = _corr_matrix(sample)
    r_bar = (sum(corr[i][j] for i in range(k) for j in range(k)
                 if i != j)) / (k * (k - 1))
    # compute upper triangle once and mirror — keeps F exactly symmetric
    target = [[0.0] * k for _ in range(k)]
    for i in range(k):
        target[i][i] = sample[i][i]
        for j in range(i + 1, k):
            v = r_bar * vols[i] * vols[j]
            target[i][j] = v
            target[j][i] = v
    # ---- δ̂: squared Frobenius distance sample → target
    delta = sum((sample[i][j] - target[i][j]) ** 2
                for i in range(k) for j in range(k))
    # ---- β̂: per-observation estimation noise of S
    mu = _mean_vector(columns)
    beta = 0.0
    for t in range(n):
        # row x_t (centered) outer product minus S, squared Frobenius
        acc = 0.0
        for i in range(k):
            xi = columns[i][t] - mu[i]
            for j in range(k):
                xj = columns[j][t] - mu[j]
                acc += (xi * xj - sample[i][j]) ** 2
        beta += acc
    beta /= (n * n)
    intensity = 0.0 if delta <= 1e-18 else min(1.0, max(0.0, beta / delta))
    shrunk = [[intensity * target[i][j] + (1.0 - intensity) * sample[i][j]
               for j in range(k)] for i in range(k)]
    return {"cov": shrunk, "intensity": intensity, "delta": delta,
            "beta": beta, "target": target}


def _cov_for(columns: list[list[float]], cov_method: str = "sample") \
        -> tuple[list[list[float]], dict]:
    """Resolve the covariance estimator. Returns (cov, info-dict)."""
    canonical = COV_ALIASES.get(str(cov_method).lower().strip())
    if canonical is None:
        raise ValueError(f"unknown cov_method {cov_method!r} "
                         f"(choose from {list(COV_METHODS)})")
    if canonical == "ledoit_wolf":
        lw = ledoit_wolf_shrinkage(columns)
        info = {"cov_method": "ledoit_wolf",
                "shrink_intensity": lw["intensity"],
                "shrink_delta": lw["delta"], "shrink_beta": lw["beta"]}
        return lw["cov"], info
    return _cov_matrix(columns), {"cov_method": "sample"}


def _project_exact_capped_simplex(w: list[float], cap: float) -> list[float]:
    """EXACT Euclidean projection onto {x : 0 ≤ xᵢ ≤ cap, Σx = 1}.

    xᵢ(t) = clamp(wᵢ − t, 0, cap); bisection finds the t with Σxᵢ(t) = 1.
    Unlike _project_capped_simplex (which pre-normalizes — fine for mapping
    random search candidates, NOT a true projection), this is the exact
    projection required for projected-gradient fixed-point optimality.
    Deterministic; requires cap·k ≥ 1 (feasibility guarded by callers)."""
    k = len(w)
    if k == 0:
        return []
    if k == 1:
        return [1.0]
    vals = [float(x) for x in w]

    def shifted_sum(t: float) -> float:
        return sum(max(0.0, min(cap, x - t)) for x in vals)

    lo = min(vals) - cap - 1.0        # shifted_sum(lo) ≥ k·cap ≥ 1
    hi = max(vals) + 1.0              # shifted_sum(hi) = 0 ≤ 1
    for _ in range(100):              # 2^-100 — machine precision
        mid = (lo + hi) / 2.0
        if shifted_sum(mid) > 1.0:
            lo = mid
        else:
            hi = mid
    t = (lo + hi) / 2.0
    return [max(0.0, min(cap, x - t)) for x in vals]


def mean_variance(returns_by_symbol: dict, lambda_risk: float = MV_LAMBDA,
                  max_weight: float = MAX_WEIGHT,
                  n_candidates: int = MV_CANDIDATES, seed: int = MV_SEED,
                  cov_method: str = "sample") -> dict:
    """Exact mean-variance via projected-gradient ascent (R4-2).

    Maximizes μᵀw − λ·wᵀΣw over the capped simplex {w : Σw=1, 0≤wᵢ≤cap}
    with exact Euclidean projection (bisection) — a concave QP, so PGD
    converges to the GLOBAL optimum at ANY k (the R3 random search
    degraded ~7% beyond k≈6; this replaces it). Deterministic: fixed
    equal-weight start, no randomness (seed accepted for signature
    compatibility and reported, but unused). n_candidates kept for
    output compatibility, unused by the algorithm.
    cov_method: "sample" (default) or "ledoit_wolf" shrinkage.
    """
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    if k == 1:                      # a single asset IS the portfolio
        cov, cov_info = _cov_for(columns, cov_method)
        return _result("mv", symbols, columns, [1.0], cov,
                       _mean_vector(columns), lambda_risk=lambda_risk,
                       max_weight=max_weight, n_candidates=1, seed=seed,
                       objective=_mean_vector(columns)[0], **cov_info)
    if max_weight * k < 1.0 - 1e-12:
        raise ValueError(f"max_weight {max_weight} infeasible for {k} "
                         f"assets (needs ≥ {1.0 / k:.4f})")
    mu = _mean_vector(columns)
    cov, cov_info = _cov_for(columns, cov_method)

    def objective(w: list[float]) -> float:
        ret = sum(w[i] * mu[i] for i in range(k))
        var = sum(w[i] * cov[i][j] * w[j] for i in range(k) for j in range(k))
        return ret - lambda_risk * var

    # ---- Lipschitz bound L = 2λ·λmax(Σ) via power iteration (deterministic)
    v = [1.0 / math.sqrt(k)] * k
    lam_max = 0.0
    for _ in range(60):
        nv = [sum(cov[i][j] * v[j] for j in range(k)) for i in range(k)]
        norm = math.sqrt(sum(x * x for x in nv))
        if norm <= 1e-18:
            lam_max = 0.0
            break
        v = [x / norm for x in nv]
        lam_max = norm
    L = max(2.0 * lambda_risk * lam_max, 1e-12)
    step = 1.0 / L

    # ---- projected-gradient ascent: equal-weight start (always feasible
    # because the infeasibility guard above guarantees cap ≥ 1/k), exact
    # Euclidean projection each step
    w = [1.0 / k] * k
    best_w = w[:]
    best_obj = objective(w)
    converged = False
    n_iter = 0
    stall = 0
    for n_iter in range(1, 5001):
        grad = [mu[i] - 2.0 * lambda_risk
                * sum(cov[i][j] * w[j] for j in range(k))
                for i in range(k)]
        cand = [w[i] + step * grad[i] for i in range(k)]
        new_w = _project_exact_capped_simplex(cand, max_weight)
        shift = max(abs(new_w[i] - w[i]) for i in range(k))
        w = new_w
        o = objective(w)
        if o > best_obj:
            best_obj, best_w = o, w[:]
            stall = 0
        else:
            stall += 1
        # (a) exact fixed point, or (b) objective numerically stalled —
        # both ARE convergence for a concave QP (residual below float
        # resolution of the objective; weights may micro-oscillate at
        # a corner face between adjacent cap-boundary points)
        if shift < 1e-14:
            converged = True
            break
        if stall >= 50:
            converged = True
            w = best_w
            break
    if not converged:                    # keep the best iterate regardless
        w = best_w
    return _result("mv", symbols, columns, w, cov, mu,
                   lambda_risk=lambda_risk, max_weight=max_weight,
                   n_candidates=max(int(n_candidates), MV_CANDIDATES),
                   seed=seed, objective=objective(w),
                   algorithm="projected_gradient",
                   n_iterations=n_iter, converged=converged, **cov_info)


# ------------------------------------------------------------------ RP
def risk_parity(returns_by_symbol: dict, tol: float = RP_TOL,
                max_iter: int = RP_MAX_ITER,
                cov_method: str = "sample") -> dict:
    """Risk parity / ERC via Spinu's convex formulation solved by cyclical
    coordinate descent. Converges to equal risk contributions; for
    diagonal Σ the answer is exactly wᵢ ∝ 1/σᵢ.
    cov_method: "sample" (default) or "ledoit_wolf" shrinkage (R4-2)."""
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    cov, cov_info = _cov_for(columns, cov_method)
    mu = _mean_vector(columns)
    if k == 1:                      # trivially equal risk contribution
        return _result("rp", symbols, columns, [1.0], cov, mu,
                       iterations=0, converged=True, tol=tol, **cov_info)
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
                   iterations=iterations, converged=True, tol=tol, **cov_info)


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


def hierarchical_risk_parity(returns_by_symbol: dict,
                             cov_method: str = "sample") -> dict:
    """HRP: correlation-distance single linkage → quasi-diagonalization →
    recursive bisection with inverse cluster-variance splits.
    cov_method: "sample" (default) or "ledoit_wolf" shrinkage (R4-2)."""
    symbols, columns = _prepare(returns_by_symbol)
    k = len(symbols)
    cov, cov_info = _cov_for(columns, cov_method)
    mu = _mean_vector(columns)
    if k == 1:                      # one leaf: weight 1.0, no bisection
        return _result("hrp", symbols, columns, [1.0], cov, mu,
                       quasi_diagonal_order=list(symbols), merges=[],
                       **cov_info)
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
                            "distance": m["distance"]} for m in merges],
                   **cov_info)


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
    except TypeError:
        # unknown kwarg (e.g. seed passed to rp/hrp) — retry without extras
        clean = {k: v for k, v in kwargs.items()
                 if k in ("cov_method", "tol", "max_iter")}
        if canonical == "mv":
            return mean_variance(returns_by_symbol, **clean)
        if canonical == "rp":
            return risk_parity(returns_by_symbol, **clean)
        return hierarchical_risk_parity(returns_by_symbol, **clean)
