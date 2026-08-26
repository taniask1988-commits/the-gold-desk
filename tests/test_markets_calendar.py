"""MARKET GAUNTLET economic calendar (ECO) tests — offline.

The ForexFactory mirror is monkeypatched everywhere (it rate-limits
bursts, so the suite never touches it); the static generator is pure
date math and pinned deterministic.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.markets import calendar as cal  # noqa: E402


def _week_monday(now: dt.datetime) -> dt.date:
    return now.date() - dt.timedelta(days=now.date().weekday())


# ---------------------------------------------------------------- parse
def test_parse_ff_events_normalizes():
    payload = [
        {"title": "CPI y/y", "country": "usd", "impact": "High",
         "date": "2026-08-25T14:00:00-04:00", "forecast": "2.3%",
         "previous": "2.7%"},
        {"title": "Retail Sales", "country": "GBP", "impact": "Medium",
         "date": "2026-08-26T08:30:00+01:00", "forecast": "",
         "previous": ""},
        {"title": "Bank Holiday", "country": "JPY", "impact": "Holiday",
         "date": "2026-08-24T00:00:00+09:00", "forecast": "",
         "previous": ""},
    ]
    ev = cal.parse_ff_events(payload)
    assert len(ev) == 3
    # sorted by ts — the +09:00 Bank Holiday is earliest
    assert [e["title"] for e in ev] == \
        ["Bank Holiday", "CPI y/y", "Retail Sales"]
    # -04:00 offset honored: 14:00 EDT == 18:00 UTC
    t0 = dt.datetime.fromtimestamp(ev[1]["ts"] / 1000,
                                   tz=dt.timezone.utc)
    assert (t0.hour, t0.minute) == (18, 0)
    assert ev[1]["country"] == "USD"
    assert ev[1]["impact"] == "high"
    assert ev[1]["forecast"] == "2.3%"
    assert ev[2]["impact"] == "medium"
    # unknown impact words degrade to low, never raise
    assert ev[0]["impact"] == "low"


def test_parse_ff_events_skips_malformed_rows():
    payload = [
        {"country": "USD", "date": "2026-08-25T14:00:00-04:00",
         "impact": "High"},                       # no title
        {"title": "X", "country": "USD", "impact": "High"},  # no date
        {"title": "Y", "country": "USD", "impact": "High",
         "date": "not-a-date"},                    # unparseable date
        "just a string",                          # not a dict
        {"title": "Z", "country": "USD", "impact": "Low",
         "date": "2026-08-25T14:00:00-04:00"},     # valid
    ]
    ev = cal.parse_ff_events(payload)
    assert len(ev) == 1 and ev[0]["title"] == "Z"
    # raw JSON string input also accepted
    assert cal.parse_ff_events(json.dumps(payload)) == ev
    # non-list payload → []
    assert cal.parse_ff_events({"nope": 1}) == []


# ----------------------------------------------------------- static gen
def test_static_nfp_lands_on_first_friday():
    # any week containing a first Friday must carry NFP that day
    for month in range(1, 13):
        first = dt.date(2026, month, 1)
        ff = first + dt.timedelta(days=(4 - first.weekday()) % 7)
        now = dt.datetime(ff.year, ff.month, ff.day, 12,
                          tzinfo=dt.timezone.utc)
        ev = cal.static_week_events(now)
        nfp = [e for e in ev if "Non-Farm" in e["title"]]
        assert nfp, f"no NFP in week of {ff}"
        t = dt.datetime.fromtimestamp(nfp[0]["ts"] / 1000,
                                      tz=dt.timezone.utc)
        assert t.date() == ff
        assert (t.hour, t.minute) == (12, 30)   # 8:30 ET ≈ 12:30 UTC
        assert nfp[0]["country"] == "USD"
        assert nfp[0]["impact"] == "high"


def test_static_fomc_on_published_2026_days():
    for day in cal.FOMC_2026:
        y, m, d = (int(x) for x in day.split("-"))
        now = dt.datetime(y, m, d, 12, tzinfo=dt.timezone.utc)
        ev = cal.static_week_events(now)
        fomc = [e for e in ev if "FOMC" in e["title"]]
        assert fomc, f"no FOMC event in week of {day}"
        t = dt.datetime.fromtimestamp(fomc[0]["ts"] / 1000,
                                      tz=dt.timezone.utc)
        assert t.date().isoformat() == day
        assert (t.hour, t.minute) == (19, 0)     # 2 PM ET ≈ 19:00 UTC


def test_static_events_confined_to_the_week():
    now = dt.datetime(2026, 8, 26, 9, 0, tzinfo=dt.timezone.utc)  # Wed
    monday = _week_monday(now)
    sunday = monday + dt.timedelta(days=6)
    ev = cal.static_week_events(now)
    assert ev, "generator produced nothing"
    for e in ev:
        d = dt.datetime.fromtimestamp(e["ts"] / 1000,
                                      tz=dt.timezone.utc).date()
        assert monday <= d <= sunday
        assert e["impact"] in ("high", "medium", "low")
        assert e["country"] and e["title"]


def test_static_is_deterministic():
    now = dt.datetime(2026, 10, 14, 15, 30, tzinfo=dt.timezone.utc)
    assert cal.static_week_events(now) == cal.static_week_events(now)


def test_static_every_title_is_honestly_labeled():
    # only the FOMC published dates may go unlabeled; every date-math
    # approximation carries "est." in its title
    now = dt.datetime(2026, 6, 10, tzinfo=dt.timezone.utc)
    for e in cal.static_week_events(now):
        if "FOMC Rate Decision" not in e["title"]:
            assert "est." in e["title"], e["title"]


# --------------------------------------------------------------- fetch
CANNED_FF = json.dumps([
    {"title": "Core CPI m/m", "country": "USD", "impact": "High",
     "date": "2026-08-25T14:00:00-04:00", "forecast": "0.2%",
     "previous": "0.1%"},
    {"title": "Flash Manufacturing PMI", "country": "EUR",
     "impact": "Medium", "date": "2026-08-26T09:00:00+02:00",
     "forecast": "49.5", "previous": "49.0"},
])


def test_fetch_calendar_live(monkeypatch, tmp_path):
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: CANNED_FF)
    out = cal.fetch_calendar(tmp_path)
    assert out["ok"] is True
    assert out["source"] == "live"
    assert out["cache_hit"] is False
    assert len(out["events"]) == 2
    assert out["events"][0]["title"] == "Core CPI m/m"
    # the live payload was cached
    cache = json.loads((tmp_path / "cache" / "markets_eco.json")
                       .read_text())
    assert cache["source"] == "live"


def test_fetch_calendar_cache_hit(monkeypatch, tmp_path):
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: CANNED_FF)
    cal.fetch_calendar(tmp_path)
    # a second call with a DEAD feed still serves the fresh cache
    def _dead(url, timeout=8.0):
        raise RuntimeError("offline")
    monkeypatch.setattr(cal, "_http_get", _dead)
    out = cal.fetch_calendar(tmp_path)
    assert out["ok"] is True and out["source"] == "live"
    assert out["cache_hit"] is True
    assert len(out["events"]) == 2


def test_fetch_calendar_stale_serve(monkeypatch, tmp_path):
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: CANNED_FF)
    cal.fetch_calendar(tmp_path)
    # expire the cache, then kill the feed → stale-serve
    p = tmp_path / "cache" / "markets_eco.json"
    cached = json.loads(p.read_text())
    cached["fetched_at"] = 0
    p.write_text(json.dumps(cached))

    def _dead(url, timeout=8.0):
        raise RuntimeError("offline")
    monkeypatch.setattr(cal, "_http_get", _dead)
    out = cal.fetch_calendar(tmp_path)
    assert out["ok"] is True and out["source"] == "live"
    assert out["cache_hit"] is True
    assert out["stale_error"] == "RuntimeError"
    assert len(out["events"]) == 2


def test_fetch_calendar_static_fallback(monkeypatch, tmp_path):
    # no cache, dead feed → ECO still works via the static schedule
    def _dead(url, timeout=8.0):
        raise RuntimeError("offline")
    monkeypatch.setattr(cal, "_http_get", _dead)
    out = cal.fetch_calendar(tmp_path)
    assert out["ok"] is True
    assert out["source"] == "static"
    assert out["note"] == cal.STATIC_NOTE
    assert out["events"], "static fallback served no events"
    # the static fallback is NOT cached as live data
    assert not (tmp_path / "cache" / "markets_eco.json").exists()


def test_fetch_calendar_rate_limit_html_is_not_json(monkeypatch, tmp_path):
    # the mirror's burst rate-limiter returns an HTML page with HTTP
    # 200 — that must fall through to the static chain, not crash
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: "<!DOCTYPE html><html>Rate Limited")
    out = cal.fetch_calendar(tmp_path)
    assert out["ok"] is True
    assert out["source"] == "static"


def test_fetch_calendar_static_not_persisted_as_live(monkeypatch,
                                                     tmp_path):
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: "<html>Rate Limited")
    cal.fetch_calendar(tmp_path)
    # feed recovers on the next call → live service resumes cleanly
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: CANNED_FF)
    out = cal.fetch_calendar(tmp_path)
    assert out["source"] == "live" and out["ok"] is True


# ------------------------------------------------------------------ CLI
def test_cli_markets_eco_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cal, "_http_get", lambda url,
                        timeout=8.0: CANNED_FF)
    from gold_desk.cli import main
    rc = main(["markets-eco", "--json", "--data-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["source"] == "live"
    assert out["events"][0]["country"] == "USD"


def test_cli_markets_eco_table_and_static_badge(monkeypatch, tmp_path,
                                                capsys):
    def _dead(url, timeout=8.0):
        raise RuntimeError("offline")
    monkeypatch.setattr(cal, "_http_get", _dead)
    from gold_desk.cli import main
    rc = main(["markets-eco", "--data-root", str(tmp_path)])
    text = capsys.readouterr().out
    assert rc == 0
    assert "ECONOMIC CALENDAR" in text
    assert "static schedule (live feed unreachable)" in text
    assert "###" in text          # high-impact marker
