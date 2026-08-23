"""§4.7 / §17 — boot recovery.

On restart: scan data/tickets/ for PENDING_SEND or SENT tickets.
  PENDING_SEND  -> retry send of the SAME ticket_id (never a new id)
  SENT & expired-> TICKET_EXPIRED, journalled
  SENT & alive  -> keep waiting for the human (idempotent resend of same id
                   is allowed; duplicates impossible by ticket store)
Emits ProcessRecovered with what it did.
"""
from __future__ import annotations

from datetime import timedelta

from .clock import parse_ts, utc_now
from .events import Journal
from .telegram_io import TelegramIO
from .ticket import TicketStore, render


def recover(journal: Journal, store: TicketStore, telegram: TelegramIO,
            constitution, now=None) -> dict:
    now = now or utc_now()
    actions: list[dict] = []
    for ticket in store.open_tickets():
        expiry = parse_ts(ticket.expiry_ts)
        if ticket.status == "PENDING_SEND":
            text = render(ticket, constitution.max_spread, demo=constitution.demo)
            channel = telegram.send_idempotent(ticket, text)
            if channel in ("telegram", "console"):
                ticket.status = "SENT"
                store.persist(ticket)
            actions.append({"ticket_id": ticket.ticket_id, "action": "resent",
                            "channel": channel})
        elif expiry < now:
            ticket.status = "TICKET_EXPIRED"
            store.persist(ticket)
            journal.emit("TicketExpired", {"ticket_id": ticket.ticket_id,
                                           "recovered": True},
                         decision_ts=ticket.decision_ts,
                         reason_code="TICKET_EXPIRED")
            actions.append({"ticket_id": ticket.ticket_id, "action": "expired"})
        else:
            actions.append({"ticket_id": ticket.ticket_id, "action": "awaiting-human"})
    journal.emit("ProcessRecovered", {"actions": actions})
    return {"actions": actions}
