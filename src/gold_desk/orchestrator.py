"""§4 — the orchestrator. NOT an agent (Law L9).

A fixed ETL + state machine run once per CLOSED H1 bar:

  bar_close -> quality -> cheap filters -> setup -> candidate filters
            -> (veto: Phase 2 only; Phase 1 = ENDORSE_BYPASS)
            -> risk gate -> ticket -> persist -> send -> wait for human

Fail closed everywhere. Idempotent tickets. Every bar ends with exactly one
terminal reason code. No LLM import on this path in Phase 1 (pinned by
test_no_llm_in_phase1, which fails CI if the veto module is ever touched
while identity.phase < 2).

Terminal code note: a bar whose ticket is issued but still awaiting the
human closes with TICKET_SENT (additive to the §9.3 list, documented in
README). FILL/HUMAN_SKIP/TICKET_EXPIRED close the bar only when they happen.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta

from .account import PaperAccountStore, PaperPosition
from .clock import iso, parse_ts, session_of
from .constitution import Constitution
from .context_pack import build_pack
from .data.bars import BarSource
from .data.model import Bar, Observation, wrap
from .data.quality import check_quality, gap_check, missing_bar_check
from .events import Journal
from .filters import MarketState, cheap_filters, candidate_filters, news_blackout_active
from .features.indicators import atr
from .risk_gate import evaluate_gate
from .setup.engine import SetupEngine
from .telegram_io import TelegramIO
from .ticket import TicketStore, apply_human_response, make_ticket, render


@dataclass
class HumanSimulator:
    """DEMO ONLY: deterministic stand-in for the human paste/skip decision."""
    enabled: bool = False
    fill_probability: float = 0.8
    late_probability: float = 0.15
    rng_seed: int = 11
    _draws: int = 0

    def respond(self, ticket, now: datetime) -> tuple[str, str | None]:
        import random
        rng = random.Random(self.rng_seed + self._draws)
        self._draws += 1
        if rng.random() < self.late_probability:
            return "EXPIRED_LATE", None
        if rng.random() < self.fill_probability:
            return "FILL", f"{ticket.entry:.2f}"
        return "SKIP", None


class Orchestrator:
    def __init__(
        self,
        constitution: Constitution,
        source: BarSource,
        journal: Journal,
        telegram: TelegramIO,
        account_store: PaperAccountStore,
        setup_engine: SetupEngine | None = None,
        data_root: str | None = None,
        human_sim: HumanSimulator | None = None,
    ):
        self.c = constitution
        self.source = source
        self.journal = journal
        self.telegram = telegram
        self.accounts = account_store
        self.engine = setup_engine or SetupEngine()
        self.store = TicketStore(data_root) if data_root else TicketStore(journal.root)
        self.kill_switch = False
        self.degraded = False
        self.human_sim = human_sim
        self.journal.emit("ProcessStart", {
            "phase": self.c.phase,
            "demo": self.c.demo,
            "constitution": self.c.summary_line(),
            "spec_hash": self.engine.spec_hash,
        })

    # ------------------------------------------------------------------ bar
    def on_bar_close(self, bar: Bar) -> str:
        """Run the full lifecycle for one closed bar. Returns terminal code."""
        decision_ts = bar.close_dt
        decision_iso = bar.ts_close
        self.journal.open_bar(decision_iso)

        self.journal.emit(
            "BarReceived",
            {"bar": {"ts_open": bar.ts_open, "ts_close": bar.ts_close,
                     "o": bar.open, "h": bar.high, "l": bar.low, "c": bar.close}},
            decision_ts=decision_iso,
            data_hash=hashlib.sha256(bar.canonical().encode()).hexdigest(),
        )

        def terminal(code: str, kind: str, payload: dict | None = None) -> str:
            self.journal.emit(kind, payload or {"code": code},
                              decision_ts=decision_iso, reason_code=code)
            self.journal.close_bar_reason(decision_iso, code)
            return code

        # ---- resolve open paper positions on this bar BEFORE new decisions
        self.accounts.mark_open_position(bar)
        for rec in self.accounts.resolve_on_bar(bar):
            self.journal.emit("Fill", {"resolution": rec, "phase": "paper-exit"},
                              decision_ts=decision_iso)

        # ---- account day rollover (+ no-overnight enforcement)
        day_key = str(bar.open_dt.date())
        prev_day = self.accounts.account.day_key
        self.accounts.account.rollover_day(day_key)
        if (prev_day and prev_day != day_key
                and self.accounts.account.positions
                and self.c.limits.get("overnight_positions") is False):
            for rec in self.accounts.force_close_all(bar, "no-overnight"):
                self.journal.emit("Fill", {"resolution": rec,
                                            "phase": "forced-close"},
                                  decision_ts=decision_iso)
        acct = self.accounts.account.account_state()

        # ---- 1. data quality (quote fetch failure = stale)
        try:
            quote = self.source.quote(decision_ts)
        except Exception:
            return terminal("STALE_DATA", "DataQualityFailed",
                            {"code": "STALE_DATA", "detail": "no quote available"})
        q = check_quality(bar, quote, decision_ts, self.c.limits, self.c.instrument)
        if not q.ok:
            kind = "FilterReject" if q.code == "SPREAD" else "DataQualityFailed"
            return terminal(q.code, kind, {"code": q.code, "detail": q.detail})

        history = self.source.bars_up_to(decision_ts, 60)
        if len(history) >= 2:
            g = gap_check(history[-2].close, bar, self.c.limits)
            if not g.ok:
                return terminal(g.code, "DataQualityFailed",
                                {"code": g.code, "detail": g.detail})
            m = missing_bar_check(history, decision_ts)
            if not m.ok:
                return terminal(m.code, "DataQualityFailed",
                                {"code": m.code, "detail": m.detail})

        # ---- 2. cheap filters (news availability + blackout checked here)
        try:
            calendar_events = self.source.calendar(decision_ts)
            calendar_ok = True
        except Exception:
            calendar_events, calendar_ok = [], False
        # feed health = transport ok AND a calendar feed is actually wired;
        # a healthy feed with zero events today is AVAILABLE, not down
        news_available = (calendar_ok and getattr(self.source, "calendar_wired", False)
                          and self.source.health(decision_ts))
        market = MarketState(
            spread=quote.spread,
            bar_close_ts=bar.ts_close,
            now_ts=iso(decision_ts),
            session=session_of(decision_ts),
            news_blackout_active=news_blackout_active(self.c, calendar_events,
                                                      decision_ts),
            news_available=news_available,
        )
        code = cheap_filters(self.c, acct, market, quote,
                             self.kill_switch, self.degraded)
        if code:
            return terminal(code, "FilterReject", {"code": code})

        # ---- 3. setup engine (no candidate -> $0 spent, no news pack)
        cand = self.engine.evaluate(history, decision_ts)
        if cand is None:
            return terminal("NO_SETUP", "NoSetup")

        # M3: record the spread at candidate-creation time so the risk gate
        # can detect widening between candidate and gate (passed either via
        # the cand field or as a kwarg below; the gate falls back to the
        # candidate's recorded value when the kwarg is absent).
        cand.spread_at_candidate = quote.spread

        self.journal.emit("SetupCandidate", cand.to_dict(),
                          decision_ts=decision_iso,
                          setup_spec_hash=cand.spec_hash, data_hash=cand.data_hash)

        # ---- 4. candidate filters
        atr14 = cand.features_used.get("atr14")
        code = candidate_filters(self.c, cand, quote, atr14)
        if code:
            return terminal(code, "FilterReject", {"code": code})

        # ---- 5. veto — Phase 2 only; Phase 1 records ENDORSE_BYPASS
        if self.c.phase >= 2:  # pragma: no cover — Phase 2 territory
            pack = self._build_context_pack(cand, history, decision_ts)
            # OpenCode Zen free model (auto-discovered catalog); the lazy
            # import keeps zero LLM code on the Phase-1 path (Law L10)
            from .llm.veto_llm import run_veto
            from .llm.zen_client import LLMUnavailable
            from .llm.zen_sync import load_catalog
            catalog = load_catalog(self.journal.root) or {}
            model = catalog.get("default")
            if not model:
                return terminal("LLM_UNAVAILABLE", "VetoDecision",
                                {"decision": "VETO",
                                 "reason": "no free model in zen catalog"})
            try:
                veto_result = run_veto(pack.to_dict(), model)
                self.journal.emit("VetoDecision", veto_result,
                                  decision_ts=decision_iso,
                                  model_id=model)
                if veto_result.get("decision") != "ENDORSE":
                    return terminal("LLM_VETO", "VetoDecision", veto_result)
                veto_state = ("ENDORSE", veto_result.get("reason", ""))
            except Exception:
                return terminal("LLM_UNAVAILABLE", "VetoDecision",
                                {"decision": "VETO",
                                 "reason": "veto failure -> fail closed"})
        else:
            veto_state = ("ENDORSE_BYPASS", "phase 1: no LLM in the loop")
            self.journal.emit("VetoDecision", {
                "decision": "ENDORSE_BYPASS", "reason": veto_state[1],
            }, decision_ts=decision_iso)

        # ---- 6. risk gate at ticket time
        gate = evaluate_gate(
            self.c, cand, acct, market, quote, decision_ts,
            atr14=atr14, kill_switch=self.kill_switch, degraded=self.degraded,
            spread_at_candidate=cand.spread_at_candidate,
            engine_spec_hash=self.engine.spec_hash,
        )
        self.journal.emit("GateDecision", gate.to_dict(), decision_ts=decision_iso,
                          reason_code=None if gate.action == "APPROVE" else "GATE_REJECT")
        if gate.action != "APPROVE":
            return terminal(gate.code or "GATE_REJECT", "GateDecision",
                            gate.to_dict())

        # ---- 7. ticket: persist BEFORE send; idempotent; dup-guarded
        ticket = make_ticket(cand, gate, self.c.content_hash,
                             veto=veto_state[0], veto_reason=veto_state[1])
        if ticket.content_key in self.store.live_content_keys():
            return terminal("GATE_REJECT", "FilterReject",
                            {"code": "GATE_REJECT",
                             "detail": "duplicate live candidate"})
        ticket.status = "PENDING_SEND"
        self.store.persist(ticket)                       # BEFORE send (§4.7)
        self.journal.emit("TicketEvent", ticket.to_dict(), decision_ts=decision_iso)

        text = render(ticket, self.c.max_spread, demo=self.c.demo)
        channel = self.telegram.send_idempotent(ticket, text)
        if channel in ("telegram", "console"):
            ticket.status = "SENT"
            self.store.persist(ticket)
            self.journal.emit("TicketEvent", ticket.to_dict(),
                              decision_ts=decision_iso)

        # ---- 8. human outcome (demo simulates; live human answers later)
        if self.human_sim and self.human_sim.enabled:
            self._simulate_human(ticket, decision_ts)

        return self._finalize_ticket(ticket, decision_iso)

    # ------------------------------------------------------------- internals
    def _build_context_pack(self, cand, history, decision_ts):
        bars_obs = [wrap("bar", b.close_dt, {
            "ts_open": b.ts_open, "ts_close": b.ts_close,
            "o": b.open, "h": b.high, "l": b.low, "c": b.close,
        }) for b in history]
        feats_obs = [wrap("feature", decision_ts, dict(cand.features_used))]
        cal_obs = [wrap("calendar", parse_ts(e.ts), {
            "ts": e.ts, "currency": e.currency, "impact": e.impact,
            "title": e.title,
        }) for e in self.source.calendar(decision_ts)]
        news_obs = [wrap("news", parse_ts(n.ts), {
            "ts": n.ts, "headline": n.headline, "source": n.source,
        }) for n in self.source.news(decision_ts)]
        return build_pack(self.c, cand, bars_obs, feats_obs, cal_obs, news_obs)

    def _simulate_human(self, ticket, decision_ts):
        action, price = self.human_sim.respond(ticket, decision_ts)  # type: ignore[union-attr]
        late = action == "EXPIRED_LATE"
        if late:
            response = f"FILL {ticket.entry:.2f}"
        elif action == "FILL":
            response = f"FILL {price}" if price else f"FILL {ticket.entry:.2f}"
        else:
            response = "SKIP"
        delay = (self.c.ticket_expiry_minutes or 10) + 5 if late else 2
        reply_ts = decision_ts + timedelta(minutes=delay)
        self.handle_human_response(ticket, response, reply_ts)

    def handle_human_response(self, ticket, response: str, now: datetime) -> str:
        prior_status = ticket.status
        new_status, price = apply_human_response(ticket, response, now)
        late_approval = (response.strip().upper().startswith("FILL")
                         and new_status == "TICKET_EXPIRED")
        # M2: HumanResponse events carry NO terminal reason_code — they're
        # annotations on a bar that already has its terminal code via the
        # TicketExpired / Fill / Skip event below. Previously this emitted
        # reason_code="IGNORED_LATE_RESPONSE" on the late-approval path,
        # which the histogram counted as a second terminal code for that bar.
        # Now IGNORED_LATE_RESPONSE lives only in the payload, and the bar
        # ends with exactly one terminal reason code (TICKET_EXPIRED).
        self.journal.emit("HumanResponse", {
            "ticket_id": ticket.ticket_id, "response": response,
            "resulting_status": new_status,
            "accepted": new_status != prior_status and not late_approval,
            "ignored_late_response": late_approval,
        }, decision_ts=ticket.decision_ts, reason_code=None)
        if late_approval:
            ticket.status = new_status
            self.store.persist(ticket)
            self.journal.emit("TicketEvent", ticket.to_dict(),
                              decision_ts=ticket.decision_ts)
            self.journal.emit("TicketExpired", {
                "ticket_id": ticket.ticket_id,
                "why": "late human approval — dead ticket, not chased",
            }, decision_ts=ticket.decision_ts, reason_code="TICKET_EXPIRED")
            return new_status
        if new_status == prior_status:
            self.journal.emit("HumanResponse", {
                "ticket_id": ticket.ticket_id, "ignored": True,
                "why": "late, duplicate, or unparseable response",
            }, decision_ts=ticket.decision_ts, reason_code=None)
            return prior_status
        ticket.status = new_status
        self.store.persist(ticket)
        # snapshot the transition into the journal so replay carries the
        # full status lifecycle, not just the PENDING_SEND moment
        self.journal.emit("TicketEvent", ticket.to_dict(),
                          decision_ts=ticket.decision_ts)
        if new_status == "FILL":
            fill_price = float(price) if price else ticket.entry
            commission = float(self.c.broker.get("commission_per_lot_rt", 0) or 0)
            self.accounts.open_position(PaperPosition(
                opened_ts=ticket.decision_ts, side=ticket.side,
                entry=fill_price, stop=ticket.stop, target=ticket.target,
                lots=ticket.lots, time_stop_ts=ticket.time_stop_ts,
                ticket_id=ticket.ticket_id,
                commission_paid=commission * ticket.lots,
            ))
            self.journal.emit("Fill", {
                "ticket_id": ticket.ticket_id, "price": fill_price,
                "lots": ticket.lots, "side": ticket.side,
                "status": "paper-position-opened",
            }, decision_ts=ticket.decision_ts, reason_code="FILL")
        elif new_status == "HUMAN_SKIP":
            self.journal.emit("Skip", {"ticket_id": ticket.ticket_id},
                              decision_ts=ticket.decision_ts,
                              reason_code="HUMAN_SKIP")
        elif new_status == "TICKET_EXPIRED":
            self.journal.emit("TicketExpired", {"ticket_id": ticket.ticket_id},
                              decision_ts=ticket.decision_ts,
                              reason_code="TICKET_EXPIRED")
        return new_status

    def _finalize_ticket(self, ticket, decision_iso: str) -> str:
        code = {
            "FILL": "FILL", "HUMAN_SKIP": "HUMAN_SKIP",
            "TICKET_EXPIRED": "TICKET_EXPIRED", "CANCELLED": "HUMAN_SKIP",
        }.get(ticket.status, "TICKET_SENT")   # PENDING_SEND / SENT -> awaiting human
        self.journal.close_bar_reason(decision_iso, code)
        return code

    # ------------------------------------------------------------- kill sw.
    def set_kill_switch(self, active: bool, why: str = "") -> None:
        self.kill_switch = active
        self.journal.emit("KillSwitch", {"active": active, "why": why})
        # L3: surface the kill-switch change to the human via the broadcast
        # path (untethered from a specific bar — decision_ts=None). This is
        # the first real caller of TelegramIO.send_message; without it the
        # method was dead code with an untested event-kind branch.
        try:
            self.telegram.send_message(
                f"{'KILL SWITCH ENGAGED' if active else 'kill switch released'}"
                f"{' — ' + why if why else ''}",
                decision_ts=None,
            )
        except Exception:
            # broadcast must never break the kill-switch state change
            pass
