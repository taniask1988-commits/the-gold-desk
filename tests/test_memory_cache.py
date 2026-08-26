"""R2-4 — reflective memory + sha256 PromptCache (offline tests).

Judged vs TradingAgents v0.3.1's TradingMemoryLog
(``tradingagents/agents/utils/memory.py``, 299 lines) + Reflector
(``tradingagents/graph/reflection.py``, 57 lines) AND ai-hedge-fund
v2.2.0's PromptCache (``hedge_fund/llm/cache.py``, 48 lines).

Test surface:
  - PromptCache: prompt_key deterministic; get None on miss;
    put/get round-trip; put_failure persists raw_response + error;
    TTL evicts stale records; thread-safe concurrent puts.
  - ReflectiveMemory: store_decision appends pending; idempotency
    guard on (run_id, symbol, action); reflect_on_decision produces
    a structured 6-field lesson; reflect is idempotent on run_id;
    recent_lessons returns k most recent by run_id; recent_lessons
    falls back to same-regime peers when symbol has < k; rotation
    caps per-symbol at MAX_PER_SYMBOL; rotation caps global index
    at MAX_GLOBAL; structured lessons have all 6 fields (the brief's
    edge over TA's 2-4 sentences of plain prose).
  - PM re-injection: when recent_lessons returns ≥1 lesson, the PM's
    user_msg contains the "RECENT LESSONS" block; when no lessons
    (cold start), the PM's user_msg does NOT contain the block.
  - Cache integration: with PromptCache wired in, a scripted test
    that calls _run_persona twice with identical inputs returns the
    same parsed result AND the LLM is called ONCE (use a counter
    mock). run_desk with cache= wires it through every persona call.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.agent.desk import engine as eng  # noqa: E402
from gold_desk.agent.desk.engine import (  # noqa: E402
    _complete_json_with_fallback,
    _format_lessons_block,
    _regime_tag,
    _run_persona,
    run_desk,
)
from gold_desk.agent.desk.personas import PERSONAS  # noqa: E402
from gold_desk.agent.memory import (  # noqa: E402
    MAX_GLOBAL,
    MAX_PER_SYMBOL,
    ReflectiveMemory,
    default_memory_dir,
)
from gold_desk.agent.budgets import Budget  # noqa: E402
from gold_desk.llm.prompt_cache import (  # noqa: E402
    PromptCache,
    default_cache_dir,
    prompt_key,
)
from gold_desk.llm.zen_client import (  # noqa: E402
    LLMInvalidJSON,
    LLMUnavailable,
)


# ============================================================ helpers / fakes

def _fake_llm_ok(messages, model, **kwargs):
    """A scripted LLM that returns a valid structured lesson dict."""
    return {
        "directional_call_correct": True,
        "alpha_pct": 1.5,
        "what_held": "Bull thesis on momentum held; RSI turning up",
        "what_failed": "Sentiment crowding warning was over-cautious",
        "lesson": "Mid-range RSI turning up is a reliable bull signal when vol is calm",
        "applicable_signals": ["technician:bullish", "fundamentalist:bullish"],
    }


def _fake_llm_counter(storage):
    """Returns a callable that records every call into ``storage`` list
    AND returns a valid analyst signal dict."""
    def fake(messages, model, **kwargs):
        storage.append((messages, model))
        return {
            "signal": "bullish",
            "confidence": 72,
            "thesis": "RSI 47.78 with MACD hist -0.151 turning up off range support.",
            "key_evidence": ["RSI 47.78 (analyst)", "MACD hist -0.151 (analyst)"],
        }
    return fake


# ============================================================ PromptCache tests

def test_prompt_key_deterministic():
    """Same inputs → same key (sha256 is deterministic)."""
    k1 = prompt_key("technician", "zen-1", "system-prompt", "user-msg")
    k2 = prompt_key("technician", "zen-1", "system-prompt", "user-msg")
    assert k1 == k2


def test_prompt_key_different_for_different_inputs():
    """Different persona_name OR model OR system OR user → different key."""
    k1 = prompt_key("technician", "zen-1", "system", "user")
    k2 = prompt_key("macro", "zen-1", "system", "user")        # diff persona
    k3 = prompt_key("technician", "zen-2", "system", "user")   # diff model
    k4 = prompt_key("technician", "zen-1", "system2", "user")  # diff system
    k5 = prompt_key("technician", "zen-1", "system", "user2")  # diff user
    assert len({k1, k2, k3, k4, k5}) == 5


def test_prompt_key_24_chars_sha256_prefix():
    """Key length matches AHF's 24-char sha256 prefix."""
    k = prompt_key("technician", "zen-1", "s", "u")
    assert len(k) == 24
    # hex chars only (sha256 hexdigest)
    assert all(c in "0123456789abcdef" for c in k)


def test_prompt_cache_get_returns_none_on_miss(tmp_path):
    """Miss → None (no file exists for the key)."""
    cache = PromptCache(tmp_path / "llm")
    assert cache.get("nonexistent-key-12345678901234") is None


def test_prompt_cache_put_then_get_round_trip(tmp_path):
    """put(key, record) then get(key) returns the persisted record
    with parse_ok=True and a created_at timestamp."""
    cache = PromptCache(tmp_path / "llm")
    key = prompt_key("technician", "zen-1", "s", "u")
    record = {
        "persona": "technician",
        "model_used": "zen-1",
        "response": None,
        "parsed": {"signal": "bullish", "confidence": 72},
    }
    cache.put(key, record)
    got = cache.get(key)
    assert got is not None
    assert got["parse_ok"] is True
    assert got["parsed"] == {"signal": "bullish", "confidence": 72}
    assert got["model_used"] == "zen-1"
    assert got["persona"] == "technician"
    assert "created_at" in got
    # created_at is ISO format with Z suffix
    assert got["created_at"].endswith("Z")


