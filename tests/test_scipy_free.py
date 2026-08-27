"""R3-3 gap-fix — scipy-free deployment proof.

The R3-2 critic found the module-level `importorskip(scipy)` collapsed
the ENTIRE test_risk_metrics.py to one skip in a stdlib-only deploy. The
fix lazy-imports scipy/numpy per test; these tests prove it works by
re-running the risk/portfolio/attribution suites in a subprocess with
`pytest --no-scipy` (a conftest meta-path blocker that makes ANY
scipy/numpy import raise ModuleNotFoundError):

* the stdlib-pure majority still PASSES (does not skip)
* only the tests that genuinely use scipy/numpy as oracles SKIP
* nothing FAILS and the production modules import cleanly
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

STDLIB_PURE_FILES = [
    "tests/test_risk_metrics.py",
    "tests/test_portfolio.py",
    "tests/test_attribution.py",
]


def _run_no_scipy(targets: list[str]) -> subprocess.CompletedProcess:
    # no -q: pytest 9 hides the "N passed" summary line in quiet mode
    return subprocess.run(
        [sys.executable, "-m", "pytest", *targets, "--no-scipy",
         "-p", "no:cacheprovider"],
        cwd=REPO, capture_output=True, text=True, timeout=600)


def test_scipy_free_risk_metrics_majority_still_runs():
    """With scipy/numpy importable NOWHERE: test_risk_metrics.py runs its
    stdlib-pure tests (≥ 20 pass) and skips only the scipy-oracle ones
    (≥ 5 skip) instead of collapsing to a single module-level skip."""
    proc = _run_no_scipy(["tests/test_risk_metrics.py"])
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    tail = proc.stdout.strip().splitlines()[-1]
    assert "failed" not in tail, tail
    passed = int(tail.split(" passed")[0].split()[-1])
    skipped = int(tail.split("skipped")[0].split()[-1])
    assert passed >= 20, tail        # the stdlib-pure majority RUNS
    assert skipped >= 5, tail        # the scipy-oracle tests skip


def test_scipy_free_portfolio_and_attribution_fully_pass():
    """The R3-3 modules are pure stdlib: 100% of their tests pass with
    scipy/numpy blocked (zero skips expected — nothing references them)."""
    proc = _run_no_scipy(["tests/test_portfolio.py",
                          "tests/test_attribution.py"])
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    tail = proc.stdout.strip().splitlines()[-1]
    assert "failed" not in tail and "skipped" not in tail, tail


def test_scipy_free_production_modules_import():
    """The production risk package imports cleanly with scipy/numpy
    blocked — no accidental scientific-stack dependency crept in."""
    code = ("import sys;"
            "sys.path.insert(0, 'src');"
            "from gold_desk.risk import portfolio, attribution, metrics;"
            "from gold_desk import cli;"
            "print('ok')")
    blocker = (
        "import importlib.abc, sys\n"
        "class B(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in ('scipy', 'numpy'):\n"
        "            raise ModuleNotFoundError('blocked: ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, B())\n"
    )
    proc = subprocess.run([sys.executable, "-c", blocker + code],
                          cwd=REPO, capture_output=True, text=True,
                          timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert proc.stdout.strip().endswith("ok")
