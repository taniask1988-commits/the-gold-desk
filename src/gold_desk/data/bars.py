"""Bar sources.

BarSource protocol: closed H1 bars only, never a forming bar. The decision
timestamp is the bar close; sources must not return bars whose ts_close is
in the future of the asked instant.

SyntheticBarSource — deterministic, seeded XAUUSD-like generator used by the
demo and the simulator skeleton. Produces Monday..Friday 24h H1 bars with
session-scaled volatility, weekend gaps, rollover/London-open spread
widening, and a sparse set of high-impact calendar events.

CsvBarSource — reads timestamp,open,high,low,close[,volume] rows (UTC).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from ..clock import iso
from .model import Bar, CalendarEvent, NewsItem, Quote

_HOURS = list(range(24))  # 24h market, Mon..Fri


@dataclass
class SyntheticConfig:
    seed: int = 7
    start_price: float = 2400.0
    base_sigma_per_hour: float = 1.2      # quiet-hours sigma (price units)
    gap_weekend_mean: float = 3.0         # Monday open gap vs Friday close
    spread_base: float = 0.22
    spread_rollover: float = 0.80         # 22:00-23:00 UTC widening
    spread_london_open: float = 0.42      # first two London hours
    blackout_event_prob_per_day: float = 0.25


class BarSource(Protocol):
    def bars_up_to(self, instant: datetime, count: int) -> list[Bar]: ...
    def quote(self, instant: datetime) -> Quote: ...
    def calendar(self, instant: datetime) -> list[CalendarEvent]: ...
    def news(self, instant: datetime) -> list[NewsItem]: ...
    def health(self, instant: datetime) -> bool: ...
    calendar_wired: bool   # False for sources with no calendar feed at all


class SyntheticBarSource:
    """Deterministic given (seed, date-range). Same seed -> same world."""

    def __init__(self, config: SyntheticConfig | None = None,
                 start: datetime | None = None, days: int = 30):
        self.cfg = config or SyntheticConfig()
        self.rng = random.Random(self.cfg.seed)
        self.start = (start or datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc))
        self.days = days
        self.calendar_wired = True
        self._bars: list[Bar] = []
        self._quotes: dict[str, Quote] = {}
        self._calendar: list[CalendarEvent] = []
        self._news: list[NewsItem] = []
        self._generate()

    # ------------------------------------------------------------ generation
    def _generate(self) -> None:
        rng = self.rng
        price = self.cfg.start_price
        day = self.start.date()
        seen_days = 0
        while seen_days < self.days:
            if day.weekday() >= 5:  # Sat/Sun: no bars
                day += timedelta(days=1)
                continue
            if seen_days > 0 and day.weekday() == 0:
                # weekend gap applied at Monday 00:00 open
                price += rng.gauss(0.0, self.cfg.gap_weekend_mean * 2)
            for hour in _HOURS:
                open_dt = datetime(day.year, day.month, day.day, hour, 0, tzinfo=timezone.utc)
                close_dt = open_dt + timedelta(hours=1)
                sigma = self._session_sigma(hour)
                drift = rng.gauss(0.0, sigma * 0.08)
                o = price
                c = max(1.0, o + drift + rng.gauss(0.0, sigma))
                hi = max(o, c) + abs(rng.gauss(0.0, sigma * 0.6))
                lo = min(o, c) - abs(rng.gauss(0.0, sigma * 0.6))
                bar = Bar(
                    ts_open=iso(open_dt), ts_close=iso(close_dt),
                    open=round(o, 2), high=round(hi, 2),
                    low=round(lo, 2), close=round(c, 2),
                    volume=round(abs(rng.gauss(1000, 250)), 1),
                )
                self._bars.append(bar)
                spread = self._spread_for(hour)
                self._quotes[bar.ts_close] = Quote(
                    ts=bar.ts_close,
                    bid=round(c - spread / 2.0, 2),
                    ask=round(c + spread / 2.0, 2),
                )
                price = c
            # occasional high-impact calendar event inside London/NY hours
            if rng.random() < self.cfg.blackout_event_prob_per_day:
                ev_hour = rng.choice([8, 12, 13, 14, 15])
                ev_dt = datetime(day.year, day.month, day.day, ev_hour, 30, tzinfo=timezone.utc)
                self._calendar.append(CalendarEvent(
                    ts=iso(ev_dt), currency=rng.choice(["USD", "EUR"]),
                    impact="high",
                    title=rng.choice([
                        "CPI y/y", "FOMC statement", "Nonfarm payrolls",
                        "PCE price index", "Retail sales",
                    ]),
                ))
                self._news.append(NewsItem(
                    ts=iso(ev_dt + timedelta(minutes=1)),
                    headline=f"{rng.choice(['Surprise beat', 'In line', 'Big miss'])} on "
                             f"{self._calendar[-1].title} ({self._calendar[-1].currency})",
                    source="DEMO-WIRE",
                ))
            seen_days += 1
            day += timedelta(days=1)
        self._bars.sort(key=lambda b: b.ts_close)

    def _session_sigma(self, hour: int) -> float:
        base = self.cfg.base_sigma_per_hour
        if 0 <= hour < 2:                    # late NY / rollover approach
            return base * 0.6
        if 2 <= hour < 6:                    # Asia
            return base * 0.9
        if 7 <= hour < 12:                   # London
            return base * 2.1
        if 12 <= hour < 16:                  # overlap
            return base * 2.5
        if 16 <= hour < 21:                  # NY afternoon
            return base * 1.4
        return base * 0.7                    # rollover

    def _spread_for(self, hour: int) -> float:
        if 22 <= hour or hour < 1:
            return self.cfg.spread_rollover
        if 7 <= hour < 9:
            return self.cfg.spread_london_open
        return max(self.cfg.spread_base, abs(self.rng.gauss(self.cfg.spread_base, 0.03)))

    # ------------------------------------------------------------- interface
    def bars_up_to(self, instant: datetime, count: int) -> list[Bar]:
        stamp = iso(instant)
        closed = [b for b in self._bars if b.ts_close <= stamp]
        return closed[-count:]

    def quote(self, instant: datetime) -> Quote:
        stamp = iso(instant)
        keys = [k for k in sorted(self._quotes) if k <= stamp]
        if not keys:
            raise RuntimeError("no quote available at or before instant")
        q = self._quotes[keys[-1]]
        # quote freshness stamp = bar close it belongs to
        return Quote(ts=q.ts, bid=q.bid, ask=q.ask)

    def calendar(self, instant: datetime) -> list[CalendarEvent]:
        stamp = iso(instant)
        return [c for c in self._calendar if c.ts <= stamp]

    def news(self, instant: datetime) -> list[NewsItem]:
        stamp = iso(instant)
        return [n for n in self._news if n.ts <= stamp]

    def health(self, instant: datetime) -> bool:
        return True


class CsvBarSource:
    """timestamp,open,high,low,close[,volume] — UTC ISO-8601 timestamps.

    Calendar/news feeds are empty (a v1 desk that requires news then fails
    closed with NEWS_UNAVAILABLE until real feeds exist)."""

    def __init__(self, path: str | Path, tz_note: str = "UTC"):
        self.path = Path(path)
        self.tz_note = tz_note
        self.calendar_wired = False   # no calendar feed: desk fails closed
        self._bars: list[Bar] = []
        self._load()

    def _load(self) -> None:
        import csv
        with self.path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                open_dt = datetime.fromisoformat(row["timestamp"].replace("Z", "+00:00"))
                if open_dt.tzinfo is None:
                    open_dt = open_dt.replace(tzinfo=timezone.utc)
                close_dt = open_dt + timedelta(hours=1)
                self._bars.append(Bar(
                    ts_open=iso(open_dt), ts_close=iso(close_dt),
                    open=float(row["open"]), high=float(row["high"]),
                    low=float(row["low"]), close=float(row["close"]),
                    volume=float(row.get("volume") or 0.0),
                ))
        self._bars.sort(key=lambda b: b.ts_close)

    def bars_up_to(self, instant: datetime, count: int) -> list[Bar]:
        stamp = iso(instant)
        closed = [b for b in self._bars if b.ts_close <= stamp]
        return closed[-count:]

    def quote(self, instant: datetime) -> Quote:
        bars = self.bars_up_to(instant, 1)
        if not bars:
            raise RuntimeError("no bar for quote")
        c = bars[-1].close
        return Quote(ts=bars[-1].ts_close, bid=round(c - 0.11, 2), ask=round(c + 0.11, 2))

    def calendar(self, instant: datetime) -> list[CalendarEvent]:
        return []

    def news(self, instant: datetime) -> list[NewsItem]:
        return []

    def health(self, instant: datetime) -> bool:
        return True