def test_prompt_cache_put_failure_persists_raw_response_and_error(tmp_path):
    """put_failure persists the raw_response + error as a parse_ok=False
    record — the AHF debug-trail concept with explicit failure records."""
    cache = PromptCache(tmp_path / "llm")
    key = prompt_key("technician", "zen-1", "s", "u")
    raw = "I am not a JSON object, just prose with maybe {something} but no real structure."
    cache.put_failure(key, raw, "LLMInvalidJSON: could not extract JSON object")
    got = cache.get(key)
    assert got is not None
    assert got["parse_ok"] is False
    assert got["response"] == raw
    assert "LLMInvalidJSON" in got["error"]
    assert "created_at" in got


def test_prompt_cache_put_failure_with_meta_preserves_persona_and_model(tmp_path):
    """put_failure_with_meta also persists persona + model_used for the
    audit trail."""
    cache = PromptCache(tmp_path / "llm")
    key = prompt_key("technician", "zen-1", "s", "u")
    cache.put_failure_with_meta(
        key, "not json", "parse error",
        persona="technician", model_used="zen-1")
    got = cache.get(key)
    assert got is not None
    assert got["persona"] == "technician"
    assert got["model_used"] == "zen-1"
    assert got["parse_ok"] is False
    assert got["response"] == "not json"
    assert got["error"] == "parse error"


def test_prompt_cache_ttl_stale_entry_returns_none(tmp_path):
    """When TTL is set, a record older than TTL returns None on get."""
    cache = PromptCache(tmp_path / "llm", ttl_seconds=1.0)
    key = prompt_key("technician", "zen-1", "s", "u")
    cache.put(key, {"parsed": {"signal": "bullish"}, "model_used": "zen-1",
                    "persona": "technician", "response": None})
    # immediately should hit
    assert cache.get(key) is not None
    # sleep past TTL
    time.sleep(1.1)
    assert cache.get(key) is None


def test_prompt_cache_ttl_none_means_forever(tmp_path):
    """When TTL is None (default), records never expire."""
    cache = PromptCache(tmp_path / "llm", ttl_seconds=None)
    key = prompt_key("technician", "zen-1", "s", "u")
    cache.put(key, {"parsed": {"signal": "bullish"}, "model_used": "zen-1",
                    "persona": "technician", "response": None})
    time.sleep(0.01)
    assert cache.get(key) is not None


def test_prompt_cache_thread_safe_concurrent_puts_different_keys(tmp_path):
    """Two threads putting different keys concurrently don't corrupt
    each other's file."""
    cache = PromptCache(tmp_path / "llm")
    k1 = prompt_key("technician", "m1", "s1", "u1")
    k2 = prompt_key("macro", "m2", "s2", "u2")
    errors = []

    def writer(key, val):
        try:
            cache.put(key, {"parsed": {"v": val}, "model_used": "m",
                            "persona": "p", "response": None})
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=(k1, "v1"))
    t2 = threading.Thread(target=writer, args=(k2, "v2"))
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors
    assert cache.get(k1)["parsed"] == {"v": "v1"}
    assert cache.get(k2)["parsed"] == {"v": "v2"}


def test_prompt_cache_thread_safe_concurrent_puts_same_key(tmp_path):
    """Two threads putting the SAME key concurrently don't corrupt the
    file — last writer wins, the file stays valid JSON."""
    cache = PromptCache(tmp_path / "llm")
    key = prompt_key("technician", "m", "s", "u")
    errors = []

    def writer(val):
        try:
            cache.put(key, {"parsed": {"v": val}, "model_used": "m",
                            "persona": "p", "response": None})
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(f"v{i}",))
               for i in range(5)]
    for t in threads: t.start()
    for t in threads: t.join()
    assert not errors
    # the file should be readable as valid JSON (no corruption)
    got = cache.get(key)
    assert got is not None
    assert got["parse_ok"] is True
    # one of the 5 writers won
    assert got["parsed"]["v"] in {f"v{i}" for i in range(5)}


def test_prompt_cache_evict_stale_returns_count(tmp_path):
    """evict_stale() returns the count of records evicted."""
    cache = PromptCache(tmp_path / "llm", ttl_seconds=1.0)
    for i in range(3):
        k = prompt_key(f"p{i}", "m", "s", f"u{i}")
        cache.put(k, {"parsed": {"i": i}, "model_used": "m",
                      "persona": f"p{i}", "response": None})
    time.sleep(1.1)
    n = cache.evict_stale()
    assert n == 3
    # all 3 are now gone
    for i in range(3):
        k = prompt_key(f"p{i}", "m", "s", f"u{i}")
        assert cache.get(k) is None


def test_prompt_cache_list_records_returns_audit_trail(tmp_path):
    """list_records() returns all records with their key + path for
    the audit trail / CLI inspection."""
    cache = PromptCache(tmp_path / "llm")
    cache.put(prompt_key("p1", "m", "s", "u1"),
              {"parsed": {"x": 1}, "model_used": "m",
               "persona": "p1", "response": None})
    cache.put_failure(prompt_key("p2", "m", "s", "u2"),
                      "raw text", "parse error")
    records = cache.list_records()
    assert len(records) == 2
    keys = [r.get("_key") for r in records]
    assert prompt_key("p1", "m", "s", "u1") in keys
    assert prompt_key("p2", "m", "s", "u2") in keys
    # the failure record shows up in the audit trail
    failures = [r for r in records if not r.get("parse_ok", True)]
    assert len(failures) == 1
    assert failures[0]["error"] == "parse error"


