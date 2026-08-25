"""Calendar + news feeds and the news-blackout window check.

v1 is fail-closed on news: if the constitution requires the news/calendar
feed for trading decisions (fail_closed.news_unavailable: no_trade) and the
feed is down/unavailable at decision time, the bar dies with
NEWS_UNAVAILABLE — no trade, no veto, no ticket.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from ..clock import parse_ts
from .model import CalendarEvent, NewsItem


@dataclass(frozen=True)
class BlackoutWindow:
    event_ts: str
    minutes_before: int
    minutes_after: int
    title: str


def blackout_windows(
    events: list[CalendarEvent],
    around: datetime,
    minutes_before: int,
    minutes_after: int,
    high_impact_only: bool = True,
    horizon_hours: float = 24.0,
) -> list[BlackoutWindow]:
    """Windows overlapping `around` for events within horizon."""
    out: list[BlackoutWindow] = []
    lo = around - timedelta(hours=horizon_hours)
    hi = around + timedelta(hours=horizon_hours)
    for ev in events:
        if high_impact_only and ev.impact != "high":
            continue
        ts = parse_ts(ev.ts)
        if not (lo <= ts <= hi):
            continue
        start = ts - timedelta(minutes=minutes_before)
        end = ts + timedelta(minutes=minutes_after)
        if start <= around <= end:
            out.append(BlackoutWindow(ev.ts, minutes_before, minutes_after, ev.title))
    return out


def in_blackout(
    events: list[CalendarEvent],
    around: datetime,
    minutes_before: int,
    minutes_after: int,
    high_impact_only: bool = True,
) -> BlackoutWindow | None:
    wins = blackout_windows(events, around, minutes_before, minutes_after, high_impact_only)
    return wins[0] if wins else None


def feed_health(items: list, source_health: bool) -> bool:
    """Feed considered healthy only if the source is healthy. For CSV bar
    sources with no calendar wiring, the desk must fail closed (no trade)
    until a real calendar feed exists — except in explicitly demo mode."""
    return source_health
