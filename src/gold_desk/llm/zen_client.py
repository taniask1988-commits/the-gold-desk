"""Keyless OpenAI-compatible client for OpenCode Zen (OPENCODE_HERMES_SETUP §1).

- base URL  : OPENCODE_ZEN_BASE_URL (default https://opencode.ai/zen/v1)
- api key   : OPENCODE_ZEN_API_KEY (placeholder) — Zen's free tier is KEYLESS;
              per the Hermes setup the Authorization header is STRIPPED at
              request time (paid-only on Zen), so we simply never send it.
- identity  : official OpenCode client User-Agent (opencode/1.18.18)

Fail-closed by design (L5): every failure raises a typed error; the caller
(veto path) converts those into LLM_UNAVAILABLE / LLM_INVALID_JSON and the
bar ends with no ticket. No retry-into-a-fill ever happens here.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from .zen_sync import USER_AGENT

DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"


class LLMUnavailable(RuntimeError):
    """Transport failure / timeout / 5xx — caller treats as no-ticket."""


class LLMInvalidJSON(RuntimeError):
    """Model output not parseable as the required JSON — caller treats as VETO."""


def base_url() -> str:
    return os.environ.get("OPENCODE_ZEN_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def api_key() -> str:
    # Hermes requires a non-empty key to consider the provider authenticated;
    # the header itself is stripped, so the value is a placeholder.
    return os.environ.get("OPENCODE_ZEN_API_KEY", "placeholder")


def complete(
    messages: list[dict],
    model: str,
    timeout: float = 30.0,
    temperature: float = 0.0,
    max_tokens: int = 500,
) -> dict:
    """One chat completion. Returns the full response body. Raises typed errors."""
    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url()}/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
            # Authorization deliberately absent: Zen free tier is keyless
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMUnavailable(f"zen http {e.code}") from e
    except TimeoutError as e:
        raise LLMUnavailable("zen timeout") from e
    except Exception as e:  # noqa: BLE001 — any transport failure = unavailable
        raise LLMUnavailable(f"zen transport: {type(e).__name__}") from e


def _iter_json_objects(text: str):
    """Yield balanced {...} substrings (innermost-last ordering is not
    guaranteed; caller prefers the LAST parseable object — models state the
    final answer at the end)."""
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


def _extract_json_object(text: str) -> dict:
    """Best-effort strict extraction: last balanced object that parses."""
    candidates = []
    for cand in _iter_json_objects(text):
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
        raise LLMInvalidJSON(f"no JSON object found: {text[:120]!r}")
    return candidates[-1]


def complete_json(
    messages: list[dict],
    model: str,
    timeout: float = 30.0,
    temperature: float = 0.0,
    max_tokens: int = 500,
) -> dict:
    """Completion restricted to a single JSON object.

    Zen separates reasoning into `reasoning_content`; only `content` is
    authoritative, but reasoning-heavy free models sometimes emit the JSON
    only inside reasoning_content when they hit the token cap — so that is
    the documented fallback. Anything unparseable is LLMInvalidJSON,
    which the veto path treats as VETO (fail closed).
    """
    body = complete(messages, model, timeout, temperature, max_tokens)
    choices = body.get("choices") or []
    if not choices:
        raise LLMInvalidJSON("no choices in response")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content or not isinstance(content, str):
        content = message.get("reasoning_content")  # fallback: capped models
    if not content or not isinstance(content, str):
        raise LLMInvalidJSON("empty content")
    return _extract_json_object(content)
