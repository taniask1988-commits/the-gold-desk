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
    benchmark_beta (None if no SPY bars provided),
    benchmark_beta_low_confidence + reason (R2-2-FIX D6)

Bars are OLDEST-FIRST (board shape). The snapshot is BUILT ONCE per
desk run; if bars are missing, returns an honest {ok: False, "no bars"}
block so the desk still runs (fail-soft per the brief).

R2-2-FIX (critic defects D1-D3):
- D1: pct regex now preserves the leading sign (`-8.8861%` stays -8.8861,
  not +8.8861) so an honest negative-pct claim yields delta=0 against a
  matching snapshot field — closes the false-positive the critic
  reproduced (delta=200% on a correct -8.8861 vs -8.8861 claim).
- D2: extract_numeric_claims is now a multi-pattern extractor that
  catches named-indicator claims (RSI 47.78, MACD hist -0.151, ATR 7.219,
  beta 0.196, %B 0.454, "change 5d -0.0548", "realized vol 1.5",
  SMA/EMA/ADX/OBV/CCI/Stoch), plain numeric price/volume claims with
  disambiguating context ("closed at 309.86", "volume 11.23M"), and
  signed-pct claims (+2.33%, -0.155%). Closes the false-negative the
  critic reproduced ("RSI is 99.5" → 0 flags).
