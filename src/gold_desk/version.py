"""Gold Decision Harness v1 — package version and phase constants."""

__version__ = "1.0.0"

# The orchestrator refuses to touch the veto module unless PHASE >= 2 (L10).
# Phase 1 = deterministic desk, zero LLM. Bump only by human commit.
PHASE = 1

SYMBOL = "XAUUSD"
TIMEFRAME = "H1"

BLOCKED = "BLOCKED"  # sentinel written in the constitution YAML


def is_blocked(value) -> bool:
    """True when a constitution field is still BLOCKED (or explicitly None
    where a number is required)."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip().upper() == BLOCKED:
        return True
    return False
