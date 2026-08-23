"""§16 row 8 + L13 — expiry: late FILL after expiry is a dead ticket; the
response is journalled and ignored; no chase."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from gold_desk.ticket import Ticket, apply_human_response, make_ticket
from conftest import good_candidate
from gold_desk.risk_gate import GateDecision

NOW = datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc)


def _sent_ticket() -> Ticket:
    gate = GateDecision(action="APPROVE", lots=0.04, risk_pct=0.0025)
    t = make_ticket(good_candidate(), gate, "h" * 64)
    t.status = "SENT"
    return t


def test_late_fill_ignored():
    t = _sent_ticket()
    late = NOW + timedelta(minutes=30)      # expiry was +10 min
    status, price = apply_human_response(t, "FILL 2420.00", late)
    # the approval is ignored: no price returned, ticket dead — never a fill
    assert status == "TICKET_EXPIRED"
    assert price is None


def test_in_time_fill_accepted():
    t = _sent_ticket()
    status, price = apply_human_response(t, "FILL 2410.50",
                                         NOW + timedelta(minutes=2))
    assert status == "FILL" and price == "2410.50"


def test_fill_without_price_defaults_to_entry_at_orchestrator():
    t = _sent_ticket()
    status, price = apply_human_response(t, "FILL", NOW + timedelta(minutes=1))
    assert status == "FILL" and price is None   # orchestrator uses ticket.entry


def test_skip_and_expired_paths():
    t = _sent_ticket()
    assert apply_human_response(t, "SKIP", NOW + timedelta(minutes=1))[0] == "HUMAN_SKIP"
    t2 = _sent_ticket()
    assert apply_human_response(t2, "EXPIRED", NOW + timedelta(minutes=20))[0] == "TICKET_EXPIRED"


def test_spread_blowout_report_is_a_skip():
    t = _sent_ticket()
    status, _ = apply_human_response(t, "SPREAD exploded did not enter",
                                     NOW + timedelta(minutes=1))
    assert status == "HUMAN_SKIP"


def test_garbage_response_no_state_change():
    t = _sent_ticket()
    status, _ = apply_human_response(t, "hello?", NOW + timedelta(minutes=1))
    assert status == "SENT"
