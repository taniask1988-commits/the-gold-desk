"""R2-2 QUANT TOOLKIT — pure-python, deterministic, no LLM, no network.

Closes the TradingAgents v0.3.1 market-data-validation bar:
tradingagents/dataflows/market_data_validator.py builds a no-LLM
ground-truth OHLCV+indicator snapshot the market analyst must treat as
"the source of truth for any exact numeric claim". This module ports
that discipline to the gold desk — all indicator math is numpy-free so
test fixtures are reproducible across environments.

Bar shape: the bars dicts come straight from markets.board.fetch_detail
``bars`` list — oldest-first, each row {ts, o, h, l, c}. compute_*
functions accept that shape and also a minimal {date, o, h, l, c, v}
shape; the callers in this module only ever pass the board shape.
Volume is OPTIONAL (board bars don't always carry it). Bars are
OLDEST-FIRST throughout this module; the snapshot caller documents the
orientation so any consumer can re-orient without surprise.

Failure contract: <window bars → that indicator is None (not 0, not
NaN). The snapshot and the desk slice are explicit about None — a
persona reading "None" knows the indicator is not available, not that
it is zero.

Float tolerance: 1e-6 in tests. All math stdlib + math only.
"""
from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

from ..markets.board import fetch_detail

# 252 trading days/year — the standard US-equity annualization factor.
TRADING_DAYS = 252

# vol_regime thresholds on annualized realized vol (realized_vol_20d).
# Calibrated to the textbook equity-vol buckets: <15% low, 15-25% normal,
# 25-40% high, >40% extreme. AAPL at ~25% realized prints "normal"; BTC
# at 60%+ prints "extreme". Documented so critics can recalibrate.
_VOL_REGIME = (
    ("low", 0.0, 0.15),
    ("normal", 0.15, 0.25),
    ("high", 0.25, 0.40),
    ("extreme", 0.40, math.inf),
)


# --------------------------------------------------------------- helpers


def _to_ohlcv(bars: list[dict]) -> list[dict]:
    """Normalize bar dicts to {o,h,l,c,v} lists, oldest-first.

    Accepts both the board shape {ts,o,h,l,c} (no v) and a fuller
    {date,o,h,l,c,v} shape. Volume defaults to 0.0 when absent (OBV
    then degrades gracefully — the board plane doesn't ship volume)."""
    out = []
    for b in bars or []:
        o = float(b.get("o", b.get("open", 0.0)) or 0.0)
        h = float(b.get("h", b.get("high", 0.0)) or 0.0)
        l = float(b.get("l", b.get("low", 0.0)) or 0.0)
        c = float(b.get("c", b.get("close", 0.0)) or 0.0)
        v = b.get("v", b.get("volume"))
        v = float(v) if isinstance(v, (int, float)) else 0.0
        out.append({"o": o, "h": h, "l": l, "c": c, "v": v})
    return out


def _closes(bars: list[dict]) -> list[float]:
    return [b["c"] for b in _to_ohlcv(bars) if b["c"]]


def _sma(values: list[float], period: int) -> float | None:
    if len(values) < period or period <= 0:
        return None
    return sum(values[-period:]) / period


def _ema(values: list[float], period: int) -> float | None:
    """Standard EMA: seed with SMA(period), then EMA(prev, α)."""
    if len(values) < period or period <= 0:
        return None
    alpha = 2.0 / (period + 1.0)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = alpha * v + (1 - alpha) * ema
    return ema


