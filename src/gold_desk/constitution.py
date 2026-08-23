"""Document 1 — the trading constitution: load, validate, hash.

Law L12: the constitution is human-owned. Runtime code may read it, hash it,
and refuse to act when required numbers are still BLOCKED. It may never
mutate it.

A "demo overlay" may be applied ONLY by the demo runner (config/demo.yaml).
Under an overlay the constitution is flagged demo=True, every event emitted
anywhere downstream is tagged demo=true, and tickets are watermarked. The
canonical constitution on disk stays fail-closed.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import yaml

from .version import is_blocked

REQUIRED_FOR_ANY_TRADE = [
    # dotted paths that MUST be real numbers before a ticket can ever exist
    "broker.contract_size",
    "broker.quote_digits",
    "broker.tick_size",
    "broker.tick_value",
    "broker.lot_step",
    "broker.min_lot",
    "broker.max_lot",
    "broker.typical_london_open_spread",
    "broker.min_spread_assumption",
    "broker.commission_per_lot_rt",
    "costs.slippage_buffer",
    "costs.unfillable_if_spread_gt",
    "internal_limits.risk_pct_per_trade",
    "internal_limits.sizing_equity_basis",
    "internal_limits.max_trades_per_day",
    "internal_limits.consecutive_loss_standdown",
    "internal_limits.overnight_positions",
    "internal_limits.allowed_sessions",
    "internal_limits.news_blackout_minutes_before",
    "internal_limits.news_blackout_minutes_after",
    "internal_limits.high_impact_only",
    "internal_limits.max_spread",
    "internal_limits.min_stop_distance_spread_mult",
    "internal_limits.min_stop_distance_atr_mult",
    "internal_limits.min_rr",
    "internal_limits.daily_loss_internal_pct",
    "internal_limits.max_bar_lag_minutes",
    "internal_limits.outlier_return_abs_pct",
    "execution.ticket_expiry_minutes",
    "firm.account_size",
    "firm.news_trading_rule",
    "firm.weekend_holding_rule",
]


def _deep_merge(base: dict, overlay: dict) -> dict:
    out = dict(base)
    for key, value in overlay.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _get_path(data: dict, dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@dataclass
class Constitution:
    """Typed view over the YAML plus its content hash."""

    raw: dict
    file_path: Path
    file_hash: str                 # sha256 of canonical file bytes
    content_hash: str              # sha256 of the effective (post-overlay) doc
    demo: bool = False
    overlay_hash: str | None = None
    problems: list[str] = field(default_factory=list)

    # --- identity -----------------------------------------------------------
    @property
    def phase(self) -> int:
        return int(self.raw.get("identity", {}).get("phase", 1))

    @property
    def instrument(self) -> str:
        return self.raw.get("identity", {}).get("instrument", "XAUUSD")

    # --- blocks -------------------------------------------------------------
    @property
    def broker(self) -> dict:
        return self.raw.get("broker", {})

    @property
    def costs(self) -> dict:
        return self.raw.get("costs", {})

    @property
    def limits(self) -> dict:
        return self.raw.get("internal_limits", {})

    @property
    def execution(self) -> dict:
        return self.raw.get("execution", {})

    @property
    def firm(self) -> dict:
        return self.raw.get("firm", {})

    @property
    def firm_rules(self) -> dict:
        # firm behavioural rules may live under firm: or demo overlay firm_rules:
        merged = {k: v for k, v in self.firm.items()}
        for k, v in (self.raw.get("firm_rules") or {}).items():
            merged.setdefault(k, v)
        return merged

    # --- frequently used values --------------------------------------------
    @property
    def risk_pct(self) -> float | None:
        value = self.limits.get("risk_pct_per_trade")
        return None if is_blocked(value) else float(value)

    @property
    def equity_basis(self) -> str | None:
        value = self.limits.get("sizing_equity_basis")
        return None if is_blocked(value) else str(value)

    @property
    def max_spread(self) -> float | None:
        value = self.limits.get("max_spread")
        return None if is_blocked(value) else float(value)

    @property
    def ticket_expiry_minutes(self) -> float | None:
        value = self.execution.get("ticket_expiry_minutes")
        return None if is_blocked(value) else float(value)

    @property
    def allowed_sessions(self) -> list[str] | None:
        value = self.limits.get("allowed_sessions")
        return None if is_blocked(value) else list(value)

    # --- hashes / validation ------------------------------------------------
    def blocked_fields(self) -> list[str]:
        return [p for p in REQUIRED_FOR_ANY_TRADE if is_blocked(_get_path(self.raw, p))]

    @property
    def trade_capable(self) -> bool:
        """False while any number required for a real decision is BLOCKED."""
        return not self.blocked_fields()

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_line(self) -> str:
        blocked = self.blocked_fields()
        state = "TRADE_CAPABLE" if not blocked else f"FAIL_CLOSED ({len(blocked)} BLOCKED)"
        mode = "DEMO-OVERLAY" if self.demo else "CANONICAL"
        return f"[{mode}] {state} content_hash={self.content_hash[:12]}"


def _canon(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def load_constitution(path: str | Path, overlay_path: str | Path | None = None) -> Constitution:
    """Load constitution YAML (+ optional demo overlay), compute hashes."""
    file_path = Path(path)
    raw_bytes = file_path.read_bytes()
    file_hash = hashlib.sha256(raw_bytes).hexdigest()
    data: dict = yaml.safe_load(raw_bytes.decode("utf-8"))

    demo = False
    overlay_hash = None
    if overlay_path is not None:
        overlay_file = Path(overlay_path)
        overlay_bytes = overlay_file.read_bytes()
        overlay = yaml.safe_load(overlay_bytes.decode("utf-8"))
        overlay_hash = hashlib.sha256(overlay_bytes).hexdigest()
        demo = bool(overlay.get("demo", False))
        if not demo:
            raise ValueError("refusing non-demo overlay: config must declare demo: true")
        overlay.pop("demo", None)
        data = _deep_merge(data, overlay)

    content_hash = hashlib.sha256(_canon(data).encode("utf-8")).hexdigest()

    problems: list[str] = []
    if data.get("schema") != "trading_constitution.v1":
        problems.append("schema mismatch: expected trading_constitution.v1")
    ident = data.get("identity", {})
    if ident.get("instrument") != "XAUUSD" or ident.get("timeframe") != "H1":
        problems.append("identity: v1 is XAUUSD H1 only")
    if data.get("fail_closed", {}).get("retry_into_fill") is not False:
        problems.append("fail_closed.retry_into_fill must be false")

    return Constitution(
        raw=data,
        file_path=file_path,
        file_hash=file_hash,
        content_hash=content_hash,
        demo=demo,
        overlay_hash=overlay_hash,
        problems=problems,
    )


def validation_report(constitution: Constitution) -> str:
    lines = [
        "GOLD DESK CONSTITUTION VALIDATION",
        "=" * 64,
        f"file            : {constitution.file_path}",
        f"file sha256     : {constitution.file_hash}",
        f"content sha256  : {constitution.content_hash}",
        f"mode            : {'DEMO OVERLAY (events tagged demo)' if constitution.demo else 'CANONICAL'}",
        f"phase           : {constitution.phase}",
        "",
    ]
    if constitution.problems:
        lines.append("STRUCTURAL PROBLEMS:")
        lines += [f"  - {p}" for p in constitution.problems]
        lines.append("")
    blocked = constitution.blocked_fields()
    if blocked:
        lines.append(f"BLOCKED FIELDS ({len(blocked)}) — the desk is FAIL-CLOSED:")
        lines += [f"  - {b}" for b in blocked]
        lines.append("")
        lines.append("Paste your numbers into trading_constitution.yaml (see")
        lines.append("config/constitution.example.yaml for a prefilled template),")
        lines.append("then re-run:  python -m gold_desk.cli validate")
    else:
        lines.append("No BLOCKED fields. Trade-capable (still proposal-only).")
    return "\n".join(lines)
