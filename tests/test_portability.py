"""R6-0 — DEVICE PORTABILITY GUARDS.

FUNDAMENTAL: the frozen test matrix is the installer's verification
gate (install.sh step 4: `pytest tests/ -q` refuses to install on any
failure). A verification gate whose verdict depends on WHICH MACHINE
runs it is not a gate — it is an environment probe. The R4-D5 and R2
sync-guard tests originally hard-coded the build machine's absolute
deployment roots, so a fresh clone on any other device failed at
collection with FileNotFoundError and the installer (correctly,
fail-closed) refused to proceed. The fix: repo location is ALWAYS
derived from the test file itself; external deployment roots resolve
through conftest fixtures (env-overridable) that SKIP when the root is
absent and stay HARD failures when it is present.

This file locks that contract in three ways:
  1. the repo root used by sync tests must be derived, never baked in;
  2. a META-GUARD forbids any absolute host path in test sources
     (the two conftest DEFAULT_* constants are the only sanctioned
     exceptions — they are the documented build-machine defaults the
     env overrides replace);
  3. the 7 sync guards, re-run in a subprocess with absent external
     roots (a simulated fresh device), must ALL skip — never fail.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

# The 7 sync guards that depend on build-machine deployment roots.
SYNC_GUARD_IDS = [
    "tests/test_quant.py::test_d5_quant_route_in_download_runtime_mirror",
    "tests/test_quant.py::test_d5_snapshot_route_in_download_runtime_mirror",
    "tests/test_quant.py::test_d5_quant_route_in_live_web_root",
    "tests/test_quant.py::test_d5_snapshot_route_in_live_web_root",
    "tests/test_quant.py::test_d5_3way_byte_identical_quant_route",
    "tests/test_quant.py::test_d5_3way_byte_identical_snapshot_route",
    "tests/test_memo_evidence.py::"
    "test_memo_evidence_files_3way_byte_identical_repo_stage_vs_gold_desk_v1",
]


def test_repo_root_derived_from_test_file():
    """The repo root every sync test uses must resolve relative to the
    test file — any clone path, any device, any username."""
    assert (REPO / "src" / "gold_desk" / "cli.py").exists(), (
        f"repo root not derivable from test file: {REPO}")
    assert (REPO / "tests" / "conftest.py").exists()


def test_no_hardcoded_machine_paths_in_tests():
    """META-GUARD — no absolute host path may appear in any test source.

    Sanctioned exceptions:
      * conftest.py lines defining the DEFAULT_* build-machine fallback
        constants (env-overridable, skipped when absent);
      * this file (the guard's own pattern literal).
    Everything else — /home/<user>/..., /Users/..., C:\\\\... — is a
    device-dependence bug and fails the matrix on every other machine.
    """
    sanctioned_markers = ("DEFAULT_MIRROR_ROOT", "DEFAULT_RUNTIME_WEB_ROOT")
    offenders: list[str] = []
    for p in sorted(Path(__file__).resolve().parent.glob("*.py")):
        if p.name == "test_portability.py":
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8")
                                 .splitlines(), 1):
            if "/home/" not in line and "/Users/" not in line:
                continue
            if p.name == "conftest.py" and any(m in line
                                               for m in sanctioned_markers):
                continue
            offenders.append(f"{p.name}:{i}: {line.strip()[:90]}")
    assert not offenders, (
        "hardcoded host paths in test sources (device-dependence "
        "— breaks the installer on every other machine):\n  "
        + "\n  ".join(offenders))


def test_sync_guards_skip_on_fresh_device(tmp_path):
    """THE FRESH-DEVICE CONTRACT — with both external deployment roots
    pointed at nonexistent paths (exactly what a fresh clone sees),
    the 7 sync guards must SKIP, never fail. Proven by re-running the
    actual guard tests in a subprocess with the env overrides set —
    the same mechanism any device can use to audit the skip path."""
    env = dict(os.environ)
    env["GOLD_DESK_MIRROR_ROOT"] = str(tmp_path / "absent_mirror")
    env["GOLD_DESK_RUNTIME_WEB_ROOT"] = str(tmp_path / "absent_runtime")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", *SYNC_GUARD_IDS,
         "-q", "-rs", "--no-header", "-p", "no:cacheprovider"],
        cwd=str(REPO), env=env, capture_output=True, text=True,
        timeout=600)
    out = r.stdout + r.stderr
    n_skipped = sum(1 for line in out.splitlines()
                    if line.startswith("SKIPPED"))
    assert r.returncode == 0, out[-2000:]
    assert n_skipped == 7, (
        f"fresh-device simulation must skip all 7 guards, "
        f"skipped {n_skipped}:\n{out[-2000:]}")
    assert "FAILED" not in out and "ERROR" not in out, out[-2000:]
