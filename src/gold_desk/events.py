"""Document 3 — journal, events, reason codes.

Append-only JSONL under data/events/YYYY-MM-DD.jsonl. One JSON object per
line. Every event carries the constitution content hash. Every bar ends with
exactly ONE terminal reason code (Law L8: journal loud, Telegram quiet).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .clock import iso, utc_now
from .ulid import new_ulid

# ---------------------------------------------------------------- reason codes
"""§9.3 + one additive code — reason codes (every bar ends with one).
TICKET_SENT is additive to the frozen plan list: a bar whose ticket was
issued and is still awaiting the human closes with it, so the one-code-per-
bar invariant stays truthful (FILL/HUMAN_SKIP/TICKET_EXPIRED close their own
bars when they happen).
"""
REASON_CODES = [
    "NO_SETUP", "SESSION", "SPREAD", "NEWS_BLACKOUT", "NEWS_UNAVAILABLE",
    "STALE_DATA", "MISSING_BAR", "OUTLIER_PRICE", "BUDGET", "MAX_TRADES",
    "CONSEC_LOSS", "OPEN_POSITION", "STOP_TOO_TIGHT", "RR_FLOOR",
    "SIZE_INVALID", "KILL_SWITCH", "LLM_VETO", "LLM_INVALID_JSON",
    "LLM_UNAVAILABLE", "GATE_REJECT", "TICKET_EXPIRED", "HUMAN_SKIP",
    "FILL", "CONSTITUTION_BLOCKED", "DEGRADED", "TZ_MISALIGN",
    "SOURCE_MISMATCH", "IGNORED_LATE_RESPONSE", "SPREAD_BLOWOUT",
    "ACCOUNT_CORRUPT_RECOVERED",
    "TICKET_SENT",
]

# LLM_INVALID_JSON is declared for completeness but is folded into LLM_VETO
# at runtime: the veto_llm path (src/gold_desk/llm/veto_llm.py) converts any
# invalid-JSON / schema-failed model response into a binary VETO decision,
# which the orchestrator then journals as LLM_VETO. So LLM_INVALID_JSON never
# appears as the terminal reason_code of any bar in the journal — it's kept
# in REASON_CODES so histogram tooling can still recognise the code if a
# downstream consumer ever emits it directly.

EVENT_KINDS = [
    "BarReceived", "DataQualityFailed", "FilterReject", "NoSetup",
    "SetupCandidate", "ContextPackBuilt", "VetoDecision", "GateDecision",
    "TicketEvent", "TicketSendAttempt", "TicketSent", "HumanResponse",
    "Fill", "Skip", "TicketExpired", "ReflectionWritten", "KillSwitch",
    "ProcessStart", "ProcessRecovered", "EodSummary",
    "AccountCorruptRecovered",
    # --- agent sidecar kinds (P0 §2.3) ---
    # These are NOT bar events: they never carry a reason_code (the
    # one-terminal-code-per-bar invariant is untouched — pinned by test).
    "AgentRunStarted", "AgentStep", "AgentToolCall",
    "AgentRunFinished", "ResearchReport", "ResearchSourceFetched",
    "ProposalDrafted", "BudgetExceeded",
]

# Agent-sidecar kinds must never carry a reason code (L13-adjacent invariant)
AGENT_KINDS = {
    "AgentRunStarted", "AgentStep", "AgentToolCall",
    "AgentRunFinished", "ResearchReport", "ResearchSourceFetched",
    "ProposalDrafted", "BudgetExceeded",
}


@dataclass
class Event:
    kind: str
    decision_ts: str | None
    payload: dict
    symbol: str = "XAUUSD"
    ts: str = field(default_factory=lambda: iso(utc_now()))
    event_id: str = field(default_factory=new_ulid)
    constitution_hash: str = ""
    setup_spec_hash: str | None = None
    prompt_hash: str | None = None
    model_id: str | None = None
    data_hash: str | None = None
    reason_code: str | None = None

    def to_dict(self) -> dict:
        d = {
            "ts": self.ts,
            "event_id": self.event_id,
            "kind": self.kind,
            "decision_ts": self.decision_ts,
            "symbol": self.symbol,
            "constitution_hash": self.constitution_hash,
            "setup_spec_hash": self.setup_spec_hash,
            "prompt_hash": self.prompt_hash,
            "model_id": self.model_id,
            "data_hash": self.data_hash,
            "reason_code": self.reason_code,
            "payload": self.payload,
        }
        return d


class Journal:
    """Append-only event journal + per-bar reason-code bookkeeping."""

    def __init__(self, root: str | Path, constitution_hash: str, demo: bool = False):
        self.root = Path(root)
        (self.root / "events").mkdir(parents=True, exist_ok=True)
        (self.root / "tickets").mkdir(parents=True, exist_ok=True)
        (self.root / "hashes").mkdir(parents=True, exist_ok=True)
        (self.root / "hashes" / "constitution.sha256").write_text(constitution_hash + "\n")
        self.constitution_hash = constitution_hash
        self.demo = demo
        self._current_bar_reason: dict[str, str] = {}
        self._injected: list[dict] = []   # events captured when journal is read-only in tests

    # ------------------------------------------------------------------ emit
    def emit(
        self,
        kind: str,
        payload: dict | None = None,
        decision_ts: str | None = None,
        reason_code: str | None = None,
        setup_spec_hash: str | None = None,
        data_hash: str | None = None,
        model_id: str | None = None,
        prompt_hash: str | None = None,
        persist: bool = True,
    ) -> Event:
        event = Event(
            kind=kind,
            decision_ts=decision_ts,
            payload=payload or {},
            constitution_hash=self.constitution_hash,
            reason_code=reason_code,
            setup_spec_hash=setup_spec_hash,
            data_hash=data_hash,
            model_id=model_id,
            prompt_hash=prompt_hash,
        )
        if self.demo:
            event.payload["demo"] = True
        if persist:
            # file by the TRADING day (decision date) when present, else ts:
            # replay --date YYYY-MM-DD reconstructs the session from one file
            file_date = (event.decision_ts or event.ts)[:10]
            path = self.root / "events" / f"{file_date}.jsonl"
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
        else:
            self._injected.append(event.to_dict())
        # per-bar terminal reason tracking (exactly one per bar is enforced
        # by the orchestrator calling close_bar_reason once)
        return event

    # -------------------------------------------------------------- per bar
    def open_bar(self, decision_ts: str) -> None:
        self._current_bar_reason[decision_ts] = "__OPEN__"

    def close_bar_reason(self, decision_ts: str, code: str) -> None:
        if code not in REASON_CODES:
            raise ValueError(f"unknown reason code: {code}")
        self._current_bar_reason[decision_ts] = code

    def bar_reason(self, decision_ts: str) -> str | None:
        return self._current_bar_reason.get(decision_ts)

    # -------------------------------------------------------------- reading
    @staticmethod
    def read_events(root: str | Path, date: str | None = None) -> list[dict]:
        root = Path(root)
        events_dir = root / "events"
        if not events_dir.exists():
            return []
        files = sorted(events_dir.glob("*.jsonl"))
        if date:
            files = [p for p in files if p.stem == date]
        out: list[dict] = []
        for path in files:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    out.append(json.loads(line))
        return out

    @staticmethod
    def reason_histogram(events: list[dict]) -> dict[str, int]:
        hist: dict[str, int] = {}
        for event in events:
            code = event.get("reason_code")
            if code and code != "__OPEN__":
                hist[code] = hist.get(code, 0) + 1
        return dict(sorted(hist.items(), key=lambda kv: -kv[1]))

    @property
    def captured(self) -> list[dict]:
        """Events captured in non-persisting mode (tests)."""
        return list(self._injected)