# ============================================================ ReflectiveMemory tests

def _make_memory(tmp_path, *, llm_call=None):
    """Helper: construct a ReflectiveMemory rooted at tmp_path/memory."""
    return ReflectiveMemory(tmp_path / "memory",
                             llm_call=llm_call or _fake_llm_ok)


def test_memory_store_decision_appends_pending_entry(tmp_path):
    """store_decision appends a pending entry to {symbol}.md. The tag
    includes [run_id | symbol | action | pending | regime=... |
    benchmark=...]."""
    mem = _make_memory(tmp_path)
    mem.store_decision(
        run_id="01HAAAAAAAAAAAAAAAAA1", symbol="AAPL", action="BUY",
        entry_price=149.0, stop_price=145.0, target_price=157.0,
        position_size_pct=0.05, conviction_label="HIGH",
        kill_criteria=["close < 145", "RSI < 40"],
        evidence_cited=[{"persona": "technician", "claim": "RSI 47.78",
                          "source": "analyst_outputs"}],
        transcript_ref="journal:run_id=AAPL",
        regime="trend:up|vol:calm", benchmark="SPY")
    symbol_path = tmp_path / "memory" / "AAPL.md"
    assert symbol_path.exists()
    text = symbol_path.read_text()
    # the pending tag is on the first line
    first_line = text.splitlines()[0]
    assert "01HAAAAAAAAAAAAAAAAA1" in first_line
    assert "AAPL" in first_line
    assert "BUY" in first_line
    assert "pending" in first_line
    assert "regime=trend:up|vol:calm" in first_line
    assert "benchmark=SPY" in first_line
    # the DECISION block follows with the structured fields
    assert "DECISION:" in text
    assert "149.0" in text  # entry_price


