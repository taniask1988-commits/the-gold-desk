"""JSON repair + extraction shared by the veto path and the agent loop
(P0 §2.2 — extracted from zen_client so both callers share one implementation).

Given free-form model output, find the LAST balanced {...} object that parses.
Reasoning-heavy free models wrap their JSON in prose or emit it only at the
end; models state the final answer at the end, so last-parseable wins.
Relaxed-quote retry handles models that emit single-quoted pseudo-JSON.
"""
from __future__ import annotations

import json
from typing import Iterator


def iter_json_objects(text: str) -> Iterator[str]:
    """Yield balanced {...} substrings, scanning depth with string-awareness.

    Innermost-last ordering is not guaranteed; callers prefer the LAST
    parseable object — models state the final answer at the end.
    """
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\" and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start:i + 1]


def extract_json_object(text: str, *,
                        on_invalid=None) -> dict:
    """Best-effort strict extraction: last balanced object that parses.

    `on_invalid` (optional) receives the exception + text when nothing
    parses, so callers can raise their own typed error (e.g.
    LLMInvalidJSON) while this module stays dependency-free.
    """
    candidates = []
    for cand in iter_json_objects(text):
        try:
            parsed = json.loads(cand)
        except json.JSONDecodeError:
            # retry with relaxed quotes (reasoning dumps use ' or none)
            relaxed = cand.replace("'", '"')
            try:
                parsed = json.loads(relaxed)
            except json.JSONDecodeError:
                continue
        if isinstance(parsed, dict):
            candidates.append(parsed)
    if not candidates:
        if on_invalid is not None:
            on_invalid(text)
        raise ValueError(f"no JSON object found: {text[:120]!r}")
    return candidates[-1]
