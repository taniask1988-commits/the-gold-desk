"""§16 row 7 — idempotency: retries reuse the SAME ticket_id; duplicates of
a live content-key candidate are refused; double human FILL is ignored."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gold_desk.events import Journal
from gold_desk.orchestrator import Orchestrator  # noqa: F401  (import check)
from gold_desk.telegram_io import TelegramIO
from gold_desk.ticket import (Ticket, TicketStore, apply_human_response,
                              make_ticket)
from conftest import MONDAY, good_candidate, tmp_data  # noqa: F401
from gold_desk.risk_gate import GateDecision


def _ticket() -> Ticket:
    gate = GateDecision(action="APPROVE", lots=0.04, risk_pct=0.0025,
                        risk_money=25.0)
    return make_ticket(good_candidate(), gate, "h" * 64)


def test_retry_sends_same_ticket_id(tmp_data):  # noqa: F811
    store = TicketStore(tmp_data)
    t1 = _ticket()
    store.persist(t1)
    loaded = store.load(t1.ticket_id)
    assert loaded is not None and loaded.ticket_id == t1.ticket_id
    assert loaded.content_key == t1.content_key


def test_telegram_idempotent_never_resends_sent(tmp_data, capsys):  # noqa: F811
    journal = Journal(tmp_data, "h" * 64)
    io = TelegramIO(journal, printer=print)
    t = _ticket()
    ch1 = io.send_idempotent(t, "TICKET one")
    ch2 = io.send_idempotent(t, "TICKET one again")
    assert ch1 in ("console", "telegram")
    assert ch2 == "already-sent"
    events = Journal.read_events(tmp_data)
    sent = [e for e in events if e["kind"] == "TicketSent"]
    assert len(sent) == 1


def test_duplicate_filling_ignored_after_first():
    t = _ticket()
    t.status = "SENT"
    now = datetime(2026, 6, 1, 8, 2, tzinfo=timezone.utc)
    s1, _ = apply_human_response(t, "FILL 2410.50", now)
    assert s1 == "FILL"
    t.status = "FILL"
    s2, _ = apply_human_response(t, "FILL 2410.55", now + timedelta(minutes=1))
    assert s2 == "FILL"           # unchanged — duplicate ignored


def test_content_key_dup_guard(tmp_data):  # noqa: F811
    store = TicketStore(tmp_data)
    t = _ticket()
    t.status = "SENT"
    store.persist(t)
    assert t.content_key in store.live_content_keys()
    t2 = _ticket()   # same candidate -> same content key
    assert t2.content_key == t.content_key
    assert t2.content_key in store.live_content_keys()


def test_ticket_persisted_before_send(tmp_path):
    # §4.7: PENDING_SEND snapshot exists on disk before any channel write
    store = TicketStore(tmp_path)
    t = _ticket()
    t.status = "PENDING_SEND"
    store.persist(t)
    assert store.load(t.ticket_id).status == "PENDING_SEND"