def test_memory_store_decision_idempotency_guard(tmp_path):
    """Two store_decision calls with the same (run_id, symbol, action)
    is a no-op — only one pending entry is appended. Mirror's TA's
    idempotency guard on (date, ticker, rating) but on run_id."""
    mem = _make_memory(tmp_path)
    mem.store_decision(run_id="01HBBB", symbol="AAPL", action="BUY",
                       kill_criteria=["k1"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    mem.store_decision(run_id="01HBBB", symbol="AAPL", action="BUY",
                       kill_criteria=["k2"], transcript_ref="t2",
                       regime="trend:up", benchmark="SPY")
    symbol_path = tmp_path / "memory" / "AAPL.md"
    text = symbol_path.read_text()
    # only one pending tag for this run_id
    pending_count = text.count("[01HBBB | AAPL | BUY | pending |")
    assert pending_count == 1


def test_memory_reflect_on_decision_produces_structured_lesson(tmp_path):
    """reflect_on_decision calls the LLM ONCE and produces a structured
    lesson with all 6 fields (the brief's edge over TA's 2-4 sentences
    of plain prose)."""
    calls = []
    def llm(messages, model, **kwargs):
        calls.append((messages, model))
        return _fake_llm_ok(messages, model, **kwargs)
    mem = _make_memory(tmp_path, llm_call=llm)
    run_id = "01HCCC1"
    mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                       kill_criteria=["k1"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    lesson = mem.reflect_on_decision(run_id, "AAPL",
                                      realized_5d_return=2.5,
                                      alpha_vs_benchmark=1.5,
                                      benchmark_name="SPY")
    # the LLM was called exactly once
    assert len(calls) == 1
    # the lesson has all 6 structured fields
    assert lesson is not None
    assert "directional_call_correct" in lesson
    assert "alpha_pct" in lesson
    assert "what_held" in lesson
    assert "what_failed" in lesson
    assert "lesson" in lesson
    assert "applicable_signals" in lesson
    # alpha_pct is the LLM's reported alpha
    assert lesson["alpha_pct"] == 1.5
    assert lesson["directional_call_correct"] is True
    assert isinstance(lesson["applicable_signals"], list)
    assert len(lesson["applicable_signals"]) >= 1


def test_memory_reflect_does_not_rerun_for_same_run_id(tmp_path):
    """A second reflect_on_decision for the same run_id is a no-op —
    returns the existing lesson, doesn't re-call the LLM. Mirrors
    TA's idempotency on (date, ticker) but on run_id."""
    calls = []
    def llm(messages, model, **kwargs):
        calls.append((messages, model))
        return _fake_llm_ok(messages, model, **kwargs)
    mem = _make_memory(tmp_path, llm_call=llm)
    run_id = "01HDDD1"
    mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                       kill_criteria=["k1"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    lesson1 = mem.reflect_on_decision(run_id, "AAPL",
                                       realized_5d_return=2.5,
                                       alpha_vs_benchmark=1.5,
                                       benchmark_name="SPY")
    lesson2 = mem.reflect_on_decision(run_id, "AAPL",
                                       realized_5d_return=2.5,
                                       alpha_vs_benchmark=1.5,
                                       benchmark_name="SPY")
    # only ONE LLM call across both reflect calls
    assert len(calls) == 1
    # both calls return the same lesson (the existing one)
    assert lesson1 == lesson2


def test_memory_recent_lessons_returns_k_most_recent(tmp_path):
    """recent_lessons(symbol, regime, k) returns up to k most-recent
    lessons for the symbol, sorted by run_id desc (ULID = monotonic)."""
    mem = _make_memory(tmp_path)
    # store 5 pending entries with sequential ULIDs (padded so lexical
    # sort = time sort). Format: "01HEEE" + 14-digit zero-padded i.
    for i in range(5):
        run_id = f"01HEEE{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k1"], transcript_ref="t",
                           regime="trend:up", benchmark="SPY")
        mem.reflect_on_decision(run_id, "AAPL",
                                 realized_5d_return=1.0 + i * 0.1,
                                 alpha_vs_benchmark=0.5 + i * 0.05,
                                 benchmark_name="SPY")
    lessons = mem.recent_lessons("AAPL", "trend:up", k=3)
    assert len(lessons) == 3
    # the most recent first (lexically highest ULID)
    assert lessons[0]["run_id"] == f"01HEEE{4:014d}"
    assert lessons[1]["run_id"] == f"01HEEE{3:014d}"
    assert lessons[2]["run_id"] == f"01HEEE{2:014d}"


def test_memory_recent_lessons_falls_back_to_same_regime_peers(tmp_path):
    """When the symbol has < k of its own lessons, fall back to same-
    regime peers from the global index. Mirrors TA's n_same + n_cross
    split but with structured regime tags."""
    mem = _make_memory(tmp_path)
    # AAPL has 1 lesson
    mem.store_decision(run_id="01HAAPL1", symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up|vol:calm", benchmark="SPY")
    mem.reflect_on_decision("01HAAPL1", "AAPL", 2.0, 1.0, "SPY")
    # MSFT has 2 lessons in the same regime
    mem.store_decision(run_id="01HMSFT1", symbol="MSFT", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up|vol:calm", benchmark="SPY")
    mem.reflect_on_decision("01HMSFT1", "MSFT", 1.5, 0.8, "SPY")
    mem.store_decision(run_id="01HMSFT2", symbol="MSFT", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up|vol:calm", benchmark="SPY")
    mem.reflect_on_decision("01HMSFT2", "MSFT", 1.2, 0.5, "SPY")
    # ask for k=3 lessons for AAPL → 1 AAPL + 2 MSFT peers
    lessons = mem.recent_lessons("AAPL", "trend:up|vol:calm", k=3)
    assert len(lessons) == 3
    syms = [L["symbol"] for L in lessons]
    assert "AAPL" in syms
    assert "MSFT" in syms
    # MSFT peer count = 2
    assert syms.count("MSFT") == 2


def test_memory_recent_lessons_empty_when_no_lessons(tmp_path):
    """Cold start — when no reflected lessons exist for the symbol OR
    any same-regime peer, recent_lessons returns []."""
    mem = _make_memory(tmp_path)
    lessons = mem.recent_lessons("AAPL", "trend:up|vol:calm", k=3)
    assert lessons == []


def test_memory_recent_lessons_returns_only_symbol_specific_when_enough(tmp_path):
    """When the symbol has ≥ k of its own lessons, don't pull regime
    peers — only symbol-specific lessons."""
    mem = _make_memory(tmp_path)
    for i in range(3):
        run_id = f"01HAAPL{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k"], transcript_ref="t",
                           regime="trend:up|vol:calm", benchmark="SPY")
        mem.reflect_on_decision(run_id, "AAPL", 1.0 + i * 0.1,
                                 0.5 + i * 0.05, "SPY")
    # add a peer in the same regime
    mem.store_decision(run_id="01HMSFT1", symbol="MSFT", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up|vol:calm", benchmark="SPY")
    mem.reflect_on_decision("01HMSFT1", "MSFT", 1.5, 0.8, "SPY")
    # ask for k=3 → only the 3 AAPL lessons, no MSFT
    lessons = mem.recent_lessons("AAPL", "trend:up|vol:calm", k=3)
    assert len(lessons) == 3
    assert all(L["symbol"] == "AAPL" for L in lessons)


def test_memory_cap_per_symbol_rotation(tmp_path):
    """When the per-symbol file exceeds MAX_PER_SYMBOL reflected
    entries, the oldest are rotated out. Pending entries are always
    kept (they represent un-processed Phase B work)."""
    mem = _make_memory(tmp_path)
    # store MAX_PER_SYMBOL + 5 reflected entries (alternating pending →
    # reflect immediately so they all become reflected)
    last_run_id = None
    for i in range(MAX_PER_SYMBOL + 5):
        run_id = f"01HFF{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k"], transcript_ref="t",
                           regime="trend:up", benchmark="SPY")
        mem.reflect_on_decision(run_id, "AAPL", 1.0 + i * 0.01,
                                 0.5 + i * 0.005, "SPY")
        last_run_id = run_id
    # reload the symbol file and count reflected entries
    symbol_path = tmp_path / "memory" / "AAPL.md"
    text = symbol_path.read_text()
    blocks = [b for b in text.split("\n\n<!-- ENTRY_END -->\n\n") if b.strip()]
    reflected_count = sum(1 for b in blocks if "| reflected |" in b.splitlines()[0])
    pending_count = sum(1 for b in blocks if "| pending |" in b.splitlines()[0])
    # rotation drops the oldest reflected entries to keep under cap
    assert reflected_count <= MAX_PER_SYMBOL
    # all pending entries are kept (none in this test — all were reflected)
    assert pending_count == 0
    # the MOST RECENT reflected entry survived (rotation drops oldest,
    # keeps the most-recent MAX_PER_SYMBOL). The last block should be
    # the last run_id we stored (i=MAX_PER_SYMBOL+4).
    last_block = blocks[-1]
    assert last_run_id in last_block.splitlines()[0], (
        f"most-recent run_id {last_run_id} should be in the last "
        f"block's tag line; got {last_block.splitlines()[0]!r}")
    # the OLDEST reflected entry (i=0) was dropped (rotated out)
    oldest_run_id = f"01HFF{0:014d}"
    oldest_survived = any(oldest_run_id in b.splitlines()[0] for b in blocks)
    assert not oldest_survived, (
        f"oldest run_id {oldest_run_id} should have been rotated out; "
        f"found it in the file")


def test_memory_pending_entries_survive_rotation(tmp_path):
    """Pending entries are never rotated out even when the per-symbol
    file exceeds MAX_PER_SYMBOL reflected entries."""
    mem = _make_memory(tmp_path)
    # store MAX_PER_SYMBOL + 5 reflected entries first
    for i in range(MAX_PER_SYMBOL + 5):
        run_id = f"01HGG{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k"], transcript_ref="t",
                           regime="trend:up", benchmark="SPY")
        mem.reflect_on_decision(run_id, "AAPL", 1.0, 0.5, "SPY")
    # now store 3 pending entries (no reflection)
    for i in range(3):
        run_id = f"01HPEN{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k"], transcript_ref="t",
                           regime="trend:up", benchmark="SPY")
    symbol_path = tmp_path / "memory" / "AAPL.md"
    text = symbol_path.read_text()
    blocks = [b for b in text.split("\n\n<!-- ENTRY_END -->\n\n") if b.strip()]
    pending_count = sum(1 for b in blocks if "| pending |" in b.splitlines()[0])
    # all 3 pending entries are kept even after rotation
    assert pending_count == 3


