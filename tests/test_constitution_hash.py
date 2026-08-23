"""§16 row 12 — constitution hash: every journal event carries the content
hash of the constitution that governed it; the canonical file's hash matches
what validation writes to data/hashes/constitution.sha256."""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.constitution import load_constitution  # noqa: E402
from gold_desk.events import Journal  # noqa: E402


def test_event_hash_matches_constitution_file(tmp_path):
    c = load_constitution(REPO / "trading_constitution.yaml")
    journal = Journal(tmp_path, c.content_hash)
    journal.emit("ProcessStart", {"note": "hash check"})
    events = Journal.read_events(tmp_path)
    assert events
    assert all(e["constitution_hash"] == c.content_hash for e in events)
    written = (tmp_path / "hashes" / "constitution.sha256").read_text().strip()
    assert written == c.content_hash


def test_hash_changes_when_numbers_change(tmp_path):
    a = load_constitution(REPO / "trading_constitution.yaml")
    import shutil
    copy = tmp_path / "copy.yaml"
    shutil.copy(REPO / "trading_constitution.yaml", copy)
    text = copy.read_text().replace("max_bar_lag_minutes: 5",
                                    "max_bar_lag_minutes: 4")
    copy.write_text(text)
    b = load_constitution(copy)
    assert a.file_hash != b.file_hash
    assert a.content_hash != b.content_hash


def test_demo_overlay_changes_hash_and_flags_demo(tmp_path):
    import shutil
    copy = tmp_path / "copy.yaml"
    shutil.copy(REPO / "trading_constitution.yaml", copy)
    base = load_constitution(copy)
    demo = load_constitution(copy, overlay_path=REPO / "config" / "demo.yaml")
    assert demo.demo and not base.demo
    assert demo.content_hash != base.content_hash
    assert demo.trade_capable and not base.trade_capable
