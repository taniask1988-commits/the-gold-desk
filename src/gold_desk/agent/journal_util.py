"""Journal helper for the agent sidecar: builds a Journal with the same
constitution hash discipline as the desk.

D-005 (GAUNTLET 8): the sidecar used to stamp a marker string into
``constitution_hash`` — the v1 sidecar tag. That string flows into
every event the sidecar emits, and the overview surface used to read
the hash from the LAST event — so a single agent run masqueraded that
tag as the constitution hash in the UI. The sidecar now derives the
REAL constitution content hash through the same loader the desk uses
(the repo's trading_constitution.yaml), which is exactly what a
demo/validate run journals. Only when that file is genuinely
unreachable does it fall back to a marker that is deliberately not a
hex digest, so no surface can mistake it for a real hash.
"""
from __future__ import annotations

from pathlib import Path

from ..events import Journal

REPO_ROOT = Path(__file__).resolve().parents[3]
CONSTITUTION_YAML = REPO_ROOT / "trading_constitution.yaml"

# Fallback ONLY when the constitution YAML is absent (broken/partial
# install): not a hex digest, not version-shaped — nothing downstream
# can confuse it with a real hash (D-005).
NO_CONSTITUTION_HASH = "constitution-file-not-found"


def real_constitution_hash() -> str:
    """The constitution's content hash via the SAME loader the desk's
    demo/validate path uses. Fail-soft: a missing/unparsable YAML yields
    the honest not-found marker, never a fabricated digest."""
    try:
        from ..constitution import load_constitution
        return load_constitution(CONSTITUTION_YAML).content_hash
    except Exception:  # noqa: BLE001 — fail-soft provenance stamp
        return NO_CONSTITUTION_HASH


def default_journal(data_root: str | Path = "data") -> Journal:
    """A Journal usable by the sidecar in any data root (existing or
    new), stamped with the REAL constitution hash (D-005).

    Root already carries a hash file (any demo/validate/watch run) →
    PRESERVE it: that root's own provenance wins (a demo-overlay root's
    content hash legitimately differs from the canonical YAML's).
    Fresh root → the real constitution content hash via the loader."""
    root = Path(data_root)
    hash_path = root / "hashes" / "constitution.sha256"
    if hash_path.exists():
        try:
            existing = hash_path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return Journal(root, existing)
    return Journal(root, real_constitution_hash())
