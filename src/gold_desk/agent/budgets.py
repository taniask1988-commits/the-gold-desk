"""Agent budgets + kill switch (P5 governor; used from P1 on).

Env-driven, journaled, fail-closed:

    GOLD_DESK_AGENT_MAX_STEPS_DAILY    (default 60)
    GOLD_DESK_AGENT_MAX_MINUTES_DAILY  (default 30)
    GOLD_DESK_AGENT_MAX_TOOL_CALLS_RUN (default 25)
    GOLD_DESK_KILL_SWITCH              (halts agent too)

Every check raises BudgetExceeded — the loop converts that into a clean
run end + BudgetExceeded journal event. Nothing else happens. The daily
counters persist in data/agent_runs/budget_day_<date>.json so a new
process inherits the day's spend (a restart must not reset the budget).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path


class BudgetExceeded(RuntimeError):
    """Raised when a run/day cap is hit or the kill switch is on."""


DEFAULTS = {
    "max_steps_daily": 60,
    "max_minutes_daily": 30,
    "max_tool_calls_run": 25,
}


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        v = int(raw)
        return v if v > 0 else default
    except ValueError:
        return default


def kill_switch_on() -> bool:
    raw = (os.environ.get("GOLD_DESK_KILL_SWITCH")
           or os.environ.get("GOLD_DESK_AGENT_KILL_SWITCH") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


class Budget:
    """Wall-clock + step caps for ONE run, plus the shared daily ledger."""

    def __init__(self, data_root: str | Path, *,
                 max_steps: int = 12,
                 max_minutes: float = 10.0,
                 max_tool_calls: int | None = None):
        self.root = Path(data_root) / "agent_runs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_steps = max(1, max_steps)
        self.deadline = time.monotonic() + max(0.1, max_minutes) * 60.0
        self.max_tool_calls = max_tool_calls or _env_int(
            "GOLD_DESK_AGENT_MAX_TOOL_CALLS_RUN", DEFAULTS["max_tool_calls_run"])
        self.tool_calls = 0
        self.steps = 0
        self._day_file = self.root / f"budget_day_{_today()}.json"
        self._load_day()

    # ------------------------------------------------------------- day ledger

    def _load_day(self) -> None:
        try:
            d = json.loads(self._day_file.read_text())
        except Exception:
            d = {}
        self.day_steps = int(d.get("steps", 0))
        self.day_minutes = float(d.get("minutes", 0.0))

    def _save_day(self) -> None:
        try:
            tmp = self._day_file.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "day": _today(),
                "steps": self.day_steps,
                "minutes": round(self.day_minutes, 3),
            }))
            tmp.replace(self._day_file)
        except Exception:
            pass  # ledger persistence is best-effort; caps still hold in-run

    # ----------------------------------------------------------------- checks

    def check_run_start(self) -> None:
        if kill_switch_on():
            raise BudgetExceeded("agent kill switch is ON")
        if self.day_steps >= _env_int(
                "GOLD_DESK_AGENT_MAX_STEPS_DAILY", DEFAULTS["max_steps_daily"]):
            raise BudgetExceeded(
                f"daily step budget exhausted ({self.day_steps} steps today)")
        if self.day_minutes >= _env_int(
                "GOLD_DESK_AGENT_MAX_MINUTES_DAILY",
                DEFAULTS["max_minutes_daily"]):
            raise BudgetExceeded(
                f"daily minute budget exhausted ({self.day_minutes:.0f} min today)")

    def check_step(self) -> None:
        if kill_switch_on():
            raise BudgetExceeded("agent kill switch engaged mid-run")
        if self.steps >= self.max_steps:
            raise BudgetExceeded(f"run step cap reached ({self.max_steps})")
        if time.monotonic() > self.deadline:
            raise BudgetExceeded("run wall-clock deadline passed")

    def record_step(self, elapsed_s: float) -> None:
        self.steps += 1
        self.day_steps += 1
        self.day_minutes += max(0.0, elapsed_s) / 60.0
        self._save_day()

    def check_tool_call(self) -> None:
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded(
                f"run tool-call cap reached ({self.max_tool_calls})")

    def record_tool_call(self) -> None:
        self.tool_calls += 1


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")
