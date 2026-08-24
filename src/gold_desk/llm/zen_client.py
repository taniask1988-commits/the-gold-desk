"""Keyless OpenAI-compatible client for OpenCode Zen (OPENCODE_HERMES_SETUP §1).

- base URL  : GOLD_DESK_LLM_BASE_URL (falls back to OPENCODE_ZEN_BASE_URL;
              default https://opencode.ai/zen/v1) — any OpenAI-compatible
              endpoint works by setting the alias (P0 §2.1)
- api key   : GOLD_DESK_LLM_API_KEY (falls back to OPENCODE_ZEN_API_KEY;
              placeholder) — the Authorization header is sent ONLY when a
              non-placeholder key is explicitly set; Zen's free tier is
              KEYLESS so by default nothing is sent.
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

from .json_repair import extract_json_object
from .zen_sync import USER_AGENT

DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"


class LLMUnavailable(RuntimeError):
    """Transport failure / timeout / 5xx — caller treats as no-ticket."""


class LLMInvalidJSON(RuntimeError):
    """Model output not parseable as the required JSON — caller treats as VETO."""


def base_url() -> str:
    # P0 §2.1 — GOLD_DESK_LLM_BASE_URL is the generic alias; falls back to
    # OPENCODE_ZEN_BASE_URL, then the Zen default.
    raw = (os.environ.get("GOLD_DESK_LLM_BASE_URL")
           or os.environ.get("OPENCODE_ZEN_BASE_URL") or "").strip()
    # Defense in depth: if set but empty/whitespace, fall back to the default
    # instead of producing a relative URL like "/chat/completions" that
    # urllib rejects with ValueError: unknown url type.
    if not raw:
        return DEFAULT_BASE_URL.rstrip("/")
    if not (raw.startswith("http://") or raw.startswith("https://")):
        # treat schemeless values as host-only (legacy config compatibility)
        raw = f"https://{raw}"
    return raw.rstrip("/")


def api_key() -> str:
    # GOLD_DESK_LLM_API_KEY is the generic alias (P0 §2.1); falls back to
    # OPENCODE_ZEN_API_KEY, then the placeholder. Hermes requires a non-empty
    # key to consider the provider authenticated; the header itself is only
    # sent when a real key is set (see auth_headers).
    return (os.environ.get("GOLD_DESK_LLM_API_KEY")
            or os.environ.get("OPENCODE_ZEN_API_KEY") or "placeholder")


def auth_headers() -> dict:
    """Authorization header — sent ONLY when a non-placeholder key is set.

    Zen's free tier is keyless: with the default placeholder (or no env var)
    the header is absent, exactly like the original Hermes setup. A real key
    (for any other OpenAI-compatible endpoint) is forwarded as a Bearer.
    """
    key = (os.environ.get("GOLD_DESK_LLM_API_KEY")
           or os.environ.get("OPENCODE_ZEN_API_KEY") or "").strip()
    if not key or key == "placeholder":
        return {}
    return {"Authorization": f"Bearer {key}"}


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
    }
    # Authorization only when a real key is set — Zen free tier is keyless
    headers.update(auth_headers())

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


def complete_json(
    messages: list[dict],
    model: str,
    timeout: float = 30.0,
    temperature: float = 0.0,
    max_tokens: int = 500,
    retries: int = 3,
) -> dict:
    """Completion restricted to a single JSON object.

    Zen separates reasoning into `reasoning_content`; only `content` is
    authoritative, but reasoning-heavy free models sometimes emit the JSON
    only inside reasoning_content when they hit the token cap — so that is
    the documented fallback. Anything unparseable is LLMInvalidJSON,
    which the veto path treats as VETO (fail closed).
    """
    body = complete(messages, model, timeout, temperature, max_tokens,
                    retries=retries)
    choices = body.get("choices") or []
    if not choices:
        raise LLMInvalidJSON("no choices in response")
    message = choices[0].get("message") or {}
    content = message.get("content")
    if not content or not isinstance(content, str):
        content = message.get("reasoning_content")  # fallback: capped models
    if not content or not isinstance(content, str):
        raise LLMInvalidJSON("empty content")
    try:
        return extract_json_object(content)
    except ValueError as e:
        raise LLMInvalidJSON(str(e)) from e


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
    headers.update(auth_headers())

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
