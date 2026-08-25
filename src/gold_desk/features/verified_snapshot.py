"""R2-2 DETERMINISTIC VERIFIED SNAPSHOT — the no-LLM ground-truth block.

Mirrors tradingagents/dataflows/market_data_validator.py:1-25: a no-LLM
ground-truth OHLCV+indicator block the technical agent (technician
persona) is told to treat as "the source of truth for any exact
OHLCV, price-level, or indicator-value claim", with a conflict-flagging
discipline the engine enforces on its thesis prose (R2-5 evidence-
checker is the full pass; this piece is the deterministic snapshot +
claim-flag discipline layer).

ALL NUMBERS here are computed DETERMINISTICALLY from bars + indicators.
NO LLM call anywhere in this module — the snapshot is the machine-
checked ground truth against which LLM prose is judged.

Field inventory (the technician's exact-claim surface):
    symbol, as_of, last_close, last_change_pct, change_pct_5d,
    change_pct_20d, change_pct_63d, atr14_value, atr_pct,
    realized_vol_20d, rsi14, macd_hist, bb_pct_b, volume_last,
    volume_avg_20d, regime_labels:{trend, vol, breakout},
    benchmark_beta (None if no SPY bars provided)

Bars are OLDEST-FIRST (board shape). The snapshot is BUILT ONCE per
desk run; if bars are missing, returns an honest {ok: False, "no bars"}
block so the desk still runs (fail-soft per the brief).
"""
from __future__ import annotations

from datetime import datetime, timezone

from .quant import compute_beta, compute_indicators, detect_regime, \
    _to_ohlcv, _closes


def _pct_change(closes: list[float], lookback: int) -> float | None:
    """Pct change over the last `lookback` closes. <lookback+1 → None."""
    if len(closes) < lookback + 1 or lookback <= 0:
        return None
    anchor = closes[-(lookback + 1)]
    last = closes[-1]
    if anchor == 0:
        return None
    return round((last - anchor) / anchor * 100.0, 4)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def build_verified_snapshot(symbol: str, bars: list[dict],
                           indicators: dict | None = None,
                           benchmark_bars: list[dict] | None = None
                           ) -> dict:
    """Build the deterministic verified snapshot.

    Args:
        symbol: the ticker.
        bars: OLDEST-FIRST board shape list of {ts,o,h,l,c}.
        indicators: optional pre-computed compute_indicators() output
            (re-computed when None so callers can re-use a cached
            battery).
        benchmark_bars: optional SPY bars for beta computation (None
            → benchmark_beta is None).

    Returns a dict — the technician treats every numeric field here
    as ground truth. When bars are missing, returns {ok: False,
    "no bars"} so the desk still runs (fail-soft per the brief).
    """
    bh = _to_ohlcv(bars)
    if not bh:
        return {"ok": False, "symbol": symbol, "as_of": _now_iso(),
                "error": "no bars", "regime_labels": {},
                "no_bars": True}
    closes = [b["c"] for b in bh if b["c"]]
    ind = indicators if indicators is not None else compute_indicators(bars)
    if not ind.get("ok"):
        # compute_indicators failed — but bars exist. Surface the failure
        # honestly while still reporting the bars we have.
        return {"ok": False, "symbol": symbol, "as_of": _now_iso(),
                "error": ind.get("error", "indicators failed"),
                "bar_count": len(bh), "no_bars": False,
                "regime_labels": {}}
    regime = detect_regime(bh, lookback=63)
    last = closes[-1] if closes else None
    # 1d change vs prior close
    last_change_pct = None
    if len(closes) >= 2 and closes[-2]:
        last_change_pct = round(
            (closes[-1] - closes[-2]) / closes[-2] * 100.0, 4)
    change_5d = _pct_change(closes, 5)
    change_20d = _pct_change(closes, 20)
    change_63d = _pct_change(closes, 63)
    vols = [b["v"] for b in bh if b["v"] > 0]
    volume_last = vols[-1] if vols else None
    volume_avg_20d = (sum(vols[-20:]) / len(vols[-20:])
                       if len(vols[-20:]) else None)
    macd = ind.get("macd") or {}
    bb = ind.get("bbands") or {}
    bench_bars = benchmark_bars or []
    beta = compute_beta(bars, bench_bars, 63) if len(bench_bars) > 5 \
        else {"beta": None, "alpha": None, "r_squared": None,
              "correlation": None, "n": 0}
    return {
        "ok": True,
        "symbol": symbol,
        "as_of": _now_iso(),
        "last_close": round(last, 6) if last is not None else None,
        "last_change_pct": last_change_pct,
        "change_pct_5d": change_5d,
        "change_pct_20d": change_20d,
        "change_pct_63d": change_63d,
        "atr14_value": ind.get("atr14"),
        "atr_pct": ind.get("atr_pct"),
        "realized_vol_20d": ind.get("realized_vol_20d"),
        "rsi14": ind.get("rsi14"),
        "macd_hist": macd.get("hist"),
        "bb_pct_b": bb.get("pct_b"),
        "volume_last": volume_last,
        "volume_avg_20d": (round(volume_avg_20d, 6)
                            if volume_avg_20d is not None else None),
        "regime_labels": {
            "trend": regime.get("trend"),
            "vol": regime.get("vol_regime"),
            "breakout": regime.get("breakout_status"),
        },
        "benchmark_beta": beta.get("beta"),
        "bar_count": len(bh),
    }


