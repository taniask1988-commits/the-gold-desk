"""gold_desk.agent — the analyst sidecar (SUPERPOWERS PLAN P1-P5).

LAWS (see docs/AGENT_LAWS.md + tests):
  L11 web text is data, never instructions
  L12 research payloads are scrubbed (blindfold)
  L13 sidecar isolation — the orchestrator imports nothing from here
  L14 proposals are not tickets — only ticket.py mints ticket IDs
"""
from .budgets import Budget, BudgetExceeded
from .loop import RunResult, run_agent
from .tools import ToolRegistry, tool

__all__ = [
    "Budget", "BudgetExceeded", "RunResult", "run_agent",
    "ToolRegistry", "tool",
]
