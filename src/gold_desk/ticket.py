"""Document 3 / §10 — tickets: make, persist, render, expire, human outcomes.

Persistence order is law (§4.7): write TicketEvent(PENDING_SEND) to disk
BEFORE any send attempt. Retries reuse the SAME ticket_id. Late human
approval after expiry_ts is ignored and journalled — dead ticket, no chase.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .clock import iso, parse_ts
from .risk_gate import GateDecision
from .setup.engine import SetupCandidate
from .ulid import new_ulid

TICKET_STATUSES = [
    "PENDING_SEND", "SENT", "FILL", "HUMAN_SKIP", "TICKET_EXPIRED", "CANCELLED",
]


@dataclass
class Ticket:
    schema: str = "ticket.v1"
    ticket_id: str = field(default_factory=new_ulid)
    status: str = "PENDING_SEND"
    decision_ts: str = ""
    expiry_ts: str = ""
    symbol: str = "XAUUSD"
    side: str = ""
    entry_type: str = ""
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    lots: float = 0.0
    risk_pct: float = 0.0
    time_stop_ts: str = ""
    invalidation: str = ""
    setup_id: str = ""
    setup_version: str = ""
    veto: str = "ENDORSE_BYPASS"
    veto_reason: str = "phase 1: no LLM in the loop"
    spread_at_gate: float = 0.0
    constitution_hash: str = ""
    spec_hash: str = ""
    prompt_hash: str | None = None
    content_key: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def make_ticket(
    cand: SetupCandidate, gate: GateDecision, constitution_hash: str,
    veto: str = "ENDORSE_BYPASS", veto_reason: str = "phase 1: no LLM in the loop",
) -> Ticket:
    content = "|".join([
        cand.decision_ts, cand.setup_version, cand.side,
        f"{cand.entry:.2f}", f"{cand.stop:.2f}", f"{cand.target:.2f}",
    ])
    content_key = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return Ticket(
        decision_ts=cand.decision_ts,
        expiry_ts=cand.expiry_ts,
        side=cand.side,
        entry_type=cand.entry_type,
        entry=cand.entry,
        stop=cand.stop,
        target=cand.target,
        lots=gate.lots,
        risk_pct=gate.risk_pct,
        time_stop_ts=cand.time_stop_ts,
        invalidation=cand.invalidation,
        setup_id=cand.setup_id,
        setup_version=cand.setup_version,
        veto=veto,
        veto_reason=veto_reason,
        spread_at_gate=gate.spread_at_gate,
        constitution_hash=constitution_hash,
        spec_hash=cand.spec_hash,
        content_key=content_key,
    )


def render(ticket: Ticket, max_spread: float | None, demo: bool = False) -> str:
    lines = [
        f"TICKET {ticket.ticket_id}",
        f"XAUUSD {ticket.side} {ticket.entry_type}",
        f"Entry: {ticket.entry:.2f}",
        f"Stop: {ticket.stop:.2f}",
        f"Target: {ticket.target:.2f}",
        f"Lots: {ticket.lots}   (gate-computed, {ticket.risk_pct * 100:.2f}% of sizing equity)",
        f"Expiry: {ticket.expiry_ts} UTC",
        f"Time-stop: {ticket.time_stop_ts} UTC",
        f"Setup: {ticket.setup_id} {ticket.setup_version}",
        "",
        f"Veto: {ticket.veto} — {ticket.veto_reason}",
        f"Invalidation: {ticket.invalidation}",
        "",
    ]
    if max_spread is not None:
        lines.append(f"If spread > {max_spread} at paste -> SKIP")
    lines.append("If time > expiry -> SKIP")
    lines.append("Reply: FILL <price> | SKIP | EXPIRED")
    if demo:
        lines.append("")
        lines.append("*** DEMO — SYNTHETIC FEED — NOT A TRADING SIGNAL ***")
    return "\n".join(lines)


class TicketStore:
    """data/tickets/TICKET_ID.json snapshots for recovery + idempotency."""

    def __init__(self, root: str | Path):
        self.root = Path(root) / "tickets"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, ticket_id: str) -> Path:
        return self.root / f"{ticket_id}.json"

    def persist(self, ticket: Ticket) -> None:
        tmp = self.path_for(ticket.ticket_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(ticket.to_dict(), sort_keys=True, indent=2))
        tmp.replace(self.path_for(ticket.ticket_id))   # atomic-ish

    def load(self, ticket_id: str) -> Ticket | None:
        p = self.path_for(ticket_id)
        if not p.exists():
            return None
        return Ticket(**json.loads(p.read_text()))

    def open_tickets(self) -> list[Ticket]:
        out = []
        for p in sorted(self.root.glob("*.json")):
            try:
                t = Ticket(**json.loads(p.read_text()))
                if t.status in ("PENDING_SEND", "SENT"):
                    out.append(t)
            except Exception:
                continue
        return out

    def live_content_keys(self) -> set[str]:
        """Content keys of tickets not terminally resolved (dup guard)."""
        keys = set()
        for p in self.root.glob("*.json"):
            try:
                t = Ticket(**json.loads(p.read_text()))
                if t.status in ("PENDING_SEND", "SENT", "FILL"):
                    keys.add(t.content_key)
            except Exception:
                continue
        return keys


def apply_human_response(
    ticket: Ticket, response: str, now: datetime,
) -> tuple[str, str | None]:
    """Returns (new_status_or_None, fill_price_or_None).

    Late FILL after expiry -> status stays unchanged; the caller journals
    IGNORED_LATE_RESPONSE and the terminal reason remains TICKET_EXPIRED.
    A human reporting EXPIRED (or SKIP) after expiry closes the dead ticket
    as TICKET_EXPIRED — it approves nothing. Duplicate terminal response on
    an already-resolved ticket -> ignored.
    """
    response = response.strip()
    upper = response.upper()
    if ticket.status in ("FILL", "HUMAN_SKIP", "TICKET_EXPIRED", "CANCELLED"):
        return ticket.status, None          # duplicate — ignored after first

    expired = parse_ts(ticket.expiry_ts) < now

    if upper.startswith("FILL"):
        if expired:
            # late approval on a dead ticket: the approval itself is IGNORED
            # (no position will ever open); bookkeeping closes it expired
            return "TICKET_EXPIRED", None
        parts = upper.split()
        price: str | None = None
        if len(parts) > 1:
            try:
                float(parts[1])
                price = parts[1]
            except ValueError:
                price = None
        return "FILL", price
    if expired:
        # any non-approval reply on an expired ticket closes it as expired
        return "TICKET_EXPIRED", None
    if upper.startswith("SKIP"):
        return "HUMAN_SKIP", None
    if upper.startswith("EXPIRED"):
        return "TICKET_EXPIRED", None
    if upper.startswith("SPREAD"):          # "spread exploded, did not enter"
        return "HUMAN_SKIP", None
    return ticket.status, None              # unparseable -> no state change
