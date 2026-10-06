"""GAUNTLET 7 Piece 7 — workflow glue tests.

Pinned invariants:
  - the canonical CSV builder round-trips EXACTLY (RFC 4180: quoted
    fields, embedded commas/newlines/quotes, unicode, None → '');
  - export-csv output is byte-identical to the rows the JSON table
    surfaces (same source function, same builder);
  - the branded EOD report builds FROM THE JOURNAL ALONE, with honest
    fail-soft market state (a dead feed reads "unavailable", never a
    fabricated number);
  - Telegram alert messages deep-link to the web drill-down.
"""
from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from gold_desk.export_csv import export_csv, rows_to_csv
from gold_desk.eod_report import eod_report_md


# ------------------------------------------------------------ CSV builder

def test_rows_to_csv_round_trip_exact():
    columns = ["symbol", "note", "value"]
    rows = [
        {"symbol": "GC=F", "note": "plain", "value": 1.5},
        {"symbol": "A,B", "note": 'has "quote" and, comma', "value": None},
        {"symbol": "multi\nline", "note": "ünïcode ✓", "value": 2},
    ]
    text = rows_to_csv(columns, rows, source="test")
    # parse back with the stdlib reader (skip the # preamble + header)
    body = text.split("\r\n", 1)[1]
    parsed = list(csv.reader(io.StringIO(body)))
    assert parsed[0] == columns
    assert parsed[1] == ["GC=F", "plain", "1.5"]
    assert parsed[2][0] == "A,B"
    assert parsed[2][1] == 'has "quote" and, comma'
    assert parsed[2][2] == ""            # None → empty cell
    assert parsed[3][0] == "multi\nline"
    assert parsed[3][1] == "ünïcode ✓"


def test_rows_to_csv_none_and_missing_keys():
    text = rows_to_csv(["a", "b"], [{"a": 1}, {"b": 2}], source="")
    parsed = list(csv.reader(io.StringIO(text)))
    assert parsed[0] == ["a", "b"]
    assert parsed[1] == ["1", ""]
    assert parsed[2] == ["", "2"]


def test_export_csv_unknown_kind_fails_soft():
    out = export_csv("nonsense", "data")
    assert out["ok"] is False and "unknown export kind" in out["errors"][0]


# ------------------------------------------------------------ journal CSV

def _write_journal(root: Path, date: str, events: list[dict]) -> None:
    d = root / "events"
    d.mkdir(parents=True, exist_ok=True)
    with (d / f"{date}.jsonl").open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def test_export_journal_matches_events(tmp_path):
    events = [
        {"ts": "2026-07-10T01:00:00Z", "kind": "BarReceived",
         "reason_code": None, "payload": {"symbol": "XAUUSD"}},
        {"ts": "2026-07-10T01:00:01Z", "kind": "FilterReject",
         "reason_code": "SPREAD", "payload": {"symbol": "XAUUSD"}},
        {"ts": "2026-07-10T02:00:00Z", "kind": "GateDecision",
         "reason_code": "GATE_PASS", "payload": {"symbol": "XAUUSD"}},
    ]
    _write_journal(tmp_path, "2026-07-10", events)
    out = export_csv("journal", tmp_path, limit=10)
    assert out["ok"] is True
    assert out["columns"] == ["ts", "kind", "reason_code", "symbol"]
    assert len(out["rows"]) == 3
    assert out["rows"][1]["reason_code"] == "SPREAD"
    # the CSV text built from these rows parses to the same rows
    text = rows_to_csv(out["columns"], out["rows"], source=out["source"])
    body = text.split("\r\n", 1)[1]
    parsed = list(csv.reader(io.StringIO(body)))
    assert len(parsed) == 4  # header + 3 rows
    assert parsed[2][2] == "SPREAD"


def test_cli_export_csv_stdout_matches_builder(tmp_path, capsys, monkeypatch):
    """CLI byte-contract: stdout of export-csv == rows_to_csv output
    (modulo the CLI adding nothing else to stdout)."""
    from gold_desk.cli import main
    events = [
        {"ts": "2026-07-10T01:00:00Z", "kind": "ProcessStart",
         "reason_code": None, "payload": {}},
        {"ts": "2026-07-10T01:00:01Z", "kind": "FilterReject",
         "reason_code": "SESSION", "payload": {"symbol": "XAUUSD"}},
    ]
    _write_journal(tmp_path, "2026-07-10", events)
    rc = main(["export-csv", "journal", "--limit", "10",
               "--data-root", str(tmp_path)])
    assert rc == 0
    captured = capsys.readouterr().out
    rows = [json.loads(line) for line in
            (tmp_path / "events" / "2026-07-10.jsonl")
            .read_text().splitlines()]
    expected = rows_to_csv(["ts", "kind", "reason_code", "symbol"],
                           [{"ts": e["ts"], "kind": e["kind"],
                             "reason_code": e["reason_code"],
                             "symbol": e["payload"].get("symbol", "")}
                            for e in rows],
                           source="events --limit 10")
    assert captured == expected


# -------------------------------------------------------------- pair CSV