def test_memory_lesson_directional_call_correct_for_buy_positive_return(tmp_path):
    """The LLM's directional_call_correct is preserved (BUY + positive
    raw return = correct directional call)."""
    mem = _make_memory(tmp_path)
    run_id = "01HYYY1"
    mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    lesson = mem.reflect_on_decision(run_id, "AAPL",
                                      realized_5d_return=2.5,
                                      alpha_vs_benchmark=1.5,
                                      benchmark_name="SPY")
    assert lesson is not None
    assert lesson["directional_call_correct"] is True
    assert lesson["alpha_pct"] == 1.5


def test_memory_applicable_signals_field_populated(tmp_path):
    """The applicable_signals list is populated with persona:signal
    strings the PM can use to weight or discount next time."""
    mem = _make_memory(tmp_path)
    run_id = "01HZZZ1"
    mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    lesson = mem.reflect_on_decision(run_id, "AAPL", 2.0, 1.0, "SPY")
    assert isinstance(lesson["applicable_signals"], list)
    assert len(lesson["applicable_signals"]) >= 1
    # at least one entry follows the persona:signal shape
    assert any(":" in s for s in lesson["applicable_signals"])


def test_memory_reflect_on_decision_returns_none_when_llm_fails(tmp_path):
    """If the LLM call fails, reflect_on_decision returns None and
    leaves the entry pending (so the operator can re-run)."""
    def llm_fail(messages, model, **kwargs):
        raise LLMUnavailable("zen down")
    mem = _make_memory(tmp_path, llm_call=llm_fail)
    run_id = "01HFAIL1"
    mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up", benchmark="SPY")
    lesson = mem.reflect_on_decision(run_id, "AAPL", 2.0, 1.0, "SPY")
    assert lesson is None
    # the entry stays pending — verify by re-running reflect (which would
    # no-op if it had been reflected)
    # we can check by reading the symbol file directly
    symbol_path = tmp_path / "memory" / "AAPL.md"
    text = symbol_path.read_text()
    assert "| pending |" in text
    assert "| reflected |" not in text


def test_memory_reflect_on_decision_returns_none_for_unknown_run_id(tmp_path):
    """reflect_on_decision for a run_id that doesn't exist returns None."""
    mem = _make_memory(tmp_path)
    lesson = mem.reflect_on_decision("01HNOSUCH", "AAPL", 2.0, 1.0, "SPY")
    assert lesson is None


