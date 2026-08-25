"""Agent streaming for the chat window — wraps run_agent with live events.

The chat popup already consumes an NDJSON protocol:
    start | reasoning | content | done | error

Agent mode extends it with two more kinds (the UI renders them as tool
activity in the reasoning panel):
    {"type":"tool",       "name": "web_search", "args": {...}}
    {"type":"tool_result","name": "web_search", "ok": true, "preview": "..."}

Everything else stays the same protocol, so ChatRoom.tsx needs only a
small extension. The final answer is emitted as `content` deltas (chunked
so the typing animation stays smooth), and `done` carries agent stats.

The heavy lifting is a monkeypatch-free callback hook: run_agent accepts
`on_event` (P1 extension) and this module turns those callbacks into a
generator the CLI can iterate.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from ..events import Journal
from .loop import RunResult, resolve_models, run_agent
from .tools import ToolRegistry

# re-exported for the CLI
DEFAULT_SYSTEM = None  # imported lazily to avoid circulars


def build_registry() -> ToolRegistry:
    """The full research registry: desk + crypto + web tools + the
    multi-analyst desk bridge (piece 6 — chat AGENT MODE can run the
    5-persona desk on any symbol)."""
    from .desk_tools import desk_registry
    from .assets import asset_tools
    from .browse import browse_tools
    from .desk_bridge import desk_bridge_tools
    reg = desk_registry()
    for t in asset_tools():
        reg.register(t)
    for t in browse_tools():
        reg.register(t)
    for t in desk_bridge_tools():
        reg.register(t)
    return reg


def agent_chat_stream(
    messages: list[dict],
    *,
    data_root: str | Path = "data",
    model: str | None = None,
    max_steps: int = 10,
    max_minutes: float = 8.0,
    registry: ToolRegistry | None = None,
    journal: Journal | None = None,
):
    """Streaming agent chat over the same NDJSON protocol as expert chat.

    messages: [{role, content}] transcript, newest last (only the last
    user message is used as the task — the agent is single-task by design;
    prior turns are prepended as context in the task text).
    Yields event dicts in order:
        start -> (tool | tool_result | reasoning)* -> content* -> done|error
    """
    reg = registry or build_registry()

    # Build the task text from the transcript: earlier turns as context,
    # the final user message as the actual task.
    turns = [m for m in (messages or [])
             if isinstance(m, dict) and str(m.get("content", "")).strip()]
    if not turns:
        yield {"type": "error", "error": "empty transcript"}
        return
    task = ""
    if len(turns) > 1:
        ctx = "\n".join(
            f"{m.get('role', 'user')}: {str(m.get('content', ''))[:600]}"
            for m in turns[:-1])
        task += f"Conversation so far (context):\n{ctx}\n\n"
    task += f"Current question: {turns[-1].get('content', '')}"

    models = resolve_models(model, data_root)
    yield {"type": "start", "model": models[0], "agent": True,
           "grounded": True, "tools": reg.names()}

    queue: list[dict] = []

    def on_event(evt: dict) -> None:
        queue.append(evt)

    started = time.monotonic()
    try:
        result: RunResult = run_agent(
            task, reg,
            data_root=data_root,
            journal=journal,
            model=model,
            max_steps=max_steps,
            max_minutes=max_minutes,
            on_event=on_event,
        )
    except Exception as e:  # noqa: BLE001 — fail closed
        yield {"type": "error", "error": f"{type(e).__name__}: {e}"}
        return

    # Replay the tool/step events collected during the run, mapped onto
    # the chat protocol.
    for evt in queue:
        kind = evt.get("kind")
        if kind == "tool_call":
            yield {"type": "tool", "name": evt.get("name"),
                   "args": evt.get("args", {})}
        elif kind == "tool_result":
            yield {"type": "tool_result", "name": evt.get("name"),
                   "ok": bool(evt.get("ok")),
                   "preview": str(evt.get("preview", ""))[:220]}
        elif kind == "step":
            yield {"type": "reasoning",
                   "delta": f"— step {evt.get('step')}: "
                            f"{evt.get('detail', 'thinking')} —\n"}

    if result.ok and result.answer:
        # chunk the final answer into content deltas for the typing effect
        answer = result.answer
        step = 220
        for i in range(0, len(answer), step):
            yield {"type": "content", "delta": answer[i:i + step]}
        yield {"type": "done", "model": result.model,
               "latency_ms": result.elapsed_ms, "grounded": True,
               "agent": True, "steps": result.steps,
               "tool_calls": result.tool_calls}
    else:
        yield {"type": "error",
               "error": result.detail or f"agent run ended: {result.status}"}


def _clip_preview(out, n: int = 200) -> str:
    try:
        s = json.dumps(out, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(out)
    return s[:n]