def _rsi(closes: list[float], period: int = 14) -> float | None:
    """Wilder RSI. <period+1 bars → None."""
    n = len(closes)
    if n < period + 1 or period <= 0:
        return None
    gains = []
    losses = []
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains) / period
    avg_l = sum(losses) / period
    # Wilder smoothing over the remaining bars
    for i in range(period + 1, n):
        d = closes[i] - closes[i - 1]
        g = max(d, 0.0)
        l = max(-d, 0.0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100.0 - (100.0 / (1.0 + rs))


def _macd(closes: list[float], fast: int = 12, slow: int = 26,
          signal: int = 9) -> dict | None:
    """MACD line = EMA(fast) - EMA(slow); signal = EMA(signal) of MACD.

    Returns {line, signal, hist}. Needs >= slow+signal bars."""
    n = len(closes)
    if n < slow + signal or fast <= 0 or slow <= 0 or signal <= 0:
        return None
    # MACD series — only meaningful after slow bars, so the first
    # slow-1 EMAs are warmup; we compute the full EMA series and slice.
    alpha_f = 2.0 / (fast + 1.0)
    alpha_s = 2.0 / (slow + 1.0)
    # seed both EMAs at SMA of their period
    ema_f = sum(closes[:fast]) / fast
    ema_s = sum(closes[:slow]) / slow
    macd_series: list[float] = []
    # EMA(fast) starts at index fast-1 (SMA seed), so loop from fast
    # forward; EMA(slow) starts at slow-1. To align, we restart the
    # fast EMA at the slow seed index using the same warmup so the
    # MACD line is comparable across the same time indices.
    # Simpler: recompute both EMAs from index 0 with SMA seeds at
    # their own periods, then take MACD = ema_f - ema_s only where
    # both have warmed up (i.e., index >= slow-1).
    ema_f_series: list[float] = []
    ema_s_series: list[float] = []
    # fast EMA seeded with first `fast` values
    ef = sum(closes[:fast]) / fast
    for i in range(fast - 1, n):
        if i == fast - 1:
            ema_f_series.append(ef)
        else:
            ef = alpha_f * closes[i] + (1 - alpha_f) * ef
            ema_f_series.append(ef)
    es = sum(closes[:slow]) / slow
    for i in range(slow - 1, n):
        if i == slow - 1:
            ema_s_series.append(es)
        else:
            es = alpha_s * closes[i] + (1 - alpha_s) * es
            ema_s_series.append(es)
    # both series are aligned to indices slow-1..n-1 (fast is longer,
    # we trim to the slow-aligned tail)
    macd_series = [ema_f_series[-len(ema_s_series):][i] - ema_s_series[i]
                   for i in range(len(ema_s_series))]
    # signal = EMA(signal) of MACD series
    if len(macd_series) < signal:
        return None
    alpha_sig = 2.0 / (signal + 1.0)
    sig = sum(macd_series[:signal]) / signal
    for i in range(signal, len(macd_series)):
        sig = alpha_sig * macd_series[i] + (1 - alpha_sig) * sig
    line = macd_series[-1]
    hist = line - sig
    return {"line": round(line, 6), "signal": round(sig, 6),
            "hist": round(hist, 6)}


def _bollinger(closes: list[float], period: int = 20,
               nstd: float = 2.0) -> dict | None:
    """SMA(period) ± nstd * stdev. Returns {upper, middle, lower, width,
    pct_b}. pct_b = (close - lower) / (upper - lower)."""
    if len(closes) < period or period <= 0:
        return None
    window = closes[-period:]
    mid = sum(window) / period
    var = sum((x - mid) ** 2 for x in window) / period
    sd = math.sqrt(var)
    upper = mid + nstd * sd
    lower = mid - nstd * sd
    width = upper - lower
    last = closes[-1]
    pct_b = (last - lower) / width if width else 0.0
    return {"upper": round(upper, 6), "middle": round(mid, 6),
            "lower": round(lower, 6),
            "width": round(width, 6), "pct_b": round(pct_b, 6)}


def _atr(bars: list[dict], period: int = 14) -> float | None:
    """Wilder ATR on the closed bars. <period+1 → None."""
    bh = _to_ohlcv(bars)
    if len(bh) < period + 1 or period <= 0:
        return None
    trs = []
    for i in range(1, len(bh)):
        b = bh[i]
        p = bh[i - 1]
        tr = max(b["h"] - b["l"],
                 abs(b["h"] - p["c"]),
                 abs(b["l"] - p["c"]))
        trs.append(tr)
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return value


def _log_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0 and closes[i] > 0:
            out.append(math.log(closes[i] / closes[i - 1]))
    return out


def _realized_vol(closes: list[float], period: int = 20,
                  annualize: bool = True) -> float | None:
    """Std of last `period` log-returns × sqrt(TRADING_DAYS)."""
    rets = _log_returns(closes)
    if len(rets) < period:
        return None
    window = rets[-period:]
    mean = sum(window) / period
    var = sum((r - mean) ** 2 for r in window) / period
    sd = math.sqrt(var)
    if annualize:
        sd *= math.sqrt(TRADING_DAYS)
    return sd


def _vol_regime(realized_vol: float | None) -> str | None:
    if realized_vol is None:
        return None
    for label, lo, hi in _VOL_REGIME:
        if lo <= realized_vol < hi:
            return label
    return None


def _adx(bars: list[dict], period: int = 14) -> float | None:
    """Wilder ADX — trend strength only (no DI+/-). <period*2 → None.

    Implements the canonical ADX: TR, +DM, -DM smoothed Wilder-style;
    DI+ = 100 * sma(+DM)/sma(TR); DI- = 100 * sma(-DM)/sma(TR);
    DX = 100 * |DI+ - DI-| / (DI+ + DI-); ADX = Wilder-smoothed DX."""
    bh = _to_ohlcv(bars)
    n = len(bh)
    if n < period * 2 + 1 or period <= 0:
        return None
    trs = []
    plus_dm = []
    minus_dm = []
    for i in range(1, n):
        b, p = bh[i], bh[i - 1]
        up = b["h"] - p["h"]
        down = p["l"] - b["l"]
        if up > down and up > 0:
            plus_dm.append(up)
        else:
            plus_dm.append(0.0)
        if down > up and down > 0:
            minus_dm.append(down)
        else:
            minus_dm.append(0.0)
        trs.append(max(b["h"] - b["l"], abs(b["h"] - p["c"]),
                       abs(b["l"] - p["c"])))
    # Wilder smooth the first period of TR, +DM, -DM
    if len(trs) < period:
        return None

    def _smooth(arr: list[float], period: int) -> list[float]:
        out = [sum(arr[:period])]
        for v in arr[period:]:
            out.append(out[-1] - out[-1] / period + v)
        return out

    tr_s = _smooth(trs, period)
    pdm_s = _smooth(plus_dm, period)
    mdm_s = _smooth(minus_dm, period)
    dxs = []
    for i in range(len(tr_s)):
        if tr_s[i] == 0:
            continue
        di_p = 100.0 * pdm_s[i] / tr_s[i]
        di_m = 100.0 * mdm_s[i] / tr_s[i]
        denom = di_p + di_m
        if denom == 0:
            continue
        dxs.append(100.0 * abs(di_p - di_m) / denom)
    if len(dxs) < period:
        return None
    # ADX = Wilder-smoothed DX, seeded with simple average
    adx = sum(dxs[:period]) / period
    for d in dxs[period:]:
        adx = (adx * (period - 1) + d) / period
    return adx


def _stoch(bars: list[dict], k_period: int = 14,
           d_period: int = 3) -> dict | None:
    """Stochastic %K = 100*(c-low_low)/(high_high-low_low) over k_period;
    %D = SMA(d_period) of %K."""
    bh = _to_ohlcv(bars)
    if len(bh) < k_period + d_period - 1 or k_period <= 0:
        return None
    ks = []
    for i in range(k_period - 1, len(bh)):
        window = bh[i - k_period + 1: i + 1]
        hh = max(b["h"] for b in window)
        ll = min(b["l"] for b in window)
        c = bh[i]["c"]
        k = 100.0 * (c - ll) / (hh - ll) if (hh - ll) > 0 else 50.0
        ks.append(k)
    if len(ks) < d_period:
        return None
    d = sum(ks[-d_period:]) / d_period
    return {"k": round(ks[-1], 6), "d": round(d, 6)}


def _cci(bars: list[dict], period: int = 20) -> float | None:
    """Commodity Channel Index = (TP - SMA(TP)) / (0.015 * mean(|dev|))."""
    bh = _to_ohlcv(bars)
    if len(bh) < period or period <= 0:
        return None
    tps = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bh]
    window = tps[-period:]
    sma = sum(window) / period
    mean_dev = sum(abs(t - sma) for t in window) / period
    if mean_dev == 0:
        return 0.0
    return (tps[-1] - sma) / (0.015 * mean_dev)


