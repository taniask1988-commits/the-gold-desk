"""The §8.3 context-veto completion, Doc 4 files, hashed prompt.

LIVE USE (Phase 2 only): the orchestrator imports this module exclusively
inside its `identity.phase >= 2` branch (Law L10; pinned by
test_no_llm_in_phase1). While the constitution says phase 1 the live bar
loop contains zero LLM code on its path — the veto is recorded as
ENDORSE_BYPASS instead.

OFFLINE USE (any phase): `run_veto()` powers the veto RESEARCH BENCH
(`python -m gold_desk.cli veto-bench`), which replays recorded blind packs
to measure model behaviour. The bench is not the live loop, cannot issue
tickets, and journals to a separate file.

Output contract (L3): {"decision": "ENDORSE"|"VETO", "reason": "<=500 chars"}.
Invalid JSON -> VETO (LLMInvalidJSON under the hood). Timeout -> no-ticket
(LLMUnavailable). Any extra schema field -> VETO. Uncertain -> the prompt
itself instructs VETO.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

from .zen_client import LLMInvalidJSON, LLMUnavailable, complete_json

REPO_ROOT = Path(__file__).resolve().parents[3]
PROMPT_PATH = REPO_ROOT / "prompts" / "veto_system.v1.txt"
SCHEMA_PATH = REPO_ROOT / "prompts" / "veto_schema.json"


def prompt_text() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def prompt_hash() -> str:
    return hashlib.sha256(prompt_text().encode("utf-8")).hexdigest()


def _validate(raw: dict) -> dict:
    decision = raw.get("decision")
    reason = str(raw.get("reason", ""))[:500]
    if decision not in ("ENDORSE", "VETO"):
        return {"decision": "VETO",
                "reason": f"invalid decision value: {decision!r} -> fail closed"}
    if set(raw.keys()) - {"decision", "reason"}:
        return {"decision": "VETO",
                "reason": "extra schema fields present -> fail closed"}
    return {"decision": decision, "reason": reason}


def run_veto(
    pack: dict,
    model: str,
    timeout: float = 30.0,
    max_tokens: int = 900,
) -> dict:
    """One completion, zero tools, blind pack in, binary verdict out.

    Returns {"decision","reason","model","prompt_hash","latency_ms"}.
    Raises LLMUnavailable for transport failures (caller: no ticket).
    LLMInvalidJSON is converted to VETO here (§4.5).
    """
    started = time.time()
    messages = [
        {"role": "system", "content": prompt_text()},
        {"role": "user", "content": json.dumps(pack, sort_keys=True)[:60000]},
    ]
    try:
        raw = complete_json(messages, model, timeout=timeout,
                            temperature=0.0, max_tokens=max_tokens)
    except LLMInvalidJSON as e:
        return {
            "decision": "VETO",
            "reason": f"LLM_INVALID_JSON: {e}",
            "model": model, "prompt_hash": prompt_hash(),
            "latency_ms": int((time.time() - started) * 1000),
        }
    verdict = _validate(raw)
    return {
        **verdict,
        "model": model,
        "prompt_hash": prompt_hash(),
        "latency_ms": int((time.time() - started) * 1000),
    }
