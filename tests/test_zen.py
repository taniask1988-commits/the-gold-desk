"""OpenCode Zen layer tests — selection rules (§2.3), default resolution
(§2.4), client fail-closed behaviour, prompt hashing (Doc 4). No network."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.llm import zen_sync  # noqa: E402
from gold_desk.llm.veto_llm import _validate, prompt_hash, prompt_text  # noqa: E402
from gold_desk.llm import zen_client  # noqa: E402


def dev_model(context=200000, tool=True, reasoning=True, vision=False,
              deprecated=False, inp=0, out=0):
    return {
        "cost": {"input": inp, "output": out},
        "limit": {"context": context, "output": 8192},
        "tool_call": tool,
        "reasoning": reasoning,
        "vision": vision,
        "deprecated": deprecated,
    }


DEVS = {
    "x-preview-f-free": dev_model(1000000),
    "hy3-free": dev_model(190000),
    "big-pickle": dev_model(200000),
    "paid-model": dev_model(200000, inp=3, out=15),          # not free
    "no-tools-model": dev_model(200000, tool=False),          # not agentic
    "deepseek-v4-flash-free": dev_model(200000, deprecated=True),
}


# ------------------------------------------------------------ §2.3 selection
def test_selection_keeps_only_served_free_agentic():
    zen_ids = {"x-preview-f-free", "hy3-free", "big-pickle", "paid-model",
               "no-tools-model", "deepseek-v4-flash-free"}
    out = zen_sync.select_free_models(zen_ids, DEVS)
    assert set(out) == {"x-preview-f-free", "hy3-free", "big-pickle",
                        "deepseek-v4-flash-free"}
    assert out["x-preview-f-free"]["context_window"] == 1000000
    assert out["deepseek-v4-flash-free"]["deprecated"] is True


def test_selection_drops_unserved_models():
    out = zen_sync.select_free_models({"hy3-free"}, DEVS)
    assert set(out) == {"hy3-free"}


def test_selection_zero_free_returns_empty():
    out = zen_sync.select_free_models({"paid-model"}, DEVS)
    assert out == {}


# ---------------------------------------------------------- §2.24 default
def test_default_prefers_preference_order_skipping_deprecated():
    models = zen_sync.select_free_models(
        {"deepseek-v4-flash-free", "big-pickle", "hy3-free"}, DEVS)
    assert zen_sync.resolve_default(models) == "hy3-free"


def test_default_falls_through_when_removed():
    # x-preview removed by Zen -> next live preference
    models = zen_sync.select_free_models({"big-pickle", "hy3-free"}, DEVS)
    assert zen_sync.resolve_default(models) == "hy3-free"


def test_default_any_live_when_preference_exhausted():
    models = zen_sync.select_free_models({"big-pickle"}, DEVS)
    assert zen_sync.resolve_default(models) == "big-pickle"


def test_bundled_fallback_is_valid():
    fb = zen_sync.BUNDLED_FALLBACK
    assert fb["schema"] == "zen_catalog.v1"
    assert fb["default"] in fb["models"]
    assert not fb["models"][fb["default"]]["deprecated"]
    assert zen_sync.resolve_default(fb["models"]) == fb["default"]


# ------------------------------------------------------------- cache replay
def test_cache_replay_fast_path(tmp_path, monkeypatch):
    calls = {"zen": 0, "dev": 0}

    def fake_zen():
        calls["zen"] += 1
        return {"x-preview-f-free", "hy3-free"}

    def fake_dev():
        calls["dev"] += 1
        return DEVS

    monkeypatch.setattr(zen_sync, "fetch_zen_ids", fake_zen)
    monkeypatch.setattr(zen_sync, "fetch_models_dev_opencode", fake_dev)

    first = zen_sync.sync_catalog(tmp_path)
    assert first["source"] == "live-sync"
    second = zen_sync.sync_catalog(tmp_path)
    assert second["source"] == "cache-replay"
    assert calls == {"zen": 2, "dev": 1}  # models.dev fetched once


def test_network_down_replays_any_age_cache(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("zen down")

    monkeypatch.setattr(zen_sync, "fetch_zen_ids", boom)
    # seed a cache first
    (tmp_path).mkdir(parents=True, exist_ok=True)
    seeded = dict(zen_sync.BUNDLED_FALLBACK)
    seeded["synced_ts"] = 0  # ancient
    (tmp_path / "zen-catalog.json").write_text(json.dumps(seeded))
    out = zen_sync.sync_catalog(tmp_path)
    assert out["source"] == "cache-replay-network-down"
    assert out["default"] == seeded["default"]


def test_no_cache_no_network_uses_bundled(tmp_path, monkeypatch):
    def boom():
        raise RuntimeError("zen down")

    monkeypatch.setattr(zen_sync, "fetch_zen_ids", boom)
    out = zen_sync.sync_catalog(tmp_path)
    assert out["source"] == "bundled-fallback"


# --------------------------------------------------------- client fail-closed
def test_complete_json_extracts_object():
    good = {"choices": [{"message": {
        "content": "thinking... {\"decision\":\"VETO\",\"reason\":\"news\"}"}}]}
    monkey_response = good

    class FakeUrlopen:
        def __init__(self, req, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(monkey_response).encode()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(zen_client.urllib.request, "urlopen", FakeUrlopen)
    try:
        out = zen_client.complete_json([{"role": "user", "content": "x"}],
                                       "m")
        assert out == {"decision": "VETO", "reason": "news"}
    finally:
        monkey.undo()


def test_complete_json_rejects_garbage():
    class FakeUrlopen:
        def __init__(self, req, timeout):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps({"choices": [{"message": {
                "content": "no json here"}}]}).encode()

    monkey = pytest.MonkeyPatch()
    monkey.setattr(zen_client.urllib.request, "urlopen", FakeUrlopen)
    try:
        with pytest.raises(zen_client.LLMInvalidJSON):
            zen_client.complete_json([{"role": "user", "content": "x"}], "m")
    finally:
        monkey.undo()


def test_transport_error_is_llm_unavailable():
    def boom(req, timeout):
        raise TimeoutError("slow")

    monkey = pytest.MonkeyPatch()
    monkey.setattr(zen_client.urllib.request, "urlopen", boom)
    try:
        with pytest.raises(zen_client.LLMUnavailable):
            zen_client.complete([{"role": "user", "content": "x"}], "m")
    finally:
        monkey.undo()


def test_request_never_sends_authorization():
    seen = {}

    class FakeReq:
        def __init__(self, url, data, headers):
            seen.update(headers)
            seen["url"] = url

    monkey = pytest.MonkeyPatch()
    monkey.setattr(zen_client.urllib.request, "Request", FakeReq)

    class FakeUrlopen:
        def __init__(self, req, timeout):
            raise zen_client.LLMUnavailable("stop here")

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    monkey.setattr(zen_client.urllib.request, "urlopen", FakeUrlopen)
    try:
        with pytest.raises(zen_client.LLMUnavailable):
            zen_client.complete([{"role": "user", "content": "x"}], "m")
    finally:
        monkey.undo()
    assert "Authorization" not in seen          # keyless free tier
    assert seen["User-Agent"].startswith("opencode/")  # official client id


# ----------------------------------------------------------------- veto L3
def test_veto_validation_binary_only():
    assert _validate({"decision": "ENDORSE", "reason": "ok"})["decision"] == "ENDORSE"
    bad = _validate({"decision": "MAYBE", "reason": "?"})
    assert bad["decision"] == "VETO"
    extra = _validate({"decision": "ENDORSE", "reason": "x", "size": 0.5})
    assert extra["decision"] == "VETO"          # extra schema field = veto


def test_prompt_hash_stable_and_doc4_files_exist():
    assert (REPO / "prompts" / "veto_system.v1.txt").exists()
    assert (REPO / "prompts" / "veto_schema.json").exists()
    text = prompt_text()
    assert "context veto" in text
    assert "ENDORSE" in text and "VETO" in text
    h1, h2 = prompt_hash(), prompt_hash()
    assert h1 == h2 and len(h1) == 64


def test_run_veto_converts_invalid_json_to_veto(monkeypatch):
    from gold_desk.llm import veto_llm

    def fake_complete_json(messages, model, **kw):
        raise zen_client.LLMInvalidJSON("garbage")

    monkeypatch.setattr(veto_llm, "complete_json", fake_complete_json)
    out = veto_llm.run_veto({}, "m")
    assert out["decision"] == "VETO"
    assert "LLM_INVALID_JSON" in out["reason"]
    assert out["model"] == "m"
