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


def _is_transient(code: int) -> bool:
    """Codes that are typically transient on Zen's free shared infra and
    worth a single retry with backoff: 429 (rate limit), 500/502/503/504
    (gateway/overload), 400 (some endpoints return 400 for transient
    payload-validation races against the model router)."""
    return code in (400, 429, 500, 502, 503, 504)


def complete(
    messages: list[dict],
    model: str,
    timeout: float = 30.0,
    temperature: float = 0.0,
    max_tokens: int = 500,
    retries: int = 3,
) -> dict:
    """One chat completion. Returns the full response body. Raises typed errors.

    Zen's free shared infra will occasionally return 400/429/5xx under load;
    we retry up to `retries` times with exponential backoff (0.6s, 1.4s, 3.0s)
    before surfacing LLMUnavailable. This keeps the chat UX resilient to
    transient overload without ever retrying into a fake success.
    """
    import time as _time

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        # Authorization deliberately absent: Zen free tier is keyless
    }

    last_exc: Exception | None = None
    backoff_s = 0.6
    for attempt in range(max(1, retries)):
        req = urllib.request.Request(
            f"{base_url()}/chat/completions",
            data=body,           # body is reused; urllib doesn't stream it
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_exc = e
            if _is_transient(e.code) and attempt < retries - 1:
                _time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable(f"zen http {e.code}") from e
        except TimeoutError as e:
            last_exc = e
            if attempt < retries - 1:
                _time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable("zen timeout") from e
        except Exception as e:  # noqa: BLE001 — any transport failure = unavailable
            last_exc = e
            if attempt < retries - 1:
                _time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable(f"zen transport: {type(e).__name__}") from e
    # Defensive: should be unreachable
    raise LLMUnavailable(f"zen exhausted retries: {last_exc!r}")


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


def complete_stream(
    messages: list[dict],
    model: str,
    timeout: float = 90.0,
    temperature: float = 0.4,
    max_tokens: int = 1800,
):
    """Streaming completion. Yields ('reasoning'|'content', delta_str) tuples.

    OpenAI-compatible SSE: each event is `data: <json>\\n\\n` and the stream
    ends with `data: [DONE]\\n\\n`. Reasoning-capable models emit deltas
    with `reasoning_content` before deltas with `content`.

    The generator handles transient 429/5xx with one retry (warm-reopen) but
    never retries a partial stream — once tokens have flowed, what was
    emitted is the truth (fail-closed, same as the non-stream path).

    Raises LLMUnavailable on transport failure before first byte.
    """
    import time as _time

    body = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": True,
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
        "Accept": "text/event-stream",
    }

    backoff_s = 0.8
    for attempt in range(2):
        req = urllib.request.Request(
            f"{base_url()}/chat/completions",
            data=body,
            headers=headers,
        )
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as e:
            if _is_transient(e.code) and attempt == 0:
                _time.sleep(backoff_s)
                continue
            raise LLMUnavailable(f"zen http {e.code}") from e
        except TimeoutError as e:
            if attempt == 0:
                _time.sleep(backoff_s)
                continue
            raise LLMUnavailable("zen timeout") from e
        except Exception as e:
            if attempt == 0:
                _time.sleep(backoff_s)
                continue
            raise LLMUnavailable(f"zen transport: {type(e).__name__}") from e

        # parse SSE event stream — we read raw bytes and decode line-by-line
        try:
            buf = b""
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                buf += chunk
                # events are split on \n\n; process complete events only
                while b"\n\n" in buf:
                    raw_event, buf = buf.split(b"\n\n", 1)
                    for line in raw_event.split(b"\n"):
                        line = line.decode("utf-8", errors="replace")
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            return
                        if not payload:
                            continue
                        try:
                            evt = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choices = evt.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        rc = delta.get("reasoning_content")
                        if rc:
                            yield ("reasoning", rc)
                        c = delta.get("content")
                        if c:
                            yield ("content", c)
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return

    # Defensive
    raise LLMUnavailable("zen stream exhausted retries")
