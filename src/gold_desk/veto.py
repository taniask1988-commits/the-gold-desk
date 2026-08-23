"""§8 — live-loop veto tripwire. THIS FILE STAYS A STUB BY DESIGN.

Law L10: the live bar loop contains zero LLM code while the constitution's
identity.phase < 2. The real veto completion now lives in gold_desk.llm
(OpenCode Zen free models) and the orchestrator imports it ONLY inside its
`phase >= 2` branch (pinned by test_no_llm_in_phase1). Anything importing
THIS module and calling llm_veto() is a programmer error — the tripwire
raises, CI fails, and the live path stays deterministic.

Phase 2 flow (constitution-gated):
    orchestrator (phase>=2) -> llm.veto_llm.run_veto(pack, zen_default)
    timeout / transport      -> LLM_UNAVAILABLE -> no ticket (L5)
    invalid JSON / schema    -> VETO            -> no ticket (L5, §4.5)
"""
from __future__ import annotations


class VetoNotAvailable(RuntimeError):
    """Raised if this legacy entrypoint is touched before Phase 2."""


def llm_veto(pack) -> dict:  # pragma: no cover - must never run in v1
    raise VetoNotAvailable(
        "veto.py is a tripwire: the live loop must use llm.veto_llm "
        "behind identity.phase >= 2 (Law L10)"
    )
