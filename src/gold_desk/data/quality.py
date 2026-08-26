"""§5.3 — data quality hard fails, checked on every bar BEFORE any setup work.

Gold-specific paranoia encoded here and pinned by tests:
  Sunday open / Monday gap        -> OUTLIER_PRICE on absurd gap returns
  Rollover spread explosion       -> SPREAD
  Broker H1 alignment             -> TZ_MISALIGN when bar not hour-aligned
  Indicator on forming bar        -> sources only return closed bars (tested)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import iso, parse_ts
from ..version import is_blocked
from .model import Bar, Quote

QUALITY_CODES = [
    "STALE_DATA", "MISSING_BAR", "OUTLIER_PRICE", "SPREAD",
    "SOURCE_MISMATCH", "TZ_MISALIGN", "NEWS_UNAVAILABLE",
]


@dataclass
class QualityReport:
    ok: bool
    code: str | None = None
    detail: str = ""


def _limits(cfg: dict):
    lag = cfg.get("max_bar_lag_minutes", 5)
    outlier = cfg.get("outlier_return_abs_pct", 2.0)
    return (None if is_blocked(lag) else float(lag),
            None if is_blocked(outlier) else float(outlier))


def check_quality(
    bar: Bar,
    quote: Quote,
    decision_ts: datetime,
    limits: dict,
    symbol_expected: str = "XAUUSD",
) -> QualityReport:
    max_lag, outlier_pct = _limits(limits)

    # 1. freshness: last close must be recent relative to decision time.
    #    decision_ts == bar close in the live loop, so allow only jitter.
    if max_lag is not None:
        age = (decision_ts - bar.close_dt).total_seconds() / 60.0
        if age < -0.001 or age > max_lag + 1.0:
            return QualityReport(False, "STALE_DATA",
                                 f"bar age {age:.1f} min exceeds max lag {max_lag} min")

    # 2. bar alignment: H1 bars must sit on hour boundaries
    o = bar.open_dt
    if o.minute or o.second or o.microsecond:
        return QualityReport(False, "TZ_MISALIGN", f"bar open not hour-aligned: {bar.ts_open}")
    if (bar.close_dt - o) != timedelta(hours=1):
        return QualityReport(False, "TZ_MISALIGN", "bar is not a 1-hour bar")

    # 3. sanity of OHLC itself
    if not (bar.low <= min(bar.open, bar.close) and max(bar.open, bar.close) <= bar.high
            and bar.low <= bar.high and bar.open > 0 and bar.close > 0):
        return QualityReport(False, "OUTLIER_PRICE", f"malformed OHLC {bar.canonical()}")

    # 4. gap / outlier return relative to previous close is checked by caller
    #    (needs history); here we bound the single-bar move itself.
    if outlier_pct is not None:
        move_pct = abs(bar.close - bar.open) / bar.open * 100.0
        if move_pct > outlier_pct:
            return QualityReport(False, "OUTLIER_PRICE",
                                 f"single-bar move {move_pct:.2f}% > {outlier_pct}%")

    # 5. spread sanity at quote level (gate re-checks at ticket time too)
    max_spread = limits.get("max_spread")
    if not is_blocked(max_spread) and quote.spread > float(max_spread):
        return QualityReport(False, "SPREAD",
                             f"spread {quote.spread} > max {max_spread}")

    return QualityReport(True)


def gap_check(prev_close: float, bar: Bar, limits: dict) -> QualityReport:
    """Weekend-gap paranoia: absurd open-vs-prev-close jump is an outlier."""
    _, outlier_pct = _limits(limits)
    if outlier_pct is None:
        return QualityReport(True)
    gap_pct = abs(bar.open - prev_close) / prev_close * 100.0
    if gap_pct > outlier_pct:
        return QualityReport(False, "OUTLIER_PRICE",
                             f"gap {gap_pct:.2f}% > {outlier_pct}% (weekend/rollover?)")
    return QualityReport(True)


def missing_bar_check(bars: list[Bar], decision_ts: datetime) -> QualityReport:
    """Expected previous hourly bar present? Called with the closed-bar tail."""
    if len(bars) < 2:
        return QualityReport(True)  # nothing to compare yet
    last, prev = bars[-1], bars[-2]
    if last.open_dt - prev.open_dt != timedelta(hours=1):
        # a gap inside a single trading day is a missing bar; Fri->Mon is fine
        if not (prev.open_dt.weekday() == 4 and last.open_dt.weekday() == 0):
            return QualityReport(False, "MISSING_BAR",
                                 f"gap between {prev.ts_open} and {last.ts_open}")
    return QualityReport(True)
