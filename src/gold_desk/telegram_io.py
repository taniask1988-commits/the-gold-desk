"""§10 — Telegram human I/O with automatic console fallback.

Stdlib Bot API client (urllib), no python-telegram-bot dependency. Token and
chat id come from env GOLD_DESK_TG_TOKEN / GOLD_DESK_TG_CHAT_ID. Missing
token -> ConsoleTelegram: every message prints to stdout and is journalled
with channel=console. Same behavior for send failures mid-run (fallback
keeps the pipeline observable; the ticket keeps retrying until expiry).

Idempotency: send_idempotent(ticket) will never send a ticket whose id was
already SENT. Retries reuse the same id and are journalled as
TicketSendAttempt. Telegram down at expiry -> TICKET_EXPIRED (§17).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Callable

from .events import Journal
from .ticket import Ticket


class TelegramIO:
    def __init__(self, journal: Journal, token: str | None = None,
                 chat_id: str | None = None, console_fallback: bool = True,
                 printer: Callable[[str], None] | None = None):
        self.journal = journal
        self.token = token or os.environ.get("GOLD_DESK_TG_TOKEN")
        self.chat_id = chat_id or os.environ.get("GOLD_DESK_TG_CHAT_ID")
        self.console_fallback = console_fallback
        self.printer = printer or print
        self._sent_ids: set[str] = set()

    # ------------------------------------------------------------- internals
    def _api_send(self, text: str) -> tuple[bool, str]:
        if not (self.token and self.chat_id):
            return False, "no token/chat configured"
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        body = json.dumps({
            "chat_id": self.chat_id, "text": text,
            "disable_web_page_preview": True,
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok = 200 <= resp.status < 300
                return ok, f"http {resp.status}"
        except urllib.error.HTTPError as e:
            return False, f"http {e.code}"
        except Exception as e:  # noqa: BLE001 — any transport failure = not sent
            return False, f"{type(e).__name__}: {e}"

    def send_message(self, text: str, decision_ts: str | None = None) -> str:
        """Best-effort human message. Returns the channel used.

        L3: when decision_ts is provided (this message is tied to a bar — a
        ticket send or a kill-switch ack for that bar), the event is
        TicketSent (a real send to the human for that bar). When decision_ts
        is None (an untethered broadcast, e.g. an EOD summary not tied to a
        specific bar), the event is TicketSendAttempt (we tried, may not be
        the canonical event for any bar). The prior code inverted this
        conditional, logging TicketSendAttempt for the very things that were
        confirmed sends.
        """
        if self.token and self.chat_id:
            ok, detail = self._api_send(text)
            channel = "telegram" if ok else ("console" if self.console_fallback else "none")
            if not ok and self.console_fallback:
                self.printer(text)
        else:
            ok = False  # noqa: F841 — kept for readability of the no-token branch
            detail = None  # noqa: F841
            channel = "console" if self.console_fallback else "none"
            if self.console_fallback:
                self.printer(text)
        self.journal.emit(
            "TicketSent" if decision_ts else "TicketSendAttempt",
            {"channel": channel, "preview": text[:80]},
            decision_ts=decision_ts,
        )
        return channel

    # ------------------------------------------------------------- idempotent
    def send_idempotent(self, ticket: Ticket, rendered: str) -> str:
        """§4.7/§10.3: persist-first ticket send; same id on every retry."""
        if ticket.ticket_id in self._sent_ids:
            return "already-sent"
        # L4: fix the broken tuple unpack. The prior form
        #   `ok, detail = (True, "console"), None`
        # unpacked as `ok=(True,"console"), detail=None` — `ok` was a tuple,
        # which then muddles the `ok and self.token` check below (a non-empty
        # tuple is truthy). The corrected form initialises both as proper
        # scalars: ok=True, detail="console" — meaning "we have not yet tried
        # the network, the assumed channel is console".
        ok: bool = True
        detail: str | None = "console"
        if self.token and self.chat_id:
            ok, detail = self._api_send(rendered)
        channel = "telegram" if (ok and self.token) else (
            "console" if self.console_fallback else "none")
        if channel == "console":
            self.printer(rendered)
        self.journal.emit(
            "TicketSendAttempt",
            {"ticket_id": ticket.ticket_id, "channel": channel, "detail": detail},
            decision_ts=ticket.decision_ts,
        )
        if channel in ("telegram", "console"):
            self._sent_ids.add(ticket.ticket_id)
            self.journal.emit(
                "TicketSent",
                {"ticket_id": ticket.ticket_id, "channel": channel},
                decision_ts=ticket.decision_ts,
            )
        return channel
