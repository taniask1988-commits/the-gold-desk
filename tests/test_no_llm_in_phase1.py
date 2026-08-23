"""§16 rows 5+9+10 — Phase 1 purity and fail-closed behaviour:

  - veto.py raises if touched before Phase 2
  - no live-path module imports an LLM SDK or calls the veto eagerly
  - a full synthetic run emits VetoDecision=ENDORSE_BYPASS, never LLM codes
  - news/calendar feed down -> NEWS_UNAVAILABLE, no ticket
  - canonical constitution BLOCKED -> CONSTITUTION_BLOCKED, no ticket
"""
from __future__ import annotations

import ast
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gold_desk import veto  # noqa: E402
from gold_desk.constitution import load_constitution  # noqa: E402
from gold_desk.events import Journal  # noqa: E402

LIVE_MODULES = [
    "orchestrator", "risk_gate", "sizing", "filters", "ticket",
    "telegram_io", "context_pack", "eod", "recover", "replay",
]
LLM_SDKS = ("openai", "anthropic", "zhipuai", "cohere", "google.generativeai",
            "langchain", "llama_index", "zai")
CONFTEST = Path(__file__).parent / "conftest.py"


def test_veto_stub_raises():
    with pytest.raises(veto.VetoNotAvailable):
        veto.llm_veto({})


def test_no_llm_sdk_imports_in_live_path():
    for name in LIVE_MODULES:
        src_file = REPO / "src" / "gold_desk" / f"{name}.py"
        tree = ast.parse(src_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            else:
                continue
            for m in mods:
                root = m.split(".")[0].lower()
                assert root not in LLM_SDKS, f"{name}.py imports {m}"


def test_veto_import_is_lazy_and_phase_gated():
    """The ONLY veto/LLM import in the orchestrator must sit inside the
    `phase >= 2` branch (the zen veto from gold_desk.llm)."""
    src = (REPO / "src" / "gold_desk" / "orchestrator.py").read_text()
    tree = ast.parse(src)
    veto_imports = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module and ("veto" in node.module or "llm" in node.module)
    ]
    assert veto_imports, "expected the phase-2 veto import to exist"
    inside_phase2 = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test_src = ast.unparse(node.test)
            if "phase" in test_src and "2" in test_src:
                for sub in ast.walk(node):
                    if sub in veto_imports:
                        inside_phase2 = True
    assert inside_phase2, "all veto/llm imports must live in the phase>=2 branch"


def test_llm_package_not_imported_by_live_modules():
    """No live-path module (other than the orchestrator's phase-2 branch)
    may import the llm package."""
    for name in [m for m in LIVE_MODULES if m != "orchestrator"] + [
        "data/bars", "data/quality", "data/calendar", "data/news",
        "setup/engine", "setup/spec", "features/indicators",
    ]:
        src_file = REPO / "src" / "gold_desk" / f"{name}.py"
        tree = ast.parse(src_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert ".llm" not in node.module and not node.module.startswith("gold_desk.llm"), \
                    f"{name}.py imports {node.module}"
            if isinstance(node, ast.Import):
                for a in node.names:
                    assert "llm" not in a.name, f"{name}.py imports {a.name}"


def _wire(constitution, tmp_path, source=None, days=3):
    from gold_desk.account import PaperAccountStore
    from gold_desk.data.bars import SyntheticBarSource, SyntheticConfig
    from gold_desk.orchestrator import Orchestrator
    from gold_desk.telegram_io import TelegramIO

    source = source or SyntheticBarSource(
        SyntheticConfig(seed=7), start=datetime(2026, 6, 1, tzinfo=timezone.utc),
        days=days)
    journal = Journal(tmp_path, constitution.content_hash, demo=constitution.demo)
    telegram = TelegramIO(journal, printer=lambda s: None)
    accounts = PaperAccountStore(tmp_path, 10000.0, journal)
    orch = Orchestrator(constitution, source, journal, telegram, accounts,
                        data_root=str(tmp_path))
    bars = source.bars_up_to(datetime(2026, 6, 1, tzinfo=timezone.utc) +
                             timedelta(days=days + 2), 10**9)
    codes = [orch.on_bar_close(b) for b in bars]
    return orch, codes, tmp_path


def test_full_run_is_phase1_bypass_never_llm(tmp_path):
    from conftest import make_constitution
    _, codes, root = _wire(make_constitution(), tmp_path)
    assert codes and all(codes)
    events = Journal.read_events(root)
    veto_events = [e for e in events if e["kind"] == "VetoDecision"]
    assert veto_events, "veto decisions must be journalled even in Phase 1"
    assert all(e["payload"]["decision"] == "ENDORSE_BYPASS" for e in veto_events)
    for e in events:
        assert e.get("reason_code") not in ("LLM_VETO", "LLM_INVALID_JSON",
                                            "LLM_UNAVAILABLE")


def test_news_feed_down_fails_closed(tmp_path):
    from conftest import make_constitution
    from gold_desk.data.bars import SyntheticBarSource, SyntheticConfig

    class BoomCalendar(SyntheticBarSource):
        def calendar(self, instant):
            raise RuntimeError("calendar feed down")

    source = BoomCalendar(SyntheticConfig(seed=3),
                          start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                          days=2)
    _, codes, root = _wire(make_constitution(), tmp_path, source=source, days=2)
    assert "NEWS_UNAVAILABLE" in codes
    events = Journal.read_events(root)
    assert not [e for e in events if e["kind"] == "TicketEvent"]


def test_canonical_constitution_fails_closed(tmp_path):
    constitution = load_constitution(REPO / "trading_constitution.yaml")
    assert not constitution.trade_capable
    _, codes, root = _wire(constitution, tmp_path)
    assert codes and set(codes) == {"CONSTITUTION_BLOCKED"}
    events = Journal.read_events(root)
    assert not [e for e in events if e["kind"] == "TicketEvent"]
    assert not [e for e in events if e["kind"] == "SetupCandidate"]
