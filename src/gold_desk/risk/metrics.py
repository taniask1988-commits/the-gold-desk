"""R3-2 BUILD 4 — risk metrics: VaR, ES, beta, stress scenarios, ratios.

Pure stdlib math, no I/O, fully deterministic. Every function operates on
either:

* a plain returns series  — list[float] of period returns (e.g. daily),
  expressed as fractions (+0.01 = +1%). VaR/ES values are RETURN
  QUANTILES, i.e. negative numbers for loss tails (mean − z·σ at 95%
  reads ≈ −0.02 for a 2% daily loss tail), matching the
  scipy.stats.norm.ppf(0.05) / numpy.percentile(r, 5) conventions the
  tests reference.

* a portfolio — list of position dicts
  [{"symbol": "SPY", "weight": 0.5, "returns": [...]}, ...]; returns are
  blended element-wise (tail-aligned) by `portfolio_returns`, and stress
  shocks are applied symbol-wise by `stress_test`.

Conventions pinned by the test suite:
* sigma is the SAMPLE standard deviation (ddof=1) — the estimator used in
  practice; the parametric-VaR test references
  scipy.stats.norm.ppf(alpha, np.mean(r), np.std(r, ddof=1)).
* z constants carry full double precision (1.6448536269514722 /
  2.3263478740408408 = scipy.stats.norm.ppf(0.95/0.99)), NOT the rounded
  1.645/2.326 from the charter — the 1e-6 test tolerance demands it.
* Monte Carlo simulates single-step GBM in log-return space with a pinned
  seed: r_log ~ N(mu_l, sigma_l) over the log-returns ln(1+r), converted
  back with exp()−1. Same seed → byte-identical paths.
* Expected Shortfall = mean of the returns at or beyond (≤) the VaR
  quantile. Max drawdown is a POSITIVE magnitude fraction (0.15 = 15%).

Law boundary: display/education telemetry + research tooling, NOT wired
into the orchestrator's decision loop (constitution-gated).
"""
from __future__ import annotations

import math
import random

# z values at full double precision (scipy.stats.norm.ppf) — the rounded
# 1.645 / 2.326 from the brief are these same numbers to 3dp.
Z_95 = 1.6448536269514722
Z_99 = 2.3263478740408408
Z_90 = 1.2815515655446004
_Z_TABLE: dict[float, float] = {0.90: Z_90, 0.95: Z_95, 0.99: Z_99}

MC_SEED = 42
MC_PATHS = 1000
RF_ANNUAL = 0.05          # risk-free annual rate (charter: Sharpe rf=0.05)
PERIODS_PER_YEAR = 252    # daily returns convention

# ------------------------------------------------------------------ stress
# Scenario shock vectors. Values are RETURNS applied to positions in the
# quoted instrument. Only charter-documented moves are encoded — SPX-family
# shocks (−38.5% GFC, −33.9% COVID, −19.4% + 2022 rate shock) and the
# 2022 10y-yield move (+2.36% on a ^TNX/10Y position, the yield's move in
# the task's own units). Assets without a documented shock are surfaced in
# each scenario's `unshocked` list rather than silently zeroed.
STRESS_SCENARIOS: dict[str, dict] = {
    "gfc_2008": {
        "label": "2008 Global Financial Crisis",
        "shocks": {"SPY": -0.385, "SPX": -0.385, "ES=F": -0.385},
    },
    "covid_2020": {
        "label": "2020 COVID crash",
        "shocks": {"SPY": -0.339, "SPX": -0.339, "ES=F": -0.339},
    },
    "rate_shock_2022": {
        "label": "2022 rate shock",
        "yield_change_pp": 2.36,
        "shocks": {"SPY": -0.194, "SPX": -0.194, "ES=F": -0.194,
                   "^TNX": 0.0236, "TNX": 0.0236, "10Y": 0.0236},
    },
}


