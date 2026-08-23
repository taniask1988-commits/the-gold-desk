"""§4.4 — blind context pack builder (Phase 2 input; built but unused in
Phase 1 so the blindfold firewall is testable from day one).

Allowed  : timestamped bars/features, the candidate package, asof-filtered
           calendar/news, static constitution excerpt (sessions, blackout,
           "risk is R% per trade; you do not size").
Forbidden: equity/balance/PnL/budget/streaks/challenge-progress/trades-today/
           open-position details — stripped in Python, never trusted to the
           model. The unit test suite fails if any forbidden key survives.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

from .constitution import Constitution
from .data.asof import filter_asof
from .data.model import Observation
from .setup.engine import SetupCandidate

FORBIDDEN_KEYS = [
    "equity", "balance", "daily_pnl", "floating_pnl", "pnl",
    "remaining_daily_budget", "daily_risk_budget_remaining",
    "win_streak", "loss_streak", "streak", "streaks",
    "challenge_progress", "challenge", "profit_target_progress",
    "trades_today", "open_positions", "open_position_details",
    "budget_remaining", "score", "account",
]


def _scrub(node):
    """Recursively remove forbidden keys anywhere in the payload."""
    if isinstance(node, dict):
        out = {}
        for key, value in node.items():
            key_l = str(key).lower()
            if any(f == key_l or f in key_l for f in FORBIDDEN_KEYS):
                continue
            out[key] = _scrub(value)
        return out
    if isinstance(node, list):
        return [_scrub(item) for item in node]
    return node


def audit_forbidden(payload: dict) -> list[str]:
    """Return forbidden keys found (recursively). Tests assert this is []."""
    found: list[str] = []

    def walk(node, path):
        if isinstance(node, dict):
            for key, value in node.items():
                key_l = str(key).lower()
                for f in FORBIDDEN_KEYS:
                    if f == key_l or f in key_l:
                        found.append(f"{path}.{key}")
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, item in enumerate(node):
                walk(item, f"{path}[{i}]")

    walk(payload, "$")
    return found


@dataclass
class ContextPack:
    schema: str = "context_pack.v1"
    decision_ts: str = ""
    candidate: dict = field(default_factory=dict)
    bars: list[dict] = field(default_factory=list)
    features: dict = field(default_factory=dict)
    calendar: list[dict] = field(default_factory=list)
    news: list[dict] = field(default_factory=list)
    constitution_excerpt: dict = field(default_factory=dict)
    must_not_know: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def hash(self) -> str:
        canon = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def build_pack(
    constitution: Constitution,
    cand: SetupCandidate,
    bars: list[Observation],
    features_obs: list[Observation],
    calendar_obs: list[Observation],
    news_obs: list[Observation],
) -> ContextPack:
    decision_ts = cand.decision_ts

    # the asof law — drop anything from the future, IN PYTHON, before build
    bars = filter_asof(bars, decision_ts)
    features_obs = filter_asof(features_obs, decision_ts)
    calendar_obs = filter_asof(calendar_obs, decision_ts)
    news_obs = filter_asof(news_obs, decision_ts)

    pack = ContextPack(
        decision_ts=decision_ts,
        candidate=_scrub(cand.to_dict()),
        bars=[_scrub(dict(o.payload, asof_ts=o.asof_ts)) for o in bars[-50:]],
        features={str(i): _scrub(o.payload) for i, o in enumerate(features_obs)},
        calendar=[_scrub(dict(o.payload, asof_ts=o.asof_ts)) for o in calendar_obs[-10:]],
        news=[_scrub(dict(o.payload, asof_ts=o.asof_ts)) for o in news_obs[-15:]],
        constitution_excerpt={
            "allowed_sessions": constitution.allowed_sessions,
            "news_blackout_minutes": [
                constitution.limits.get("news_blackout_minutes_before"),
                constitution.limits.get("news_blackout_minutes_after"),
            ],
            "risk_rule": "Risk is a fixed R% per trade; you do not size.",
        },
        must_not_know=[
            "account equity, balance, daily or floating PnL",
            "remaining daily risk budget",
            "win/loss streaks",
            "challenge progress",
            "trades taken today",
            "open position details",
        ],
    )
    leaked = audit_forbidden(pack.to_dict())
    if leaked:  # defensive: fail closed rather than ship a leaky pack
        raise RuntimeError(f"blindfold violation building pack: {leaked}")
    return pack
