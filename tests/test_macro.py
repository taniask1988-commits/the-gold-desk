"""GAUNTLET 7 Piece 4 — macro/FRED plane tests. Offline: FRED CSV/JSON
parsers against recorded fixtures; Treasury curve parser against a
fixture; fail-soft degradation (FRED down → keyless fallback path, never
a crash). The gold-vs-real join is monkeypatched at both seams.
"""
from __future__ import annotations

import json

import pytest

from gold_desk.macro import fred
from gold_desk.markets import institutional as inst


FRED_CSV_SAMPLE = (
    "DATE,DFII10\n"
    "2026-09-26,2.24\n"
    "2026-09-27,.\n"
    "2026-09-29,2.31\n"
    "2026-09-30,2.35\n"
)

FRED_JSON_SAMPLE = {
    "observations": [
        {"date": "2026-09-26", "value": "2.24"},
        {"date": "2026-09-27", "value": "."},       # missing → skipped
        {"date": "2026-09-29", "value": "2.31"},
    ],
}


# ---------------------------------------------------------------- parsers
def test_parse_fred_csv_skips_missing_and_sorts():
    rows = fred.parse_fred_csv(FRED_CSV_SAMPLE)
    assert [r["date"] for r in rows] == ["2026-09-26", "2026-09-29",
                                         "2026-09-30"]
    assert rows[-1]["value"] == 2.35
    assert fred.parse_fred_csv("") == []


def test_parse_fred_json_skips_dots():
    rows = fred.parse_fred_json(FRED_JSON_SAMPLE)
    assert len(rows) == 2 and rows[0]["value"] == 2.24


def test_cpi_yoy_twelve_month_change():
    obs = [{"date": f"2025-{m:02d}-01", "value": 100.0 + m} for m in
           range(1, 13)]
    obs += [{"date": f"2026-{m:02d}-01", "value": 100.0 + m + 6}
            for m in range(1, 13)]     # +6 points over the year
    out = fred.cpi_yoy(obs)
    assert len(out) == 12
    assert out[-1]["value"] == pytest.approx(
        (118.0 / 112.0 - 1) * 100, abs=1e-3)  # module publishes 4dp


# ------------------------------------------------------------- fetch path
def test_fetch_series_prefers_api_key(monkeypatch, tmp_path):
    monkeypatch.setenv("FRED_API_KEY", "test-key-123")
    seen = {}

    def fake_get(url, timeout=15.0):
        seen["url"] = url
        return json.dumps(FRED_JSON_SAMPLE)

    monkeypatch.setattr(fred, "_http_get", fake_get)
    out = fred.fetch_series("DFII10", tmp_path)
    assert out["ok"] is True and out["source"] == "fred_api"
    assert "test-key-123" in seen["url"]   # the request carried the key


def test_fetch_series_falls_back_keyless(monkeypatch, tmp_path):
    """FRED_API_KEY absent → keyless CSV path (fail-closed to fallback,
    per the ROADMAP-noted exception)."""
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    seen = {}

    def fake_get(url, timeout=15.0):
        seen["url"] = url
        return FRED_CSV_SAMPLE

    monkeypatch.setattr(fred, "_http_get", fake_get)
    out = fred.fetch_series("DFII10", tmp_path)
    assert out["ok"] is True and out["source"] == "fred_csv"
    assert "fredgraph.csv" in seen["url"] and "DFII10" in seen["url"]


def test_fetch_series_key_failure_then_csv(monkeypatch, tmp_path):
    """A 400 from the API path falls through to the keyless CSV."""
    monkeypatch.setenv("FRED_API_KEY", "bad-key")
    calls = []

    def fake_get(url, timeout=15.0):
        calls.append(url)
        if "api_key" in url:
            raise RuntimeError("HTTP 400 Bad Request")
        return FRED_CSV_SAMPLE

    monkeypatch.setattr(fred, "_http_get", fake_get)
    out = fred.fetch_series("DFII10", tmp_path)
    assert out["ok"] is True and out["source"] == "fred_csv"
    assert len(calls) == 2


