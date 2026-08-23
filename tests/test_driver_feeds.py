"""Real driver-value feed tests. All network calls mocked."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.data import driver_feeds as df  # noqa: E402

REAL_CSV = (
    'Date,"5 YR","7 YR","10 YR","20 YR","30 YR"\n'
    '08/21/2026,2.09,2.23,2.40,2.78,3.00\n'
    '08/20/2026,2.05,2.18,2.35,2.73,2.95\n'
)
NOMINAL_CSV = (
    'Date,"1 Mo","3 Mo","1 Yr","10 Yr","30 Yr"\n'
    '08/21/2026,3.80,3.88,4.03,4.74,5.27\n'
)


def yahoo_quote_payload(price):
    import json
    return json.dumps({"chart": {"result": [{
        "meta": {"regularMarketPrice": price}}]}})


def cot_payload():
    return (
        '[{"report_date_as_yyyy_mm_dd": "2026-08-18", '
        '"managed_money_long_all": "268951", '
        '"managed_money_short_all": "100067"}]'
    )


def test_treasury_real_parse(monkeypatch):
    monkeypatch.setattr(df, "_treasury_csv", lambda kind: REAL_CSV)
    out = df._treasury_latest("real")
    assert out["10 Yr"] == 2.40  # title-cased key
    assert out["5 Yr"] == 2.09


def test_collect_full_success(monkeypatch):
    monkeypatch.setattr(df, "_treasury_csv",
                        lambda kind: REAL_CSV if kind == "real" else NOMINAL_CSV)

    def fake_quote(symbol):
        if symbol == "DX-Y.NYB":
            return 98.839
        if symbol == "^VIX":
            return 15.13
        raise RuntimeError("unknown symbol")
    monkeypatch.setattr(df, "_yahoo_quote", fake_quote)
    monkeypatch.setattr(df, "_cftc_managed_money_net", lambda: 168884.0)

    out = df._collect()
    live = out["live"]
    assert out["ok"] is True
    assert live["D1"]["value"] == 2.40
    assert live["D2"]["value"] == 98.839
    assert live["D3"]["value"] == 3.80
    assert live["D4"]["value"] == 2.34  # 4.74 - 2.40
    assert live["D5"]["value"] == 168884.0
    assert live["D5"]["display_k"] == 168.9
    assert live["D10"]["value"] == 15.13
    assert "D9" in live and "D11" in live  # computed always present
    assert out["unavailable"] == []


def test_collect_fail_soft_per_driver(monkeypatch):
    def boom_treasury(kind):
        raise RuntimeError("treasury down")
    def boom_quote(symbol):
        raise RuntimeError("yahoo down")
    def boom_cot():
        raise RuntimeError("cftc blocked")
    monkeypatch.setattr(df, "_treasury_csv", boom_treasury)
    monkeypatch.setattr(df, "_yahoo_quote", boom_quote)
    monkeypatch.setattr(df, "_cftc_managed_money_net", boom_cot)

    out = df._collect()
    live = out["live"]
    # computed drivers survive everything
    assert "D9" in live and "D11" in live
    assert out["ok"] is True  # at least the computed ones
    for did in ("D1", "D2", "D3", "D4", "D5", "D10"):
        assert did in out["unavailable"]


def test_cot_parse(monkeypatch):
    monkeypatch.setattr(df, "_http_get", lambda url, timeout=15: cot_payload())
    net = df._cftc_managed_money_net()
    assert net == 168884.0


def test_nfp_clock_known_dates():
    # 2026-08-23 16:00 UTC (Sunday) -> next NFP = Fri 2026-09-04 13:30 UTC
    now = datetime(2026, 8, 23, 16, 0, tzinfo=timezone.utc)
    h = df._hours_to_next_nfp(now)
    expected = (datetime(2026, 9, 4, 13, 30, tzinfo=timezone.utc) - now).total_seconds() / 3600
    assert abs(h - expected) < 0.01

    # right after release -> next month's NFP
    just_after = datetime(2026, 9, 4, 14, 0, tzinfo=timezone.utc)
    h2 = df._hours_to_next_nfp(just_after)
    expected2 = (datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc) - just_after).total_seconds() / 3600
    assert abs(h2 - expected2) < 0.01

    # eve of NFP: Thursday 2026-09-03 15:00 UTC -> ~22.5h
    eve = datetime(2026, 9, 3, 15, 0, tzinfo=timezone.utc)
    assert df._hours_to_next_nfp(eve) == 22.5


def test_session_liquidity_scores():
    assert df._session_liquidity_score(datetime(2026, 8, 21, 14, 0, tzinfo=timezone.utc)) == 10.0  # overlap
    assert df._session_liquidity_score(datetime(2026, 8, 21, 9, 0, tzinfo=timezone.utc)) == 8.0   # london
    assert df._session_liquidity_score(datetime(2026, 8, 21, 17, 0, tzinfo=timezone.utc)) == 6.0  # ny
    assert df._session_liquidity_score(datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)) == 4.0   # asia
    assert df._session_liquidity_score(datetime(2026, 8, 21, 22, 0, tzinfo=timezone.utc)) == 2.0  # rollover


def test_cache_roundtrip(tmp_path, monkeypatch):
    calls = {"n": 0}

    def fake_collect():
        calls["n"] += 1
        return {"ok": True, "live": {"D1": {"value": 2.4, "unit": "%",
                                            "source": "x"}},
                "unavailable": []}
    out1 = df._cached_fetch(tmp_path, "drivers", 300, fake_collect)
    out2 = df._cached_fetch(tmp_path, "drivers", 300, fake_collect)
    assert calls["n"] == 1
    assert out2.get("cache_hit") is True
    assert out1["live"]["D1"]["value"] == 2.4
