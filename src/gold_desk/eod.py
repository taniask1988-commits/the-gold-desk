"""§10.1 — end-of-day summary. To the HUMAN only, never into any veto path.
Reason-code histogram, tickets, paper PnL, and an explicit "did the filters
eat the session" read-out — that is how you learn 'no edge' vs 'spread
filter ate London open'."""
from __future__ import annotations

from .events import Journal
from .replay import replay_day


def eod_summary(data_root, date: str, account=None) -> str:
    report = replay_day(data_root, date)
    lines = [f"EOD SUMMARY {date}", "=" * 40]
    bars = report["bars"]
    lines.append(f"bars processed : {len(bars)}")
    hist = report["histogram"]
    for code, n in sorted(hist.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {code:24s} {n}")
    lines.append(f"tickets        : {len(report['tickets'])}")
    for t in report["tickets"]:
        lines.append(f"  - {t['ticket_id']} {t['side']} status={t['status']}")
    if account is not None:
        lines.append(f"paper balance  : {account.balance:.2f}")
        lines.append(f"paper equity   : {account.equity:.2f}")
        lines.append(f"day pnl        : {account.daily_pnl:.2f}")
        wins = [t for t in account.closed_trades if t.get("pnl", 0) > 0]
        losses = [t for t in account.closed_trades if t.get("pnl", 0) <= 0]
        total = wins + losses
        if total:
            lines.append(f"closed trades  : {len(total)} "
                         f"(W {len(wins)} / L {len(losses)})")
    lines.append("llm spend      : $0.00 (phase 1 — no LLM in the loop)")
    return "\n".join(lines)
