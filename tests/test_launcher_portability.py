"""GAUNTLET 8 Piece 1 — installer/launcher portability contract (D-001).

The installer renders bin/gold-desk with `sed g` (EVERY sentinel
occurrence is rewritten, including the launcher's own substituted? check).
Before the D-001 fix that check compared the variable against the SAME
sentinel string — after substitution it compared the install dir against
itself, self-satisfied, and every installed launcher re-derived its root
from ~/.local/bin (breaking doctor/venv resolution on every machine).

These tests render the launcher EXACTLY like install.sh step 7 and pin
the contract end to end:

  1. substituted launcher keeps the install dir as the root;
  2. `gold-desk doctor` on the rendered launcher resolves THAT root
     (never the binary's own parent directory — the old bug signature);
  3. an unsubstituted launcher (source clone) still self-derives;
  4. the web deck is runnable without bun (D-016): `npm start` falls
     back to node, and the launcher prefers the standalone build;
  5. next.config.ts pins allowedDevOrigins + fail-on-type-errors
     (D-003/D-014).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LAUNCHER_SRC = REPO / "bin" / "gold-desk"
SENTINEL = "__GOLD_DESK_ROOT__"


def _render(install_dir: str, out: Path) -> Path:
    """install.sh step 7, verbatim: sed g-substitution into the bin dir."""
    rendered = out.read_text() if out.exists() else LAUNCHER_SRC.read_text()
    rendered = LAUNCHER_SRC.read_text().replace(SENTINEL, install_dir)
    out.write_text(rendered)
    out.chmod(0o755)
    return out


def _doctor_root(launcher: Path) -> str:
    """Run `gold-desk doctor` and return the resolved root it prints."""
    r = subprocess.run(["bash", str(launcher), "doctor"],
                       capture_output=True, text=True, timeout=120)
    out = r.stdout + r.stderr
    # doctor prints either "code : <hash> at <root>" (git repo) or
    # "repo missing at <root>" — both carry the resolved root.
    for line in out.splitlines():
        if " at " in line and ("code" in line or "repo missing" in line):
            return line.rsplit(" at ", 1)[1].strip()
    raise AssertionError(f"doctor printed no resolved root:\n{out[-1500:]}")


def test_substituted_launcher_keeps_install_root(tmp_path):
    """The rendered launcher must point at the INSTALL dir, not re-derive
    from its own location (the D-001 self-satisfying-if bug)."""
    install_dir = str(REPO)            # install from this clone
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = _render(install_dir, bin_dir / "gold-desk")
    src = launcher.read_text()
    assert f'GOLD_DESK_ROOT="{install_dir}"' in src, (
        "assignment sentinel not substituted")
    assert 'GOLD_DESK_ROOT="__GOLD_DESK_ROOT__"' not in src, (
        "assignment sentinel survived the substitution")
    # the comparison must still be the sed-immune prefix-strip form
    assert "${GOLD_DESK_ROOT#__GOLD_DESK_ROOT}" in src


def test_doctor_resolves_install_root_not_bin_parent(tmp_path):
    """BAR-INSTALL core: doctor on the RENDERED launcher must resolve the
    install dir. The old bug resolved the binary's parent — assert it
    does not reappear."""
    install_dir = str(REPO)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    launcher = _render(install_dir, bin_dir / "gold-desk")
    root = _doctor_root(launcher)
    assert root == str(REPO), (
        f"doctor resolved {root!r}, expected the install dir {REPO}")
    # the D-001 bug signature: root derived from the launcher's parent
    assert Path(root) != bin_dir.parent.resolve(), (
        f"launcher re-derived its root from the binary location "
        f"({bin_dir.parent}) — the D-001 self-satisfying if is back")


def test_source_clone_launcher_self_derives():
    """An unsubstituted launcher (running straight from a clone) must
    still derive the repo root from its own location."""
    assert SENTINEL in LAUNCHER_SRC.read_text(), (
        "source launcher lost its sentinel — the installer has nothing "
        "to substitute")
    root = _doctor_root(REPO / "bin" / "gold-desk")
    assert Path(root).resolve() == REPO.resolve()


def test_npm_start_works_without_bun():
    """D-016 — `npm start` must not hard-require bun: the start script
    falls back to node when bun is absent."""
    pkg = json.loads((REPO / "web" / "package.json").read_text())
    start = pkg["scripts"]["start"]
    assert "node" in start, f"no node fallback in start script: {start}"
    # the fallback must be conditional, not bun-only
    assert "command -v bun" in start
    assert ".next/standalone/server.js" in start


def test_next_config_dev_origins_and_type_gate():
    """D-003 + D-014 — dev hydration on 127.0.0.1 (allowedDevOrigins)
    and the build must fail on type errors."""
    cfg = (REPO / "web" / "next.config.ts").read_text()
    assert "allowedDevOrigins" in cfg
    for host in ("localhost", "127.0.0.1", "0.0.0.0"):
        assert f'"{host}"' in cfg, f"allowedDevOrigins missing {host}"
    assert "ignoreBuildErrors: false" in cfg, (
        "next build must not ignore type errors (D-014)")


def test_launcher_prefers_standalone_build():
    """D-003b — the launcher serves the production standalone build by
    default (dev mode is the fallback, not the default)."""
    src = LAUNCHER_SRC.read_text()
    assert ".next/standalone/server.js" in src
    assert "GOLD_DESK_DEV" in src  # documented dev escape hatch
    # the standalone path must come BEFORE the dev path in launch_web
    assert src.index("standalone/server.js") < src.index("run dev")