- D3: a verbatim technician thesis (the builder's AAPL prose) now
  yields >=5 claims instead of 0 — the discipline stops being
  decorative on real prose.
"""
from __future__ import annotations

import re
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
              "correlation": None, "n": 0,
              "low_confidence": True,
              "low_confidence_reason": "no benchmark bars"}
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
        "benchmark_beta_low_confidence": beta.get("low_confidence",
                                                    False),
        "benchmark_beta_low_confidence_reason":
            beta.get("low_confidence_reason"),
        "bar_count": len(bh),
    }


# ----------------------------------------------------------------- claim flag
# R2-2-FIX D1+D2+D3: the multi-pattern claim extractor. The critic proved
# the old regex (only $price + N.N%) was both over-active on negative
# pcts (sign-stripping false positive D1) and under-active on the
# technician's most-cited indicators (RSI/MACD/ATR/beta/%B plain-number
# false negatives D2). The new extractor is multi-pattern:
#
#   (a) $-prefixed dollar amounts      → kind 'price' (matches last_close)
#   (b) signed-or-unsigned pct        → kind 'pct' (matches closest
#       snapshot pct field: last_change_pct / change_pct_{5d,20d,63d}
#       / atr_pct / realized_vol_20d)
#   (c) named indicator with value    → kind 'rsi' | 'macd_hist' |
#       'atr' | 'atr_pct' | 'realized_vol' | 'beta' | 'bb_pct_b' |
#       'sma' | 'ema' | 'adx' | 'cci' | 'stoch' | 'obv'
#       (each matches its snapshot counterpart by name)
#   (d) named "change Nd" reference   → kind 'pct_change_Nd' etc.,
#       matched directly against the named snapshot pct field
#   (e) named "last close" / "closed at" / "close at" plain number →
#       kind 'price' (matches last_close; closes the "AAPL closed at
#       309.86" gap — the technician cites closes without a $ prefix).
#       ALSO supports the reversed form: "309.86 last_close".
#   (f) named "volume" plain number (with optional K/M/B suffix) →
#       kind 'volume' (matches volume_last OR volume_avg_20d, whichever
#       is closer)
#
# A claim dict is {kind, raw, value, name?}. flag_claim_conflicts routes
# each claim by `kind` to the matching snapshot field and flags when
# relative delta > 0.5% OR absolute delta > 0.01 (the dual threshold
# covers small-magnitude fields where the relative delta alone would
# under-flag — e.g. a MACD hist drift from 0.15 to 0.20 is 33% relative
# but 0.05 absolute, both fire; a drift from 0.0 to 0.02 is inf% rel
# but 0.02 abs which fires the abs threshold).
#
# Gap convention: `[^+\d\-]{0,40}?` is a lazy match for up to 40
# non-numeric, non-sign chars — this catches "RSI is 47.78" (gap " is "),
# "RSI14 at 47.78" (gap " at "), "Bollinger %B sits at 0.454" (gap
# " sits at "), "change_pct_5d is just -0.0548" (gap " is just "),
# "MACD hist at -0.15117" (gap " at ").

# (a) $price with optional K/M/B suffix (unchanged from R2-2)
_PRICE_RE = re.compile(
    r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)\s?([KMBkmb]?)",
    re.UNICODE)

# (b) signed-or-unsigned pct — R2-2-FIX D1: the leading [-+]? captures
# the minus sign so "-8.8861%" becomes value=-8.8861 (not +8.8861).
_PCT_RE = re.compile(r"([-+]?\d+(?:\.\d+)?)\s?%")

# numeric captures + lazy gap
_NUM = r"([-+]?\d+(?:\.\d+)?)"
_KMB = r"([KMBkmb]?)"
_GAP = r"[^+\d\-]{0,40}?"  # lazy gap, no digits or signs

# (c1) RSI — "RSI is 47.78", "RSI 47.78", "RSI=47.78", "RSI14 47.78"
_RSI_RE = re.compile(
    rf"(?<![\w])RSI(?:14)?{_GAP}{_NUM}", re.IGNORECASE)

# (c2) MACD hist — "MACD hist -0.151", "MACD histogram 0.5",
#     "MACD hist at -0.15117"
_MACD_HIST_RE = re.compile(
    rf"(?<![\w])MACD\s*(?:hist|histogram){_GAP}{_NUM}",
    re.IGNORECASE)

# (c4) ATR pct — "atr_pct 2.329614", "ATR pct 2.33", "ATR% 2.33"
#     MUST run before raw ATR so "atr_pct 2.33" isn't captured as "atr 2.33"
_ATR_PCT_RE = re.compile(
    rf"(?<![\w])ATR(?:14)?\s*(?:pct|_pct|%)_?{_GAP}{_NUM}",
    re.IGNORECASE)

# (c3) ATR raw value — "ATR 7.219", "ATR14 7.219", "ATR is 2251"
#     The negative-lookahead `(?!\s*\d*\s*%)` prevents matching "ATR pct 2.33"
_ATR_RE = re.compile(
    rf"(?<![\w])ATR(?:14)?{_GAP}{_NUM}(?!\s*\d*\s*%)",
    re.IGNORECASE)

# (c5) beta — "beta 0.196", "beta vs SPY 0.196", "beta is -0.476"
_BETA_RE = re.compile(
    rf"(?<![\w])beta{_GAP}{_NUM}", re.IGNORECASE)

# (c6) %B / Bollinger pct_b — "Bollinger %B 0.454", "%B 0.454",
#     "pct_b 0.454", "Bollinger %b sits at 0.453573"
_BB_PCT_B_RE = re.compile(
    rf"(?:Bollinger\s*)?%b{_GAP}{_NUM}|"
    rf"(?<![\w])pct_b{_GAP}{_NUM}|"
    rf"Bollinger\s+(?:pct_b|pct b|percent b|percent_b){_GAP}{_NUM}",
    re.IGNORECASE)

# (c7) realized vol — "realized vol 0.317", "realized_vol_20d 0.317",
#     "20d realized vol 0.317"
_REALIZED_VOL_RE = re.compile(
    rf"(?<![\w])(?:realized[_\s]vol(?:atility)?(?:_20d|_20)?"
    rf"|20d\s+realized\s+vol){_GAP}{_NUM}",
    re.IGNORECASE)

# (c8) vol regime — qualitative label, handled separately (no number).
_VOL_REGIME_RE = re.compile(
    r"\bvol(?:atility)?\s+regime\s+(low|normal|high|extreme)\b",
    re.IGNORECASE)

# (c9) SMA / EMA — "SMA20 130.5", "EMA12 152.0", "SMA(50) 132", "SMA 50 132"
# (snapshot fields: sma{20,50,200}, ema{12,26})
_SMA_RE = re.compile(
    rf"(?<![\w])SMA\s*\(?(\d+)\)?{_GAP}{_NUM}", re.IGNORECASE)
_EMA_RE = re.compile(
    rf"(?<![\w])EMA\s*\(?(\d+)\)?{_GAP}{_NUM}", re.IGNORECASE)

# (c10) ADX — "ADX14 25.3", "ADX 25"
_ADX_RE = re.compile(
    rf"(?<![\w])ADX(?:14)?{_GAP}{_NUM}", re.IGNORECASE)

# (c11) Stoch %K/%D — "Stoch %K 80", "Stochastic 80"
_STOCH_RE = re.compile(
    rf"(?<![\w])Stoch(?:astic)?\s*(?:%k)?{_GAP}{_NUM}",
    re.IGNORECASE)

# (c12) CCI — "CCI20 100", "CCI 100"
_CCI_RE = re.compile(
    rf"(?<![\w])CCI(?:20)?{_GAP}{_NUM}", re.IGNORECASE)

# (c13) OBV — "OBV 1.2M", "OBV -3.5K"
_OBV_RE = re.compile(
    rf"(?<![\w])OBV{_GAP}{_NUM}\s?{_KMB}", re.IGNORECASE)

# (d) named "change Nd" — "change 5d -0.0548", "change_pct_20d -8.8861",
#     "5d change -0.0548", "20d change -8.8861", "5d pct -0.0548",
#     "change_pct_5d is just -0.0548", "change_pct_20d of -8.8861"
_CHANGE_ND_RE = re.compile(
    rf"(?<![\w])(\d+)[dD]\s+change{_GAP}{_NUM}|"
    rf"(?<![\w])change(?:_pct)?[_\s]+(\d+)[dD]{_GAP}{_NUM}|"
    rf"(?<![\w])change\s+(\d+)[dD]{_GAP}{_NUM}",
    re.IGNORECASE)

# (e) named "last close" / "closed at" / "close at" / "price at"
#     plain number — the technician cites closes without $ prefix
#     ("AAPL closed at 309.86"). Numeric supports K/M/B suffix.
#     ALSO supports the reversed form: "309.86 last_close" /
#     "309.86 last close" (the builder's verbatim AAPL thesis uses
#     this order).
_LAST_CLOSE_RE = re.compile(
    rf"(?:last[\s_]+close|closed\s+at|closes?\s+at|"
    rf"price\s+(?:is|at)\s*(?:approximately\s*)?){_GAP}{_NUM}\s?{_KMB}|"
    rf"{_NUM}\s?{_KMB}{_GAP}(?:last[\s_]+close|last[\s_]+price)\b",
    re.IGNORECASE)

# (f) named "volume" plain number — "volume 11.23M", "vol 1.2B"
_VOLUME_RE = re.compile(
    rf"(?<![\w])(?:volume|vol){_GAP}{_NUM}\s?{_KMB}",
    re.IGNORECASE)


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


def _to_float(raw: str, suffix: str | None = None) -> float | None:
    """Parse a numeric capture, applying K/M/B suffix when supplied."""
    try:
        v = float(raw.replace(",", ""))
    except (TypeError, ValueError):
        return None
    s = (suffix or "").upper()
    if s == "K":
        v *= 1_000.0
    elif s == "M":
        v *= 1_000_000.0
    elif s == "B":
        v *= 1_000_000_000.0
    return v


def extract_numeric_claims(thesis: str) -> list[dict]:
    """Pull every numeric claim out of thesis prose.

    R2-2-FIX D1+D2: now multi-pattern. Returns a list of
    {kind, raw, value, name?} dicts. `kind` routes the claim to its
    snapshot counterpart in flag_claim_conflicts:
       'price' | 'pct' | 'rsi' | 'macd_hist' | 'atr' | 'atr_pct'
       | 'realized_vol' | 'beta' | 'bb_pct_b' | 'sma' | 'ema'
       | 'adx' | 'cci' | 'stoch' | 'obv' | 'volume' | 'pct_change_Nd'
       | 'vol_regime'
    A `vol_regime` claim has `value: None` and a `label` field instead —
    flag_claim_conflicts compares it against the snapshot's regime_labels.vol
    field as a string-equality check.
    """
    out: list[dict] = []
    if not thesis:
        return out
    seen_spans: list[tuple[int, int]] = []  # to avoid double-matching

    def _overlaps_existing(s: int, e: int) -> bool:
        for a, b in seen_spans:
            if not (e <= a or s >= b):
                return True
        return False

    def _claim(kind: str, m: re.Match, value: float | None,
               name: str | None = None, label: str | None = None):
        if _overlaps_existing(m.start(), m.end()):
            return
        seen_spans.append((m.start(), m.end()))
        out.append({
            "kind": kind, "raw": m.group(0).strip(),
            "value": value, "name": name, "label": label,
        })

    # (a) $price — first so it claims "$79000" before any plain-number
    # pattern tries the digits
    for m in _PRICE_RE.finditer(thesis):
        v = _normalize_price(m.group(1), m.group(2))
        if v is not None:
            _claim("price", m, v)

    # (b) signed-or-unsigned pct
    for m in _PCT_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("pct", m, v)

    # (c1) RSI
    for m in _RSI_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("rsi", m, v, name="rsi14")

    # (c2) MACD hist
    for m in _MACD_HIST_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("macd_hist", m, v, name="macd_hist")

    # (c4) ATR pct — MUST run before raw ATR so "atr_pct 2.33" isn't
    # captured as "atr 2.33" by the looser pattern
    for m in _ATR_PCT_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("atr_pct", m, v, name="atr_pct")

    # (c3) ATR raw value — negative-lookahead prevents matching
    # "ATR pct 2.33" (the pct pattern above already took it via the
    # earlier pass when both run; the lookahead is belt-and-braces for
    # direct invocation).
    for m in _ATR_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("atr", m, v, name="atr14_value")

    # (c5) beta
    for m in _BETA_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("beta", m, v, name="benchmark_beta")

    # (c6) %B / Bollinger pct_b
    for m in _BB_PCT_B_RE.finditer(thesis):
        # three alternations, each with its own numeric capture group
        raw = next((g for g in (m.group(1), m.group(2), m.group(3))
                    if g is not None), None)
        if raw is None:
            continue
        v = _to_float(raw)
        if v is not None:
            _claim("bb_pct_b", m, v, name="bb_pct_b")

    # (c7) realized vol
    for m in _REALIZED_VOL_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("realized_vol", m, v, name="realized_vol_20d")

    # (c8) vol regime — qualitative label
    for m in _VOL_REGIME_RE.finditer(thesis):
        _claim("vol_regime", m, None,
               name="regime_labels.vol", label=m.group(1).lower())

    # (c9) SMA / EMA with named period (period is group 1, value group 2)
    for m in _SMA_RE.finditer(thesis):
        period = m.group(1)
        v = _to_float(m.group(2))
        if v is not None and period in ("20", "50", "200"):
            _claim("sma", m, v, name=f"sma.{period}")
    for m in _EMA_RE.finditer(thesis):
        period = m.group(1)
        v = _to_float(m.group(2))
        if v is not None and period in ("12", "26"):
            _claim("ema", m, v, name=f"ema.{period}")

    # (c10) ADX
    for m in _ADX_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("adx", m, v, name="adx14")

    # (c11) Stoch
    for m in _STOCH_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("stoch", m, v, name="stoch.k")

    # (c12) CCI
    for m in _CCI_RE.finditer(thesis):
        v = _to_float(m.group(1))
        if v is not None:
            _claim("cci", m, v, name="cci20")

    # (c13) OBV — value (group 1), suffix (group 2)
    for m in _OBV_RE.finditer(thesis):
        v = _to_float(m.group(1), m.group(2))
        if v is not None:
            _claim("obv", m, v, name="obv")

    # (d) named "change Nd" — explicit period + value
    for m in _CHANGE_ND_RE.finditer(thesis):
        # three alternations, each with period + value groups:
        # group(1)/group(2), group(3)/group(4), group(5)/group(6)
        period = next((g for g in (m.group(1), m.group(3), m.group(5))
                      if g is not None), None)
        raw_v = next((g for g in (m.group(2), m.group(4), m.group(6))
                     if g is not None), None)
        if period is None or raw_v is None:
            continue
        v = _to_float(raw_v)
        if v is not None and period in ("5", "20", "63"):
            _claim(f"pct_change_{period}d", m, v,
                   name=f"change_pct_{period}d")

    # (e) named "last close" / "closed at" / "close at" / "price at"
    # OR reversed "NNN last_close". The regex has two alternations,
    # each with value + optional suffix. For the FIRST alternation
    # (label-then-number) the groups are 1=value, 2=suffix. For the
    # SECOND alternation (number-then-label) the groups are 3=value,
    # 4=suffix.
    for m in _LAST_CLOSE_RE.finditer(thesis):
        if m.group(1) is not None:
            # label-then-number form
            v = _to_float(m.group(1), m.group(2))
        elif m.group(3) is not None:
            # number-then-label form
            v = _to_float(m.group(3), m.group(4))
        else:
            continue
        if v is not None:
            _claim("price", m, v, name="last_close")

    # (f) named "volume" plain number
    for m in _VOLUME_RE.finditer(thesis):
        v = _to_float(m.group(1), m.group(2))
        if v is not None:
            _claim("volume", m, v, name="volume_last_or_avg_20d")

    # sort by position for deterministic ordering
    def _pos(c: dict) -> int:
        idx = thesis.find(c["raw"])
        return idx if idx >= 0 else 0
    out.sort(key=_pos)
    return out


def _compare_closest(claim: float, snapshot_value: float) -> float | None:
    """Relative delta between a claim and the snapshot value."""
    if snapshot_value == 0:
        return None
    return abs(claim - snapshot_value) / abs(snapshot_value) * 100.0


def _abs_delta(claim: float, snapshot_value: float) -> float:
    return abs(claim - snapshot_value)


def _flag(claim: dict, kind: str, snapshot_field: str,
          snapshot_value: float, delta_threshold_pct: float,
          abs_threshold: float = 0.01) -> dict | None:
    """Return a conflict dict if the claim drifts from the snapshot by
    either threshold; None otherwise. Handles the case where the
    snapshot_value is None (no comparable ground-truth → no flag)."""
    if snapshot_value is None or claim["value"] is None:
        return None
    try:
        sv = float(snapshot_value)
        cv = float(claim["value"])
    except (TypeError, ValueError):
        return None
    rel = _compare_closest(cv, sv)
    ad = _abs_delta(cv, sv)
    # relative threshold OR absolute threshold (covers small-magnitude
    # fields where relative alone under-flags)
    fires_rel = rel is not None and rel > delta_threshold_pct
    fires_abs = ad > abs_threshold
    if not (fires_rel or fires_abs):
        return None
    return {
        "kind": kind,
        "claim": claim["raw"],
        "snapshot_field": snapshot_field,
        "claim_value": round(cv, 6),
        "snapshot_value": round(sv, 6),
        "delta_pct": round(rel, 4) if rel is not None else None,
        "delta_abs": round(ad, 6),
        "name": claim.get("name"),
    }


def flag_claim_conflicts(thesis: str, snapshot: dict,
                         delta_threshold_pct: float = 0.5
                         ) -> list[dict]:
    """Compare numeric claims in `thesis` to `snapshot` ground-truth.

    R2-2-FIX D1+D2+D3: now multi-kind. Routes each claim by `kind` to
    its matching snapshot field and flags when relative delta >
    `delta_threshold_pct` (default 0.5%) OR absolute delta > 0.01
    (covers small-magnitude fields like MACD hist / beta / %B where
    relative alone under-flags).

    Returns [{kind, claim, snapshot_field, claim_value,
              snapshot_value, delta_pct, delta_abs, name?}] — empty
    list when no conflict. Never raises.
    """
    if not snapshot or not snapshot.get("ok"):
        return []
    out: list[dict] = []
    ABS = 0.01
    # build a kind → (snapshot_field, snapshot_value) routing table
    # for the named kinds
    named_routes: dict[str, tuple[str, float | None]] = {
        "rsi": ("rsi14", snapshot.get("rsi14")),
        "macd_hist": ("macd_hist", snapshot.get("macd_hist")),
        "atr": ("atr14_value", snapshot.get("atr14_value")),
        "atr_pct": ("atr_pct", snapshot.get("atr_pct")),
        "realized_vol": ("realized_vol_20d",
                         snapshot.get("realized_vol_20d")),
        "beta": ("benchmark_beta", snapshot.get("benchmark_beta")),
        "bb_pct_b": ("bb_pct_b", snapshot.get("bb_pct_b")),
        "adx": ("adx14", snapshot.get("adx14")),
        "cci": ("cci20", snapshot.get("cci20")),
        "stoch": ("stoch.k",
                  (snapshot.get("stoch") or {}).get("k")
                  if isinstance(snapshot.get("stoch"), dict)
                  else None),
        "obv": ("obv", snapshot.get("obv")),
    }
    # SMA/EMA: snapshot has nested dicts (snapshot from compute_indicators
    # does; build_verified_snapshot doesn't ship them but the test
    # fixtures might supply the richer form)
    for period in ("20", "50", "200"):
        named_routes[f"sma.{period}"] = (
            f"sma.{period}",
            (snapshot.get("sma") or {}).get(period)
            if isinstance(snapshot.get("sma"), dict) else None)
    for period in ("12", "26"):
        named_routes[f"ema.{period}"] = (
            f"ema.{period}",
            (snapshot.get("ema") or {}).get(period)
            if isinstance(snapshot.get("ema"), dict) else None)
    # named change pct claims (the kind is "pct_change_Nd")
    for period in ("5", "20", "63"):
        named_routes[f"pct_change_{period}d"] = (
            f"change_pct_{period}d",
            snapshot.get(f"change_pct_{period}d"))
    # vol_regime label — string-equality flag, not numeric
    vol_regime_snap = (snapshot.get("regime_labels") or {}).get("vol")

    snapshot_price = snapshot.get("last_close")
    # pct claims match against the closest snapshot pct field (unchanged
    # from R2-2 except for the sign-preserved value)
    snapshot_pcts = {
        "last_change_pct": snapshot.get("last_change_pct"),
        "change_pct_5d": snapshot.get("change_pct_5d"),
        "change_pct_20d": snapshot.get("change_pct_20d"),
        "change_pct_63d": snapshot.get("change_pct_63d"),
        "atr_pct": snapshot.get("atr_pct"),
        "realized_vol_20d": snapshot.get("realized_vol_20d"),
    }

    for claim in extract_numeric_claims(thesis):
        kind = claim["kind"]
        # vol regime: string-equality
        if kind == "vol_regime":
            label = claim.get("label")
            if label and vol_regime_snap and label != vol_regime_snap:
                out.append({
                    "kind": "vol_regime",
                    "claim": claim["raw"],
                    "snapshot_field": "regime_labels.vol",
                    "claim_value": label,
                    "snapshot_value": vol_regime_snap,
                    "delta_pct": None,
                    "delta_abs": None,
                    "name": claim.get("name"),
                })
            continue
        # price (covers $-prefixed AND "closed at NNN" claims)
        if kind == "price" and snapshot_price is not None:
            f = _flag(claim, "price", "last_close",
                      snapshot_price, delta_threshold_pct, ABS)
            if f:
                out.append(f)
            continue
        # volume — match against volume_last OR volume_avg_20d, whichever
        # is closer
        if kind == "volume":
            vol_last = snapshot.get("volume_last")
            vol_avg = snapshot.get("volume_avg_20d")
            candidates: list[tuple[str, float]] = []
            if vol_last is not None:
                candidates.append(("volume_last", float(vol_last)))
            if vol_avg is not None:
                candidates.append(("volume_avg_20d", float(vol_avg)))
            if not candidates:
                continue
            # pick the closest by relative delta
            best_field, best_val = min(
                candidates,
                key=lambda fv: _compare_closest(
                    float(claim["value"]), fv[1]) or float("inf"))
            f = _flag(claim, "volume", best_field, best_val,
                      delta_threshold_pct, ABS)
            if f:
                out.append(f)
            continue
        # generic pct — match against the closest snapshot pct field
        if kind == "pct":
            best_field = None
            best_delta = None
            best_val = None
            for k, v in snapshot_pcts.items():
                if v is None:
                    continue
                d = _compare_closest(float(claim["value"]), float(v))
                if d is None:
                    continue
                if best_delta is None or d < best_delta:
                    best_field, best_delta, best_val = k, d, v
            if best_field is not None and best_val is not None:
                f = _flag(claim, "pct", best_field, best_val,
                          delta_threshold_pct, ABS)
                if f:
                    out.append(f)
            continue
        # named pct_change_Nd — direct field match (no closest-pick)
        if kind.startswith("pct_change_"):
            field, val = named_routes.get(kind, (None, None))
            if field and val is not None:
                f = _flag(claim, kind, field, val,
                          delta_threshold_pct, ABS)
                if f:
                    out.append(f)
            continue
        # named indicators (rsi/macd_hist/atr/atr_pct/realized_vol/beta/
        # bb_pct_b/sma.N/ema.N/adx/cci/stoch/obv) — direct field match.
        # Also fallback to the claim's `name` (handles "sma.50" routed
        # through kind="sma" with name="sma.50").
        route = named_routes.get(kind) or named_routes.get(
            claim.get("name", ""))
        if route:
            field, val = route
            if val is not None:
                f = _flag(claim, kind, field, val,
                          delta_threshold_pct, ABS)
                if f:
                    out.append(f)
            continue
    return out
