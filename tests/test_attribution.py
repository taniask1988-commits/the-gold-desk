"""R3-3 Build 5b — tests for risk/attribution.py (P&L attribution).

LEDGER10: 10 hand-computed trades across 3 symbols / 3 setups / 3
sessions — every view is pinned against hand math (exact totals, per
bucket, win rates, profit factor) and the conservation law
Σ by_asset == Σ by_setup == Σ by_hour == total is asserted at 1e-9.

The journal-reconstruction tests build mock event streams with the REAL
journal shapes (TicketEvent payloads, entry Fill with status
"paper-position-opened", exit Fill with resolution records and
paper-exit / forced-close phases) — including an open position and an
orphan exit to pin the honesty counters. No network anywhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gold_desk.risk import attribution as at

# (symbol, side, qty, entry, exit, hour, setup, pnl) — hand-computable
LEDGER10 = [
    {"symbol": "XAUUSD", "side": "buy", "qty": 1.0, "entry": 2400.0,
     "exit": 2412.0, "timestamp": "2026-06-01T08:00:00Z",
     "setup_tag": "breakout"},                                    # +12
    {"symbol": "XAUUSD", "side": "short", "qty": 2.0, "entry": 2410.0,
     "exit": 2404.0, "timestamp": "2026-06-02T09:30:00Z",
     "setup_tag": "breakout"},                                    # +12
    {"symbol": "XAUUSD", "side": "buy", "qty": 1.0, "entry": 2405.0,
     "exit": 2399.0, "timestamp": "2026-06-03T12:00:00Z",
     "setup_tag": "fade"},                                        # −6
    {"symbol": "SPY", "side": "buy", "qty": 10.0, "entry": 450.0,
     "exit": 459.0, "timestamp": "2026-06-04T14:00:00Z",
     "setup_tag": "breakout"},                                    # +90
    {"symbol": "SPY", "side": "sell", "qty": 10.0, "entry": 459.0,
     "exit": 450.0, "timestamp": "2026-06-05T15:00:00Z",
     "setup_tag": "fade"},                                        # +90
    {"symbol": "SPY", "side": "buy", "qty": 10.0, "entry": 455.0,
     "exit": 449.0, "timestamp": "2026-06-08T16:00:00Z",
     "setup_tag": "fade"},                                        # −60
    {"symbol": "BTC-USD", "side": "short", "qty": 0.5, "entry": 80000.0,
     "exit": 76000.0, "timestamp": "2026-06-09T20:00:00Z",
     "setup_tag": "breakdown"},                                   # +2000
    {"symbol": "BTC-USD", "side": "buy", "qty": 0.5, "entry": 76000.0,
     "exit": 79200.0, "timestamp": "2026-06-10T21:00:00Z",
     "setup_tag": "breakdown"},                                   # +1600
    {"symbol": "BTC-USD", "side": "buy", "qty": 0.25, "entry": 79200.0,
     "exit": 78000.0, "timestamp": "2026-06-11T03:00:00Z",
     "setup_tag": "fade"},                                        # −300
    {"symbol": "XAUUSD", "side": "short", "qty": 1.0, "entry": 2400.0,
     "exit": 2410.0, "timestamp": "2026-06-12T05:00:00Z",
     "setup_tag": "fade"},                                        # −10
]
TOTAL10 = 3428.0
ASSET10 = {"XAUUSD": 8.0, "SPY": 120.0, "BTC-USD": 3300.0}
SETUP10 = {"breakout": 114.0, "fade": -286.0, "breakdown": 3600.0}


# ------------------------------------------------------------------ P&L sign
def test_short_trade_entry_100_exit_90_positive():
    """Spec pin: short, entry 100, exit 90, qty 2 → +20."""
    out = at.attribute([{"symbol": "XAUUSD", "side": "short", "qty": 2.0,
                         "entry": 100.0, "exit": 90.0,
                         "timestamp": "2026-06-01T10:00:00Z",
                         "setup_tag": "t"}])
    assert out["total_pnl"] == pytest.approx(20.0)


def test_long_trade_pnl_and_side_aliases():
    row = {"symbol": "SPY", "side": "buy", "qty": 10.0, "entry": 100.0,
           "exit": 110.0, "timestamp": "2026-06-01T10:00:00Z",
           "setup_tag": "t"}
    assert at.trade_pnl(row) == pytest.approx(100.0)
    for alias in ("sell", "SHORT", "Short"):
        assert at.trade_pnl({**row, "side": alias}) == pytest.approx(
            -100.0), alias
    for alias in ("long", "BUY", "B"):
        assert at.trade_pnl({**row, "side": alias}) == pytest.approx(
            100.0), alias


# ------------------------------------------------------------------ LEDGER10
def test_ledger10_exact_totals():
    out = at.attribute(LEDGER10)
    assert out["ok"] is True
    assert out["n_trades"] == 10
    assert out["total_pnl"] == pytest.approx(TOTAL10)
    assert out["n_wins"] == 6 and out["n_losses"] == 4
    assert out["win_rate"] == pytest.approx(0.6)
    assert out["gross_profit"] == pytest.approx(3804.0)
    assert out["gross_loss"] == pytest.approx(376.0)
    assert out["profit_factor"] == pytest.approx(3804.0 / 376.0)


def test_ledger10_by_asset_exact():
    out = at.attribute(LEDGER10)
    rows = {r["symbol"]: r for r in out["by_asset"]}
    assert set(rows) == set(ASSET10)
    for sym, pnl in ASSET10.items():
        assert rows[sym]["pnl"] == pytest.approx(pnl), sym
        assert rows[sym]["pct_of_total"] == pytest.approx(pnl / TOTAL10), sym
        assert rows[sym]["n_trades"] == {"XAUUSD": 4, "SPY": 3,
                                         "BTC-USD": 3}[sym]
    assert sum(r["pct_of_total"] for r in out["by_asset"]) == pytest.approx(
        1.0, abs=1e-9)


def test_ledger10_by_setup_exact_with_win_rate():
    out = at.attribute(LEDGER10)
    rows = {r["setup"]: r for r in out["by_setup"]}
    assert set(rows) == set(SETUP10)
    for setup, pnl in SETUP10.items():
        assert rows[setup]["pnl"] == pytest.approx(pnl), setup
    assert rows["breakout"]["win_rate"] == pytest.approx(1.0)   # 3/3
    assert rows["fade"]["win_rate"] == pytest.approx(0.2)       # 1/5
    assert rows["breakdown"]["win_rate"] == pytest.approx(1.0)  # 2/2


def test_ledger10_by_hour_exact():
    out = at.attribute(LEDGER10)
    rows = {r["hour"]: r for r in out["by_hour"]}
    assert rows[8]["pnl"] == pytest.approx(12.0)      # London
    assert rows[9]["pnl"] == pytest.approx(12.0)      # London
    assert rows[12]["pnl"] == pytest.approx(-6.0)     # London
    assert rows[14]["pnl"] == pytest.approx(90.0)     # NY
    assert rows[20]["pnl"] == pytest.approx(2000.0)   # NY
    assert rows[3]["pnl"] == pytest.approx(-300.0)    # Asia
    assert rows[5]["pnl"] == pytest.approx(-10.0)     # Asia
    assert rows[0]["n_trades"] == 0                   # zero-filled bucket


def test_session_boundaries_eight_twelve_thirteen():
    """Spec pin: hour 8 → London, 12 → London, 13 → NY (and 7 → Asia)."""
    rows = {r["hour"]: r for r in at.attribute(LEDGER10)["by_hour"]}
    assert rows[8]["session"] == "London"
    assert rows[12]["session"] == "London"
    ledger = [{"symbol": "XAUUSD", "side": "buy", "qty": 1.0,
               "entry": 100.0, "exit": 101.0,
               "timestamp": f"2026-06-01T{h:02d}:00:00Z", "setup_tag": "t"}
              for h in (7, 13, 23, 0)]
    by_hour = {r["hour"]: r for r in at.attribute(ledger)["by_hour"]}
    assert by_hour[7]["session"] == "Asia"
    assert by_hour[13]["session"] == "NY"
    assert by_hour[23]["session"] == "NY"
    assert by_hour[0]["session"] == "Asia"


def test_hour_view_always_24_buckets():
    for ledger in ([], LEDGER10, at.synthetic_ledger()):
        hours = [r["hour"] for r in at.attribute(ledger)["by_hour"]]
        assert hours == list(range(24))


def test_conservation_all_views_1e_9():
    """Σ by_asset == Σ by_setup == Σ by_hour == total (spec: 1e-9)."""
    for ledger in (LEDGER10, at.synthetic_ledger(), at.synthetic_ledger(5)):
        out = at.attribute(ledger)
        for view in ("by_asset", "by_setup", "by_hour"):
            assert sum(r["pnl"] for r in out[view]) == pytest.approx(
                out["total_pnl"], abs=1e-9), view


def test_views_sorted_by_pnl_desc():
    out = at.attribute(LEDGER10)
    for view in ("by_asset", "by_setup"):
        pnls = [r["pnl"] for r in out[view]]
        assert pnls == sorted(pnls, reverse=True)


# ------------------------------------------------------------------ degenerate
def test_empty_ledger_zeros_no_crash():
    out = at.attribute([])
    assert out["ok"] is True
    assert out["total_pnl"] == 0.0
    assert out["n_trades"] == 0 and out["win_rate"] == 0.0
    assert out["by_asset"] == [] and out["by_setup"] == []
    assert len(out["by_hour"]) == 24
    assert all(r["pnl"] == 0.0 for r in out["by_hour"])
    assert out["profit_factor"] is None


def test_invalid_rows_skipped_with_reasons():
    ledger = LEDGER10 + [
        {"symbol": "SPY", "side": "buy", "qty": "lots", "entry": 1.0,
         "exit": 2.0, "timestamp": "2026-06-01T10:00:00Z",
         "setup_tag": "x"},                          # non-numeric qty
        {"symbol": "", "side": "buy", "qty": 1.0, "entry": 1.0,
         "exit": 2.0, "timestamp": "2026-06-01T10:00:00Z",
         "setup_tag": "x"},                          # no symbol
        {"symbol": "SPY", "side": "straddle", "qty": 1.0, "entry": 1.0,
         "exit": 2.0, "timestamp": "2026-06-01T10:00:00Z",
         "setup_tag": "x"},                          # unknown side
        "not even a dict",
    ]
    out = at.attribute(ledger)
    assert out["n_trades"] == 10                     # valid rows intact
    assert out["total_pnl"] == pytest.approx(TOTAL10)
    assert out["n_skipped_rows"] == 4
    assert len(out["skipped_rows"]) == 4
    assert all("reason" in s for s in out["skipped_rows"])


def test_unparseable_timestamp_excluded_from_hourly_only():
    ledger = [{**LEDGER10[0], "timestamp": "not-a-timestamp"}]
    out = at.attribute(ledger)
    assert out["n_trades"] == 1
    assert out["by_asset"][0]["pnl"] == pytest.approx(12.0)   # still booked
    assert all(r["n_trades"] == 0 for r in out["by_hour"])
    assert out["n_unparsed_timestamps"] == 1


def test_epoch_ms_and_naive_timestamps():
    from datetime import datetime, timezone
    epoch_ms = int(datetime(2026, 6, 1, 13, 0, tzinfo=timezone.utc)
                   .timestamp() * 1000)
    rows = [
        {"symbol": "A", "side": "buy", "qty": 1.0, "entry": 100.0,
         "exit": 101.0, "timestamp": epoch_ms, "setup_tag": "t"},
        {"symbol": "A", "side": "buy", "qty": 1.0, "entry": 100.0,
         "exit": 102.0, "timestamp": "2026-06-01T08:00:00", "setup_tag": "t"},
    ]
    out = at.attribute(rows)
    by_hour = {r["hour"]: r for r in out["by_hour"]}
    assert by_hour[13]["session"] == "NY"            # epoch-ms → UTC hour
    assert by_hour[8]["session"] == "London"         # naive ISO → UTC
    assert out["n_unparsed_timestamps"] == 0


def test_zero_qty_and_break_even_rows():
    rows = [
        {"symbol": "A", "side": "buy", "qty": 0.0, "entry": 100.0,
         "exit": 110.0, "timestamp": "2026-06-01T10:00:00Z",
         "setup_tag": "t"},                          # zero qty → 0 P&L
        {"symbol": "A", "side": "buy", "qty": 1.0, "entry": 100.0,
         "exit": 100.0, "timestamp": "2026-06-01T11:00:00Z",
         "setup_tag": "t"},                          # break-even
    ]
    out = at.attribute(rows)
    assert out["total_pnl"] == 0.0
    assert out["n_wins"] == 2                        # ≥ 0 counts as a win
    assert out["win_rate"] == 1.0


def test_normalize_ledger_symbol_case_and_default_setup():
    rows, skipped = at.normalize_ledger([
        {"symbol": "xauusd", "side": "BUY", "qty": 1.0, "entry": 1.0,
         "exit": 2.0, "timestamp": "2026-06-01T10:00:00Z"}])
    assert skipped == []
    assert rows[0]["symbol"] == "XAUUSD"
    assert rows[0]["side"] == "buy"
    assert rows[0]["setup_tag"] == "untagged"
    assert rows[0]["hour"] == 10


# ------------------------------------------------------------------ synthetic
def test_synthetic_ledger_deterministic():
    assert at.synthetic_ledger() == at.synthetic_ledger()
    assert at.synthetic_ledger(seed=5) == at.synthetic_ledger(seed=5)
    assert at.synthetic_ledger(seed=5) != at.synthetic_ledger(seed=6)


def test_synthetic_ledger_shape():
    rows = at.synthetic_ledger()
    assert len(rows) == 24
    assert {r["symbol"] for r in rows} == {"XAUUSD", "SPY", "BTC-USD"}
    assert len({r["setup_tag"] for r in rows}) == 4
    assert {r["side"] for r in rows} == {"buy", "short"}
    hours = [at.parse_timestamp(r["timestamp"]).hour for r in rows]
    assert {at.session_for_hour(h) for h in hours} == {"Asia", "London", "NY"}
    out = at.attribute(rows)
    assert out["ok"] is True and out["n_trades"] == 24


def test_synthetic_ledger_custom_size():
    assert len(at.synthetic_ledger(n_trades=7)) == 7
    assert len(at.synthetic_ledger(n_trades=100)) == 100


# ------------------------------------------------------------------ journal
def _ticket_event(tid, sym="XAUUSD", side="buy", setup="GUESS_test",
                  lots=1.0, ts="2026-06-01T08:00:00Z"):
    return {"kind": "TicketEvent", "ts": ts, "decision_ts": ts,
            "payload": {"ticket_id": tid, "symbol": sym, "side": side,
                        "lots": lots, "setup_id": setup, "entry": 2400.0}}


def _entry_fill(tid, price, lots, side, ts):
    return {"kind": "Fill", "ts": ts, "decision_ts": ts,
            "payload": {"ticket_id": tid, "price": price, "lots": lots,
                        "side": side, "status": "paper-position-opened"},
            "reason_code": "FILL"}


def _exit_fill(tid, exit_price, ts, phase="paper-exit", pnl=0.0):
    return {"kind": "Fill", "ts": ts, "decision_ts": ts,
            "payload": {"resolution": {"ticket_id": tid,
                                       "exit": exit_price, "reason": "target",
                                       "pnl": pnl, "closed_ts": ts},
                        "phase": phase}}


def test_journal_reconstruction_basic():
    events = [
        _ticket_event("T1", setup="GUESS_london_range_breakout"),
        _entry_fill("T1", 2401.0, 1.0, "buy", "2026-06-01T08:00:00Z"),
        _exit_fill("T1", 2412.0, "2026-06-01T09:00:00Z", pnl=1100.0),
    ]
    rec = at.ledger_from_journal(events)
    assert rec["matched"] == 1 and rec["unmatched_exits"] == 0
    assert rec["open_or_unmatched"] == 0
    row = rec["ledger"][0]
    assert row == {"symbol": "XAUUSD", "side": "buy", "qty": 1.0,
                   "entry": 2401.0, "exit": 2412.0,
                   "timestamp": "2026-06-01T08:00:00Z",
                   "setup_tag": "GUESS_london_range_breakout"}
    # ledger P&L convention: qty × Δprice (the account's own point-value
    # pnl stays in account.py — here +11.0 price-units)
    assert at.attribute(rec["ledger"])["total_pnl"] == pytest.approx(11.0)


def test_journal_reconstruction_mock_orchestrator_stream():
    """A realistic multi-day mock: 3 closed trades (one forced-close), one
    position still open, one orphan exit — the honesty counters must
    separate them and the closed-trades P&L must be exact."""
    events = [
        _ticket_event("T1", sym="XAUUSD", side="buy", setup="breakout"),
        _entry_fill("T1", 2400.0, 1.0, "buy", "2026-06-01T08:00:00Z"),
        _exit_fill("T1", 2412.0, "2026-06-01T09:00:00Z", pnl=1200.0),
        _ticket_event("T2", sym="XAUUSD", side="short", setup="breakout"),
        _entry_fill("T2", 2410.0, 2.0, "short", "2026-06-02T09:00:00Z"),
        _exit_fill("T2", 2398.0, "2026-06-02T10:00:00Z", pnl=2400.0),
        _ticket_event("T3", sym="XAUUSD", side="buy", setup="fade"),
        _entry_fill("T3", 2405.0, 1.0, "buy", "2026-06-03T12:00:00Z"),
        _exit_fill("T3", 2400.0, "2026-06-05T20:00:00Z",
                   phase="forced-close", pnl=-500.0),
        # T4 opened, never closed (weekend force-close not yet run)
        _ticket_event("T4", sym="XAUUSD", side="buy", setup="fade"),
        _entry_fill("T4", 2402.0, 1.0, "buy", "2026-06-05T13:00:00Z"),
        # orphan exit: journal truncated before its entry fill
        _exit_fill("T9", 2000.0, "2026-06-05T21:00:00Z", pnl=1.0),
    ]
    rec = at.ledger_from_journal(events)
    assert rec["n_entry_fills"] == 4
    assert rec["n_exit_fills"] == 4
    assert rec["matched"] == 3
    assert rec["open_or_unmatched"] == 1          # T4 still open
    assert rec["unmatched_exits"] == 1            # T9 orphan
    out = at.attribute(rec["ledger"])
    # T1: +12, T2: short 2410→2398 ×2 = +24, T3: −5 → total +31
    assert out["total_pnl"] == pytest.approx(31.0)
    assert out["n_trades"] == 3
    setups = {r["setup"]: r for r in out["by_setup"]}
    assert setups["breakout"]["pnl"] == pytest.approx(36.0)
    assert setups["fade"]["pnl"] == pytest.approx(-5.0)
    hours = {r["hour"]: r for r in out["by_hour"]}
    assert hours[8]["pnl"] == pytest.approx(12.0)
    assert hours[9]["pnl"] == pytest.approx(24.0)
    assert hours[12]["pnl"] == pytest.approx(-5.0)


def test_journal_reconstruction_empty_and_garbage():
    assert at.ledger_from_journal([])["ledger"] == []
    rec = at.ledger_from_journal([{"kind": "NoSetup"},
                                  {"kind": "Fill", "payload": {}},
                                  "junk", None,
                                  {"kind": "Fill", "payload": "string"}])
    assert rec["matched"] == 0 and rec["ledger"] == []


def test_load_journal_ledger_from_disk(tmp_path):
    (tmp_path / "events").mkdir()
    with (tmp_path / "events" / "2026-06-01.jsonl").open("w") as fh:
        fh.write(json.dumps(_ticket_event("T1")) + "\n")
        fh.write(json.dumps(_entry_fill("T1", 100.0, 2.0, "buy",
                                        "2026-06-01T08:00:00Z")) + "\n")
        fh.write(json.dumps(_exit_fill("T1", 105.0,
                                       "2026-06-01T09:00:00Z")) + "\n")
    rec = at.load_journal_ledger(tmp_path)
    assert rec["matched"] == 1
    assert at.attribute(rec["ledger"])["total_pnl"] == pytest.approx(10.0)
    # missing events dir → honest zeros, no crash
    empty = at.load_journal_ledger(tmp_path / "nowhere")
    assert empty["matched"] == 0 and empty["ledger"] == []


# ------------------------------------------------------------------ ledger file
def test_read_ledger_file_jsonl_and_array(tmp_path):
    jsonl = tmp_path / "ledger.jsonl"
    jsonl.write_text("\n".join(json.dumps(r) for r in LEDGER10[:3]) + "\n")
    rows = at.read_ledger_file(jsonl)
    assert len(rows) == 3
    arr = tmp_path / "ledger.json"
    arr.write_text(json.dumps(LEDGER10[:2]))
    assert len(at.read_ledger_file(arr)) == 2
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert at.read_ledger_file(empty) == []


def test_session_for_hour_full_table():
    """All 24 UTC hours → the charter's three sessions, nothing else."""
    expect = (["Asia"] * 8) + (["London"] * 5) + (["NY"] * 11)
    assert [at.session_for_hour(h) for h in range(24)] == expect
    assert set(at.session_for_hour(h) for h in range(24)) == {
        "Asia", "London", "NY"}