def _obv(bars: list[dict]) -> float | None:
    """On-Balance Volume — close up adds vol, down subtracts, equal holds."""
    bh = _to_ohlcv(bars)
    if len(bh) < 2:
        return None
    obv = 0.0
    for i in range(1, len(bh)):
        if bh[i]["c"] > bh[i - 1]["c"]:
            obv += bh[i]["v"]
        elif bh[i]["c"] < bh[i - 1]["c"]:
            obv -= bh[i]["v"]
    return obv


# ---------------------------------------------------------- public API


def compute_indicators(bars: list[dict]) -> dict:
    """Indicator battery — TradingAgents' stockstats set, ported to pure
    python. Bars are OLDEST-FIRST (the board ships them oldest-first;
    documented here so consumers can re-orient if needed).

    Returns a dict with keys (None when the window is too short):
        rsi14, macd{line,signal,hist}, bbands{upper,middle,lower,width,
        pct_b}, atr14, atr_pct, realized_vol_20d, vol_regime,
        sma{20,50,200}, ema{12,26}, adx14, stoch{k,d}, cci20, obv

    Float tolerance 1e-6 in tests. Pure-python (numpy-free)."""
    bh = _to_ohlcv(bars)
    closes = [b["c"] for b in bh if b["c"]]
    if len(bh) < 2:
        return {"ok": False, "error": "insufficient bars",
                "bar_count": len(bh)}
    last_close = closes[-1] if closes else None
    atr14 = _atr(bh, 14)
    atr_pct = None
    if atr14 is not None and last_close:
        atr_pct = round(atr14 / last_close * 100.0, 6)
    rv = _realized_vol(closes, 20)
    out = {
        "ok": True,
        "bar_count": len(bh),
        "last_close": last_close,
        "rsi14": _rsi(closes, 14),
        "macd": _macd(closes, 12, 26, 9),
        "bbands": _bollinger(closes, 20, 2.0),
        "atr14": round(atr14, 6) if atr14 is not None else None,
        "atr_pct": atr_pct,
        "realized_vol_20d": round(rv, 6) if rv is not None else None,
        "vol_regime": _vol_regime(rv),
        "sma": {
            "20": round(_sma(closes, 20), 6) if _sma(closes, 20) else None,
            "50": round(_sma(closes, 50), 6) if _sma(closes, 50) else None,
            "200": round(_sma(closes, 200), 6) if _sma(closes, 200) else None,
        },
        "ema": {
            "12": round(_ema(closes, 12), 6) if _ema(closes, 12) else None,
            "26": round(_ema(closes, 26), 6) if _ema(closes, 26) else None,
        },
        "adx14": round(_adx(bh, 14), 6) if _adx(bh, 14) is not None else None,
        "stoch": _stoch(bh, 14, 3),
        "cci20": round(_cci(bh, 20), 6) if _cci(bh, 20) is not None else None,
        "obv": _obv(bh),
    }
    return out


