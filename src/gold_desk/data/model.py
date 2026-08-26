"""Data plane value objects. Every observation is timestamped with asof_ts
(the moment the fact became true/known) and ingested_ts (when we stored it).
Prices in quote units; XAUUSD quoted in USD per ounce, 2 digits typical.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from ..clock import iso, parse_ts


@dataclass(frozen=True)
class Bar:
    ts_open: str      # ISO-8601 UTC — bar open time, hour-aligned
    ts_close: str     # ISO-8601 UTC — bar close time == decision_ts ceiling
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def open_dt(self) -> datetime:
        return parse_ts(self.ts_open)

    @property
    def close_dt(self) -> datetime:
        return parse_ts(self.ts_close)

    def canonical(self) -> str:
        return "|".join([
            self.ts_open, self.ts_close,
            f"{self.open:.2f}", f"{self.high:.2f}",
            f"{self.low:.2f}", f"{self.close:.2f}", f"{self.volume:.2f}",
        ])


@dataclass(frozen=True)
class Quote:
    ts: str
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return round(self.ask - self.bid, 4)

    @property
    def mid(self) -> float:
        return (self.ask + self.bid) / 2.0


@dataclass(frozen=True)
class CalendarEvent:
    ts: str            # event time, UTC
    currency: str
    impact: str        # high | medium | low
    title: str


@dataclass(frozen=True)
class NewsItem:
    ts: str            # publication time, UTC
    headline: str
    source: str = ""


@dataclass
class Observation:
    """The asof_ts envelope (§5.2). Everything the pipeline knows is one."""
    kind: str          # bar | news | calendar | spread | dxy | feature | quote
    asof_ts: str
    ingested_ts: str
    payload: dict = field(default_factory=dict)


def wrap(kind: str, asof: datetime, payload: dict) -> Observation:
    return Observation(
        kind=kind,
        asof_ts=iso(asof),
        ingested_ts=iso(asof),   # in live use, ingested may lag asof
        payload=payload,
    )


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
