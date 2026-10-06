"""GAUNTLET 7 Piece 5 — pair / relative-value analytics (pure stdlib).

spread/ratio between any two symbols + rolling z-score with ±2σ bands +
an Engle-Granger residual cointegration test (exploratory, labeled).

Everything is computed over the two bar series the desk already fetches
(board.fetch_daily_bars, keyless Yahoo v8) — no new data source. The
cointegration stat is exploratory: small samples, no small-sample
corrections; every output carries the window label.

Law boundary: display/telemetry only — never in the live decision loop.
"""
from __future__ import annotations

from pathlib import Path

from .board import fetch_daily_bars
from .registry import normalize


# ------------------------------------------------------------------ math
def _mean(xs):
    return sum(xs) / len(xs)


def _stdev(xs, ddof: int = 1):
    n = len(xs)
    if n <= ddof:
        return None
    m = _mean(xs)
    return (sum((x - m) ** 2 for x in xs) / (n - ddof)) ** 0.5


def ols_slope_intercept(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """OLS y = α + βx (closed form). Returns (α, β)."""
    n = len(xs)
    if n < 3 or n != len(ys):
        raise ValueError(f"ols needs >=3 aligned points, got {n}")
    mx, my = _mean(xs), _mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        raise ValueError("zero variance in x")
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    alpha = my - beta * mx
    return alpha, beta


def adf_t_stat(series: list[float]) -> float:
    """ADF t-statistic with zero lags, no trend, no constant suppression:
    Δy_t = ρ y_{t-1} + ε → t = ρ̂ / se(ρ̂). Pure stdlib.

    This is the Engle-Granger second step on the residuals.
    """
    ys = series[1:]
    xs = series[:-1]
    n = len(ys)
    if n < 10:
        raise ValueError(f"adf needs >=10 points, got {n}")
    mx, my = _mean(xs), _mean(ys)
    dy = [y - p for y, p in zip(ys, xs)]       # Δy_t
    mdy = _mean(dy)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        raise ValueError("zero variance in levels")
    rho = sum((x - mx) * (d - mdy) for x, d in zip(xs, dy)) / sxx
    alpha = mdy - rho * mx
    resid = [d - (alpha + rho * x) for x, d in zip(xs, dy)]
    rss = sum(r * r for r in resid)
    # se(ρ̂)² = σ²_ε / Σ(x−x̄)², σ²_ε = RSS / (n − 2)
    if n <= 2:
        raise ValueError("not enough degrees of freedom")
    sigma2 = rss / (n - 2)
    if sigma2 <= 0:
        return 0.0
    se = (sigma2 / sxx) ** 0.5
    return rho / se


# EG response-surface critical values, n=2 variables, no trend
# (Engle & Granger 1987 Table; honest small-sample caveat applied)
EG_CRITICAL_N2 = {"1%": -3.90, "5%": -3.34, "10%": -3.04}


def eg_cointegration(a: list[float], b: list[float]) -> dict:
    """Engle-Granger two-step on aligned levels a, b (exploratory)."""
    alpha, beta = ols_slope_intercept(b, a)
    resid = [ai - (alpha + beta * bi) for ai, bi in zip(a, b)]
    # degenerate hedge (perfectly linear pair, zero residuals) — ADF is
    # undefined; report honestly instead of raising
    if max(abs(r) for r in resid) < 1e-12:
        return {"alpha": round(alpha, 6), "beta": round(beta, 6),
                "adf_t_stat": None, "critical_values": EG_CRITICAL_N2,
                "verdict": "degenerate (zero-residual fit — pair is "
                           "perfectly linear in-sample; ADF undefined)",
                "exploratory": True,
                "note": "Engle-Granger 2-step, zero-lag ADF, small-sample "
                        "uncorrected — treat as exploratory, never a gate"}
    t = adf_t_stat(resid)
    verdict = "cointegrated (10%)" if t < EG_CRITICAL_N2["10%"] else \
        "not cointegrated at 10%"
    return {"alpha": round(alpha, 6), "beta": round(beta, 6),
            "adf_t_stat": round(t, 4), "critical_values": EG_CRITICAL_N2,
            "verdict": verdict, "exploratory": True,
            "note": "Engle-Granger 2-step, zero-lag ADF, small-sample "
                    "uncorrected — treat as exploratory, never a gate"}


def rolling_zscore(series: list[float], window: int) -> list[dict | None]:
    """[{z, mean, sd}] aligned to series indices (None before warm-up)."""
    out: list[dict | None] = [None] * len(series)
    if window < 5:
        raise ValueError(f"window must be >=5, got {window}")
    for i in range(window - 1, len(series)):
        chunk = series[i - window + 1:i + 1]
        m = _mean(chunk)
        sd = _stdev(chunk)
        if sd and sd > 0:
            out[i] = {"z": round((series[i] - m) / sd, 4),
                      "mean": round(m, 6), "sd": round(sd, 6)}
    return out


# ----------------------------------------------------------------- public
def _daily_map(symbol: str, data_root) -> dict[str, float]:
    import datetime as _dt
    bars = fetch_daily_bars(symbol, "1y", data_root or "data")
    out: dict[str, float] = {}
    for b in bars:
        d = _dt.datetime.fromtimestamp(b["ts"] / 1000,
                                       tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        out[d] = b["c"]
    return out


def pair_analytics(symbol_a: str, symbol_b: str, window: int = 60,
                   data_root=None, mode: str = "ratio") -> dict:
    """Spread/ratio + z-score bands + EG cointegration for a pair.

    mode: ratio (a/b) or spread (a − β·b, β from full-sample OLS).
    """
    a_sym = normalize(symbol_a) or symbol_a.upper()
    b_sym = normalize(symbol_b) or symbol_b.upper()
    am = _daily_map(a_sym, data_root)
    bm = _daily_map(b_sym, data_root)
    dates = sorted(set(am) & set(bm))
    if len(dates) < window + 10:
        return {"ok": False,
                "errors": [f"only {len(dates)} aligned daily closes "
                           f"(need >= {window + 10})"],
                "a": a_sym, "b": b_sym}
    a = [am[d] for d in dates]
    b = [bm[d] for d in dates]
    try:
        alpha, beta = ols_slope_intercept(b, a)
    except ValueError:
        # zero-variance partner — hedge is undefined; report honestly
        alpha, beta = None, None
    if mode == "spread":
        if beta is None:
            return {"ok": False, "a": a_sym, "b": b_sym,
                    "errors": ["spread mode needs a non-constant partner "
                               "series (zero variance in b)"]}
        spread = [ai - beta * bi for ai, bi in zip(a, b)]
        spread_label = f"{a_sym} − {beta:.4f}·{b_sym}"
    else:
        spread = [ai / bi for ai, bi in zip(a, b) if bi]
        spread_label = f"{a_sym}/{b_sym}"
    if len(spread) != len(dates):
        dates, a, b = dates[-len(spread):], a[-len(spread):], b[-len(spread):]
    zs = rolling_zscore(spread, window)
    z_now = next((z["z"] for z in reversed(zs) if z), None)
    eg = eg_cointegration(a, b) if beta is not None else \
        {"alpha": None, "beta": None, "adf_t_stat": None,
         "critical_values": EG_CRITICAL_N2,
         "verdict": "degenerate (zero-variance partner)",
         "exploratory": True,
         "note": "Engle-Granger 2-step, zero-lag ADF, small-sample "
                 "uncorrected — treat as exploratory, never a gate"}
    return {
        "ok": True,
        "a": a_sym, "b": b_sym, "mode": mode, "window": window,
        "spread_label": spread_label,
        "dates": dates,
        "spread": [round(s, 6) for s in spread],
        "zscore": zs,
        "zscore_now": z_now,
        "band": 2.0,
        "hedge": {"alpha": round(alpha, 6) if alpha is not None else None,
                  "beta": round(beta, 6) if beta is not None else None},
        "cointegration": eg,
        "source": "yahoo_v8_chart (keyless)",
        "n_days": len(dates),
    }