def test_export_pair_with_monkeypatched_fetch(monkeypatch):
    import gold_desk.markets.pairs as pr_mod
    import datetime as _dt

    def _bars(dates_and_closes):
        out = []
        for d, c in dates_and_closes:
            ts = int(_dt.datetime.strptime(d, "%Y-%m-%d").replace(
                tzinfo=_dt.timezone.utc).timestamp() * 1000)
            out.append({"ts": ts, "o": c, "h": c, "l": c, "c": c, "v": 1.0})
        return out

    dates = [(f"2026-{m:02d}-{dd:02d}", 100.0 + 5 * m + dd)
             for m in range(1, 13) for dd in (1, 15)]
    dates_b = [(d, c * 0.5 + (0.03 if i % 2 else -0.03))
               for i, (d, c) in enumerate(dates)]
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates if s.endswith("A") else dates_b))
    out = export_csv("pair", "/tmp/unused", a="TESTA", b="TESTB", window=8)
    assert out["ok"] is True
    assert out["columns"] == ["date", "spread", "z"]
    assert len(out["rows"]) == len(dates)
    # z is None during warm-up (honest), then numeric
    assert out["rows"][0]["z"] in (None, "")
    assert out["rows"][-1]["z"] is not None
    text = rows_to_csv(out["columns"], out["rows"], source=out["source"])
    assert "TESTA" in out["source"] and "TESTB" in out["source"]


# ----------------------------------------------------------- EOD report

def test_eod_report_builds_from_journal_alone(tmp_path):
    events = [
        {"ts": "2026-07-10T01:00:00Z", "kind": "BarReceived",
         "decision_ts": "2026-07-10T01:00:00Z", "reason_code": None,
         "payload": {"symbol": "XAUUSD", "bar": {"c": 4000}}},
        {"ts": "2026-07-10T01:00:01Z", "kind": "FilterReject",
         "decision_ts": "2026-07-10T01:00:00Z", "reason_code": "SPREAD",
         "payload": {"symbol": "XAUUSD"}},
        {"ts": "2026-07-10T02:00:00Z", "kind": "BarReceived",
         "decision_ts": "2026-07-10T02:00:00Z", "reason_code": None,
         "payload": {"symbol": "XAUUSD", "bar": {"c": 4001}}},
        {"ts": "2026-07-10T02:00:01Z", "kind": "FilterReject",
         "decision_ts": "2026-07-10T02:00:00Z", "reason_code": "SESSION",
         "payload": {"symbol": "XAUUSD"}},
    ]
    _write_journal(tmp_path, "2026-07-10", events)
    md = eod_report_md(tmp_path, "2026-07-10")
    assert "GOLD DESK COMMAND — END OF DAY" in md
    assert "2026-07-10" in md
    assert "SPREAD" in md and "SESSION" in md
    assert "█" in md                       # inline histogram bars
    assert "no tickets issued" in md       # honest flat-day statement
    assert "from journal alone" in md
    # fail-soft market state: no network here → unavailable, never fake
    assert "unavailable" in md


def test_eod_report_with_ticket_lists_it(tmp_path):
    events = [
        {"ts": "2026-07-10T01:00:00Z", "kind": "BarReceived",
         "decision_ts": "2026-07-10T01:00:00Z", "reason_code": None,
         "payload": {"symbol": "XAUUSD", "bar": {"c": 4000}}},
        {"ts": "2026-07-10T01:00:05Z", "kind": "TicketEvent",
         "decision_ts": "2026-07-10T01:00:00Z", "reason_code": None,
         "payload": {"ticket_id": "01HTEST", "side": "long",
                     "status": "TICKET_SENT"}},
    ]
    _write_journal(tmp_path, "2026-07-10", events)
    md = eod_report_md(tmp_path, "2026-07-10")
    assert "01HTEST" in md and "long" in md and "TICKET_SENT" in md


# ----------------------------------------------------- telegram deep-links

def test_alert_deep_link_format(monkeypatch):
    from gold_desk.watch.loop import WatchLoop
    monkeypatch.delenv("GOLD_DESK_DECK_URL", raising=False)
    link = WatchLoop._deep_link("GC=F")
    assert link == "http://localhost:3000/markets/GC%3DF"
    assert link.startswith("http")


def test_alert_deep_link_env_override(monkeypatch):
    from gold_desk.watch.loop import WatchLoop
    monkeypatch.setenv("GOLD_DESK_DECK_URL", "https://desk.example.com/")
    link = WatchLoop._deep_link("BTC-USD")
    assert link == "https://desk.example.com/markets/BTC-USD"


def test_alert_delivery_appends_deep_link():
    """The delivered Telegram message = alert text + deep-link line."""
    from gold_desk.watch.alerts import AlertEvent
    from gold_desk.watch.loop import WatchLoop

    ev = AlertEvent(rule_id="r1", symbol="GC=F", kind="price_above",
                    message="GC=F price 4200 >= 4100", value=4200.0,
                    threshold=4100.0, fired_at="2026-07-10T01:00:00Z",
                    snapshot_ref={})

    class FakeTG:
        token = "test-token"
        chat_id = "test-chat"

        def __init__(self):
            self.sent = []

        def send_message(self, text, decision_ts=None):
            self.sent.append(text)
            return "telegram"

        def __bool__(self):
            return True

    loop = WatchLoop.__new__(WatchLoop)   # skip __init__ (rules/feeds)
    loop.telegram = FakeTG()
    loop.logger = lambda s: None
    channel = loop._deliver(ev)
    assert channel == "telegram"
    assert loop.telegram.sent[0].startswith("GC=F price 4200 >= 4100\n")
    assert "/markets/GC%3DF" in loop.telegram.sent[0]


def test_alert_delivery_skips_silently_without_telegram():
    from gold_desk.watch.alerts import AlertEvent
    from gold_desk.watch.loop import WatchLoop, telegram_configured

    ev = AlertEvent(rule_id="r1", symbol="GC=F", kind="price_above",
                    message="m", value=1, threshold=1,
                    fired_at="2026-07-10T01:00:00Z", snapshot_ref={})
    loop = WatchLoop.__new__(WatchLoop)
    loop.telegram = None
    assert not telegram_configured(loop.telegram)
    assert loop._deliver(ev) == "skipped"