def test_memory_index_md_appended_for_every_store_decision(tmp_path):
    """Every store_decision appends a JSON-line entry to index.md so the
    regime-peer fallback can find this entry without scanning every
    symbol file. JSON-lines (not pipe-separated) so the regime tag's
    embedded pipes (e.g. ``trend:up|vol:calm``) don't break parsing."""
    mem = _make_memory(tmp_path)
    mem.store_decision(run_id="01HIDX1", symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:up|vol:calm", benchmark="SPY")
    mem.store_decision(run_id="01HIDX2", symbol="MSFT", action="SELL",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="trend:down|vol:expanding", benchmark="SPY")
    index_path = tmp_path / "memory" / "index.md"
    assert index_path.exists()
    text = index_path.read_text()
    lines = [L for L in text.splitlines() if L.strip()]
    # exactly 2 JSON lines
    assert len(lines) == 2
    import json as _json
    e1 = _json.loads(lines[0])
    e2 = _json.loads(lines[1])
    assert e1["run_id"] == "01HIDX1"
    assert e1["symbol"] == "AAPL"
    assert e1["regime"] == "trend:up|vol:calm"  # pipes preserved
    assert e1["status"] == "pending"
    assert e2["run_id"] == "01HIDX2"
    assert e2["symbol"] == "MSFT"
    assert e2["regime"] == "trend:down|vol:expanding"
    assert e2["status"] == "pending"


# ============================================================ _format_lessons_block + _regime_tag

def test_format_lessons_block_empty_when_no_lessons():
    """Cold start — empty list → empty block (no padding)."""
    assert _format_lessons_block([]) == ""


def test_format_lessons_block_renders_each_lesson_in_brief_format():
    """Each lesson renders as: - [date | action | alpha +X.XX%]: lesson
    (the brief's exact format)."""
    lessons = [
        {"date": "01HAA", "action": "BUY", "alpha_pct": 2.30,
         "lesson": "RSI mid-range turning up holds; crowding is the swing risk."},
        {"date": "01HBB", "action": "SELL", "alpha_pct": -1.50,
         "lesson": "Sentiment exhaustion flag fires before RSI extreme."},
    ]
    block = _format_lessons_block(lessons)
    assert "RECENT LESSONS (apply to this decision):" in block
    assert "- [01HAA | BUY | alpha +2.30%]: " in block
    assert "- [01HBB | SELL | alpha -1.50%]: " in block
    assert "RSI mid-range turning up holds" in block
    assert "Sentiment exhaustion flag fires" in block


def test_format_lessons_block_handles_missing_alpha():
    """Missing alpha_pct → 0.00% (defensive — never break the block)."""
    lessons = [{"date": "01HCC", "action": "HOLD", "lesson": "no edge"}]
    block = _format_lessons_block(lessons)
    assert "alpha +0.00%" in block


def test_regime_tag_extracts_from_verified_snapshot_regime_labels():
    """The regime tag is the pipe-joined sorted-keys of the snapshot's
    regime_labels dict."""
    snap = {"ok": True,
            "regime_labels": {"trend": "up", "vol": "calm",
                                "momentum": "turning"}}
    tag = _regime_tag(snap)
    # sorted keys → momentum, trend, vol
    assert "momentum:turning" in tag
    assert "trend:up" in tag
    assert "vol:calm" in tag
    assert tag.count("|") == 2  # 3 parts joined by |


def test_regime_tag_returns_unknown_for_empty_snapshot():
    """No regime_labels → 'unknown'."""
    assert _regime_tag({"ok": True, "regime_labels": {}}) == "unknown"
    assert _regime_tag({"ok": True}) == "unknown"
    assert _regime_tag(None) == "unknown"


# ============================================================ PM re-injection (via _run_pm_debate)

def _patch_context_no_inst(monkeypatch):
    """Patch the markets-plane calls so run_desk runs offline. Same as
    test_debate._patch_context but without the institutional slice."""
    monkeypatch.setattr(eng, "fetch_detail",
                        lambda s, d: {"ok": True, "symbol": "AAPL",
                                       "name": "Apple", "sector": "tech",
                                       "price": 149.0, "change_pct": 1.0,
                                       "range_5d_change_pct": 3.4,
                                       "bars": [], "news": {"ok": True,
                                                            "items": []}})
    monkeypatch.setattr(eng, "fetch_daily_bars",
                        lambda s, data_root=None: [])
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": True, "as_of": "now",
                                   "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": True, "as_of": "now",
                                   "gainers": [], "losers": []})
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})


def _fake_complete_json_for_debate(calls=None):
    """Scripted complete_json that returns the test_debate replies."""
    from tests.test_debate import (
        ANALYST_REPLIES, BULL_REPLY, BEAR_REPLY, MANAGER_REPLY,
        TRADER_REPLY, DEBATOR_REPLIES, PM_DEBATE_REPLY, _MARKERS)
    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        key = next((k for marker, k in _MARKERS.items()
                    if system.startswith(marker)), None)
        if calls is not None:
            calls.append((key, model))
        if key == "pm":
            return dict(PM_DEBATE_REPLY)
        if key == "trader":
            return dict(TRADER_REPLY)
        if key == "research_manager":
            return dict(MANAGER_REPLY)
        if key == "bull_researcher":
            return dict(BULL_REPLY)
        if key == "bear_researcher":
            return dict(BEAR_REPLY)
        if key in ("aggressive_debator", "conservative_debator",
                   "neutral_debator"):
            return dict(DEBATOR_REPLIES[key])
        return dict(ANALYST_REPLIES[key])
    return fake


def test_pm_reinjects_lessons_block_when_lessons_exist(monkeypatch, tmp_path):
    """When memory has ≥1 reflected lesson for the symbol, the PM's
    user_msg contains the 'RECENT LESSONS' block. Verified by
    capturing the PM's user_msg via the cache key derivation (the
    cache key is sha256(persona_name | model | system | user_msg),
    so the SAME user_msg → SAME key → identical cached parsed result
    on a second call)."""
    _patch_context_no_inst(monkeypatch)
    # build a memory log with 1 reflected AAPL lesson
    mem = _make_memory(tmp_path)
    mem.store_decision(run_id="01HPM1111", symbol="AAPL", action="BUY",
                       kill_criteria=["k"], transcript_ref="t",
                       regime="unknown", benchmark="SPY")
    mem.reflect_on_decision("01HPM1111", "AAPL", 2.5, 1.5, "SPY")
    # capture the PM's user_msg via a custom complete_json that
    # records the messages
    captured_user_msgs = []
    def fake_complete_json(messages, model, **kwargs):
        sys_prompt = messages[0]["content"]
        if "You are The Portfolio Manager" in sys_prompt:
            captured_user_msgs.append(messages[-1]["content"])
        # delegate to the scripted replies from test_debate
        return _fake_complete_json_for_debate()(messages, model, **kwargs)
    monkeypatch.setattr(eng, "complete_json", fake_complete_json)
    out = run_desk("AAPL", data_root=tmp_path, memory=mem)
    assert out["ok"] is True
    # the PM's user_msg was captured exactly once
    assert len(captured_user_msgs) == 1
    pm_user = captured_user_msgs[0]
    assert "RECENT LESSONS (apply to this decision):" in pm_user
    assert "alpha" in pm_user.lower()
    assert "- [" in pm_user