def _daily_log_returns(symbol_closes: list[float],
                       bench_closes: list[float],
                       window: int) -> tuple[list[float], list[float]]:
    """Align two close series to their tail `window` log-returns. Returns
    (sym_rets, bench_rets) of equal length = min(window, min len of each
    log-return series after the tail slice)."""
    sr = _log_returns(symbol_closes)[-window:]
    br = _log_returns(bench_closes)[-window:]
    n = min(len(sr), len(br))
    return sr[-n:], br[-n:]


def compute_beta(symbol_bars: list[dict], benchmark_bars: list[dict],
                 window: int = 63) -> dict:
    """OLS regression symbol ~ benchmark over daily log-returns.

    Returns {beta, alpha, r_squared, correlation, n}. r_squared in [0,1].
    <2 returns → all None. Pure-python OLS (numpy-free) so the test
    fixture is reproducible."""
    sym_c = _closes(symbol_bars)
    bench_c = _closes(benchmark_bars)
    if len(sym_c) < 2 or len(bench_c) < 2:
        return {"beta": None, "alpha": None, "r_squared": None,
                "correlation": None, "n": 0}
    sr, br = _daily_log_returns(sym_c, bench_c, window)
    n = len(sr)
    if n < 2:
        return {"beta": None, "alpha": None, "r_squared": None,
                "correlation": None, "n": 0}
    mean_s = sum(sr) / n
    mean_b = sum(br) / n
    cov = sum((sr[i] - mean_s) * (br[i] - mean_b) for i in range(n)) / n
    var_s = sum((sr[i] - mean_s) ** 2 for i in range(n)) / n
    var_b = sum((br[i] - mean_b) ** 2 for i in range(n)) / n
    sd_s = math.sqrt(var_s) if var_s > 0 else 0.0
    sd_b = math.sqrt(var_b) if var_b > 0 else 0.0
    if var_b == 0 or sd_s == 0 or sd_b == 0:
        return {"beta": None, "alpha": None, "r_squared": None,
                "correlation": None, "n": n}
    beta = cov / var_b
    alpha = mean_s - beta * mean_b
    corr = cov / (sd_s * sd_b)
    r2 = corr * corr
    return {
        "beta": round(beta, 6),
        "alpha": round(alpha, 6),
        "r_squared": round(r2, 6),
        "correlation": round(corr, 6),
        "n": n,
    }


def _fetch_bars_for(symbol: str, data_root: str = "data") -> list[dict]:
    """Fail-soft bars fetch via the board pattern — returns the bars
    list or [] when unreachable."""
    try:
        d = fetch_detail(symbol, data_root)
        if d.get("ok"):
            return d.get("bars") or []
    except Exception:  # noqa: BLE001 — fail-soft, never raises
        pass
    return []


