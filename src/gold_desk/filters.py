"""§4.2 — cheap hard filters (before setup, before any news fetch, LLM $0)
and candidate filters (after a candidate exists).

Every check returns None when passing or a reason code when failing, so the
orchestrator can journal FilterReject with the first failing code and the
bar ends with exactly one terminal reason code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clock import SessionClock, session_of
from .constitution import Constitution
from .data.calendar import in_blackout
from .data.model import Bar, Quote
from .setup.engine import SetupCandidate
from .version import is_blocked


@dataclass
class MarketState:
    spread: float
    bar_close_ts: str
    now_ts: str
    session: str
    news_blackout_active: bool
    news_available: bool = True


@dataclass
class AccountState:
    equity: float
    balance: float
    daily_pnl: float
    open_positions: list[dict]
    trades_today: int
    consecutive_losses: int
    high_water: float


def cheap_filters(
    constitution: Constitution,
    acct: AccountState,
    market: MarketState,
    quote: Quote,
    kill_switch: bool,
    degraded: bool,
) -> str | None:
    """Order matters: cheapest and most-certain blocks first."""
    limits = constitution.limits

    if kill_switch:
        return "KILL_SWITCH"
    if degraded:
        return "DEGRADED"

    if not constitution.trade_capable:
        return "CONSTITUTION_BLOCKED"

    # session
    allowed = constitution.allowed_sessions
    if allowed is not None and market.session not in allowed:
        return "SESSION"

    # spread
    max_spread = constitution.max_spread
    if max_spread is not None and quote.spread > max_spread:
        return "SPREAD"

    # freshness
    max_lag = limits.get("max_bar_lag_minutes")
    if is_blocked(max_lag):
        return "CONSTITUTION_BLOCKED"
    from .clock import parse_ts
    age = (parse_ts(market.now_ts) - parse_ts(market.bar_close_ts)).total_seconds() / 60.0
    if age > float(max_lag) + 1.0:
        return "STALE_DATA"

    # news feed availability (fail closed: no calendar -> no trade)
    if not market.news_available:
        return "NEWS_UNAVAILABLE"

    # news blackout
    if market.news_blackout_active:
        return "NEWS_BLACKOUT"

    # budget: internal daily stop on equity
    daily_pct = limits.get("daily_loss_internal_pct")
    if is_blocked(daily_pct):
        return "CONSTITUTION_BLOCKED"
    day_start_equity = acct.equity - acct.daily_pnl
    if acct.daily_pnl <= -float(daily_pct) * day_start_equity:
        return "BUDGET"

    # max trades
    max_trades = limits.get("max_trades_per_day")
    if is_blocked(max_trades):
        return "CONSTITUTION_BLOCKED"
    if acct.trades_today >= int(max_trades):
        return "MAX_TRADES"

    # consecutive-loss stand-down
    standdown = limits.get("consecutive_loss_standdown")
    if is_blocked(standdown):
        return "CONSTITUTION_BLOCKED"
    if acct.consecutive_losses >= int(standdown):
        return "CONSEC_LOSS"

    # flat-or-one
    if any(p.get("symbol") == "XAUUSD" and p.get("open") for p in acct.open_positions):
        return "OPEN_POSITION"

    return None


def candidate_filters(
    constitution: Constitution,
    cand: SetupCandidate,
    quote: Quote,
    atr14: float | None,
) -> str | None:
    limits = constitution.limits

    # stop distance floors
    spread_mult = limits.get("min_stop_distance_spread_mult")
    atr_mult = limits.get("min_stop_distance_atr_mult")
    if is_blocked(spread_mult) or is_blocked(atr_mult):
        return "CONSTITUTION_BLOCKED"
    floor = float(spread_mult) * quote.spread
    if atr14 is not None:
        floor = max(floor, float(atr_mult) * atr14)
    if cand.stop_distance < floor:
        return "STOP_TOO_TIGHT"

    # RR floor
    min_rr = limits.get("min_rr")
    if not is_blocked(min_rr) and min_rr is not None:
        rr = abs(cand.target - cand.entry) / cand.stop_distance
        if rr < float(min_rr) - 1e-9:
            return "RR_FLOOR"

    return None


def news_blackout_active(
    constitution: Constitution, events: list, around: datetime
) -> bool:
    before = constitution.limits.get("news_blackout_minutes_before")
    after = constitution.limits.get("news_blackout_minutes_after")
    high_only = constitution.limits.get("high_impact_only", True)
    if is_blocked(before) or is_blocked(after) or is_blocked(high_only):
        return False  # constitution BLOCKED path already fails closed earlier
    return in_blackout(events, around, int(before), int(after), bool(high_only)) is not None


def session_clock(constitution: Constitution) -> SessionClock:
    allowed = constitution.allowed_sessions or ("london", "london_ny_overlap")
    return SessionClock(allowed_sessions=tuple(allowed))