# ------------------------------------------------------------ basic stats
def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def sample_stdev(values: list[float]) -> float:
    """Sample standard deviation (ddof=1). 0.0 for n < 2."""
    n = len(values)
    if n < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / (n - 1))


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile — bit-for-bit the numpy default
    (np.percentile 'linear' method): rank = (n−1)·q/100, interpolated
    between floor and ceil neighbors."""
    if not values:
        return None
    s = sorted(values)
    if len(s) == 1:
        return float(s[0])
    rank = (len(s) - 1) * (float(q) / 100.0)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(s[int(rank)])
    frac = rank - lo
    return float(s[lo] * (1.0 - frac) + s[hi] * frac)


def _z_for(confidence: float) -> float:
    z = _Z_TABLE.get(float(confidence))
    if z is None:
        raise ValueError(f"confidence must be one of {sorted(_Z_TABLE)}, "
                         f"got {confidence}")
    return z


# ------------------------------------------------------------------ VaR
def var_parametric(returns: list[float], confidence: float = 0.95) -> float | None:
    """Gaussian VaR as a return quantile: mean − z·σ (negative = loss).

    σ = sample stdev (ddof=1); z is scipy-exact (see module docstring).
    """
    if len(returns) < 2:
        return None
    mu = mean(returns)
    sd = sample_stdev(returns)
    return mu - _z_for(confidence) * sd


def var_historical(returns: list[float], confidence: float = 0.95) -> float | None:
    """Historical VaR: the (1−confidence) percentile of actual returns —
    exactly numpy.percentile(returns, (1−confidence)·100)."""
    if not returns:
        return None
    return percentile(returns, (1.0 - confidence) * 100.0)


def monte_carlo_returns(returns: list[float], n_paths: int = MC_PATHS,
                        seed: int = MC_SEED) -> list[float]:
    """Single-step GBM simulation in log space, seed-pinned.

    Log-returns are estimated from the observed series (ln(1+r) for
    r > −1), then n_paths draws r_log ~ N(mu_l, sigma_l) from
    random.Random(seed) are converted back with exp()−1. Same seed → the
    identical path list (bit-for-bit).
    """
    usable = [float(r) for r in returns if r > -1.0]
    if not usable:
        usable = [0.0]
    logs = [math.log(1.0 + r) for r in usable]
    mu = mean(logs)
    sd = sample_stdev(logs)
    rng = random.Random(seed)
    return [math.exp(rng.gauss(mu, sd)) - 1.0 for _ in range(max(1, n_paths))]


def var_monte_carlo(returns: list[float], confidence: float = 0.95,
                    n_paths: int = MC_PATHS, seed: int = MC_SEED) -> float | None:
    """Monte Carlo VaR: the (1−confidence) percentile of the seed-pinned
    GBM simulation (1000 paths by default)."""
    if not returns:
        return None
    sim = monte_carlo_returns(returns, n_paths=n_paths, seed=seed)
    return percentile(sim, (1.0 - confidence) * 100.0)


# ------------------------------------------------------------------ ES
def expected_shortfall(returns: list[float], confidence: float = 0.95,
                       var_value: float | None = None) -> float | None:
    """Expected Shortfall (CVaR): mean of the returns at or beyond the VaR
    quantile (r ≤ VaR for a loss tail). Pass `var_value` to reuse an
    externally computed VaR (e.g. the parametric one); defaults to the
    historical VaR. Degenerate empty tail (VaR below the sample minimum —
    only possible with an injected parametric VaR) returns the VaR itself.
    """
    if not returns:
        return None
    v = var_value if var_value is not None else var_historical(returns, confidence)
    if v is None:
        return None
    tail = [r for r in returns if r <= v]
    if not tail:
        return v
    return mean(tail)


# ------------------------------------------------------------------ beta
def beta_vs_benchmark(returns: list[float],
                      benchmark: list[float]) -> dict:
    """Beta-adjusted exposure vs a benchmark (SPY or any passed series).

    Series are tail-aligned to the shorter length; beta =
    Σ(p−p̄)(b−b̄) / Σ(b−b̄)² (the ddof cancels — exact for p = a + k·b),
    alpha is the per-period intercept, correlation/r_squared are the
    Pearson pieces. Zero benchmark variance → beta None (degenerate).
    """
    n = min(len(returns), len(benchmark))
    if n < 2:
        return {"beta": None, "alpha": None, "correlation": None,
                "r_squared": None, "n": n}
    p = [float(x) for x in returns[-n:]]
    b = [float(x) for x in benchmark[-n:]]
    mp, mb = mean(p), mean(b)
    cov = sum((p[i] - mp) * (b[i] - mb) for i in range(n))
    var_b = sum((b[i] - mb) ** 2 for i in range(n))
    var_p = sum((p[i] - mp) ** 2 for i in range(n))
    if var_b == 0 or var_p == 0:
        return {"beta": None, "alpha": None, "correlation": None,
                "r_squared": None, "n": n}
    beta = cov / var_b
    alpha = mp - beta * mb
    corr = max(-1.0, min(1.0, cov / math.sqrt(var_b * var_p)))
    return {"beta": beta, "alpha": alpha, "correlation": corr,
            "r_squared": corr * corr, "n": n}


# ------------------------------------------------------------------ stress
def stress_test(positions: list[dict],
                scenarios: dict | None = None) -> dict:
    """Apply the stress shock vectors to a portfolio.

    positions: [{"symbol": "SPY", "weight": 0.5}, ...] — weights may sum to
    anything (leverage allowed); CASH and any asset without a documented
    shock contribute 0 and are surfaced in `unshocked` (never silently
    guessed). Returns {scenarios: [{name, label, portfolio_shock,
    shocked, unshocked, yield_change_pp?}], portfolio_shocks: {name: value}}.
    """
    scen = scenarios or STRESS_SCENARIOS
    out_scenarios: list[dict] = []
    shocks_map: dict[str, float | None] = {}
    for name, spec in scen.items():
        vector = spec.get("shocks") or {}
        total = 0.0
        shocked: list[str] = []
        unshocked: list[str] = []
        for pos in positions or []:
            sym = str(pos.get("symbol", "")).upper()
            weight = float(pos.get("weight", 0.0))
            shock = vector.get(sym)
            if shock is None:
                unshocked.append(sym)
                continue
            shocked.append(sym)
            total += weight * float(shock)
        entry = {"name": name, "label": spec.get("label", name),
                 "portfolio_shock": round(total, 6),
                 "shocked": sorted(set(shocked)),
                 "unshocked": sorted(set(unshocked))}
        if "yield_change_pp" in spec:
            entry["yield_change_pp"] = spec["yield_change_pp"]
        out_scenarios.append(entry)
        shocks_map[name] = entry["portfolio_shock"]
    return {"scenarios": out_scenarios, "portfolio_shocks": shocks_map}


# ------------------------------------------------------------------ ratios
def sharpe_ratio(returns: list[float], rf_annual: float = RF_ANNUAL,
                 periods: int = PERIODS_PER_YEAR) -> float | None:
    """(mean − rf/periods) / sample σ, annualized by √periods."""
    if len(returns) < 2:
        return None
    sd = sample_stdev(returns)
    if sd == 0:
        return None
    excess = mean(returns) - rf_annual / periods
    return excess / sd * math.sqrt(periods)


def sortino_ratio(returns: list[float], rf_annual: float = RF_ANNUAL,
                  periods: int = PERIODS_PER_YEAR) -> float | None:
    """Like Sharpe but with the full-sample downside deviation:
    σ_d = √(Σ min(r − target, 0)² / n) over ALL periods (not just the
    downside subset) — the standard textbook definition."""
    if len(returns) < 2:
        return None
    target = rf_annual / periods
    downside = math.sqrt(sum(min(r - target, 0.0) ** 2 for r in returns)
                         / len(returns))
    if downside == 0:
        return None
    excess = mean(returns) - target
    return excess / downside * math.sqrt(periods)


def max_drawdown(equity: list[float]) -> float | None:
    """Largest peak-to-trough decline as a POSITIVE magnitude fraction
    (0.15 = 15% drawdown). Needs strictly positive equity values."""
    if not equity:
        return None
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            mdd = max(mdd, (peak - v) / peak)
    return mdd


def calmar_ratio(total_return: float, max_dd: float | None,
                 n_periods: int, periods_per_year: int = PERIODS_PER_YEAR
                 ) -> float | None:
    """Annualized compound return / |max drawdown|.

    total_return is the whole-run simple return (end/start − 1) over
    `n_periods` periods; the annualization compounds at
    (1+total)^(periods_per_year/n) − 1. None on non-positive drawdown,
    wiped-out equity or empty runs.
    """
    if max_dd is None or max_dd <= 0 or n_periods <= 0 or total_return <= -1.0:
        return None
    ann = (1.0 + total_return) ** (periods_per_year / n_periods) - 1.0
    return ann / max_dd


# ------------------------------------------------------------------ portfolio
def date_aligned_returns(closes_by_symbol: dict[str, dict[str, float]]
                         ) -> dict[str, list[float]]:
    """Date-align daily closes across symbols before computing returns.

    closes_by_symbol: {symbol: {"YYYY-MM-DD": close}}. Returns
    {symbol: [simple returns]} computed on the INTERSECTION of dates
    (sorted) — the R3-1 D2 lesson applied to portfolio math: a 24/7
    asset (BTC, ~365 closes/yr) must never be paired position-wise with
    a ~5-day/week asset (SPY, ~252). Symbols with < 2 common dates map
    to [].
    """
    maps = {s: dict(m) for s, m in (closes_by_symbol or {}).items() if m}
    if not maps:
        return {}
    common = set.intersection(*(set(m.keys()) for m in maps.values()))
    dates = sorted(d for d in common)
    if len(dates) < 2:
        return {s: [] for s in maps}
    out: dict[str, list[float]] = {}
    for sym, m in maps.items():
        rets: list[float] = []
        for prev, cur in zip(dates, dates[1:]):
            a, b = m.get(prev), m.get(cur)
            if a is None or b is None or a <= 0:
                rets.append(0.0)
                continue
            rets.append(b / a - 1.0)
        out[sym] = rets
    return out


def portfolio_returns(positions: list[dict]) -> list[float]:
    """Blend a portfolio's per-position return series element-wise.

    positions: [{"symbol", "weight", "returns": [...]}, ...]; series are
    tail-aligned to the shortest; positions without returns contribute 0.
    """
    live = [p for p in (positions or []) if p.get("returns")]
    if not live:
        return []
    n = min(len(p["returns"]) for p in live)
    return [sum(float(p.get("weight", 0.0)) * float(p["returns"][i])
               for p in live) for i in range(n)]


def risk_report(returns: list[float], benchmark: list[float] | None = None,
                positions: list[dict] | None = None,
                mc_paths: int = MC_PATHS, mc_seed: int = MC_SEED) -> dict:
    """The full VaR/ES/stress table the CLI and web RiskPanel render.

    VaR: 3 methods × {95%, 99%}; ES at both confidences (historical
    quantile basis); beta block when a benchmark series is passed; stress
    block when positions are passed. Deterministic (MC seed-pinned).
    """
    rets = [float(r) for r in (returns or [])]
    ok = len(rets) >= 2
    out: dict = {
        "ok": ok,
        "n_observations": len(rets),
        "mean": mean(rets) if rets else None,
        "stdev": sample_stdev(rets) if ok else None,
        "var": {
            "parametric": {"95": None, "99": None},
            "historical": {"95": None, "99": None},
            "monte_carlo": {"95": None, "99": None},
        },
        "expected_shortfall": {"historical_95": None, "historical_99": None},
    }
    if not ok:
        if benchmark:
            out["beta"] = beta_vs_benchmark(rets, benchmark)
        if positions:
            out["stress"] = stress_test(positions)
        return out
    for conf_key, conf in (("95", 0.95), ("99", 0.99)):
        out["var"]["parametric"][conf_key] = var_parametric(rets, conf)
        out["var"]["historical"][conf_key] = var_historical(rets, conf)
        out["var"]["monte_carlo"][conf_key] = var_monte_carlo(
            rets, conf, n_paths=mc_paths, seed=mc_seed)
        out["expected_shortfall"][f"historical_{conf_key}"] = expected_shortfall(
            rets, conf, var_value=out["var"]["historical"][conf_key])
    if benchmark:
        out["beta"] = beta_vs_benchmark(rets, benchmark)
    if positions:
        out["stress"] = stress_test(positions)
    return out
