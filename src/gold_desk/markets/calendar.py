"""MARKET GAUNTLET economic calendar (ECO) — keyless ForexFactory mirror.

Feed (live-probed 2026-08-25, GAUNTLET-P13-BUILDER):

    https://nfs.faireconomy.media/ff_calendar_thisweek.json
        HTTP 200 · application/json · this week's global releases
        [{title, country, date, impact, forecast, previous}, ...]
            date   "2026-08-23T18:45:00-04:00" (ISO with offset)
            impact "High" | "Medium" | "Low" | "Holiday" | "Non-Economic"
            country ISO-ish currency code: USD, EUR, GBP, JPY, CNY, NZD...

    The mirror RATE-LIMITS bursts (3-4 rapid requests → an HTML
    "Rate Limited" page instead of JSON) — a burst probe looks DEAD
    while the feed is fine 30s later. fetch_calendar therefore treats
    an unparseable/HTML body exactly like a transport failure (never
    a crash) and the 30-minute cache means the deployed surface hits
    it ~2/hour, far under the limit.

Resilience chain (ECO always works — the gauntlet bar demands it):

    1. fresh file cache  (<data_root>/cache/markets_eco.json, 30min)
    2. live fetch        → parse → serve, source: "live"
    3. stale cache       → serve with source: "live", stale_error set
    4. static schedule   → date-math generator, source: "static",
                           note: "static schedule (live feed unreachable)"

The static generator (step 4) is pure local date math — the recurring
known events, labeled "est." because cadences approximate the exact
2026 calendars:

    * FOMC rate decisions — the published 2026 statement days (Jan 28,
      Mar 18, Apr 29, Jun 17, Jul 29, Sep 16, Oct 28, Dec 9, 19:00 UTC)
    * US Non-Farm Payrolls — first Friday of every month, 12:30 UTC
    * US CPI — mid-month (the 12th), 13:00 UTC
    * ECB / BoE — 6-week Thursday cadence, 13:45 / 12:00 UTC
    * BoJ — 6-week Friday cadence, 03:00 UTC
    * RBI — first Friday of every even month, 04:30 UTC (10:00 IST)

Law boundary: display/education telemetry for the gauntlet surface,
NOT wired into the orchestrator's decision loop.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
FF_CALENDAR_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

CALENDAR_TTL_S = 30 * 60      # 30 minutes — the mirror rate-limits bursts
HTTP_TIMEOUT = 8.0
STATIC_NOTE = "static schedule (live feed unreachable)"

# published 2026 FOMC statement days (second day of each meeting)
FOMC_2026 = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17",
             "2026-07-29", "2026-09-16", "2026-10-28", "2026-12-09"]


def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/xml, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ------------------------------------------------------------------ parse
def parse_ff_events(payload) -> list[dict]:
    """ForexFactory-mirror payload → normalized events.

    [{ts (ms epoch UTC), country, impact (high|medium|low), title,
      forecast, previous}] sorted by ts. Malformed rows are skipped
    (never raise); impact values outside High/Medium/Low (Holiday,
    Non-Economic) degrade to "low". Accepts a pre-decoded list or a
    raw JSON string.
    """
    if isinstance(payload, str):
        payload = json.loads(payload)
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for row in payload:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        when = str(row.get("date") or "").strip()
        if not title or not when:
            continue
        try:
            t = dt.datetime.fromisoformat(when)
            if t.tzinfo is None:
                t = t.replace(tzinfo=dt.timezone.utc)
            ts = int(t.astimezone(dt.timezone.utc).timestamp() * 1000)
        except ValueError:
            continue
        raw_impact = str(row.get("impact") or "").strip().lower()
        impact = {"high": "high", "medium": "medium", "low": "low"}.get(
            raw_impact, "low")
        out.append({
            "ts": ts,
            "country": str(row.get("country") or "").strip().upper()[:4],
            "impact": impact,
            "title": title[:120],
            "forecast": str(row.get("forecast") or "")[:24],
            "previous": str(row.get("previous") or "")[:24],
        })
    out.sort(key=lambda e: e["ts"])
    return out


# ------------------------------------------------------------- static gen
def _utc(y: int, m: int, d: int, h: int, minute: int) -> int:
    t = dt.datetime(y, m, d, h, minute, tzinfo=dt.timezone.utc)
    return int(t.timestamp() * 1000)


def _first_friday(year: int, month: int) -> dt.date:
    d = dt.date(year, month, 1)
    # Monday=0 ... Friday=4
    return d + dt.timedelta(days=(4 - d.weekday()) % 7)


def static_week_events(now: dt.datetime | None = None) -> list[dict]:
    """Recurring known events inside the Monday..Sunday week containing
    `now` (UTC), generated by pure local date math. Deterministic —
    tests pin NFP-on-first-Friday, FOMC on its published days, and the
    week-bounds filter. Every title carries "est." honesty labels."""
    now = now or dt.datetime.now(dt.timezone.utc)
    today = now.date()
    monday = today - dt.timedelta(days=today.weekday())
    sunday = monday + dt.timedelta(days=6)
    out: list[dict] = []

    def add(ts: int, country: str, impact: str, title: str) -> None:
        d = dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).date()
        if monday <= d <= sunday:
            out.append({"ts": ts, "country": country, "impact": impact,
                        "title": title, "forecast": "", "previous": ""})

    # sweep the month before, during and after the week (events near
    # month boundaries belong to neighbouring months' schedules)
    months = set()
    for offset in (-1, 0, 1):
        y, m = (monday.year + (monday.month - 1 + offset) // 12,
                (monday.month - 1 + offset) % 12 + 1)
        months.add((y, m))
    for y, m in sorted(months):
        # NFP — first Friday, 12:30 UTC
        ff = _first_friday(y, m)
        add(_utc(ff.year, ff.month, ff.day, 12, 30), "USD", "high",
            "Non-Farm Payrolls (est.)")
        # US CPI — mid-month (the 12th), 13:00 UTC
        add(_utc(y, m, 12, 13, 0), "USD", "high",
            "CPI y/y (mid-month est.)")
        # FOMC — published 2026 statement days, 19:00 UTC
        for day in FOMC_2026:
            fy, fm, fd = (int(x) for x in day.split("-"))
            if (fy, fm) == (y, m):
                add(_utc(fy, fm, fd, 19, 0), "USD", "high",
                    "FOMC Rate Decision")
    # central-bank cadences (Thursday/Friday rhythms at ~6-week gaps)
    # — walk each cadence from a fixed seed; emit any landing inside
    # the week. weekday: Mon=0 .. Fri=4. Seeds are staggered so the
    # three banks never land on the same day.
    for seed_day, weekday, gap_days, country, hour, minute, title in (
        (5, 3, 42, "EUR", 13, 45, "ECB Rate Decision (cadence est.)"),
        (26, 3, 42, "GBP", 12, 0, "BoE Rate Decision (cadence est.)"),
        (9, 4, 42, "JPY", 3, 0, "BoJ Policy Decision (cadence est.)"),
    ):
        seed = dt.date(2026, 1, seed_day)  # a Monday; snap to weekday
        seed = seed + dt.timedelta(days=(weekday - seed.weekday()) % 7)
        d = seed
        while d <= sunday:
            if monday <= d:
                add(_utc(d.year, d.month, d.day, hour, minute),
                    country, "high", title)
            d = d + dt.timedelta(days=gap_days)
    # RBI — first Friday of every even month, 04:30 UTC (10:00 IST)
    for y, m in sorted(months):
        if m % 2 == 0:
            ff = _first_friday(y, m)
            add(_utc(ff.year, ff.month, ff.day, 4, 30), "INR", "high",
                "RBI Policy Decision (est.)")
    out.sort(key=lambda e: e["ts"])
    return out


# ------------------------------------------------------------------ cache
def _cache_path(data_root: str | Path) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / "markets_eco.json"


# ------------------------------------------------------------------ fetch
def fetch_calendar(data_root: str | Path = "data") -> dict:
    """This week's economic calendar, fail-soft. Never raises.

    Returns {ok, source: "live"|"static", as_of, week_start, events}
    (static adds note=STATIC_NOTE; failures add stale_error). See the
    module docstring for the 4-step resilience chain.
    """
    def _now_iso() -> str:
        return dt.datetime.now(dt.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ")

    def _week_start() -> str:
        today = dt.datetime.now(dt.timezone.utc).date()
        monday = today - dt.timedelta(days=today.weekday())
        return monday.isoformat()

    def _fetch() -> dict:
        # JSON-only validation: the mirror's rate-limit page is HTML,
        # so json.loads raises and the chain falls through cleanly
        events = parse_ff_events(json.loads(_http_get(FF_CALENDAR_URL)))
        return {"ok": True, "source": "live", "as_of": _now_iso(),
                "week_start": _week_start(), "events": events}

    path = _cache_path(data_root)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = {}
    if cached.get("fetched_at") and \
            time.time() - cached["fetched_at"] < CALENDAR_TTL_S:
        cached["cache_hit"] = True
        return cached
    try:
        fresh = _fetch()
        fresh["fetched_at"] = time.time()
        fresh["cache_hit"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as e:  # noqa: BLE001 — display telemetry fails soft
        if cached.get("events"):
            cached["cache_hit"] = True
            cached["stale_error"] = f"{type(e).__name__}"
            return cached
        return {"ok": True, "source": "static", "as_of": _now_iso(),
                "week_start": _week_start(),
                "events": static_week_events(),
                "note": STATIC_NOTE, "fetched_at": time.time(),
                "cache_hit": False}
