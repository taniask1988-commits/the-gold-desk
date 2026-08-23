"""Doc 1.5 — the offline exam. NEVER importable from the live loop.

Shares decision code with the live path (asof, setup, filters, sizing,
costs, blackout) and nothing else. While the constitution or data range is
BLOCKED, `run_battery` returns verdict INCOMPLETE with the list of what is
missing — the exam fails closed exactly like the desk.

Usage (offline only):
    python -m sim.runner --constitution ../trading_constitution.yaml \
                         --bars XAUUSD_H1.csv
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.constitution import load_constitution  # noqa: E402
from gold_desk.data.bars import CsvBarSource, SyntheticBarSource  # noqa: E402
from gold_desk.data.model import Bar  # noqa: E402
from gold_desk.features.indicators import atr  # noqa: E402
from gold_desk.setup.engine import SetupEngine  # noqa: E402


@dataclass
class BatteryReport:
    schema: str = "sim_battery_report.v1"
    verdict: str = "INCOMPLETE"          # KILLED | INCOMPLETE | FROZEN_LIVE_CANDIDATE
    constitution_hash: str = ""
    spec_hash: str = ""
    bars: int = 0
    trades: int = 0
    missing_inputs: list[str] = field(default_factory=list)
    walk_forward: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def run_battery(constitution_path: Path, bars_source, kill_criteria: dict | None = None) -> BatteryReport:
    constitution = load_constitution(constitution_path)
    report = BatteryReport(
        constitution_hash=constitution.content_hash,
        spec_hash=SetupEngine().spec_hash,
    )
    missing = constitution.blocked_fields()
    if missing:
        report.missing_inputs = [f"constitution:{m}" for m in missing]
        report.notes.append(
            "constitution BLOCKED — the exam refuses to produce verdict numbers"
        )
        return report
    if kill_criteria is None:
        report.missing_inputs.append("kill_criteria (Doc 1.5 numbers, human-owned)")
        report.notes.append(
            "kill criteria not frozen — INCOMPLETE by design, not by bug"
        )
        return report

    # ---- mechanical replay of the shared decision path (no veto: zero LLM)
    engine = SetupEngine()
    bars: list[Bar] = bars_source.bars_up_to(datetime(2100, 1, 1), 10**9)
    report.bars = len(bars)
    trades = 0
    wf: list[dict] = []
    for i in range(200, len(bars)):
        window = bars[max(0, i - 60):i]     # closed bars only
        cand = engine.evaluate(window, bars[i - 1].close_dt)
        if cand is not None:
            trades += 1
            wf.append({
                "decision_ts": cand.decision_ts,
                "side": cand.side,
                "entry": cand.entry,
                "stop": cand.stop,
                "target": cand.target,
                "stop_distance": cand.stop_distance,
                "atr14": cand.features_used.get("atr14"),
            })
    report.trades = trades
    report.walk_forward = wf
    report.notes.append(
        "mechanical candidate replay only — cost paths, DD distributions, "
        "random-start challenge paths and holdout split activate when the "
        "owner freezes data ranges + kill criteria (see sim/contract.md)"
    )
    report.verdict = "INCOMPLETE"
    return report


def main(argv=None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="sim-runner")
    parser.add_argument("--constitution", type=Path, required=True)
    parser.add_argument("--bars", type=Path, default=None,
                        help="CSV of XAUUSD H1 bars; omit for synthetic smoke")
    args = parser.parse_args(argv)

    if args.bars:
        source = CsvBarSource(args.bars)
    else:
        source = SyntheticBarSource(days=60)
    report = run_battery(args.constitution, source)
    import json
    print(json.dumps({k: v for k, v in report.to_dict().items()
                      if k != "walk_forward"}, indent=2))
    print(f"candidates replayed: {len(report.walk_forward)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
