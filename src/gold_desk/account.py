"""Paper account state (proposal-only desk; nothing here touches a broker).

Tracks equity/balance/day stats/open positions for the risk gate. Paper
fills resolve deterministically from later bars: stop, target, or time-stop
— never a "feel" exit. Day rollover resets daily counters; weekend holding
is forbidden by default so any open paper position is force-closed at the
last Friday 21:00 UTC bar (journalled as such).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path

from .clock import iso
from .data.model import Bar
from .events import Journal
from .filters import AccountState


@dataclass
class PaperPosition:
    opened_ts: str
    side: str
    entry: float
    stop: float
    target: float
    lots: float
    time_stop_ts: str
    ticket_id: str
    commission_paid: float = 0.0
    cost_per_unit: float = 0.0      # pessimistic spread+slippage per side
    open: bool = True


@dataclass
class PaperAccount:
    balance: float
    equity: float
    day_key: str = ""
    daily_pnl: float = 0.0
    trades_today: int = 0
    consecutive_losses: int = 0
    high_water: float = 0.0
    positions: list[PaperPosition] = field(default_factory=list)
    closed_trades: list[dict] = field(default_factory=list)

    # ------------------------------------------------------------------ state
    def account_state(self) -> AccountState:
        return AccountState(
            equity=self.equity,
            balance=self.balance,
            daily_pnl=self.daily_pnl,
            open_positions=[{"symbol": "XAUUSD", "open": p.open} for p in self.positions],
            trades_today=self.trades_today,
            consecutive_losses=self.consecutive_losses,
            high_water=self.high_water,
        )

    def rollover_day(self, day_key: str) -> None:
        if self.day_key and self.day_key != day_key:
            self.daily_pnl = 0.0
            self.trades_today = 0
        self.day_key = day_key


class PaperAccountStore:
    def __init__(self, root: str | Path, starting_balance: float, journal: Journal,
                 point_value_per_lot: float = 100.0):
        self.root = Path(root)
        self.journal = journal
        self.path = self.root / "account.json"
        # Point value per lot: USD value of a 1.00 price move per lot. Derived
        # from the constitution's tick_value/tick_size (or contract_size when
        # the tick fields are absent). Default 100.0 keeps demo data backwards-
        # compatible. See sizing.point_value_from_constitution().
        self.point_value_per_lot = float(point_value_per_lot)
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
                # adopt whatever point value was persisted (so a reloaded
                # account keeps using its original contract, not the caller's)
                self.point_value_per_lot = float(data.pop("point_value_per_lot",
                                                           self.point_value_per_lot))
                self.account = PaperAccount(**data)
            except (json.JSONDecodeError, TypeError, ValueError) as e:
                # L6: corrupt account.json — start fresh and journal the
                # recovery so the operator sees it instead of silent loss.
                self.journal.emit(
                    "AccountCorruptRecovered",
                    {"path": str(self.path), "error": str(e),
                     "recovered_to": "fresh account at starting_balance"},
                    reason_code="ACCOUNT_CORRUPT_RECOVERED",
                )
                # rename the corrupt file aside so we don't loop on it
                backup = self.path.with_suffix(".json.corrupt")
                try:
                    self.path.rename(backup)
                except Exception:
                    pass
                self.account = PaperAccount(
                    balance=starting_balance, equity=starting_balance,
                    high_water=starting_balance,
                )
        else:
            self.account = PaperAccount(
                balance=starting_balance, equity=starting_balance,
                high_water=starting_balance,
            )

    # -------------------------------------------------------------- mutation
    def mark_open_position(self, bar: Bar) -> None:
        """Recompute equity from open positions at bar close (paper)."""
        acct = self.account
        floating = 0.0
        for pos in acct.positions:
            if not pos.open:
                continue
            direction = 1 if pos.side == "buy" else -1
            worst = min(bar.low, pos.stop) if pos.side == "buy" else max(bar.high, pos.stop)
            best = max(bar.high, pos.target) if pos.side == "buy" else min(bar.low, pos.target)
            # conservative equity: use worst case for open positions
            floating += direction * (worst - pos.entry) * pos.lots * self.point_value_per_lot
        acct.equity = acct.balance + floating
        acct.high_water = max(acct.high_water, acct.equity)

    def open_position(self, pos: PaperPosition) -> None:
        self.account.positions.append(pos)
        self.account.trades_today += 1
        self._persist()

    def resolve_on_bar(self, bar: Bar) -> list[dict]:
        """Mechanical exits only: stop, target, time-stop. Returns closures."""
        closed: list[dict] = []
        acct = self.account
        for pos in acct.positions:
            if not pos.open:
                continue
            direction = 1 if pos.side == "buy" else -1
            exit_price: float | None = None
            reason = ""
            hit_stop = (bar.low <= pos.stop) if pos.side == "buy" else (bar.high >= pos.stop)
            hit_target = (bar.high >= pos.target) if pos.side == "buy" else (bar.low <= pos.target)
            # pessimistic ordering: stop before target inside the same bar
            if hit_stop:
                exit_price, reason = pos.stop, "stop"
            elif hit_target:
                exit_price, reason = pos.target, "target"
            elif bar.ts_close >= pos.time_stop_ts:
                exit_price, reason = bar.close, "time_stop"
            if exit_price is None:
                continue
            pnl = direction * (exit_price - pos.entry) * pos.lots * self.point_value_per_lot
            pnl -= pos.commission_paid
            acct.balance += pnl
            acct.daily_pnl += pnl
            acct.equity = acct.balance
            if pnl < 0:
                acct.consecutive_losses += 1
            else:
                acct.consecutive_losses = 0
            record = {
                "ticket_id": pos.ticket_id, "exit": exit_price,
                "reason": reason, "pnl": round(pnl, 2),
                "closed_ts": bar.ts_close,
            }
            closed.append(record)
            acct.closed_trades.append(record)
            pos.open = False
        acct.positions = [p for p in acct.positions if p.open]
        self._persist()
        return closed

    def force_close_all(self, bar: Bar, why: str) -> list[dict]:
        closed = []
        for pos in list(self.account.positions):
            if not pos.open:
                continue
            direction = 1 if pos.side == "buy" else -1
            pnl = direction * (bar.close - pos.entry) * pos.lots * self.point_value_per_lot
            pnl -= pos.commission_paid
            self.account.balance += pnl
            self.account.daily_pnl += pnl
            self.account.equity = self.account.balance
            record = {
                "ticket_id": pos.ticket_id, "exit": bar.close,
                "reason": f"forced:{why}", "pnl": round(pnl, 2),
                "closed_ts": bar.ts_close,
            }
            closed.append(record)
            self.account.closed_trades.append(record)
            pos.open = False
        self.account.positions = [p for p in self.account.positions if p.open]
        self._persist()
        return closed

    def _persist(self) -> None:
        data = asdict(self.account)
        data["point_value_per_lot"] = self.point_value_per_lot
        self.path.write_text(json.dumps(data, sort_keys=True, indent=2))
