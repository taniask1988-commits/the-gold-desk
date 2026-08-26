"""Journal helper for the agent sidecar: builds a Journal with the same
constitution hash discipline as the desk (the hash file already exists in
the data root from any demo/validate run; when absent we write the agent
marker hash — the journal format is identical, only the provenance stamp
differs, and read_events ignores it)."""
from __future__ import annotations

from pathlib import Path

from ..events import Journal

AGENT_JOURNAL_HASH = "agent-sidecar-v1"


def default_journal(data_root: str | Path = "data") -> Journal:
    """A Journal usable by the sidecar in any data root (existing or new)."""
    return Journal(data_root, AGENT_JOURNAL_HASH)
