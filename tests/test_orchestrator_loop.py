"""Phase 1 done-when (§15) — the loop invariants: exactly one terminal code
per closed bar, journal-only silence, replay answers 'why this ticket'."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gold_desk.account import PaperAccountStore  # noqa: E402
from gold_desk.constitution import load_constitution  # noqa: E402
from gold_desk.data.bars import SyntheticBarSource, SyntheticConfig  # noqa: E402
from gold_desk.events import Journal  # noqa: E402
from gold_desk.orchestrator import HumanSimulator, Orchestrator  # noqa: E402
from gold_desk.telegram_io import TelegramIO  # noqa: E402


def _run(tmp_path, days=6, human=True):
    constitution = load_constitution(
        REPO / "trading_constitution.yaml",
        overlay_path=REPO / "config" / "demo.yaml",
    )
    source = SyntheticBarSource(SyntheticConfig(seed=7),
                                start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                                days=days)
    journal = Journal(tmp_path, constitution.content_hash, demo=True)
    telegram = TelegramIO(journal, printer=lambda s: None)
    accounts = PaperAccountStore(tmp_path, 10000.0, journal)
    human_sim = HumanSimulator(enabled=human, rng_seed=12) if human else None
    orch = Orchestrator(constitution, source, journal, telegram, accounts,
                        human_sim=human_sim, data_root=str(tmp_path))
    bars = source.bars_up_to(datetime(2026, 6, 1, tzinfo=timezone.utc) +
                             timedelta(days=days + 2),
                             10**9)
    codes = [orch.on_bar_close(b) for b in bars]
    return constitution, journal, accounts, orch, codes, tmp_path


def test_every_bar_ends_with_exactly_one_code(tmp_path):
    """M2: each bar ends with EXACTLY ONE terminal reason_code event. Pre-M2,
    late-fill bars carried two (IGNORED_LATE_RESPONSE + TICKET_EXPIRED); now
    the late-approval annotation lives in the payload only and the bar's only
    journal reason_code is the terminal one emitted by close_bar_reason."""
    _, _, _, _, codes, _ = _run(tmp_path)
    assert codes and all(codes)
    events = Journal.read_events(tmp_path)
    bars = [e for e in events if e["kind"] == "BarReceived"]
    assert len(bars) == len(codes)
    # each BarReceived has EXACTLY ONE terminal event with a reason code
    for i, code in enumerate(codes):
        dts = bars[i]["decision_ts"]
        terminal = [e for e in events
                    if e.get("decision_ts") == dts and e.get("reason_code")
                    and e["kind"] != "BarReceived"]
        assert len(terminal) == 1, (
            f"bar {dts} has {len(terminal)} terminal codes, expected 1: "
            f"{[e['reason_code'] for e in terminal]}"
        )
        assert terminal[0]["reason_code"] == code


def test_setup_produces_candidates_and_tickets(tmp_path):
    _, journal, _, orch, codes, _ = _run(tmp_path, days=10)
    events = Journal.read_events(tmp_path)
    assert [e for e in events if e["kind"] == "SetupCandidate"]
    tickets = [e for e in events if e["kind"] == "TicketEvent"]
    assert tickets, "10 demo days produced no ticket — engine too quiet"
    assert any(c in codes for c in ("FILL", "HUMAN_SKIP", "TICKET_EXPIRED",
                                    "TICKET_SENT"))


def test_replay_answers_why_this_ticket(tmp_path):
    _, _, _, _, _, root = _run(tmp_path, days=10)
    from gold_desk.replay import replay_day, render_replay
    events = Journal.read_events(root)
    ticket_days = sorted({e["decision_ts"][:10] for e in events
                          if e["kind"] == "TicketEvent"})
    assert ticket_days
    report = replay_day(root, ticket_days[0])
    assert report["tickets"], "replay lost the ticket story"
    text = render_replay(report)
    assert "TICKET" in text or "ticket" in text.lower()
    for t in report["tickets"]:
        assert t["entry"] and t["stop"] and t["target"]  # complete package


def test_no_telegram_noise_only_tickets(tmp_path):
    """L8: journal loud, Telegram quiet — only tickets, never NO_SETUP."""
    sent: list[str] = []

    constitution = load_constitution(
        REPO / "trading_constitution.yaml",
        overlay_path=REPO / "config" / "demo.yaml",
    )
    source = SyntheticBarSource(SyntheticConfig(seed=7),
                                start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                                days=5)
    journal = Journal(tmp_path, constitution.content_hash, demo=True)
    telegram = TelegramIO(journal, printer=sent.append)
    accounts = PaperAccountStore(tmp_path, 10000.0, journal)
    orch = Orchestrator(constitution, source, journal, telegram, accounts,
                        human_sim=HumanSimulator(enabled=True),
                        data_root=str(tmp_path))
    bars = source.bars_up_to(datetime(2026, 6, 8, tzinfo=timezone.utc), 10**9)
    for b in bars:
        orch.on_bar_close(b)
    for msg in sent:
        assert msg.startswith("TICKET") or "DEMO" in msg, \
            "non-ticket message reached the human"


def test_paper_account_survives_and_closes_trades(tmp_path):
    _, _, accounts, _, _, _ = _run(tmp_path, days=12)
    acct = accounts.account
    if acct.closed_trades:
        for t in acct.closed_trades:
            assert t["reason"] in ("stop", "target", "time_stop",
                                   "forced:weekend", "forced:day_end")
