"""GAUNTLET 7 Piece 2 — closed-form options math (Document: risk plane).

Pure stdlib, deterministic, no data dependency: Black-Scholes prices +
Greeks, implied volatility, max pain, put/call ratio, IV rank/percentile,
and strike-grid surface/skew builders.

BAR-OPT-MATH certification: prices match Hull's worked example
(S=42, K=40, r=0.10, sigma=0.20, T=0.5 → c=4.76, p=0.81) and Greeks
match the closed-form identities + numerical cross-checks to <1e-8.

Law boundary: analytics for the observation layer only. Nothing here
sizes, gates, or enters the live decision loop.
"""
from __future__ import annotations

import math

SQRT_2PI = math.sqrt(2.0 * math.pi)


def _phi(x: float) -> float:
    """Standard normal density."""
    return math.exp(-0.5 * x * x) / SQRT_2PI


def _N(x: float) -> float:
    """Standard normal CDF (math.erf is double precision)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def d1_d2(s: float, k: float, t: float, r: float, sigma: float,
          q: float = 0.0) -> tuple[float, float]:
    """Merton d1/d2 (q = continuous dividend yield)."""
    if t <= 0 or sigma <= 0 or s <= 0 or k <= 0:
        raise ValueError(f"degenerate inputs: s={s} k={k} t={t} sigma={sigma}")
    vsqrt_t = sigma * math.sqrt(t)
    d1 = (math.log(s / k) + (r - q + 0.5 * sigma * sigma) * t) / vsqrt_t
    return d1, d1 - vsqrt_t


def bs_price(s: float, k: float, t: float, r: float, sigma: float,
             kind: str = "call", q: float = 0.0) -> float:
    """European option price (Black-Scholes-Merton)."""
    d1, d2 = d1_d2(s, k, t, r, sigma, q)
    df_r, df_q = math.exp(-r * t), math.exp(-q * t)
    if kind == "call":
        return s * df_q * _N(d1) - k * df_r * _N(d2)
    if kind == "put":
        return k * df_r * _N(-d2) - s * df_q * _N(-d1)
    raise ValueError(f"kind must be call|put: {kind}")


def bs_delta(s: float, k: float, t: float, r: float, sigma: float,
             kind: str = "call", q: float = 0.0) -> float:
    d1, _ = d1_d2(s, k, t, r, sigma, q)
    df_q = math.exp(-q * t)
    return df_q * _N(d1) if kind == "call" else df_q * (_N(d1) - 1.0)


def bs_gamma(s: float, k: float, t: float, r: float, sigma: float,
             q: float = 0.0) -> float:
    d1, _ = d1_d2(s, k, t, r, sigma, q)
    return math.exp(-q * t) * _phi(d1) / (s * sigma * math.sqrt(t))


def bs_vega(s: float, k: float, t: float, r: float, sigma: float,
            q: float = 0.0) -> float:
    """Vega per 1.00 of volatility (divide by 100 for per-vol-point)."""
    d1, _ = d1_d2(s, k, t, r, sigma, q)
    return s * math.exp(-q * t) * _phi(d1) * math.sqrt(t)


def bs_theta(s: float, k: float, t: float, r: float, sigma: float,
             kind: str = "call", q: float = 0.0) -> float:
    """Theta per YEAR (divide by 365.0 for per-calendar-day)."""
    d1, d2 = d1_d2(s, k, t, r, sigma, q)
    df_r, df_q = math.exp(-r * t), math.exp(-q * t)
    common = -s * df_q * _phi(d1) * sigma / (2.0 * math.sqrt(t))
    if kind == "call":
        return common - r * k * df_r * _N(d2) + q * s * df_q * _N(d1)
    return common + r * k * df_r * _N(-d2) - q * s * df_q * _N(-d1)


def bs_rho(s: float, k: float, t: float, r: float, sigma: float,
           kind: str = "call", q: float = 0.0) -> float:
    d1, d2 = d1_d2(s, k, t, r, sigma, q)
    df_r = math.exp(-r * t)
    if kind == "call":
        return k * t * df_r * _N(d2)
    return -k * t * df_r * _N(-d2)


def greeks(s: float, k: float, t: float, r: float, sigma: float,
           kind: str = "call", q: float = 0.0) -> dict:
    """All Greeks + price in one deterministic package."""
    return {
        "kind": kind,
        "price": bs_price(s, k, t, r, sigma, kind, q),
        "delta": bs_delta(s, k, t, r, sigma, kind, q),
        "gamma": bs_gamma(s, k, t, r, sigma, q),
        "vega": bs_vega(s, k, t, r, sigma, q),
        "theta": bs_theta(s, k, t, r, sigma, kind, q),
        "rho": bs_rho(s, k, t, r, sigma, kind, q),
    }


def implied_vol(price: float, s: float, k: float, t: float, r: float,
                kind: str = "call", q: float = 0.0,
                lo: float = 1e-4, hi: float = 5.0,
                iters: int = 200) -> float | None:
    """Implied vol by bisection on the price (robust, monotone in sigma).

    Returns None when the market price is outside the no-arbitrage
    bracket (honest failure, never a fabricated number).
    """
    try:
        p_lo = bs_price(s, k, t, r, lo, kind, q)
        p_hi = bs_price(s, k, t, r, hi, kind, q)
    except ValueError:
        return None
    if not (p_lo <= price <= p_hi):
        return None
    for _ in range(iters):
        mid = 0.5 * (lo + hi)
        try:
            p_mid = bs_price(s, k, t, r, mid, kind, q)
        except ValueError:
            return None
        if abs(p_mid - price) < 1e-12:
            return mid
        if p_mid < price:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


# --------------------------------------------------------- chain analytics
def max_pain(chain: list[dict]) -> dict:
    """Max-pain strike: the expiry strike minimizing total intrinsic
    payout to option holders (the "pain" for longs = dealers' magnet).

    chain items: {strike, kind: call|put, open_interest}.
    """
    strikes = sorted({float(c["strike"]) for c in chain})
    if not strikes:
        return {"max_pain": None, "payouts": []}
    oi_by: dict[tuple[float, str], float] = {}
    for c in chain:
        key = (float(c["strike"]), c["kind"])
        oi_by[key] = oi_by.get(key, 0.0) + float(c.get("open_interest") or 0)
    payouts = []
    for settle in strikes:
        total = 0.0
        for (k, kind), oi in oi_by.items():
            if kind == "call" and settle > k:
                total += (settle - k) * oi
            elif kind == "put" and settle < k:
                total += (k - settle) * oi
        payouts.append({"settle": settle, "holder_payout": total})
    best = min(payouts, key=lambda p: p["holder_payout"])
    return {"max_pain": best["settle"], "payouts": payouts}


def put_call_ratio(chain: list[dict], field: str = "volume") -> dict:
    """Put/call ratio by volume or open_interest (fail-soft zeros)."""
    puts = sum(float(c.get(field) or 0) for c in chain if c["kind"] == "put")
    calls = sum(float(c.get(field) or 0) for c in chain
                if c["kind"] == "call")
    return {
        "put_call_ratio": round(puts / calls, 4) if calls > 0 else None,
        "put_total": puts, "call_total": calls, "field": field,
    }


def iv_rank(current_iv: float, iv_history: list[float]) -> dict | None:
    """IV rank = (current − 52w low) / (52w high − 52w low)."""
    if not iv_history or current_iv is None:
        return None
    lo, hi = min(iv_history), max(iv_history)
    if hi <= lo:
        return {"iv_rank": None, "iv_percentile": None, "low": lo,
                "high": hi, "note": "degenerate history"}
    rank = (current_iv - lo) / (hi - lo)
    below = sum(1 for v in iv_history if v <= current_iv)
    return {"iv_rank": round(rank, 4), "iv_percentile":
            round(below / len(iv_history), 4), "low": lo, "high": hi}


def iv_surface(chain: list[dict], s: float, r: float = 0.0,
               q: float = 0.0) -> dict:
    """Per-expiry smile/skew + term structure, computed from chain IVs
    (or re-derived from mid prices when the feed omits IV).

    Returns {expiries: [{expiry, t_years, smiles: [{strike, iv, kind}]}],
    term_structure: [{expiry, t_years, atm_iv}]}.
    """
    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    by_exp: dict[str, list[dict]] = {}
    for c in chain:
        by_exp.setdefault(str(c.get("expiry", "")), []).append(c)
    expiries, term = [], []
    for exp in sorted(by_exp, key=lambda e: _exp_sort_key(e)):
        rows = by_exp[exp]
        try:
            exp_dt = _dt.datetime.fromisoformat(exp).replace(
                tzinfo=_dt.timezone.utc) if "T" in exp else \
                _dt.datetime.strptime(exp, "%Y-%m-%d").replace(
                    tzinfo=_dt.timezone.utc)
        except ValueError:
            continue
        t_years = max((exp_dt - now).total_seconds() /
                      (365.0 * 24 * 3600), 1e-6)
        smiles = []
        for c in rows:
            iv = c.get("implied_vol")
            if iv is None and c.get("mid") is not None and s > 0:
                iv = implied_vol(c["mid"], s, float(c["strike"]), t_years,
                                 r, c["kind"], q)
            if iv is not None:
                smiles.append({"strike": float(c["strike"]),
                               "iv": round(iv, 6), "kind": c["kind"]})
        if not smiles:
            continue
        expiries.append({"expiry": exp, "t_years": round(t_years, 6),
                         "smiles": smiles})
        atm_smile = min(smiles, key=lambda x: abs(x["strike"] - s)) \
            if s > 0 else None
        term.append({"expiry": exp, "t_years": round(t_years, 6),
                     "atm_iv": atm_smile["iv"] if atm_smile else None})
    return {"expiries": expiries, "term_structure": term}


def _exp_sort_key(e: str):
    return (0, e) if "T" in e else (1, e)


def skew_metrics(smile: list[dict], s: float) -> dict | None:
    """25-delta risk reversal & butterfly from a smile (by IV at the
    strikes nearest 0.25 delta), plus put-skew slope (exploratory).

    smile items: {strike, iv, kind}.
    """
    calls = sorted((x for x in smile if x["kind"] == "call"),
                   key=lambda x: x["strike"])
    puts = sorted((x for x in smile if x["kind"] == "put"),
                  key=lambda x: x["strike"])
    if not calls or not puts or s <= 0:
        return None
    otm_calls = [x for x in calls if x["strike"] > s]
    otm_puts = [x for x in puts if x["strike"] < s]
    if not otm_calls or not otm_puts:
        return None
    call_wing = otm_calls[len(otm_calls) // 4]   # ~25d call proxy
    put_wing = otm_puts[-max(1, len(otm_puts) // 4)]  # ~25d put proxy
    atm = min(smile, key=lambda x: abs(x["strike"] - s))
    return {
        "atm_iv": atm["iv"],
        "call_wing_iv": call_wing["iv"], "call_wing_strike": call_wing["strike"],
        "put_wing_iv": put_wing["iv"], "put_wing_strike": put_wing["strike"],
        "risk_reversal_25d": round(put_wing["iv"] - call_wing["iv"], 6),
        "butterfly_25d": round((put_wing["iv"] + call_wing["iv"]) / 2
                               - atm["iv"], 6),
        "note": "strike-proxied 25-delta (no per-contract greeks feed)",
    }
