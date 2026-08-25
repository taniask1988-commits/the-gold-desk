"""§7.3 — fixed-fraction sizing. No Kelly. No reduce_size. Ever.

    risk_money  = equity * risk_pct
    raw_lots    = risk_money / (stop_distance * point_value_per_lot)
    lots        = floor_to_lot_step(raw_lots)
    lots < min_lot or lots > max_lot  ->  SIZE_INVALID (no silent clip)

point_value_per_lot = value of a 1.00 price move for one lot, in account
currency. For XAUUSD quoted in USD with contract_size ounces per lot this is
simply contract_size (e.g. 100 -> $100 per 1.00 move per lot).

The unit tests use a FAKE contract only (plan Doc 5); the live path reads
the constitution broker block and fails closed while it is BLOCKED.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .constitution import Constitution


@dataclass
class SizingResult:
    ok: bool
    lots: float = 0.0
    raw_lots: float = 0.0
    risk_money: float = 0.0
    code: str | None = None
    detail: str = ""


def compute_lots(
    equity: float,
    risk_pct: float,
    stop_distance: float,
    point_value_per_lot: float,
    lot_step: float,
    min_lot: float,
    max_lot: float,
) -> SizingResult:
    if stop_distance <= 0 or point_value_per_lot <= 0 or lot_step <= 0:
        return SizingResult(False, code="SIZE_INVALID",
                            detail="non-positive stop/point/step")
    risk_money = equity * risk_pct
    raw = risk_money / (stop_distance * point_value_per_lot)
    lots = math.floor(raw / lot_step) * lot_step
    lots = round(lots, 4)
    if lots < min_lot:
        return SizingResult(False, raw_lots=raw, risk_money=risk_money,
                            code="SIZE_INVALID",
                            detail=f"lots {lots} < min_lot {min_lot} (raw {raw:.4f})")
    if lots > max_lot:
        return SizingResult(False, raw_lots=raw, risk_money=risk_money,
                            code="SIZE_INVALID",
                            detail=f"lots {lots} > max_lot {max_lot} — refused, not clipped")
    return SizingResult(True, lots=lots, raw_lots=round(raw, 6),
                        risk_money=round(risk_money, 2))


def size_with_constitution(
    constitution: Constitution, equity: float, stop_distance: float
) -> SizingResult:
    """Live-path sizing: refuses while the contract block is BLOCKED."""
    b = constitution.broker
    from .version import is_blocked
    needed = [b.get("contract_size"), b.get("lot_step"), b.get("min_lot"),
              b.get("max_lot"), constitution.risk_pct]
    if any(is_blocked(v) for v in needed):
        return SizingResult(False, code="SIZE_INVALID",
                            detail="constitution broker/risk fields BLOCKED")
    point_value = point_value_from_constitution(constitution)
    return compute_lots(
        equity=equity,
        risk_pct=constitution.risk_pct,          # type: ignore[arg-type]
        stop_distance=stop_distance,
        point_value_per_lot=point_value,
        lot_step=float(b["lot_step"]),
        min_lot=float(b["min_lot"]),
        max_lot=float(b["max_lot"]),
    )


def point_value_from_constitution(constitution: Constitution) -> float:
    """USD value of a 1.00 price move per lot, derived from the constitution.

    Order: tick_value / tick_size if both present and positive (this is the
    USD value of one full price unit per lot); else fall back to contract_size.
    For the demo overlay (tick_size=0.01, tick_value=1.0) this is 100 — same
    as contract_size=100, so existing demo balances stay reproducible.
    Returns 100.0 (the historical default) when the broker block is absent or
    fully BLOCKED, so the paper account can still operate in tests/sim.
    """
    b = constitution.broker
    from .version import is_blocked
    contract = b.get("contract_size")
    if is_blocked(contract):
        return 100.0
    tick_size = b.get("tick_size")
    tick_value = b.get("tick_value")
    if (not is_blocked(tick_size) and not is_blocked(tick_value)
            and float(tick_size) > 0 and float(tick_value) > 0):
        return float(tick_value) / float(tick_size)
    return float(contract)
