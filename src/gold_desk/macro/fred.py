"""GAUNTLET 7 Piece 4 — the macro / fixed-income plane.

FRED access (the ONE sanctioned key exception, noted in ROADMAP.md):
  1. `FRED_API_KEY` env  → api.stlouisfed.org JSON observations (the
     reliable path; free instant key at fred.stlouisfed.org/docs/api)
  2. keyless fallback    → fred.stlouisfed.org fredgraph.csv (public CSV,
     no account) — may be bot-walled from datacenter IPs, so failures
     are fail-soft with honest errors[]
  3. rates depth that NEVER needs FRED: the Treasury.gov daily yield
     curve XML (already keyless, parsed by markets.institutional)

Every fetch: TTL cache + stale-serve; every number stamps source + date.
Display/telemetry layer only — never wired into the live decision loop.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ..markets.board import fetch_daily_bars_ex

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gold-desk/1.0"
FRED_API_URL = ("https://api.stlouisfed.org/fred/series/observations"
                "?series_id={sid}&api_key={key}&file_type=json")
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"
SERIES_TTL_S = 3600
# D-007 (GAUNTLET 8): failures are NEGATIVELY cached for 10 minutes —
# a bot-walled fredgraph.csv or a missing key must not cost every
# dashboard call its full network timeout; the tombstone makes every
# retry within the window instant. Keyless CSV attempts get a tight
# timeout so even a COLD call stays bounded (~4s worst case).
NEG_TTL_S = 600
FRED_CSV_TIMEOUT_S = 4.0

# the dashboard catalog (all keyless-downloadable, all daily/monthly)
SERIES_CATALOG = {
    "DGS10":   "10-Year Treasury Constant Maturity (%)",
    "DGS2":    "2-Year Treasury Constant Maturity (%)",
    "DFII10":  "10-Year TIPS Real Yield (%)",
    "T10YIE":  "10-Year Breakeven Inflation (%)",
    "FEDFUNDS": "Effective Fed Funds Rate (%)",
    "CPIAUCSL": "CPI, All Urban Consumers (index)",
    "VIXCLS":  "VIX Close",
    "DTWEXBGS": "Broad Dollar Index",
}


def _cache_path(data_root, name: str) -> Path:
    d = Path(data_root)
    (d / "cache").mkdir(parents=True, exist_ok=True)
    return d / "cache" / name


def _cached_fetch(data_root, name: str, ttl: int, fetch,
                  neg_ttl: int | None = None) -> dict:
    """Cache-through fetch under <data_root>/cache/: fresh within TTL →
    fetch → stale-serve on error.

    D-007 (GAUNTLET 8): `neg_ttl` opts into NEGATIVE caching — a failed
    fetch with nothing stale to serve writes a short-lived {ok: False}
    tombstone so a persistently dead endpoint is not re-timed-out on
    every call. Default None keeps the legacy behavior."""
    path = _cache_path(data_root, name)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            cached = {}
    if cached.get("fetched_at"):
        age = time.time() - cached["fetched_at"]
        if cached.get("negative"):
            if age < (neg_ttl if neg_ttl is not None else ttl):
                out = dict(cached)
                out["cached"] = True
                return out
        elif age < ttl:
            cached["cached"] = True
            return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["stale_error"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as exc:  # noqa: BLE001 — stale-serve
        if cached and not cached.get("negative"):
            out = dict(cached)
            out["cached"] = False
            out["stale_error"] = True
            out["stale_reason"] = f"{type(exc).__name__}: {exc}"
            return out
        if neg_ttl is not None:
            tomb = {"ok": False, "negative": True,
                    "errors": [f"{type(exc).__name__}: {exc}"],
                    "fetched_at": time.time(), "stale_error": False}
            try:
                path.write_text(json.dumps(tomb))
            except OSError:
                pass
            return tomb
        return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"],
                "fetched_at": time.time(), "stale_error": False}


def _http_get(url: str, timeout: float = 8.0) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT, "Accept": "text/csv, application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


# ------------------------------------------------------------- parsing
def parse_fred_csv(text: str) -> list[dict]:
    """fredgraph.csv → [{date, value}] (pure; '.' = missing → skipped)."""
    rows: list[dict] = []
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return rows
    header = lines[0].split(",")
    date_i = 0
    val_i = 1 if len(header) > 1 else None
    if val_i is None:
        return rows
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) <= val_i:
            continue
        date, raw = parts[date_i].strip(), parts[val_i].strip()
        if not date or raw in (".", ""):
            continue
        try:
            rows.append({"date": date, "value": round(float(raw), 6)})
        except ValueError:
            continue
    return rows


def parse_fred_json(payload: dict) -> list[dict]:
    """api.stlouisfed.org observations → [{date, value}]."""
    out: list[dict] = []
    for obs in payload.get("observations") or []:
        v = obs.get("value")
        if v in (".", None, ""):
            continue
        try:
            out.append({"date": obs["date"],
                        "value": round(float(v), 6)})
        except (ValueError, KeyError):
            continue
    return out


# -------------------------------------------------------------- fetch
def fetch_series(series_id: str, data_root=None) -> dict:
    """One FRED series. FRED_API_KEY path first, keyless CSV fallback.

    D-007 (GAUNTLET 8): without a key the API attempt is SKIPPED, not
    tried-and-raised (fail-fast), the keyless CSV gets a tight timeout,
    and failures are negatively cached (NEG_TTL_S) so a bot-walled
    CSV endpoint costs one bounded attempt per window instead of one
    full timeout per dashboard call."""
    sid = str(series_id or "").strip().upper()
    if not sid or not sid.replace("_", "").isalnum():
        return {"ok": False, "series_id": sid,
                "errors": ["invalid series id"]}

    def _via_api() -> dict:
        key = os.environ.get("FRED_API_KEY", "").strip()
        if not key:
            raise RuntimeError("no FRED_API_KEY set")
        payload = json.loads(_http_get(FRED_API_URL.format(sid=sid, key=key)))
        obs = parse_fred_json(payload)
        if not obs:
            raise RuntimeError("fred api returned no usable observations")
        return {"ok": True, "series_id": sid, "source": "fred_api",
                "observations": obs}

    def _via_csv() -> dict:
        text = _http_get(FRED_CSV_URL.format(
            sid=urllib.parse.quote(sid)), timeout=FRED_CSV_TIMEOUT_S)
        obs = parse_fred_csv(text)
        if not obs:
            raise RuntimeError("fred csv returned no usable rows")
        return {"ok": True, "series_id": sid, "source": "fred_csv",
                "observations": obs}

    def _attempts() -> list:
        # D-007 fail-fast: no key → no wasted API attempt. The keyless
        # CSV path stays (it is the sanctioned keyless fallback).
        att = []
        if os.environ.get("FRED_API_KEY", "").strip():
            att.append(_via_api)
        att.append(_via_csv)
        return att

    out = _cached_fetch(data_root or "data", f"fred_{sid}.json",
                        SERIES_TTL_S,
                        lambda: _try_in_order(_attempts()),
                        neg_ttl=NEG_TTL_S)
    out.setdefault("series_id", sid)
    out.setdefault("label", SERIES_CATALOG.get(sid, sid))
    return out


def _try_in_order(attempts: list) -> dict:
    errs: list[str] = []
    for attempt in attempts:
        try:
            return attempt()
        except Exception as exc:  # noqa: BLE001 — try the next path
            errs.append(f"{type(exc).__name__}: {exc}")
    raise RuntimeError(" · ".join(errs))


def last_value(obs: list[dict]) -> dict | None:
    return obs[-1] if obs else None


def cpi_yoy(observations: list[dict]) -> list[dict]:
    """CPI index → YoY % series (12-month % change), pure."""
    out: list[dict] = []
    for i, row in enumerate(observations):
        if i < 12:
            continue
        base = observations[i - 12].get("value")
        v = row.get("value")
        if base and base > 0 and v:
            out.append({"date": row["date"],
                        "value": round((v / base - 1.0) * 100.0, 4)})
    return out


# -------------------------------------------------------- curve history
def curve_history(data_root=None, years: int = 2) -> dict:
    """Treasury.gov daily curve, N years → tenors × dates matrix."""
    import datetime as _dt
    from ..markets.institutional import (fetch_yield_curve,  # noqa: F401
                                         _parse_treasury_xml,
                                         _http_get_bytes,
                                         TREASURY_XML_URL, TREASURY_UA)
    this_year = _dt.datetime.now(_dt.timezone.utc).year
    days: dict[str, dict] = {}
    errors: list[str] = []
    for yr in range(this_year, this_year - years, -1):
        try:
            xml = _http_get_bytes(TREASURY_XML_URL.format(year=yr),
                                  TREASURY_UA, timeout=15)
            for row in _parse_treasury_xml(xml):
                days[row["date"]] = row
        except Exception as exc:  # noqa: BLE001 — fail-soft per year
            errors.append(f"{yr}: {type(exc).__name__}")
    if not days:
        return {"ok": False, "errors": errors or ["no curve data"]}
    dates = sorted(days)
    tenors = ["1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "20Y", "30Y"]
    matrix = [{"date": d,
               **{t: days[d].get(t) for t in tenors}} for d in dates]
    return {"ok": True, "source": "treasury.gov", "years": years,
            "tenors": tenors, "dates": dates, "matrix": matrix,
            "errors": errors}


# ------------------------------------------------- gold vs real yields
def _treasury_real_history() -> dict[str, float]:
    """Keyless fallback for the real-yield history: the Treasury.gov
    daily real-yield-curve CSV (the SAME keyless feed the Driver Board's
    D1 uses) parsed for the WHOLE current year → {YYYY-MM-DD: 10y}.
    Current-year only — labeled honestly by the caller."""
    import csv as _csv
    import datetime as _dt
    import io as _io
    from ..data.driver_feeds import _is_float, _treasury_csv
    text = _treasury_csv("real")
    rows = list(_csv.reader(_io.StringIO(text)))
    if len(rows) < 2:
        raise RuntimeError("empty treasury real yield csv")
    header = [h.strip().strip('"').title() for h in rows[0]]
    col = None
    for i, h in enumerate(header):
        if h == "10 Yr":
            col = i
            break
    if col is None:
        raise RuntimeError("no 10 Yr column in treasury real yield csv")
    out: dict[str, float] = {}
    for row in rows[1:]:
        if not row or len(row) <= col:
            continue
        raw = row[0].strip().strip('"')
        try:
            d = _dt.datetime.strptime(raw, "%m/%d/%Y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        v = row[col].strip().strip('"')
        if _is_float(v):
            out[d] = float(v)
    if not out:
        raise RuntimeError("no parsable rows in treasury real yield csv")
    return out


def gold_vs_real(data_root=None, lookback: int = 63) -> dict:
    """Gold daily closes joined with the 10y real yield + the rolling
    Pearson correlation over `lookback` aligned days.

    Real-yield source chain (fail-soft, first that works):
      1. FRED DFII10 (key or fredgraph keyless CSV)
      2. Treasury.gov daily real-yield-curve CSV — keyless, current year
    """
    gold_x = fetch_daily_bars_ex("GC=F", "1y", data_root or "data")
    gold_bars = (gold_x.get("bars") or []) if gold_x.get("ok") else []
    real = fetch_series("DFII10", data_root)
    fallback_note = None
    if not gold_bars:
        # D-009 honesty: distinguish "feed failed" from "no bars"
        if gold_x.get("no_bars"):
            return {"ok": False, "errors": ["no gold daily bars"]}
        return {"ok": False, "errors": [
            "gold daily bars fetch failed: "
            f"{gold_x.get('error') or gold_x.get('stale_error') or 'unknown'}"
            " (no cache to stale-serve)"]}
    if real.get("ok"):
        real_map = {r["date"]: r["value"] for r in real["observations"]}
        real_source = real.get("source", "fred")
    else:
        try:
            real_map = _treasury_real_history()
            real_source = "treasury.gov (keyless fallback, current year)"
            fallback_note = ("FRED DFII10 unavailable (" +
                             "; ".join(real.get("errors") or ["failed"])[:1]  # noqa: PD005
                             + ") — degraded to the keyless Treasury "
                               "real-yield curve, current year only")
        except Exception as exc:  # noqa: BLE001 — fail-soft, honest errors
            return {"ok": False, "stage": "DFII10",
                    "errors": (real.get("errors") or []) + [
                        f"real yield unavailable keyless: "
                        f"{type(exc).__name__}: {exc}"]}
    import datetime as _dt
    gold: dict[str, float] = {}
    for b in gold_bars:
        d = _dt.datetime.fromtimestamp(b["ts"] / 1000,
                                       tz=_dt.timezone.utc).strftime("%Y-%m-%d")
        gold[d] = b["c"]
    joined = [{"date": d, "gold": gold[d], "real_yield": real_map[d]}
              for d in sorted(set(gold) & set(real_map))]
    if len(joined) < 5:
        return {"ok": False, "errors": [
            f"only {len(joined)} aligned days (gold {len(gold)}, "
            f"real yield {len(real_map)})"]}
    tail = joined[-lookback:]
    corr = _pearson([r["gold"] for r in tail],
                    [r["real_yield"] for r in tail])
    out = {"ok": True, "source": "yahoo_v8_chart + " + real_source,
           "joined": joined, "n_days": len(joined),
           "rolling_correlation_window": len(tail),
           "rolling_correlation": corr}
    if fallback_note:
        out["degraded"] = True
        out["degraded_note"] = fallback_note
        out["errors"] = [fallback_note]
    return out


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0 or syy <= 0:
        return None
    return round(sxy / (sxx * syy) ** 0.5, 6)


# ------------------------------------------------------------- dashboard
def macro_dashboard(data_root=None) -> dict:
    """Aggregate macro snapshot: the rates complex (FRED or Treasury
    fallback), dollar, VIX, CPI path, and gold-vs-real-yields."""
    import concurrent.futures as ex
    dr = data_root or "data"
    wanted = ("DGS10", "DFII10", "T10YIE", "FEDFUNDS", "CPIAUCSL",
              "VIXCLS", "DTWEXBGS")
    series_out: dict[str, dict] = {}
    with ex.ThreadPoolExecutor(max_workers=8) as pool:
        futs = {pool.submit(fetch_series, sid, dr): sid for sid in wanted}
        # D-007 — gold_vs_real runs CONCURRENTLY with the series fetches
        # (it used to serialize after them, doubling the cold-call cost).
        gvr_fut = pool.submit(gold_vs_real, dr)
        for fut in futs:
            sid = futs[fut]
            try:
                series_out[sid] = fut.result(timeout=30)
            except Exception as exc:  # noqa: BLE001 — fail-soft per series
                series_out[sid] = {"ok": False,
                                   "errors": [f"{type(exc).__name__}: {exc}"]}
        try:
            gvr = gvr_fut.result(timeout=60)
        except Exception as exc:  # noqa: BLE001 — fail-soft
            gvr = {"ok": False,
                   "errors": [f"gold_vs_real: {type(exc).__name__}: {exc}"]}

    # live DXY/VIX from the existing keyless driver feeds (verified live)
    live = {}
    try:
        from ..data.driver_feeds import fetch_driver_values
        dv = fetch_driver_values(dr)
        # D-006 (GAUNTLET 8) — the driver payload is {"live": {"D2": …,
        # "D10": …}} keyed by driver ID (D2 = DXY via DX-Y.NYB, D10 = VIX
        # via ^VIX). The old lookup probed dv["drivers"]["dxy"]/["vix"] —
        # keys that do not exist — so live_drivers was always empty and
        # the UI showed no dollar/VIX row.
        live_map = (dv or {}).get("live") or {}
        for k, did in (("dxy", "D2"), ("vix", "D10")):
            v = live_map.get(did)
            if v:
                live[k] = v
        if not live and dv and not dv.get("ok"):
            live["error"] = "driver feeds unavailable"
    except Exception as exc:  # noqa: BLE001 — fail-soft
        live["error"] = f"{type(exc).__name__}"
    cpi = series_out.get("CPIAUCSL") or {}
    cpi_series = cpi_yoy(cpi.get("observations") or []) \
        if cpi.get("ok") else []

    def _latest(sid):
        s = series_out.get(sid) or {}
        if not s.get("ok"):
            return None
        lv = last_value(s.get("observations") or [])
        return {"date": lv["date"], "value": lv["value"]} if lv else None

    snapshot = {sid: _latest(sid) for sid in SERIES_CATALOG}
    n_ok = sum(1 for s in series_out.values() if s.get("ok"))
    return {
        "ok": n_ok > 0,
        "n_series_ok": n_ok,
        "n_series": len(SERIES_CATALOG),
        "snapshot": snapshot,
        "labels": SERIES_CATALOG,
        "series_errors": {sid: (series_out[sid].get("errors") or
                                [series_out[sid].get("stale_reason")])
                          for sid in series_out if not series_out[sid].get("ok")},
        "cpi_yoy": {"latest": cpi_series[-1] if cpi_series else None,
                    "series_tail": cpi_series[-24:] if cpi_series else []},
        "live_drivers": live,
        "gold_vs_real": {k: gvr[k] for k in
                         ("ok", "source", "n_days", "rolling_correlation",
                          "rolling_correlation_window", "errors",
                          "degraded", "degraded_note")
                         if k in gvr},
        "sources": sorted({s.get("source") for s in series_out.values()
                           if s.get("source")}),
    }
