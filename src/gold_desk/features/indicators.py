"""Closed-bar indicators. Sources only hand us closed bars; the forming-bar
guard is asserted again here so no future refactor can leak the forming bar
into feature math (test_forming_bar pins this)."""
from __future__ import annotations

from datetime import datetime

from ..clock import iso, parse_ts
from ..data.model import Bar


def assert_closed(bars: list[Bar], decision_ts: str | datetime) -> None:
    ceiling = decision_ts if isinstance(decision_ts, datetime) else parse_ts(decision_ts)
    for b in bars:
        if b.close_dt > ceiling:
            raise AssertionError(
                f"forming/future bar reached indicator math: {b.ts_close} > {iso(ceiling)}"
            )


def atr(bars: list[Bar], period: int = 14, decision_ts: str | datetime | None = None) -> float | None:
    """Wilder ATR on closed H1 bars."""
    if decision_ts is not None:
        assert_closed(bars, decision_ts)
    if len(bars) < period + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        prev_close = bars[i - 1].close
        b = bars[i]
        tr = max(b.high - b.low, abs(b.high - prev_close), abs(b.low - prev_close))
        trs.append(tr)
    # Wilder smoothing
    value = sum(trs[:period]) / period
    for tr in trs[period:]:
        value = (value * (period - 1) + tr) / period
    return round(value, 4)


def range_stats(bars: list[Bar], decision_ts: str | datetime | None = None) -> tuple[float, float] | None:
    """(high, low) over the given closed bars."""
    if decision_ts is not None:
        assert_closed(bars, decision_ts)
    if not bars:
        return None
    return max(b.high for b in bars), min(b.low for b in bars)
