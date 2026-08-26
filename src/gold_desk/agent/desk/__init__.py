"""gold_desk.agent.desk — the multi-analyst desk (MARKET GAUNTLET piece 4).

Six market-timing personas (technician / macro / news / sentiment /
risk / fundamentalist) judge any Yahoo symbol in parallel, then a
Portfolio Manager call synthesizes the consensus. The ai-hedge-fund
failure contract holds: context-gather errors propagate (fail loud),
per-persona LLM failures abstain (the desk never dies on one model call).

R2-3 — adversarial debate + execution architecture (judged vs
TradingAgents v0.3.1 tradingagents/agents/): when debate=True (the
default), run_desk runs the full 6-phase flow:
  Phase 1: 6 analyst personas in parallel (unchanged)
  Phase 2: bull_researcher + bear_researcher in parallel
           (cross-examine Phase 1 outputs)
  Phase 3: research_manager (synthesizes Phase 2 into a memo)
  Phase 4: trader (turns memo into entry/stop/target/size)
  Phase 5: 3 risk debators in parallel (debate the trader's plan)
  Phase 6: PM (final decision using Phases 3 + 4 + 5; mechanical
           validation: r:r re-compute, conviction calibration,
           abstention discipline)
When debate=False, run_desk runs the legacy Phase 1 + PM flow (used
by the existing test_desk.py tests for backward-compat coverage).

    from gold_desk.agent.desk import run_desk
    report = run_desk("BTC-USD", data_root="data")  # debate=True
"""
from .engine import DeskContextError, run_desk
from .personas import (
    DESK_TOOLS,
    PERSONAS,
    Persona,
    persona_by_name,
    DEBATE_PERSONAS,
    RESEARCHER_PERSONAS,
    MANAGER_PERSONA,
    TRADER_PERSONA,
    DEBATOR_PERSONAS,
)

__all__ = [
    "run_desk",
    "DeskContextError",
    "PERSONAS",
    "Persona",
    "DESK_TOOLS",
    "persona_by_name",
    "DEBATE_PERSONAS",
    "RESEARCHER_PERSONAS",
    "MANAGER_PERSONA",
    "TRADER_PERSONA",
    "DEBATOR_PERSONAS",
]
