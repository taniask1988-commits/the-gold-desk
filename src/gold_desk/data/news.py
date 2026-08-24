"""News feed wrapper (Phase 2 context only; Phase 1 uses it only for the
fail-closed availability check). Kept deliberately tiny: v1 does not parse
news, it only timestamps it."""
from __future__ import annotations

from .model import NewsItem


class NewsFeed:
    def __init__(self, items: list[NewsItem] | None = None, healthy: bool = True):
        self._items = sorted(items or [], key=lambda n: n.ts)
        self.healthy = healthy

    def up_to(self, instant_iso: str) -> list[NewsItem]:
        return [n for n in self._items if n.ts <= instant_iso]

    def recent(self, instant_iso: str, hours: float = 6.0) -> list[NewsItem]:
        from datetime import datetime, timedelta
        from ..clock import iso, parse_ts
        dt = parse_ts(instant_iso) - timedelta(hours=hours)
        lo = iso(dt)
        return [n for n in self._items if lo <= n.ts <= instant_iso]
