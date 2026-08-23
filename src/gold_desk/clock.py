"""Clock and session math. UTC everywhere internally; broker tz only decides
H1 candle alignment (constitution broker.session_timezone).

Session windows are fixed UTC approximations (no DST chasing in v1 — when
in doubt, the filter fails closed with SESSION, never guesses):

    asia               00:00 - 07:00
    london             07:00 - 12:00
    london_ny_overlap  12:00 - 16:00
    ny                 16:00 - 21:00
    off                otherwise
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

SESSION_BOUNDS = [
    ("asia", 0, 7),
    ("london", 7, 12),
    ("london_ny_overlap", 12, 16),
    ("ny", 16, 21),
]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def session_of(dt: datetime) -> str:
    hour = dt.astimezone(timezone.utc).hour
    for name, start, end in SESSION_BOUNDS:
        if start <= hour < end:
            return name
    return "off"


@dataclass(frozen=True)
class SessionClock:
    allowed_sessions: tuple[str, ...]

    def is_allowed(self, dt: datetime) -> bool:
        return session_of(dt) in self.allowed_sessions

    def session(self, dt: datetime) -> str:
        return session_of(dt)


def next_hour_close(after: datetime) -> datetime:
    """Next top-of-the-hour boundary strictly after `after` (UTC)."""
    nxt = after.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return nxt.astimezone(timezone.utc)
