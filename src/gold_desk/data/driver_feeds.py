"""Real driver values for the Driver Board — free, keyless, fail-soft.

Verified-live sources (2026-08, no keys, no accounts):
  D1  10y real yield   : US Treasury daily real yield curve CSV
  D2  DXY              : Yahoo Finance DX-Y.NYB chart quote
  D3  policy rate path : US Treasury 1-Mo bill (market's near-policy rate)
  D4  10y breakeven    : computed = Treasury nominal 10y − real 10y
  D5  COT managed money: CFTC public Socrata API (fail-soft; some networks
                         block it — falls back to simulated honestly)
  D9  event clock      : computed — hours to next NFP (first Friday, 13:30 UTC)
  D10 VIX              : Yahoo Finance ^VIX chart quote
  D11 session liquidity: computed from UTC hour (deterministic)

D6 ETF flows, D7 central-bank buying, D8 EFP, D12 dealer gamma have no free
feeds — they stay simulated and are honestly badged SIM in the UI.

All fetches: TTL cache, fail-soft (never raises), source-stamped.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .feeds import _cached_fetch, _http_get

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gold-desk/1.0"
DRIVERS_TTL_S = 5 * 60  # quotes + daily yields: 5 minutes is plenty


def _treasury_csv(kind: str) -> str:
    """kind: 'real' | 'nominal' → latest rows of the Treasury yield curve."""
    year = datetime.now(timezone.utc).year
    t = "daily_treasury_real_yield_curve" if kind == "real" else "daily_treasury_yield_curve"
    url = (
        f"https://home.treasury.gov/resource-center/data-chart-center/"
        f"interest-rates/daily-treasury-rates.csv/{year}/all"
        f"?type={t}&field_tdr_date_value={year}&page&_format=csv"
    )
    return _http_get(url, timeout=15)


def _treasury_latest(kind: str) -> dict[str, float]:
    """Parse the newest CSV row into {column: value} (values in %).
    Keys are normalized to title-case ("10 Yr") — Treasury mixes "10 YR"/"10 Yr".
    """
    text = _treasury_csv(kind)
    lines = [l for l in text.strip().splitlines() if l.strip()]
    header = [h.strip().strip('"').title() for h in lines[0].split(",")]
    row = [v.strip().strip('"') for v in lines[1].split(",")]
    return {h: float(v) for h, v in zip(header, row) if _is_float(v)}


def _is_float(v) -> bool:
    try:
        float(v)
        return True
    except (TypeError, ValueError):
        return False


def _yahoo_quote(symbol: str) -> float:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/"
           f"{urllib.request.quote(symbol)}?interval=1d&range=5d")
    data = json.loads(_http_get(url))
    meta = data["chart"]["result"][0]["meta"]
    price = meta.get("regularMarketPrice")
    if price is None:
        raise RuntimeError(f"no price for {symbol}")
    return float(price)


def _cftc_managed_money_net() -> float:
    """CFTC disaggregated COT: gold managed-money net, in contracts.
    Public Socrata API, keyless. Some networks block it → caller fail-softs.
    """
    import urllib.parse
    params = urllib.parse.urlencode({
        "market_code": "088691",
        "$order": "report_date_as_yyyy_mm_dd DESC",
        "$limit": 1,
    })
    url = f"https://publicreporting.cftc.gov/resource/gpe5-46if.json?{params}"
    body = _http_get(url, timeout=15)
    rows = json.loads(body)
    if not rows:
        raise RuntimeError("empty COT response")
    r = rows[0]
    longs = r.get("managed_money_long_all")
    shorts = r.get("managed_money_short_all")
    if not longs or not shorts:
        raise RuntimeError("managed-money fields missing")
    return float(int(longs) - int(shorts))


def _hours_to_next_nfp(now: datetime | None = None) -> float:
    """NFP prints the first Friday of each month at 13:30 UTC (rule, no feed)."""
    now = now or datetime.now(timezone.utc)
    for offset in range(0, 3):  # this month + next two
        y, m = now.year, now.month + offset
        y, m = (y + (m - 1) // 12, (m - 1) % 12 + 1)
        first = datetime(y, m, 1, tzinfo=timezone.utc)
        # Friday=4; days until first Friday
        days = (4 - first.weekday()) % 7
        nfp = first + timedelta(days=days, hours=13, minutes=30)
        if nfp > now:
            return round((nfp - now).total_seconds() / 3600, 1)
    return 999.0


def _session_liquidity_score(now: datetime | None = None) -> float:
    """Deterministic hour-of-day liquidity score 0-10 (see docs/MARKET_DRIVERS.md)."""
    now = now or datetime.now(timezone.utc)
    h = now.hour
    if 12 <= h < 16:
        return 10.0  # London-NY overlap — deepest
    if 7 <= h < 12:
        return 8.0  # London
    if 16 <= h < 21:
        return 6.0  # NY afternoon
    if 0 <= h < 7:
        return 4.0  # Asia
    return 2.0  # rollover / off-session


def _collect() -> dict:
    live: dict[str, dict] = {}
    unavailable: list[str] = []

    def attempt(did: str, fn, unit: str, source: str, transform=float):
        try:
            live[did] = {
                "value": transform(fn()),
                "unit": unit,
                "source": source,
            }
        except Exception:  # noqa: BLE001 — fail-soft per driver
            unavailable.append(did)

    # D1 + D3 + D4 share two CSV fetches
    real = nominal = None
    try:
        real = _treasury_latest("real")
    except Exception:
        unavailable.append("D1")
    try:
        nominal = _treasury_latest("nominal")
    except Exception:
        pass

    if real and "10 Yr" in real:
        live["D1"] = {"value": real["10 Yr"], "unit": "%",
                      "source": "US Treasury (real yield curve)"}
    elif "D1" not in live and "D1" not in unavailable:
        unavailable.append("D1")

    if nominal:
        if "1 Mo" in nominal:
            live["D3"] = {"value": nominal["1 Mo"], "unit": "%",
                          "source": "US Treasury (1-Mo bill)"}
        else:
            unavailable.append("D3")
        if real and "10 Yr" in real and "10 Yr" in nominal:
            live["D4"] = {
                "value": round(nominal["10 Yr"] - real["10 Yr"], 2),
                "unit": "%",
                "source": "computed: Treasury nominal − real (10y)",
            }
        else:
            unavailable.append("D4")
    else:
        unavailable.extend(["D3", "D4"])

    attempt("D2", lambda: _yahoo_quote("DX-Y.NYB"), "idx",
            "Yahoo Finance (DX-Y.NYB)")
    attempt("D5", _cftc_managed_money_net, "contracts",
            "CFTC COT (disaggregated, gold)")
    attempt("D10", lambda: _yahoo_quote("^VIX"), "idx",
            "Yahoo Finance (^VIX)")

    # computed (always available)
    now = datetime.now(timezone.utc)
    live["D9"] = {"value": _hours_to_next_nfp(now), "unit": "h",
                  "source": "computed: NFP first-Friday 13:30 UTC"}
    live["D11"] = {"value": _session_liquidity_score(now), "unit": "score",
                   "source": "computed: session clock"}

    if "D5" in live:
        live["D5"]["display_k"] = round(live["D5"]["value"] / 1000, 1)

    return {"ok": bool(live), "live": live, "unavailable": unavailable}


def fetch_driver_values(data_root: str | Path = "data") -> dict:
    out = _cached_fetch(data_root, "drivers", DRIVERS_TTL_S, _collect)
    out["kind"] = "driver_values"
    return out
