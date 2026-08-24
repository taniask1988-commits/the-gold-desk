"""Agent loop tests (P1 §3) — fake provider, no network, no spend.

Pins:
  - tool-call round-trip (assistant tool_calls -> tool result -> final)
  - max_steps enforcement (fail-closed BudgetExceeded, journaled)
  - provider-error fail-closed (LLMUnavailable after fall-through)
  - transcript written BEFORE the result (audit-first ordering)
  - daily budget ledger persists across Budget instances
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.agent.budgets import Budget, BudgetExceeded  # noqa: E402
from gold_desk.agent.loop import run_agent  # noqa: E402
from gold_desk.agent.tools import ToolRegistry, tool  # noqa: E402
from gold_desk.events import Journal  # noqa: E402
from gold_desk.llm.zen_client import LLMUnavailable  # noqa: E402


@tool("Add two numbers (test)")
def add(a: int, b: int = 1) -> dict:
    return {"ok": True, "sum": a + b}


@tool("Echo the text (test)")
def echo(text: str) -> dict:
    return {"ok": True, "text": text}


@pytest.fixture()
def registry():
    reg = ToolRegistry()
    reg.register(add)
    reg.register(echo)
    return reg


# ------------------------------------------------------------- fake provider

class FakeProvider:
    """Scripted responses, popped in order. Records what it was asked."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, messages, model, tools):
        self.calls.append({"messages": [dict(m) for m in messages],
                           "model": model, "tools": tools})
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_call_msg(name, args_json, call_id="call_1"):
    return {
        "tool_calls": [{"id": call_id, "name": name, "arguments": args_json}],
        "text": None, "finish_reason": "tool_calls",
    }


# -------------------------------------------------------------------- tests

def test_tool_round_trip(tmp_path, registry):
    """Provider asks for a tool, gets the result, then answers."""
    prov = FakeProvider([
        tool_call_msg("add", json.dumps({"a": 2, "b": 3})),
        {"text": "The sum is 5.", "tool_calls": [], "finish_reason": "stop"},
    ])
    result = run_agent("add 2 and 3", registry,
                       data_root=tmp_path, provider=prov,
                       max_steps=4, max_minutes=1)
    assert result.ok is True
    assert result.answer == "The sum is 5."
    assert result.tool_calls == 1
    assert result.steps == 2
    # the tool result reached the model on the second call
    second = prov.calls[1]["messages"]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert json.loads(tool_msgs[0]["content"])["sum"] == 5


def test_bad_tool_arguments_fail_soft(tmp_path, registry):
    """Unknown tool / malformed args -> tool error dict, run continues."""
    prov = FakeProvider([
        tool_call_msg("nope", "{not json"),
        tool_call_msg("add", json.dumps({"a": 1})),
        {"text": "done", "tool_calls": [], "finish_reason": "stop"},
    ])
    result = run_agent("x", registry, data_root=tmp_path, provider=prov,
                       max_steps=5, max_minutes=1)
    assert result.ok is True
    second = prov.calls[1]["messages"]
    tool_msgs = [m for m in second if m.get("role") == "tool"]
    assert tool_msgs[0]["content"].startswith("{")  # json error dict


def test_max_steps_enforced(tmp_path, registry):
    """Provider never answers -> step cap ends the run fail-closed."""
    prov = FakeProvider([
        tool_call_msg("echo", json.dumps({"text": f"n{i}"})) for i in range(20)
    ])
    result = run_agent("loop forever", registry, data_root=tmp_path,
                       provider=prov, max_steps=3, max_minutes=1)
    assert result.ok is False
    assert result.status == "budget"
    assert "step cap" in result.detail
    # journal got BudgetExceeded
    events = Journal.read_events(tmp_path)
    assert any(e["kind"] == "BudgetExceeded" for e in events)


def test_provider_error_fail_closed(tmp_path, registry):
    """Transport failure after model fall-through ends the run cleanly."""
    prov = FakeProvider([
        LLMUnavailable("zen http 503"),
        LLMUnavailable("zen http 503"),
        LLMUnavailable("zen http 503"),
    ])
    result = run_agent("q", registry, data_root=tmp_path, provider=prov,
                       max_steps=3, max_minutes=1, max_model_fallbacks=2)
    assert result.ok is False
    assert result.status == "provider_error"
    events = Journal.read_events(tmp_path)
    finished = [e for e in events if e["kind"] == "AgentRunFinished"]
    assert finished and finished[-1]["payload"]["status"] == "provider_error"


def test_transcript_written_before_result(tmp_path, registry):
    """Audit-first: the transcript file exists the moment the run ends,
    and contains exactly the messages the model saw (plus tool results)."""
    prov = FakeProvider([
        tool_call_msg("add", json.dumps({"a": 4, "b": 4})),
        {"text": "8", "tool_calls": [], "finish_reason": "stop"},
    ])
    result = run_agent("add 4 and 4", registry, data_root=tmp_path,
                       provider=prov, max_steps=4, max_minutes=1)
    p = Path(result.transcript_path)
    assert p.exists()
    lines = [json.loads(ln) for ln in p.read_text().splitlines()]
    roles = [m["role"] for m in lines]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    # assistant tool_call message carries the OpenAI-shaped tool_calls
    assert lines[2]["tool_calls"][0]["function"]["name"] == "add"


def test_run_events_journaled(tmp_path, registry):
    prov = FakeProvider([
        {"text": "immediate", "tool_calls": [], "finish_reason": "stop"},
    ])
    run_agent("hi", registry, data_root=tmp_path, provider=prov)
    events = Journal.read_events(tmp_path)
    kinds = [e["kind"] for e in events]
    assert "AgentRunStarted" in kinds
    assert "AgentStep" in kinds
    assert "AgentRunFinished" in kinds


def test_daily_budget_ledger_persists(tmp_path):
    """A restart must not reset the day's budget spend."""
    b1 = Budget(tmp_path, max_steps=100, max_minutes=10)
    b1.check_run_start()
    b1.record_step(5.0)
    b1.record_step(5.0)
    assert b1.day_steps == 2

    b2 = Budget(tmp_path, max_steps=100, max_minutes=10)
    assert b2.day_steps == 2, "new Budget must inherit the day ledger"
    assert b2.day_minutes == pytest.approx(10.0 / 60.0, abs=1e-3)  # ledger rounds to 3dp


def test_kill_switch_halts_agent(tmp_path, registry, monkeypatch):
    monkeypatch.setenv("GOLD_DESK_KILL_SWITCH", "1")
    prov = FakeProvider([
        {"text": "should never run", "tool_calls": [], "finish_reason": "stop"},
    ])
    result = run_agent("q", registry, data_root=tmp_path, provider=prov)
    assert result.ok is False
    assert result.status == "budget"
    assert "kill switch" in result.detail
    assert prov.calls == [], "provider must not be called with kill switch on"


def test_tool_schema_shape(registry):
    schemas = registry.schemas()
    add_schema = [s for s in schemas
                  if s["function"]["name"] == "add"][0]
    props = add_schema["function"]["parameters"]["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["type"] == "integer"
    assert props["b"].get("default") == 1
    assert add_schema["function"]["parameters"]["required"] == ["a"]
