"""GAUNTLET 7 Piece 1 — pro-charting data plane tests.

Offline: board._fetch_chart is monkeypatched with canned v8/chart
payloads everywhere (same seam the markets tests use). No live network.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gold_desk.markets import board as mb
from gold_desk.markets import chart as mc


def _result(bars: list[dict], symbol="GC=F") -> dict:
    """A minimal chart.result[0] payload from compact bar dicts."""
    return {
        "meta": {"symbol": symbol, "currency": "USD",
                 "exchangeName": "CMX", "regularMarketPrice": bars[-1]["c"]},
        "timestamp": [b["t"] // 1000 for b in bars],
        "indicators": {"quote": [{
            "open": [b["o"] for b in bars],
            "high": [b["h"] for b in bars],
            "low": [b["l"] for b in bars],
            "close": [b["c"] for b in bars],
            "volume": [b.get("v", 0) for b in bars],
        }]},
    }


def _synth_bars(n: int, start: float = 100.0) -> list[dict]:
    """Deterministic synthetic OHLCV bars (hourly, ascending)."""
    out = []
    price = start
    for i in range(n):
        o = price
        c = price + (0.5 if i % 3 else -0.3)
        h = max(o, c) + 0.4
        low = min(o, c) - 0.4
        out.append({"t": 1_700_000_000_000 + i * 3_600_000,
                    "o": o, "h": h, "l": low, "c": c, "v": 1000.0 + i})
        price = c
    return out


BARS = _synth_bars(80)


@pytest.fixture()
def offline(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "_fetch_chart",
                        lambda s, r="1d", i="15m": _result(BARS, s))
    return tmp_path


# ------------------------------------------------------------- bars shape
def test_fetch_chart_bars_shape(offline):
    out = mc.fetch_chart("GC=F", "1h", offline)
    assert out["ok"] is True
    assert len(out["bars"]) == len(BARS)
    b = out["bars"][0]
    assert set(b) == {"t", "o", "h", "l", "c", "v"}
    assert isinstance(b["t"], int) and b["t"] > 10**12  # ms epoch
    for k in "ohlc":
        assert isinstance(b[k], float)


def test_fetch_chart_stamps_source_and_delay(offline):
    out = mc.fetch_chart("GC=F", "1h", offline)
    assert out["source"] == "yahoo_v8_chart"
    assert out["data_delay"] in ("delayed", "real-time-ish")


def test_chart_timeframe_switch_changes_range(offline):
    a = mc.fetch_chart("GC=F", "15m", offline)
    d = mc.fetch_chart("GC=F", "1d", offline)
    assert a["interval"] == "15m" and d["interval"] == "1d"
    assert a["range"] != d["range"]


def test_chart_unsupported_timeframe_fails_soft(offline):
    out = mc.fetch_chart("GC=F", "7m", offline)
    assert out["ok"] is False
    assert "7m" in out["errors"][0]
    assert sorted(mc.TIMEFRAMES) == out["supported"]


def test_chart_empty_symbol_fails_soft(offline):
    assert mc.fetch_chart("", "1h", offline)["ok"] is False
    assert mc.fetch_chart("  ", "1h", offline)["ok"] is False


def test_chart_fail_soft_on_network_error(monkeypatch, tmp_path):
    def dead(symbol, range_, interval):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(mb, "_fetch_chart", dead)
    out = mc.fetch_chart("GC=F", "1h", tmp_path)
    assert out["ok"] is False
    assert "connection refused" in out["errors"][0]


def test_chart_stale_serve_on_error(monkeypatch, tmp_path):
    """A cached good answer is served stale when the feed dies."""
    calls = {"n": 0}

    def flaky(symbol, range_, interval):
        calls["n"] += 1
        if calls["n"] == 1:
            return _result(BARS, symbol)
        raise RuntimeError("feed down")

    monkeypatch.setattr(mb, "_fetch_chart", flaky)
    good = mc.fetch_chart("GC=F", "1h", tmp_path)
    assert good["ok"] is True
    # poison the TTL so the second call refetches
    cache_file = tmp_path / "cache" / "chart_GC=F_1h.json"
    blob = json.loads(cache_file.read_text())
    blob["fetched_at"] = 0.0
    cache_file.write_text(json.dumps(blob))
    stale = mc.fetch_chart("GC=F", "1h", tmp_path)
    assert stale["ok"] is True and len(stale["bars"]) == len(BARS)
    assert stale["stale_error"] is True and calls["n"] == 2


def test_chart_cache_hit(offline):
    a = mc.fetch_chart("GC=F", "1h", offline)
    cache_file = offline / "cache" / "chart_GC=F_1h.json"
    assert cache_file.exists()
    b = mc.fetch_chart("GC=F", "1h", offline)
    assert a["bars"] == b["bars"] and b.get("cached") is True


# ------------------------------------------------------------- indicators
def test_indicator_series_alignment(offline):
    out = mc.fetch_chart("GC=F", "1h", offline,
                         ["sma20", "sma50", "rsi14", "bb20", "atr14"])
    series = {s["name"]: s for s in out["indicator_series"]}
    assert set(series) == {"SMA20", "SMA50", "RSI14", "BB20", "ATR14"}
    # SMA20 is defined from bar index 19 → 80-19 = 61 points
    assert len(series["SMA20"]["points"]) == len(BARS) - 19
    # every point's t matches a bar timestamp (alignment invariant)
    bar_ts = {b["t"] for b in out["bars"]}
    for s in series.values():
        for p in s["points"]:
            assert p["t"] in bar_ts


def test_sma_series_values_exact():
    vals = [float(i) for i in range(1, 26)]  # 1..25
    ser = mc._sma_series(vals, 5)
    assert ser[4] == 3.0          # mean(1..5)
    assert ser[24] == 23.0        # mean(21..25)
    assert ser[3] is None
    # window slides by one: consecutive values differ by exactly 1
    assert abs(ser[10] - ser[9] - 1.0) < 1e-12


def test_ema_series_seed_is_sma():
    vals = [float(i) for i in range(1, 31)]
    ser = mc._ema_series(vals, 10)
    assert ser[9] == 5.5          # EMA seeded with the SMA of the first 10
    assert all(v is None for v in ser[:9])


def test_rsi_series_bounds_and_direction():
    up = [100.0 + i for i in range(30)]       # monotonic up
    ser = mc._rsi_series(up, 14)
    assert ser[-1] == 100.0                    # no losses → RSI 100
    down = [100.0 - i for i in range(30)]     # monotonic down
    ser_d = mc._rsi_series(down, 14)
    assert ser_d[-1] == 0.0                    # no gains → RSI 0


def test_bb_series_contains_closes_with_fat_window():
    vals = [float(i) for i in range(60)]
    up, mid, lo = mc._bb_series(vals, 20, 2.0)
    assert mid[19] == 9.5
    assert up[19] > mid[19] > lo[19]
    # a constant series has zero deviation → upper == lower == mid
    flat = [5.0] * 30
    up2, mid2, lo2 = mc._bb_series(flat, 20, 2.0)
    assert up2[29] == 5.0 and lo2[29] == 5.0 and mid2[29] == 5.0


def test_atr_series_matches_manual_wilder_seed():
    bars = _synth_bars(30)
    ser = mc._atr_series(bars, 14)
    trs = [0.0]
    for i in range(1, 30):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    seed = sum(trs[1:15]) / 14
    assert abs(ser[14] - seed) < 1e-12


def test_indicator_filter_drops_unknown(offline):
    out = mc.fetch_chart("GC=F", "1h", offline, ["sma20", "macd99", "zzz"])
    names = [s["name"] for s in out["indicator_series"]]
    assert names == ["SMA20"]


# ------------------------------------------------------------------- CLI
def test_cli_markets_chart_json_contract(offline, capsys):
    from gold_desk.cli import main

    rc = main(["markets-chart", "gold", "--timeframe", "1h", "--json",
               "--data-root", str(offline)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True and out["symbol"] == "GC=F"
    assert isinstance(out["bars"], list) and out["bars"]


def test_cli_markets_chart_dead_symbol_exit_1(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main

    def dead(symbol, range_, interval):
        raise RuntimeError("no chart result")
    monkeypatch.setattr(mb, "_fetch_chart", dead)
    rc = main(["markets-chart", "ZZZZZZ", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
