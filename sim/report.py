"""Pass/fail report rendering for the Doc 1.5 battery. The report schema is
part of the frozen contract: seeing a report and tweaking parameters means
a NEW spec version and a NEW holdout, or a declared burn."""
from __future__ import annotations

import json
from pathlib import Path

SCHEMA_KEYS = [
    "schema", "verdict", "constitution_hash", "spec_hash", "bars", "trades",
    "missing_inputs", "walk_forward", "notes",
]


def render_report(report_dict: dict) -> str:
    lines = ["SIM BATTERY REPORT", "=" * 60]
    lines.append(f"verdict          : {report_dict.get('verdict')}")
    lines.append(f"constitution     : {str(report_dict.get('constitution_hash'))[:16]}")
    lines.append(f"spec             : {str(report_dict.get('spec_hash'))[:16]}")
    lines.append(f"bars examined    : {report_dict.get('bars')}")
    lines.append(f"candidates       : {report_dict.get('trades')}")
    if report_dict.get("missing_inputs"):
        lines.append("missing inputs   :")
        for m in report_dict["missing_inputs"]:
            lines.append(f"  - {m}")
    for note in report_dict.get("notes", []):
        lines.append(f"note: {note}")
    return "\n".join(lines)


def save_report(report_dict: dict, path: str | Path) -> None:
    Path(path).write_text(json.dumps(report_dict, indent=2, sort_keys=True))
