"""R3-2 Build 4 — tests for risk/backtest.py (GUESS setup vs historical bars).

All bar series are SYNTHETIC (hand-built Bar objects) or canned Yahoo chart
JSON via the _TEST_BARS seam — no network anywhere.

The synthetic "squeeze" pattern pins the engine's mechanics end-to-end:
  * flat days  → 24 TR=2 bars, no signal
  * breakout day → pre-London range 02:00..06:59 (high base+1), signal bar
    at 08:00 closing base+2 (above range high + 0.1·ATR buffer), then a
    follow-through bar whose high/low decide the trade's fate
  * always-win follow-through (high = signal+100) → target hit, hit-rate 1.0
  * always-lose follow-through (low = signal−100) → stop hit, hit-rate 0.0
  * flat follow-through → 6-bar time stop
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gold_desk.data.model import Bar
from gold_desk.risk import backtest as bt
from gold_desk.risk.backtest import BacktestEngine, fetch_hourly_bars
from gold_desk.setup.spec import SetupSpec

MONDAY = datetime(2026, 6, 1, tzinfo=timezone.utc)   # 2026-06-01 is a Monday


def _bar(day: datetime, hour: int, o: float, c: float,
         h: float | None = None, l: float | None = None,
         duration_h: int = 1) -> Bar:
    open_dt = day.replace(hour=0, minute=0, second=0, microsecond=0,
                          tzinfo=timezone.utc) + timedelta(hours=hour)
    close_dt = open_dt + timedelta(hours=duration_h)
    return Bar(
        ts_open=open_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ts_close=close_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=o, high=h if h is not None else max(o, c) + 1.0,
        low=l if l is not None else min(o, c) - 1.0,
        close=c, volume=100.0,
    )


def _flat_bar(day: datetime, hour: int, p: float) -> Bar:
    """o=c=p, h=p+1, l=p−1 → TR = 2 everywhere (keeps ATR calm)."""
    return _bar(day, hour, p, p, h=p + 1.0, l=p - 1.0)


def _flat_day(day: datetime, p: float) -> list[Bar]:
    return [_flat_bar(day, h, p) for h in range(24)]


def _breakout_day(day: datetime, p: float, mode: str = "win") -> list[Bar]:
    """One GUESS signal day. `mode` picks the follow-through:
    win → target, lose → stop, flat → time stop, none → no follow-through."""
    bars: list[Bar] = []
    for h in range(0, 8):                       # 00..07 flat (incl. pre-range)
        bars.append(_flat_bar(day, h, p))
    # signal bar 08:00 open, closes 09:00 at p+2 — breaks the pre-London
    # high (p+1) by more than the 0.1·ATR buffer (ATR ≈ 2 → buffer ≈ 0.2)
    bars.append(_bar(day, 8, p, p + 2.0, h=p + 3.0, l=p - 1.0))
    if mode == "win":                           # high far above any 2R target
        bars.append(_bar(day, 9, p + 2, p + 2, h=p + 102.0, l=p + 1.0))
        for h in range(10, 24):
            bars.append(_flat_bar(day, h, p + 2))
    elif mode == "lose":                        # low far below any 1R stop
        bars.append(_bar(day, 9, p + 2, p + 2, h=p + 3.0, l=p - 102.0))
        for h in range(10, 24):
            bars.append(_flat_bar(day, h, p + 2))
    elif mode == "flat":                        # neither stop nor target
        for h in range(9, 24):
            bars.append(_flat_bar(day, h, p + 2))
    elif mode == "none":                        # series ends at the signal
        pass
    return bars


def _squeeze_series(mode: str = "win", n_breakout_days: int = 3) -> list[Bar]:
    """Warmup flat day + alternating flat/breakout days."""
    bars = _flat_day(MONDAY, 2400.0)
    day = MONDAY + timedelta(days=1)
    p = 2400.0
    made = 0
    while made < n_breakout_days:
        bars.extend(_flat_day(day, p))
        day += timedelta(days=1)
        bars.extend(_breakout_day(day, p, mode=mode))
        p += 2.0
        day += timedelta(days=1)
        made += 1
    return bars


# ---------------------------------------------------------- always win
def test_always_win_setup_hit_rate_one():
    bars = _squeeze_series("win", n_breakout_days=3)
    out = BacktestEngine(bars, window=20).run()
    assert out["ok"] is True
    assert out["n_trades"] == 3
    assert out["hit_rate"] == 1.0
    assert all(t["reason"] == "target" for t in out["trades"])
    assert all(t["side"] == "buy" for t in out["trades"])
    assert out["n_wins"] == 3 and out["n_losses"] == 0
    assert out["total_return"] > 0
    assert out["equity_end"] > out["equity_start"]
    # every win is a 2R target minus costs — hand-verifiable from the fields
    for t in out["trades"]:
        assert t["pnl"] == pytest.approx(
            (t["exit"] - t["entry"]) * t["units"] - t["units"] * 0.10,
            abs=0.02)
        assert t["exit"] > t["entry"]


def test_always_lose_setup_hit_rate_zero():
    bars = _squeeze_series("lose", n_breakout_days=2)
    out = BacktestEngine(bars, window=20).run()
    assert out["n_trades"] == 2
    assert out["hit_rate"] == 0.0
    assert all(t["reason"] == "stop" for t in out["trades"])
    assert out["n_losses"] == 2
    assert out["total_return"] < 0
    assert out["profit_factor"] == 0.0          # gross profit 0 / gross loss
    assert out["avg_loss"] < 0


def test_time_stop_exits_at_six_bars():
    bars = _flat_day(MONDAY, 2400.0) + _breakout_day(
        MONDAY + timedelta(days=1), 2400.0, mode="flat")
    out = BacktestEngine(bars, window=20).run()
    assert out["n_trades"] == 1
    t = out["trades"][0]
    assert t["reason"] == "time_stop"
    assert t["bars_held"] == 6                     # 6-bar time stop
    assert t["exit_ts"] == "2026-06-02T15:00:00Z"  # 09:00 + 6h close


def test_end_of_data_force_close():
    bars = _flat_day(MONDAY, 2400.0) + _breakout_day(
        MONDAY + timedelta(days=1), 2400.0, mode="none")
    out = BacktestEngine(bars, window=20).run()
    assert out["n_trades"] == 1
    t = out["trades"][0]
    assert t["reason"] == "end_of_data"
    assert t["entry"] == t["exit"]                 # closed at the last close
    assert t["pnl"] == pytest.approx(-t["units"] * 0.10, abs=0.02)  # costs


def test_no_signal_on_flat_days_only():
    bars = _flat_day(MONDAY, 2400.0) + _flat_day(
        MONDAY + timedelta(days=1), 2400.0)
    out = BacktestEngine(bars, window=20).run()
    assert out["n_trades"] == 0
    assert out["hit_rate"] is None
    assert out["total_return"] == 0.0
    assert out["equity_end"] == out["equity_start"] == 100_000.0


# ---------------------------------------------------------- determinism
def test_determinism_same_bars_same_seed_byte_identical():
    bars = _squeeze_series("win", 2)
    a = BacktestEngine(bars, seed=7, slippage_atr_mult=0.05).run()
    b = BacktestEngine(bars, seed=7, slippage_atr_mult=0.05).run()
    assert a["equity_curve_sha256"] == b["equity_curve_sha256"]
    assert a["equity_curve"] == b["equity_curve"]
    assert a["trades"] == b["trades"]


def test_determinism_different_seed_diverges_under_slippage():
    bars = _squeeze_series("win", 2)
    a = BacktestEngine(bars, seed=7, slippage_atr_mult=0.05).run()
    b = BacktestEngine(bars, seed=99, slippage_atr_mult=0.05).run()
    assert a["equity_curve_sha256"] != b["equity_curve_sha256"]


def test_determinism_pure_mechanical_fills_ignore_seed():
    """slippage_atr_mult=0 → the seed must not be load-bearing."""
    bars = _squeeze_series("win", 2)
    a = BacktestEngine(bars, seed=1).run()
    b = BacktestEngine(bars, seed=987654).run()
    assert a["equity_curve_sha256"] == b["equity_curve_sha256"]


def test_result_echoes_seed_and_config():
    bars = _squeeze_series("win", 1)
    out = BacktestEngine(bars, seed=42, window=20,
                         starting_equity=50_000.0, risk_pct=0.02,
                         cost_per_unit=0.25).run()
    assert out["seed"] == 42
    assert out["config"]["starting_equity"] == 50_000.0
    assert out["config"]["risk_pct"] == 0.02
    assert out["config"]["cost_per_unit"] == 0.25
    assert out["config"]["spec_hash"] == SetupSpec().hash()
    assert out["setup_id"] == "GUESS_london_range_breakout"


# ---------------------------------------------------------- metrics wiring
def test_stats_internal_consistency():
    bars = _squeeze_series("win", 3)
    out = BacktestEngine(bars, window=20).run()
    trades = out["trades"]
    wins = [t["pnl"] for t in trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
    assert out["n_trades"] == len(trades) == out["n_wins"] + out["n_losses"]
    assert out["hit_rate"] == pytest.approx(len(wins) / len(trades))
    assert out["avg_win"] == pytest.approx(sum(wins) / len(wins), abs=0.01)
    assert out["avg_loss"] is None or out["avg_loss"] == pytest.approx(
        sum(losses) / len(losses), abs=0.01)
    if losses:
        assert out["profit_factor"] == pytest.approx(
            sum(wins) / abs(sum(losses)), abs=1e-3)
    # equity wiring
    assert out["total_return"] == pytest.approx(
        out["equity_end"] / out["equity_start"] - 1.0, abs=1e-6)
    assert len(out["equity_curve"]) == out["n_bars"]
    assert out["equity_curve"][0] == pytest.approx(100_000.0, abs=0.01)
    assert 0.0 <= out["max_drawdown"] <= 1.0
    assert isinstance(out["sharpe"], float)


def test_buy_and_hold_comparison_present():
    bars = _squeeze_series("win", 2)
    out = BacktestEngine(bars, window=20).run()
    assert out["buy_hold_return"] is not None
    assert out["buy_hold_return"] == pytest.approx(
        bars[-1].close / bars[0].close - 1.0, abs=5e-7)   # rounded to 6dp
    # both strategies surfaced in one payload
    assert "total_return" in out and "buy_hold_return" in out


def test_equity_curve_length_matches_bars_and_marks():
    bars = _squeeze_series("win", 2)
    out = BacktestEngine(bars, window=20).run()
    curve = out["equity_curve"]
    assert len(curve) == len(bars)
    assert all(v > 0 for v in curve)               # 1% risk never wipes out


# ---------------------------------------------------------- journal
def test_journal_jsonl_written(tmp_path: Path):
    bars = _squeeze_series("win", 1)
    jp = tmp_path / "equity.jsonl"
    out = BacktestEngine(bars, window=20).run(journal_path=str(jp))
    assert jp.exists()
    lines = jp.read_text().strip().split("\n")
    assert len(lines) == out["n_bars"] + out["n_trades"] * 2  # bars + entry/exit
    first = json.loads(lines[0])
    assert set(first) >= {"ts", "equity", "event"}
    assert first["event"] == "bar"
    events = [json.loads(l)["event"] for l in lines]
    assert events.count("entry") == out["n_trades"]
    assert events.count("exit") == out["n_trades"]
    # the journal text is what the sha256 pins
    assert out["equity_curve_sha256"] == __import__("hashlib").sha256(
        (jp.read_text()).encode("utf-8")).hexdigest()


def test_journal_hash_pins_every_bar():
    """One altered bar → a different journal hash (the hash is load-bearing)."""
    bars = _squeeze_series("win", 2)
    a = BacktestEngine(bars, window=20).run()
    bars2 = list(bars)
    bars2[50] = _flat_bar(MONDAY, 0, 2411.0)      # perturb one warmup bar
    b = BacktestEngine(bars2, window=20).run()
    assert a["equity_curve_sha256"] != b["equity_curve_sha256"]


# ---------------------------------------------------------- parser + fetch
def _chart_body(ts0: datetime, n: int = 48, base: float = 2400.0) -> str:
    ts = [int((ts0 + timedelta(hours=i)).timestamp()) for i in range(n)]
    quote = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    for i in range(n):
        o = round(base + i * 0.1, 2)
        quote["open"].append(o)
        quote["high"].append(round(o + 1.0, 2))
        quote["low"].append(round(o - 1.0, 2))
        quote["close"].append(round(o + 0.5, 2))
        quote["volume"].append(100)
    body = {"chart": {"result": [{
        "meta": {"symbol": "GC=F"},
        "timestamp": ts,
        "indicators": {"quote": [quote]},
    }]}}
    return json.dumps(body)


def test_parse_hourly_chart_roundtrip(monkeypatch):
    t0 = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bt, "_TEST_BARS", _chart_body(t0, n=48))
    bars = fetch_hourly_bars("GC=F", "1y", data_root="/tmp/bt_test_a")
    assert len(bars) == 48
    assert bars[0].open == 2400.0
    assert bars[0].ts_close.endswith("03:00:00Z")  # open 02:00 → close 03:00
    assert bars[-1].close == pytest.approx(2400.0 + 47 * 0.1 + 0.5)
    assert bars == sorted(bars, key=lambda b: b.ts_close)


def test_parse_skips_null_rows_and_forming_bar(monkeypatch):
    t0 = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
    body = json.loads(_chart_body(t0, n=48))
    res = body["chart"]["result"][0]
    res["indicators"]["quote"][0]["close"][5] = None      # null row skipped
    # a forming bar: closes 1h in the future → firewall drops it
    res["timestamp"].append(int(time.time()) + 3600)
    for k in ("open", "high", "low", "close", "volume"):
        res["indicators"]["quote"][0][k].append(2500.0)
    monkeypatch.setattr(bt, "_TEST_BARS", json.dumps(body))
    bars = fetch_hourly_bars("GC=F", "1y", data_root="/tmp/bt_test_b")
    assert len(bars) == 47                              # 48 − null − forming
    assert all(b.ts_close.endswith("Z") for b in bars)


def test_fetch_cache_roundtrip_identical(monkeypatch, tmp_path):
    t0 = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bt, "_TEST_BARS", _chart_body(t0, n=24))
    first = fetch_hourly_bars("GC=F", "1y", data_root=str(tmp_path))
    cache = tmp_path / "cache" / "bt_hourly_GC%3DF_1y.json"
    assert cache.exists()                              # TTL cache written
    # second read comes from the cache — identical bars (canonical round-trip)
    monkeypatch.setattr(bt, "_TEST_BARS", None)        # network now forbidden
    second = fetch_hourly_bars("GC=F", "1y", data_root=str(tmp_path))
    assert first == second


def test_fetch_raises_on_no_result(monkeypatch, tmp_path):
    monkeypatch.setattr(bt, "_TEST_BARS",
                        json.dumps({"chart": {"result": []}}))
    with pytest.raises(RuntimeError):
        fetch_hourly_bars("GC=F", "1y", data_root=str(tmp_path))


def test_range_key_validation(monkeypatch, tmp_path):
    """Unsupported range falls back to the 1y default rather than erroring."""
    t0 = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bt, "_TEST_BARS", _chart_body(t0, n=10))
    bars = fetch_hourly_bars("GC=F", "10y", data_root=str(tmp_path))
    assert len(bars) == 10


# ---------------------------------------------------------- CLI wiring
def test_cli_backtest_json(monkeypatch, capsys, tmp_path):
    from gold_desk.cli import cmd_backtest
    t0 = datetime(2026, 1, 5, 2, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(bt, "_TEST_BARS", _chart_body(t0, n=200))

    class _Args:
        bars = "1y"
        setup = "guess"
        symbol = "GC=F"
        seed = 7
        slippage = 0.0
        journal = str(tmp_path / "j.jsonl")
        json = True
        data_root = str(tmp_path)

    rc = cmd_backtest(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["symbol"] == "GC=F" and out["range"] == "1y"
    assert out["n_bars"] == 200
    assert "equity_curve_sha256" in out
    assert Path(out["journal_path"]).exists()


def test_cli_backtest_fetch_failure(monkeypatch, capsys, tmp_path):
    from gold_desk.cli import cmd_backtest
    monkeypatch.setattr(bt, "_TEST_BARS",
                        json.dumps({"chart": {"result": []}}))

    class _Args:
        bars = "1y"
        setup = "guess"
        symbol = "GC=F"
        seed = 7
        slippage = 0.0
        journal = None
        json = True
        data_root = str(tmp_path)

    rc = cmd_backtest(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False


def test_cli_risk_offline_returns(monkeypatch, capsys):
    from gold_desk.cli import cmd_risk

    class _Args:
        returns = json.dumps(R_SERIES)
        benchmark_returns = json.dumps(R_SERIES)   # self-benchmark → β = 1
        positions = json.dumps([{"symbol": "SPY", "weight": 0.5},
                                {"symbol": "CASH", "weight": 0.5}])
        json = True
        data_root = "/tmp/risk_cli"

    monkeypatch.setattr("gold_desk.cli._risk_default_portfolio",
                        lambda root: None)      # never hit the network
    rc = cmd_risk(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["var"]["parametric"]["95"] < 0
    assert out["beta"]["beta"] == pytest.approx(1.0, abs=1e-9)  # self-benchmark
    assert out["stress"]["portfolio_shocks"]["gfc_2008"] == pytest.approx(
        -0.1925)


def test_cli_risk_pretty_renders_table(monkeypatch, capsys):
    from gold_desk.cli import cmd_risk

    class _Args:
        returns = json.dumps(R_SERIES)
        benchmark_returns = None
        positions = json.dumps([{"symbol": "SPY", "weight": 0.5},
                                {"symbol": "CASH", "weight": 0.5}])
        json = False
        data_root = "/tmp/risk_cli"

    monkeypatch.setattr("gold_desk.cli._risk_default_portfolio",
                        lambda root: None)
    rc = cmd_risk(_Args())
    text = capsys.readouterr().out
    assert rc == 0
    assert "Gaussian" in text and "historical" in text and "Monte Carlo" in text
    assert "expected shortfall" in text
    assert "2008 Global Financial Crisis" in text


def test_cli_risk_default_portfolio_unreachable(monkeypatch, capsys):
    """Live default portfolio fails → honest error, rc=1 (fail-closed)."""
    from gold_desk.cli import cmd_risk

    class _Args:
        returns = None
        benchmark_returns = None
        positions = None
        json = True
        data_root = "/tmp/risk_cli_dead"

    monkeypatch.setattr("gold_desk.cli._risk_default_portfolio",
                        lambda root: None)
    rc = cmd_risk(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False
    assert "offline" in out["error"]


def test_cli_risk_default_portfolio_epoch_ms_bars(monkeypatch, capsys):
    """REGRESSION (found live): fetch_daily_bars stamps bars with EPOCH-MS
    INTEGER ts (board.py's shape), not ISO strings — the default-portfolio
    path must convert to date keys, not crash with TypeError. BTC trades
    weekends so the date-alignment intersection also gets exercised.

    HERMETIC (R3-3 FIX 0): the bar fetch is fully monkeypatched, so the
    test runs offline forever; the stress assertions pin the exact
    documented shock math for the default book 40% SPY / 30% GC=F /
    15% BTC-USD / 15% cash (CASH unshocked → surfaced, never zeroed
    silently):
      GFC   0.40×(−0.385) + 0.30×(−0.20) + 0.15×(−0.45) = −0.2815
      COVID 0.40×(−0.339) + 0.30×(−0.12) + 0.15×(−0.50) = −0.2466
      2022  0.40×(−0.194) + 0.30×(−0.05) + 0.15×(−0.65) = −0.1901
    (the pre-gold/BTC-vector value −0.154 = 0.4×−0.385 only held while
    gold+BTC were unmodeled; R3-3 shocked them, so this pin moved.)
    """
    from datetime import datetime, timedelta, timezone

    from gold_desk.cli import cmd_risk

    t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)   # a Monday
    base = {"SPY": 400.0, "GC=F": 2400.0, "BTC-USD": 80000.0}

    def fake_fetch_daily(symbol, range_, data_root="data"):
        bars = []
        price = base[symbol]
        for i in range(120):                       # ~17 weeks, 7d/week
            day = t0 + timedelta(days=i)
            if symbol != "BTC-USD" and day.weekday() >= 5:
                continue                           # SPY/GC=F weekdays only
            price *= 1.0 + (0.001 if i % 3 else -0.001)
            bars.append({"ts": int(day.timestamp() * 1000),  # EPOCH-MS int
                         "o": price, "h": price * 1.01,
                         "l": price * 0.99, "c": price, "v": 1000.0})
        return bars

    monkeypatch.setattr("gold_desk.markets.board.fetch_daily_bars",
                        fake_fetch_daily)

    class _Args:
        returns = None
        benchmark_returns = None
        positions = None
        json = True
        data_root = "/tmp/risk_cli_epochms"

    rc = cmd_risk(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["portfolio"].startswith("default 40% SPY")
    # ~85 common weekdays across the three calendars (intersection < 120)
    assert 60 <= out["n_observations"] <= 119
    assert out["beta"]["n"] == out["n_observations"]  # SPY benchmark aligned
    # exact documented stress math for the default book (gold+BTC now
    # shocked, CASH the only unmodeled leg)
    shocks = out["stress"]["portfolio_shocks"]
    assert shocks["gfc_2008"] == pytest.approx(
        0.40 * -0.385 + 0.30 * -0.20 + 0.15 * -0.45, abs=1e-12)      # −0.2815
    assert shocks["covid_2020"] == pytest.approx(
        0.40 * -0.339 + 0.30 * -0.12 + 0.15 * -0.50, abs=1e-12)      # −0.2466
    assert shocks["rate_shock_2022"] == pytest.approx(
        0.40 * -0.194 + 0.30 * -0.05 + 0.15 * -0.65, abs=1e-12)     # −0.1901
    gfc = {s["name"]: s for s in out["stress"]["scenarios"]}["gfc_2008"]
    assert gfc["shocked"] == ["BTC-USD", "GC=F", "SPY"]
    assert gfc["unshocked"] == []   # CASH leg is excluded from positions


R_SERIES = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.012,
            0.008, -0.014, 0.003, -0.006, 0.011, 0.007, -0.009]
