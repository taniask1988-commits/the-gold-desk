"""gold_desk.agent.desk — the multi-analyst desk (MARKET GAUNTLET piece 4).

Five market-timing personas (technician / macro / news / sentiment /
risk) judge any Yahoo symbol in parallel, then a Portfolio Manager call
synthesizes the consensus. The ai-hedge-fund failure contract holds:
context-gather errors propagate (fail loud), per-persona LLM failures
abstain (the desk never dies on one model call).

    from gold_desk.agent.desk import run_desk
    report = run_desk("BTC-USD", data_root="data")
"""
from .engine import DeskContextError, run_desk
from .personas import DESK_TOOLS, PERSONAS, Persona, persona_by_name

__all__ = [
    "run_desk",
    "DeskContextError",
    "PERSONAS",
    "Persona",
    "DESK_TOOLS",
    "persona_by_name",
]