# ----------------------------------------------------------------- claim flag

# regex for "$NNN[.NN][KMB]?" and "NN[.NN]%" in the LLM's prose. Matches
# patterns a verifier can compare to verified-snapshot numbers. The price
# regex handles plain big integers ($79000), thousands-separated
# ($79,443), decimal ($79.44), and an optional K/M/B suffix. The pct
# regex matches "NN.N%".
import re as _re

_PRICE_RE = _re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([KMBkmb]?)",
    _re.UNICODE)
_PCT_RE = _re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _normalize_price(raw: str, suffix: str) -> float | None:
    """$79K → 79000.0, $1.2M → 1200000.0, $79,443 → 79443.0."""
    try:
        v = float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None
    suffix = (suffix or "").upper()
    if suffix == "K":
        v *= 1_000.0
    elif suffix == "M":
        v *= 1_000_000.0
    elif suffix == "B":
        v *= 1_000_000_000.0
    return v


def extract_numeric_claims(thesis: str) -> list[dict]:
    """Pull $(price) and (pct) claims out of thesis prose.

    Returns a list of {kind: 'price'|'pct', raw: str, value: float}.
    Used by the engine to compare against verified-snapshot numbers.
    """
    out: list[dict] = []
    if not thesis:
        return out
    for m in _PRICE_RE.finditer(thesis):
        v = _normalize_price(m.group(1), m.group(2))
        if v is not None:
            out.append({"kind": "price", "raw": m.group(0).strip(),
                        "value": v})
    for m in _PCT_RE.finditer(thesis):
        try:
            v = float(m.group(1))
        except (TypeError, ValueError):
            continue
        out.append({"kind": "pct", "raw": m.group(0).strip(), "value": v})
    return out


def _compare_closest(claim: float, snapshot_value: float) -> float | None:
    """Relative delta between a claim and the snapshot value."""
    if not snapshot_value:
        return None
    return abs(claim - snapshot_value) / abs(snapshot_value) * 100.0


def flag_claim_conflicts(thesis: str, snapshot: dict,
                         delta_threshold_pct: float = 0.5
                         ) -> list[dict]:
    """Compare numeric claims in `thesis` to `snapshot` ground-truth.

    Logs a `claim_conflicts` entry whenever a claim's relative delta
    against the closest snapshot number exceeds `delta_threshold_pct`.

    A "price" claim matches against last_close. A "pct" claim matches
    against the snapshot's pct fields (last_change_pct, change_pct_5d,
    change_pct_20d, change_pct_63d, atr_pct, realized_vol_20d).

    Returns [{kind, claim, snapshot_field, claim_value,
              snapshot_value, delta_pct}] — empty list when no
    conflict. Never raises.
    """
    if not snapshot or not snapshot.get("ok"):
        return []
    out: list[dict] = []
    snapshot_pcts = {
        "last_change_pct": snapshot.get("last_change_pct"),
        "change_pct_5d": snapshot.get("change_pct_5d"),
        "change_pct_20d": snapshot.get("change_pct_20d"),
        "change_pct_63d": snapshot.get("change_pct_63d"),
        "atr_pct": snapshot.get("atr_pct"),
        "realized_vol_20d": snapshot.get("realized_vol_20d"),
    }
    snapshot_price = snapshot.get("last_close")
    for claim in extract_numeric_claims(thesis):
        if claim["kind"] == "price" and snapshot_price:
            delta = _compare_closest(claim["value"], snapshot_price)
            if delta is not None and delta > delta_threshold_pct:
                out.append({
                    "kind": "price", "claim": claim["raw"],
                    "snapshot_field": "last_close",
                    "claim_value": round(claim["value"], 6),
                    "snapshot_value": round(snapshot_price, 6),
                    "delta_pct": round(delta, 4),
                })
        elif claim["kind"] == "pct":
            # find the snapshot pct closest to the claim
            best_field = None
            best_delta = None
            for k, v in snapshot_pcts.items():
                if v is None:
                    continue
                d = _compare_closest(claim["value"], v)
                if d is None:
                    continue
                if best_delta is None or d < best_delta:
                    best_field, best_delta = k, d
            if best_field is not None and best_delta is not None \
                    and best_delta > delta_threshold_pct:
                out.append({
                    "kind": "pct", "claim": claim["raw"],
                    "snapshot_field": best_field,
                    "claim_value": round(claim["value"], 6),
                    "snapshot_value": round(
                        snapshot_pcts[best_field], 6),
                    "delta_pct": round(best_delta, 4),
                })
    return out