def test_pm_skips_lessons_block_when_no_lessons(monkeypatch, tmp_path):
    """Cold start — when memory has NO lessons, the PM's user_msg does
    NOT contain the 'RECENT LESSONS' block (don't pad the prompt)."""
    _patch_context_no_inst(monkeypatch)
    mem = _make_memory(tmp_path)   # empty — no lessons
    captured_user_msgs = []
    def fake_complete_json(messages, model, **kwargs):
        sys_prompt = messages[0]["content"]
        if "You are The Portfolio Manager" in sys_prompt:
            captured_user_msgs.append(messages[-1]["content"])
        return _fake_complete_json_for_debate()(messages, model, **kwargs)
    monkeypatch.setattr(eng, "complete_json", fake_complete_json)
    out = run_desk("AAPL", data_root=tmp_path, memory=mem)
    assert out["ok"] is True
    assert len(captured_user_msgs) == 1
    pm_user = captured_user_msgs[0]
    assert "RECENT LESSONS" not in pm_user


def test_pm_reinjects_at_most_k3_lessons(monkeypatch, tmp_path):
    """The PM re-injects at most k=3 lessons (the brief: k=3). Even when
    memory has 5+ lessons for the symbol, only 3 appear."""
    _patch_context_no_inst(monkeypatch)
    mem = _make_memory(tmp_path)
    for i in range(5):
        run_id = f"01HK{i:014d}"
        mem.store_decision(run_id=run_id, symbol="AAPL", action="BUY",
                           kill_criteria=["k"], transcript_ref="t",
                           regime="unknown", benchmark="SPY")
        mem.reflect_on_decision(run_id, "AAPL", 1.0 + i * 0.1,
                                 0.5 + i * 0.05, "SPY")
    captured_user_msgs = []
    def fake_complete_json(messages, model, **kwargs):
        sys_prompt = messages[0]["content"]
        if "You are The Portfolio Manager" in sys_prompt:
            captured_user_msgs.append(messages[-1]["content"])
        return _fake_complete_json_for_debate()(messages, model, **kwargs)
    monkeypatch.setattr(eng, "complete_json", fake_complete_json)
    out = run_desk("AAPL", data_root=tmp_path, memory=mem)
    assert out["ok"] is True
    pm_user = captured_user_msgs[0]
    # count the "- [" bullet prefix — should be exactly 3
    bullet_count = pm_user.count("\n- [")
    assert bullet_count == 3, (
        f"PM should re-inject at most 3 lessons; got {bullet_count}")


# ============================================================ cache integration

def test_run_persona_caches_success_and_skips_llm_on_second_call(
        monkeypatch, tmp_path):
    """With PromptCache wired in, calling _run_persona twice with
    identical inputs returns the same parsed result AND the LLM is
    called ONCE (the second call short-circuits via cache hit)."""
    # counter mock — counts complete_json calls
    calls = []
    monkeypatch.setattr(eng, "complete_json", _fake_llm_counter(calls))
    # cache rooted in tmp_path/llm
    cache = PromptCache(tmp_path / "llm")
    persona = PERSONAS[0]   # technician
    user_msg = "Briefing for AAPL (Apple Inc), sector tech.\n\nTest user msg."
    models = ["zen-test-model"]
    budget = Budget(tmp_path, max_steps=20, max_minutes=10)
    # first call — miss, calls LLM, caches parsed
    out1 = _run_persona(persona, user_msg, models, budget, 60,
                        2400, None, cache=cache)
    assert out1["abstained"] is False
    assert out1["signal"] == "bullish"
    assert len(calls) == 1   # LLM was called once
    # second call — HIT (same inputs → same cache key), no LLM call
    # need a fresh budget because the first call consumed a step
    budget2 = Budget(tmp_path, max_steps=20, max_minutes=10)
    out2 = _run_persona(persona, user_msg, models, budget2, 60,
                        2400, None, cache=cache)
    assert out2["abstained"] is False
    assert out2["signal"] == "bullish"
    # still only 1 LLM call across both _run_persona invocations
    assert len(calls) == 1, (
        f"Second _run_persona should have hit the cache and skipped "
        f"the LLM call; got {len(calls)} calls")


def test_run_persona_persists_failure_on_llm_unavailable(
        monkeypatch, tmp_path):
    """When the LLM raises LLMUnavailable, _run_persona abstains AND
    the PromptCache persists a failure record (parse_ok=False) for
    the audit trail — the AHF debug-trail concept."""
    def llm_fail(messages, model, **kwargs):
        raise LLMUnavailable("zen transport down")
    monkeypatch.setattr(eng, "complete_json", llm_fail)
    cache = PromptCache(tmp_path / "llm")
    persona = PERSONAS[0]   # technician
    user_msg = "test user msg"
    models = ["zen-test-model"]
    budget = Budget(tmp_path, max_steps=20, max_minutes=10)
    out = _run_persona(persona, user_msg, models, budget, 60,
                       2400, None, cache=cache)
    # persona abstained
    assert out["abstained"] is True
    assert "zen transport down" in out["thesis"]
    # the failure record was persisted to the cache
    key = prompt_key(persona.name, models[0], persona.system, user_msg)
    record = cache.get(key)
    assert record is not None
    assert record["parse_ok"] is False
    assert record["persona"] == persona.name
    assert "zen transport down" in record["error"]