def test_fetch_series_both_paths_dead_fail_soft(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "_http_get",
                        lambda url, timeout=15.0: (_ for _ in ()).throw(
                            RuntimeError("connection refused")))
    out = fred.fetch_series("DFII10", tmp_path)
    assert out["ok"] is False
    assert "connection refused" in out["errors"][0]


def test_fetch_series_stale_serve(monkeypatch, tmp_path):
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    state = {"n": 0}

    def flaky(url, timeout=15.0):
        state["n"] += 1
        if state["n"] == 1:
            return FRED_CSV_SAMPLE
        raise RuntimeError("down")

    monkeypatch.setattr(fred, "_http_get", flaky)
    a = fred.fetch_series("DFII10", tmp_path)
    cache = tmp_path / "cache" / "fred_DFII10.json"
    blob = json.loads(cache.read_text())
    blob["fetched_at"] = 0.0
    cache.write_text(json.dumps(blob))
    b = fred.fetch_series("DFII10", tmp_path)
    assert a["ok"] and b["ok"] and b["stale_error"] is True


def test_fetch_series_rejects_weird_ids(tmp_path):
    assert fred.fetch_series("../etc", tmp_path)["ok"] is False
    assert fred.fetch_series("", tmp_path)["ok"] is False


# ----------------------------------------------------------- curve history
def test_curve_history_from_fixture(monkeypatch, tmp_path):
    xml = b"""<?xml version="1.0"?>
    <feed xmlns="http://www.w3.org/2005/Atom">
      <entry><d:NEW_DATE xmlns:d="x">2026-10-01T00:00:00</d:NEW_DATE>
        <d:BC_1MONTH xmlns:d="x">4.20</d:BC_1MONTH>
        <d:BC_2YEAR xmlns:d="x">3.90</d:BC_2YEAR>
        <d:BC_10YEAR xmlns:d="x">4.10</d:BC_10YEAR>
        <d:BC_30YEAR xmlns:d="x">4.55</d:BC_30YEAR>
      </entry>
    </feed>"""
    monkeypatch.setattr(inst, "_http_get_bytes",
                        lambda url, ua, timeout=15: xml)
    out = fred.curve_history(tmp_path, years=1)
    assert out["ok"] is True and len(out["matrix"]) == 1
    row = out["matrix"][0]
    assert row["date"] == "2026-10-01" and row["10Y"] == 4.1
    assert row["30Y"] == 4.55 and row["1M"] == 4.2


def test_curve_history_all_years_dead_fail_soft(monkeypatch, tmp_path):
    def dead(url, ua, timeout=15):
        raise RuntimeError("treasury down")
    monkeypatch.setattr(inst, "_http_get_bytes", dead)
    out = fred.curve_history(tmp_path, years=1)
    assert out["ok"] is False and out["errors"]


# ----------------------------------------------------- gold vs real join
def test_gold_vs_real_join_and_correlation(monkeypatch, tmp_path):
    import datetime as _dt
    bars = []
    base = _dt.datetime(2026, 8, 1, tzinfo=_dt.timezone.utc)
    for i in range(30):
        d = base + _dt.timedelta(days=i)
        bars.append({"ts": int(d.timestamp() * 1000),
                     "o": 4000, "h": 4010, "l": 3990,
                     "c": 4000.0 + i * 2.0, "v": 100})   # gold up-trend
    real = {"ok": True, "source": "fred_csv", "observations": [
        {"date": (base + _dt.timedelta(days=i)).strftime("%Y-%m-%d"),
         "value": 2.0 - i * 0.01} for i in range(30)]}    # yields down
    monkeypatch.setattr(fred, "fetch_daily_bars",
                        lambda s, r, dr: bars)
    monkeypatch.setattr(fred, "fetch_series", lambda s, dr: real)
    out = fred.gold_vs_real(tmp_path, lookback=30)
    assert out["ok"] is True and out["n_days"] == 30
    # gold up while real yields down → strongly negative correlation
    assert out["rolling_correlation"] < -0.99