def test_attribution_report_wrapper_echoes_source():
    out = at.attribution_report(LEDGER10, source="ledger")
    assert out["ok"] is True
    assert out["source"] == "ledger"
    assert out["total_pnl"] == pytest.approx(TOTAL10)
    assert at.attribution_report(None)["total_pnl"] == 0.0


# ------------------------------------------------------------------ CLI
class _PnlArgs:
    source = "journal"
    ledger = None
    json = True
    data_root = "/tmp/pnl_cli"


def test_cli_pnl_synthetic_ledger_json(capsys):
    from gold_desk.cli import cmd_pnl
    args = _PnlArgs()
    args.source = "ledger"
    rc = cmd_pnl(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["n_trades"] == 24
    assert "synthetic" in out["source"]
    assert abs(sum(r["pnl"] for r in out["by_asset"])
               - out["total_pnl"]) < 1e-9


def test_cli_pnl_journal_empty_zeros(capsys, tmp_path):
    from gold_desk.cli import cmd_pnl
    args = _PnlArgs()
    args.data_root = str(tmp_path)          # no events dir at all
    rc = cmd_pnl(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["total_pnl"] == 0.0
    assert out["reconstruction"]["matched"] == 0
    assert out["source"].startswith("journal reconstruction")


def test_cli_pnl_ledger_file(capsys, tmp_path):
    from gold_desk.cli import cmd_pnl
    path = tmp_path / "ledger.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in LEDGER10) + "\n")
    args = _PnlArgs()
    args.source = "ledger"
    args.ledger = str(path)
    rc = cmd_pnl(args)
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["total_pnl"] == pytest.approx(TOTAL10)
    assert out["n_trades"] == 10
    assert "ledger file" in out["source"]


def test_cli_pnl_pretty(capsys, tmp_path):
    from gold_desk.cli import cmd_pnl
    args = _PnlArgs()
    args.json = False
    args.source = "ledger"
    args.data_root = str(tmp_path)
    rc = cmd_pnl(args)
    text = capsys.readouterr().out
    assert rc == 0
    assert "P&L ATTRIBUTION" in text
    assert "BY ASSET" in text and "BY SETUP" in text and "BY HOUR" in text
    assert "XAUUSD" in text