def test_run_persona_persists_failure_on_llm_invalid_json(
        monkeypatch, tmp_path):
    """When the LLM returns text that can't be parsed as JSON,
    _run_persona abstains AND the PromptCache persists the raw response
    + the parse error. The LLMInvalidJSON now carries a raw_response
    attribute (R2-4 change to zen_client.py) so the audit trail gets
    the unparseable text."""
    def llm_bad(messages, model, **kwargs):
        err = LLMInvalidJSON("could not extract JSON object")
        err.raw_response = "This is prose, not JSON. {almost but not really}"
        raise err
    monkeypatch.setattr(eng, "complete_json", llm_bad)
    cache = PromptCache(tmp_path / "llm")
    persona = PERSONAS[0]
    user_msg = "test user msg"
    models = ["zen-test-model"]
    budget = Budget(tmp_path, max_steps=20, max_minutes=10)
    out = _run_persona(persona, user_msg, models, budget, 60,
                       2400, None, cache=cache)
    assert out["abstained"] is True
    key = prompt_key(persona.name, models[0], persona.system, user_msg)
    record = cache.get(key)
    assert record is not None
    assert record["parse_ok"] is False
    assert record["response"] == "This is prose, not JSON. {almost but not really}"
    assert "could not extract JSON object" in record["error"]


def test_run_desk_with_cache_wires_through_every_persona(monkeypatch, tmp_path):
    """run_desk(cache=...) wires the cache through every _run_persona
    call (6 analysts + 2 researchers + 1 manager + 1 trader + 3
    debators + 1 PM = 14 LLM calls on the first run). A second
    run_desk with the same cache should hit on every key (0 LLM
    calls) and return the same PM decision."""
    _patch_context_no_inst(monkeypatch)
    # R2-4 fix: freeze _now_iso so all 14 cache keys are stable across
    # both runs. Without this, the PM (which runs last, after 13
    # parallel personas return) can cross a wall-clock second boundary
    # and get a different _now_iso() in its user_msg → cache key drifts
    # → PM misses the cache. The 13 parallel personas stay within the
    # same second and hit; the PM (serial, after the parallel block) is
    # the one that crosses the boundary. Freezing makes the cache key
    # deterministic for the whole 14-call flow.
    monkeypatch.setattr(eng, "_now_iso",
                        lambda: "2026-08-26T00:00:00Z")
    calls_run1 = []
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json_for_debate(calls=calls_run1))
    cache = PromptCache(tmp_path / "llm")
    out1 = run_desk("AAPL", data_root=tmp_path, cache=cache)
    assert out1["ok"] is True
    # 14 LLM calls on the first run (cache cold)
    assert len(calls_run1) == 14
    # second run — every persona + PM should hit the cache (0 LLM calls)
    calls_run2 = []
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json_for_debate(calls=calls_run2))
    out2 = run_desk("AAPL", data_root=tmp_path, cache=cache)
    assert out2["ok"] is True
    # the second run made 0 LLM calls — every prompt hit the cache
    assert len(calls_run2) == 0, (
        f"Second run_desk should have hit the cache on every persona; "
        f"got {len(calls_run2)} LLM calls")
    # the PM's decision is identical across both runs (cached parsed
    # dict flows through the same mechanical validators)
    assert out1["pm"]["action"] == out2["pm"]["action"]
    assert out1["pm"]["conviction_label"] == out2["pm"]["conviction_label"]
    assert out1["pm"]["risk_reward_ratio"] == out2["pm"]["risk_reward_ratio"]


def test_run_desk_with_memory_stores_pending_decision_after_pm_returns(
        monkeypatch, tmp_path):
    """run_desk(memory=...) stores the PM's decision as a pending entry
    on the memory log AFTER the PM returns. Phase B (reflection) is
    a separate CLI subcommand; the storage path is wired here."""
    _patch_context_no_inst(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json_for_debate())
    mem = _make_memory(tmp_path)
    out = run_desk("AAPL", data_root=tmp_path, memory=mem)
    assert out["ok"] is True
    # the PM's BUY decision was stored as a pending entry
    symbol_path = tmp_path / "memory" / "AAPL.md"
    assert symbol_path.exists()
    text = symbol_path.read_text()
    assert "| pending |" in text
    assert "AAPL" in text
    assert "BUY" in text
    # the stored entry carries the PM's artifact fields (entry, stop,
    # target, conviction_label, kill_criteria)
    assert "149.0" in text  # entry_price
    assert "145.0" in text  # stop_price
    assert "157.0" in text  # target_price
    assert "HIGH" in text   # conviction_label
    # the index.md was updated too
    index_path = tmp_path / "memory" / "index.md"
    assert index_path.exists()
    assert "AAPL" in index_path.read_text()


def test_default_cache_dir_resolves_sibling_of_data_root():
    """default_cache_dir(data_root) returns <parent>/cache/llm — a
    sibling of data_root so the cache lives at the repo root next
    to data/."""
    p = default_cache_dir("/abs/path/data")
    assert p == Path("/abs/path/cache/llm")
    p2 = default_cache_dir("data")
    assert p2 == Path("cache/llm")


def test_default_memory_dir_resolves_sibling_of_data_root():
    """default_memory_dir(data_root) returns <parent>/cache/memory."""
    p = default_memory_dir("/abs/path/data")
    assert p == Path("/abs/path/cache/memory")
    p2 = default_memory_dir("data")
    assert p2 == Path("cache/memory")


def test_default_cache_and_memory_share_cache_root(tmp_path):
    """The cache root is shared between memory/ and llm/ — both live
    under <repo>/cache/."""
    cache_dir = default_cache_dir(tmp_path / "data")
    memory_dir = default_memory_dir(tmp_path / "data")
    assert cache_dir.parent == memory_dir.parent
    assert cache_dir.parent.name == "cache"
    assert cache_dir.name == "llm"
    assert memory_dir.name == "memory"