def test_gold_vs_real_fail_soft_when_no_real_yields(monkeypatch, tmp_path):
    """FRED dead AND the keyless Treasury fallback dead → honest errors."""
    monkeypatch.setattr(fred, "fetch_daily_bars",
                        lambda s, r, dr: [{"ts": 1700000000000,
                                           "c": 4000.0}])
    monkeypatch.setattr(fred, "fetch_series",
                        lambda s, dr: {"ok": False, "errors": ["fred down"]})
    import gold_desk.data.driver_feeds as df
    def dead_csv(kind):
        raise RuntimeError("treasury down")
    monkeypatch.setattr(df, "_treasury_csv", dead_csv)
    out = fred.gold_vs_real(tmp_path)
    assert out["ok"] is False
    assert any("real yield unavailable" in e for e in out["errors"])


def test_pearson_edge_cases():
    assert fred._pearson([1, 2, 3], [1, 2, 3]) == 1.0
    assert fred._pearson([1, 2, 3], [3, 2, 1]) == -1.0
    assert fred._pearson([5, 5, 5], [1, 2, 3]) is None   # zero variance
    assert fred._pearson([1, 2], [1, 2]) is None          # too short


# ------------------------------------------------------------------- CLI
def test_cli_macro_series_json_contract(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    monkeypatch.delenv("FRED_API_KEY", raising=False)
    monkeypatch.setattr(fred, "_http_get", lambda url, timeout=15.0:
                        FRED_CSV_SAMPLE)
    rc = main(["macro-series", "--ids", "DFII10", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["DFII10"]["ok"] is True
    assert out["DFII10"]["observations"]


def test_cli_curve_history_json_contract(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    xml = b"""<feed xmlns="http://www.w3.org/2005/Atom">
      <entry><d:NEW_DATE xmlns:d="x">2026-10-01T00:00:00</d:NEW_DATE>
      <d:BC_10YEAR xmlns:d="x">4.10</d:BC_10YEAR></entry></feed>"""
    monkeypatch.setattr(inst, "_http_get_bytes",
                        lambda url, ua, timeout=15: xml)
    rc = main(["curve-history", "--years", "1", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True and out["matrix"]


def test_gold_vs_real_treasury_fallback_when_fred_dead(monkeypatch, tmp_path):
    """D3 fix (final critic): FRED absent → degrade to the KEYLESS
    Treasury.gov real-yield-curve CSV, honestly labeled degraded."""
    import datetime as _dt
    bars = []
    base = _dt.datetime(2026, 8, 1, tzinfo=_dt.timezone.utc)
    for i in range(30):
        d = base + _dt.timedelta(days=i)
        bars.append({"ts": int(d.timestamp() * 1000),
                     "o": 4000, "h": 4010, "l": 3990,
                     "c": 4000.0 + i * 2.0, "v": 100})
    monkeypatch.setattr(fred, "fetch_daily_bars", lambda s, r, dr: bars)
    monkeypatch.setattr(fred, "fetch_series",
                        lambda s, dr: {"ok": False,
                                       "errors": ["fred unreachable"]})
    csv_text = (
        "Date,Standard Deviation,1 Mo,2 Mo,3 Mo,10 Yr,20 Yr,30 Yr\n"
        + "\n".join(
            f"{(base + _dt.timedelta(days=i)).strftime('%m/%d/%Y')},0.01,"
            f"4.0,4.0,4.0,{2.0 - i * 0.01:.2f},4.4,4.5"
            for i in range(30)))
    import gold_desk.data.driver_feeds as df
    monkeypatch.setattr(df, "_treasury_csv", lambda kind: csv_text)
    out = fred.gold_vs_real(tmp_path, lookback=30)
    assert out["ok"] is True
    assert "treasury.gov" in out["source"] and "keyless fallback" in out["source"]
    assert out["degraded"] is True and "FRED DFII10 unavailable" in out["degraded_note"]
    assert out["n_days"] == 30
    assert out["rolling_correlation"] < -0.99


def test_gold_vs_real_both_sources_dead_fail_soft(monkeypatch, tmp_path):
    import datetime as _dt
    monkeypatch.setattr(fred, "fetch_daily_bars", lambda s, r, dr: [])
    monkeypatch.setattr(fred, "fetch_series",
                        lambda s, dr: {"ok": False, "errors": ["x"]})
    out = fred.gold_vs_real(tmp_path)
    assert out["ok"] is False and out["errors"]