def compute_correlation_matrix(symbols: list[str],
                               window: int = 63,
                               data_root: str | None = None
                               ) -> dict:
    """Symmetric Pearson correlation matrix across `symbols` daily log-
    returns. Parallel fetch via ThreadPoolExecutor (board pattern, 30-min
    TTL on the underlying caches). Matrix is symmetric, diagonal = 1.0."""
    syms = [s for s in symbols if s]
    if not syms:
        return {"symbols": [], "window": window, "matrix": {}}
    root = data_root or "data"
    bars_map: dict[str, list[dict]] = {s: [] for s in syms}
    # parallel fetch — board caches TTL 120s, so 4 parallel is plenty
    with ThreadPoolExecutor(max_workers=min(8, len(syms))) as ex:
        futs = {ex.submit(_fetch_bars_for, s, root): s for s in syms}
        for fut in as_completed(futs):
            s = futs[fut]
            try:
                bars_map[s] = fut.result()
            except Exception:  # noqa: BLE001 — fail-soft per symbol
                bars_map[s] = []
    closes_map = {s: _closes(bars_map[s]) for s in syms}
    matrix: dict[str, dict[str, float | None]] = {s: {} for s in syms}
    for i, si in enumerate(syms):
        for sj in syms[i:]:
            if si == sj:
                matrix[si][sj] = 1.0
                matrix[sj][si] = 1.0
                continue
            sr, br = _daily_log_returns(closes_map[si], closes_map[sj],
                                        window)
            n = len(sr)
            if n < 2:
                matrix[si][sj] = None
                matrix[sj][si] = None
                continue
            mean_s = sum(sr) / n
            mean_b = sum(br) / n
            cov = sum((sr[i] - mean_s) * (br[i] - mean_b)
                      for i in range(n)) / n
            var_s = sum((sr[i] - mean_s) ** 2 for i in range(n)) / n
            var_b = sum((br[i] - mean_b) ** 2 for i in range(n)) / n
            sd_s = math.sqrt(var_s) if var_s > 0 else 0.0
            sd_b = math.sqrt(var_b) if var_b > 0 else 0.0
            if sd_s == 0 or sd_b == 0:
                matrix[si][sj] = None
                matrix[sj][si] = None
                continue
            r = cov / (sd_s * sd_b)
            # clamp to [-1, 1] against float drift
            r = max(-1.0, min(1.0, r))
            matrix[si][sj] = round(r, 6)
            matrix[sj][si] = round(r, 6)
    return {
        "symbols": syms,
        "window": window,
        "matrix": matrix,
    }


def detect_regime(bars: list[dict], lookback: int = 63) -> dict:
    """Trend + vol + breakout regime classification.

    trend: 'up' if last close > sma50 AND adx14 > 25, 'down' if last
    close < sma50 AND adx14 > 25, else 'range'. (sma20 fallback when
    sma50 is unavailable.) breakout_status: above/below/near sma200
    (2% band). liquidity_proxy: avg_volume_20d / volume_last (board
    bars carry no volume — degrades to None honestly)."""
    bh = _to_ohlcv(bars)
    closes = [b["c"] for b in bh if b["c"]]
    out = {"bar_count": len(bh)}
    if len(bh) < 14:
        out["trend"] = None
        out["trend_strength"] = None
        out["vol_regime"] = None
        out["liquidity_proxy"] = None
        out["breakout_status"] = None
        return out
    last = closes[-1]
    sma50 = _sma(closes, 50) or _sma(closes, 20)
    sma200 = _sma(closes, 200)
    adx = _adx(bh, 14)
    rv = _realized_vol(closes, 20)
    out["trend_strength"] = round(adx, 6) if adx is not None else None
    out["vol_regime"] = _vol_regime(rv)
    # trend classification
    trend = "range"
    if sma50 is not None and adx is not None and adx > 25:
        if last > sma50:
            trend = "up"
        elif last < sma50:
            trend = "down"
    out["trend"] = trend
    # breakout vs sma200
    if sma200 is not None and sma200 > 0:
        delta = (last - sma200) / sma200
        if abs(delta) <= 0.02:
            out["breakout_status"] = "near_sma200"
        elif delta > 0:
            out["breakout_status"] = "above_sma200"
        else:
            out["breakout_status"] = "below_sma200"
    else:
        out["breakout_status"] = None
    # liquidity proxy: avg vol 20d / last vol (None when no volume)
    vols = [b["v"] for b in bh[-20:] if b["v"] > 0]
    if vols:
        avg_v = sum(vols) / len(vols)
        last_v = bh[-1]["v"]
        out["liquidity_proxy"] = (round(avg_v / last_v, 6)
                                  if last_v else None)
    else:
        out["liquidity_proxy"] = None
    return out
