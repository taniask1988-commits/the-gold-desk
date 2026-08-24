"""§4.7 — crash mid-send recovers the SAME ticket_id; expired stale tickets
are closed, not chased."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.clock import iso  # noqa: E402
from gold_desk.events import Journal  # noqa: E402
from gold_desk.recover import recover  # noqa: E402
from gold_desk.telegram_io import TelegramIO  # noqa: E402
from gold_desk.ticket import TicketStore, make_ticket  # noqa: E402
from conftest import good_candidate  # noqa: E402
from gold_desk.risk_gate import GateDecision  # noqa: E402


def _ticket(expired=False):
    gate = GateDecision(action="APPROVE", lots=0.04, risk_pct=0.0025)
    t = make_ticket(good_candidate(), gate, "h" * 64)
    if expired:
        past = datetime(2026, 6, 1, 7, 0, tzinfo=timezone.utc)
        from gold_desk.clock import iso as _iso
        t.expiry_ts = _iso(past)
    t.status = "PENDING_SEND"
    return t


def test_crash_mid_send_recovers_same_id(tmp_path):
    store = TicketStore(tmp_path)
    journal = Journal(tmp_path, "h" * 64)
    io = TelegramIO(journal, printer=lambda s: None)
    t = _ticket()
    store.persist(t)                       # crash BEFORE send completed

    result = recover(journal, store, io,
                     constitution=_fake_constitution(),
                     now=datetime(2026, 6, 1, 8, 1, tzinfo=timezone.utc))
    assert result["actions"][0]["action"] == "resent"
    reloaded = store.load(t.ticket_id)
    assert reloaded.ticket_id == t.ticket_id     # SAME id, never a new one
    assert reloaded.status == "SENT"


def test_stale_sent_ticket_expired_not_chased(tmp_path):
    store = TicketStore(tmp_path)
    journal = Journal(tmp_path, "h" * 64)
    io = TelegramIO(journal, printer=lambda s: None)
    t = _ticket(expired=True)
    t.status = "SENT"
    store.persist(t)
    result = recover(journal, store, io,
                     constitution=_fake_constitution(),
                     now=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))
    assert result["actions"][0]["action"] == "expired"
    assert store.load(t.ticket_id).status == "TICKET_EXPIRED"


def test_expired_pending_send_not_resent(tmp_path):
    """M1 — an expired PENDING_SEND ticket is closed as EXPIRED on restart,
    NOT re-sent. The previous code re-sent the expired ticket and only then
    flipped it to SENT, leaking an extraneous TicketSendAttempt event for an
    already-dead ticket into the journal."""
    store = TicketStore(tmp_path)
    journal = Journal(tmp_path, "h" * 64)
    sent: list[str] = []
    io = TelegramIO(journal, printer=sent.append)
    t = _ticket(expired=True)            # expiry_ts is in the past
    t.status = "PENDING_SEND"            # still pending — has not been sent yet
    store.persist(t)

    result = recover(journal, store, io,
                     constitution=_fake_constitution(),
                     now=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc))

    # the expired PENDING_SEND must be closed, not sent
    assert result["actions"][0]["action"] == "expired"
    assert store.load(t.ticket_id).status == "TICKET_EXPIRED"
    # the printer must never have been called — no console send of a dead ticket
    assert sent == [], f"expected no send, got: {sent}"
    # the journal must show an expired event, NOT a TicketSendAttempt for it
    events = Journal.read_events(tmp_path)
    kinds = [e["kind"] for e in events]
    assert "TicketExpired" in kinds
    assert "TicketSendAttempt" not in kinds


def test_alive_pending_send_is_resent(tmp_path):
    """Negative control for M1: an un-expired PENDING_SEND IS re-sent."""
    store = TicketStore(tmp_path)
    journal = Journal(tmp_path, "h" * 64)
    sent: list[str] = []
    io = TelegramIO(journal, printer=sent.append)
    t = _ticket()                         # expiry_ts is in the future
    t.status = "PENDING_SEND"
    store.persist(t)
    result = recover(journal, store, io,
                     constitution=_fake_constitution(),
                     now=datetime(2026, 6, 1, 8, 1, tzinfo=timezone.utc))
    assert result["actions"][0]["action"] == "resent"
    assert store.load(t.ticket_id).status == "SENT"
    assert sent, "expected the alive PENDING_SEND to be re-sent via console"


def _fake_constitution():
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from conftest import make_constitution
    return make_constitution()
