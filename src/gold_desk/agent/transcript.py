"""Agent transcripts — persist the FULL message list before anything else
(P1 §3.1: journal-first, mirroring the ticket persist-before-send law).

    data/agent_runs/<run_id>.jsonl   one JSON message per line, in order

The transcript is exactly what the model saw (plus tool results), which is
the audit trail for every agent step. Also emits the journal events:
    AgentRunStarted {run_id, task, model, prompt_hash, tool_names}
    AgentStep      {run_id, step, n_tool_calls, finish_reason}
    AgentToolCall  {run_id, step, tool, args_digest, ok, ms}
    AgentRunFinished {run_id, steps, tool_calls, elapsed_ms, status}
    BudgetExceeded {run_id, reason}
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import IO

from ..events import Journal


def prompt_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class Transcript:
    def __init__(self, data_root: str | Path, run_id: str, journal: Journal):
        self.root = Path(data_root) / "agent_runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / f"{run_id}.jsonl"
        self.journal = journal
        self.run_id = run_id
        self._fh: IO[str] | None = None
        self._closed = False

    # ------------------------------------------------------------------ file

    def open(self) -> "Transcript":
        if self._fh is None and not self._closed:
            self._fh = self.path.open("a", encoding="utf-8")
        return self

    def append(self, message: dict) -> None:
        """Write one message; flush immediately (crash-safe audit trail)."""
        if self._fh is None:
            self.open()
        if self._fh is not None and not self._closed:
            self._fh.write(json.dumps(message, ensure_ascii=False,
                                      default=str) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None
        self._closed = True

    # --------------------------------------------------------------- journal

    def emit_run_started(self, task: str, model: str, system_hash: str,
                         tool_names: list[str]) -> None:
        self.journal.emit("AgentRunStarted", {
            "run_id": self.run_id,
            "task": task[:500],
            "model": model,
            "tools": tool_names,
        }, model_id=model, prompt_hash=system_hash)

    def emit_step(self, step: int, n_tool_calls: int, finish_reason: str) -> None:
        self.journal.emit("AgentStep", {
            "run_id": self.run_id, "step": step,
            "tool_calls": n_tool_calls, "finish_reason": finish_reason,
        })

    def emit_tool_call(self, step: int, tool: str, arguments: str,
                       ok: bool, ms: float) -> None:
        self.journal.emit("AgentToolCall", {
            "run_id": self.run_id, "step": step, "tool": tool,
            "args_sha256_16": hashlib.sha256(
                (tool + arguments).encode("utf-8")).hexdigest()[:16],
            "ok": ok, "ms": int(ms),
        })

    def emit_run_finished(self, steps: int, tool_calls: int,
                          elapsed_ms: int, status: str, detail: str = "") -> None:
        self.journal.emit("AgentRunFinished", {
            "run_id": self.run_id, "steps": steps,
            "tool_calls": tool_calls, "elapsed_ms": elapsed_ms,
            "status": status, "detail": detail[:300],
        })

    def emit_budget_exceeded(self, reason: str) -> None:
        self.journal.emit("BudgetExceeded", {
            "run_id": self.run_id, "reason": reason,
        })
