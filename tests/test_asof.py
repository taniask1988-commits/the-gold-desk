"""§16 row 1 — asof drop: future news/calendar/bars never survive the filter."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gold_desk.data.asof import filter_asof, violates_asof
from gold_desk.data.model import wrap

DECISION = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)


def test_future_observation_dropped():
    obs = [
        wrap("news", DECISION - timedelta(minutes=30), {"headline": "old"}),
        wrap("news", DECISION + timedelta(minutes=1), {"headline": "future"}),
        wrap("calendar", DECISION + timedelta(hours=2), {"title": "FOMC"}),
        wrap("bar", DECISION - timedelta(hours=1), {"c": 2400.0}),
    ]
    kept = filter_asof(obs, DECISION)
    headlines = [o.payload.get("headline") or o.payload.get("title") or "bar"
                 for o in kept]
    assert headlines == ["old", "bar"], headlines


def test_violations_are_exactly_the_future_tail():
    obs = [
        wrap("news", DECISION, {"headline": "edge"}),
        wrap("news", DECISION + timedelta(seconds=1), {"headline": "after"}),
    ]
    dropped = violates_asof(obs, DECISION)
    assert [o.payload["headline"] for o in dropped] == ["after"]


def test_exact_boundary_included():
    obs = [wrap("calendar", DECISION, {"title": "exactly at decision"})]
    assert filter_asof(obs, DECISION) == obs
