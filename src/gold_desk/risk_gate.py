"""§7 — the risk gate. Pure Python, no LLM on the path, boolean only.

Checks in fixed order (first reject wins — deterministic, table-tested).
No approve_with_reduction. No half-size-because-news. News-adjacent -> REJECT.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime

from .clock import iso, parse_ts
from .constitution import Constitution
from .filters import AccountState, MarketState
from .setup.engine import SetupCandidate
from .sizing import SizingResult, size_with_constitution
from .version import is_blocked


@dataclass
class GateDecision:
    schema: str = "gate_decision.v1"
    action: str = "REJECT"          # APPROVE | REJECT
    code: str | None = None
    lots: float = 0.0
    risk_pct: float = 0.0
    risk_money: float = 0.0
    reasons: list[str] = field(default_factory=list)
    checked_at: str = ""
    spread_at_gate: float = 0.0
    equity_at_gate: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


REJECT_ORDER = [
    "KILL_SWITCH", "DEGRADED", "CONSTITUTION_BLOCKED", "CONSTITUTION_MISMATCH",
    "SOURCE_MISMATCH",
    "SESSION", "NEWS_UNAVAILABLE", "NEWS_BLACKOUT", "STALE_DATA",
    "SPREAD", "SPREAD_BLOWOUT",
    "BUDGET", "MAX_TRADES", "CONSEC_LOSS", "OPEN_POSITION", "TICKET_EXPIRED",
    "STOP_TOO_TIGHT", "RR_FLOOR", "SIZE_INVALID",
]


def evaluate_gate(
    constitution: Constitution,
    cand: SetupCandidate,
    acct: AccountState,
    market: MarketState,
    quote,
    now: datetime,
    atr14: float | None = None,
    kill_switch: bool = False,
    degraded: bool = False,
    spread_at_candidate: float | None = None,
    engine_spec_hash: str | None = None,
) -> GateDecision:
    gate = GateDecision(
        checked_at=iso(now),
        spread_at_gate=quote.spread,
        equity_at_gate=acct.equity,
        risk_pct=constitution.risk_pct or 0.0,
    )

    def reject(code: str, detail: str = "") -> GateDecision:
        gate.action = "REJECT"
        gate.code = code
        gate.reasons = [detail or code]
        return gate

    if kill_switch:
        return reject("KILL_SWITCH")
    if degraded:
        return reject("DEGRADED")
    if not constitution.trade_capable:
        return reject("CONSTITUTION_BLOCKED",
                      f"{len(constitution.blocked_fields())} constitution fields BLOCKED")
    # H3: source spec-hash guard. Compares the candidate's spec_hash to the
    # engine's current spec_hash (passed in by the orchestrator). The prior
    # form `cand.spec_hash != cand.spec_hash` was a self-comparison and
    # therefore never fired. A mismatch means the candidate was produced
    # under a different setup version than the one currently loaded — reject.
    if (engine_spec_hash and cand.spec_hash
            and cand.spec_hash != engine_spec_hash):
        return reject("SOURCE_MISMATCH",
                      f"spec_hash {cand.spec_hash[:8]} != engine {engine_spec_hash[:8]}")

    # fall back to the candidate's recorded spread if caller didn't pass one
    if spread_at_candidate is None and getattr(cand, "spread_at_candidate", None):
        spread_at_candidate = float(cand.spread_at_candidate)  # type: ignore[arg-type]

    # time: candidate must still be alive
    if parse_ts(cand.expiry_ts) < now:
        return reject("TICKET_EXPIRED", "candidate expired before gate")

    # session
    allowed = constitution.allowed_sessions
    if allowed is not None and market.session not in allowed:
        return reject("SESSION", f"session {market.session} not allowed")

    # news
    if not market.news_available:
        return reject("NEWS_UNAVAILABLE")
    if market.news_blackout_active:
        return reject("NEWS_BLACKOUT")

    # freshness
    max_lag = constitution.limits.get("max_bar_lag_minutes")
    if is_blocked(max_lag):
        return reject("CONSTITUTION_BLOCKED", "max_bar_lag_minutes BLOCKED")
    age = (now - parse_ts(market.bar_close_ts)).total_seconds() / 60.0
    if age > float(max_lag) + 1.0:
        return reject("STALE_DATA", f"bar age {age:.0f} min")

    # spread — current vs max, and widening since candidate
    max_spread = constitution.max_spread
    if max_spread is not None:
        if quote.spread > max_spread:
            return reject("SPREAD", f"spread {quote.spread} > {max_spread}")
    if spread_at_candidate is not None and quote.spread > spread_at_candidate * 1.5:
        return reject("SPREAD_BLOWOUT", "spread widened >1.5x since candidate")

    # budget / caps / position — same semantics as cheap filters
    daily_pct = constitution.limits.get("daily_loss_internal_pct")
    if is_blocked(daily_pct):
        return reject("CONSTITUTION_BLOCKED")
    day_start_equity = acct.equity - acct.daily_pnl
    if acct.daily_pnl <= -float(daily_pct) * day_start_equity:
        return reject("BUDGET")
    max_trades = constitution.limits.get("max_trades_per_day")
    if is_blocked(max_trades):
        return reject("CONSTITUTION_BLOCKED")
    if acct.trades_today >= int(max_trades):
        return reject("MAX_TRADES")
    standdown = constitution.limits.get("consecutive_loss_standdown")
    if is_blocked(standdown):
        return reject("CONSTITUTION_BLOCKED")
    if acct.consecutive_losses >= int(standdown):
        return reject("CONSEC_LOSS")
    if any(p.get("symbol") == "XAUUSD" and p.get("open") for p in acct.open_positions):
        return reject("OPEN_POSITION")

    # stop-distance floors + RR
    spread_mult = constitution.limits.get("min_stop_distance_spread_mult")
    atr_mult = constitution.limits.get("min_stop_distance_atr_mult")
    if is_blocked(spread_mult) or is_blocked(atr_mult):
        return reject("CONSTITUTION_BLOCKED")
    floor = float(spread_mult) * quote.spread
    if atr14 is not None:
        floor = max(floor, float(atr_mult) * atr14)
    if cand.stop_distance < floor:
        return reject("STOP_TOO_TIGHT",
                      f"stop {cand.stop_distance} < floor {floor:.2f}")
    min_rr = constitution.limits.get("min_rr")
    if not is_blocked(min_rr) and min_rr is not None:
        rr = abs(cand.target - cand.entry) / cand.stop_distance
        if rr < float(min_rr) - 1e-9:
            return reject("RR_FLOOR", f"RR {rr:.2f} < {min_rr}")

    # sizing — the only producer of APPROVE beyond this point
    basis = constitution.equity_basis
    equity_for_sizing = acct.equity if basis == "equity" else acct.balance
    sizing: SizingResult = size_with_constitution(
        constitution, equity_for_sizing, cand.stop_distance
    )
    if not sizing.ok:
        return reject("SIZE_INVALID", sizing.detail)

    gate.action = "APPROVE"
    gate.lots = sizing.lots
    gate.risk_money = sizing.risk_money
    gate.reasons = [f"fixed fraction {constitution.risk_pct} on {basis}"]
    return gate
