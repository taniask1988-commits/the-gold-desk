"""Agent provider adapter — one function over any OpenAI-compatible API
(P1 §3.1). Reuses zen_client's transport (base_url/auth_headers/retry) so
the agent loop inherits the same fail-closed discipline and $0 Zen default.

    chat(messages, model, tools) -> {"text": str|None, "tool_calls": [...]}

Tool-calls are normalised to:
    [{"id": str, "name": str, "arguments": str(json)}]

Provider failures raise LLMUnavailable; the loop converts them into a
fail-closed run end (never a retry into mutation).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from ..llm.zen_client import LLMUnavailable, auth_headers, base_url
from ..llm.zen_sync import USER_AGENT


def chat(
    messages: list[dict],
    model: str,
    tools: list[dict] | None = None,
    timeout: float = 60.0,
    temperature: float = 0.2,
    max_tokens: int = 2000,
    retries: int = 2,
) -> dict:
    """One tool-aware chat completion over the OpenAI-compatible API."""
    body: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"

    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "User-Agent": USER_AGENT,
    }
    headers.update(auth_headers())

    last_exc: Exception | None = None
    backoff_s = 0.8
    for attempt in range(max(1, retries)):
        req = urllib.request.Request(
            f"{base_url()}/chat/completions",
            data=payload,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return _normalise(data)
        except urllib.error.HTTPError as e:
            last_exc = e
            if e.code in (400, 429, 500, 502, 503, 504) and attempt < retries - 1:
                time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable(f"agent provider http {e.code}") from e
        except TimeoutError as e:
            last_exc = e
            if attempt < retries - 1:
                time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable("agent provider timeout") from e
        except Exception as e:  # noqa: BLE001 — transport failure = unavailable
            last_exc = e
            if attempt < retries - 1:
                time.sleep(backoff_s)
                backoff_s *= 2.2
                continue
            raise LLMUnavailable(
                f"agent provider transport: {type(e).__name__}") from e
    raise LLMUnavailable(f"agent provider exhausted retries: {last_exc!r}")


def _normalise(body: dict) -> dict:
    """Map an OpenAI-style response to {text, tool_calls}."""
    choices = body.get("choices") or []
    if not choices:
        raise LLMUnavailable("agent provider: no choices in response")
    message = choices[0].get("message") or {}
    raw_calls = message.get("tool_calls") or []

    tool_calls = []
    for call in raw_calls:
        fn = call.get("function") or {}
        tool_calls.append({
            "id": call.get("id") or f"call_{len(tool_calls)}",
            "name": fn.get("name") or "",
            "arguments": fn.get("arguments") or "{}",
        })

    text = message.get("content")
    if text is None and not tool_calls:
        # some free models dump reasoning only — treat as text
        text = message.get("reasoning_content")
    if text is not None and not isinstance(text, str):
        text = str(text)

    finish = choices[0].get("finish_reason") or ""
    return {"text": text, "tool_calls": tool_calls, "finish_reason": finish}
