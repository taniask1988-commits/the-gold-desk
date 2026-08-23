"""§5.2 — the asof_ts law. THE ONLY lookahead filter in the system.

    keep if asof_ts <= decision_ts
    drop otherwise
    never send dropped items to the LLM (or to the setup engine)

The live context builder and the offline simulator import THIS function.
If a variant ever exists in two places, the simulator is a lie.
"""
from __future__ import annotations

from datetime import datetime

from ..clock import parse_ts
from .model import Observation


def filter_asof(observations: list[Observation], decision_ts: str | datetime) -> list[Observation]:
    ceiling = decision_ts if isinstance(decision_ts, datetime) else parse_ts(decision_ts)
    kept: list[Observation] = []
    for obs in observations:
        if parse_ts(obs.asof_ts) <= ceiling:
            kept.append(obs)
    return kept


def violates_asof(observations: list[Observation], decision_ts: str | datetime) -> list[Observation]:
    """The dropped tail — used by tests and audit asserts."""
    ceiling = decision_ts if isinstance(decision_ts, datetime) else parse_ts(decision_ts)
    return [o for o in observations if parse_ts(o.asof_ts) > ceiling]
