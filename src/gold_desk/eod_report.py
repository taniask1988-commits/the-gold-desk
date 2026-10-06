"""GAUNTLET 7 Piece 7 — the branded EOD report.

Builds the desk's end-of-day report FROM THE JOURNAL ALONE (plus
fail-soft, clearly-labelled market state): reason-code histogram with
inline bar blocks, tickets table, desk view, market state. Markdown
output — the same artifact the web deck and Telegram consume.

Honesty labels: market-state rows carry their source + timestamp; a
failed feed appears as "unavailable", never omitted silently.
No LLM, no PnL plumbing beyond what the journal itself records.
"""
from __future__ import annotations

from datetime import datetime, timezone


_BRAND = "GOLD DESK COMMAND"
_HIST_BAR_CHARS = 28


def _bar(n: int, n_max: int) -> str:
    if n_max <= 0:
        return ""
    return "█" * max(1, round(n / n_max * _HIST_BAR_CHARS))


def _market_state(data_root) -> list[str]:
    """Fail-soft spot + drivers read-out, every row source-labelled."""
    lines: list[str] = ["## Market state", ""]
    try:
        from .data.feeds import fetch_spot
        spot = fetch_spot(data_root)
        if spot.get("ok"):
            lines.append(f"- XAUUSD spot: **{spot.get('price')}** "
                         f"(source: {spot.get('source')}, "
                         f"market time {spot.get('market_time', 'n/a')})")
        else:
            lines.append("- XAUUSD spot: unavailable "
                         f"({spot.get('error', 'feed failed')})")
    except Exception as exc:  # noqa: BLE001 — fail-soft
        lines.append(f"- XAUUSD spot: unavailable ({type(exc).__name__})")
    try:
        from .data.driver_feeds import fetch_driver_values
        dv = fetch_driver_values(data_root) or {}
        live = dv.get("live") or {}
        names = {"D1": "10y real yield", "D2": "DXY", "D3": "policy rate",
                 "D4": "10y breakeven", "D5": "COT managed money",
                 "D9": "NFP clock", "D10": "VIX", "D11": "session liq"}
        for did in sorted(live)[:8]:
            row = live.get(did) or {}
            if isinstance(row, dict) and row.get("value") is not None:
                lines.append(f"- {names.get(did, did)}: "
                             f"**{row.get('value')}** {row.get('unit', '')} "
                             f"(source: {row.get('source', 'n/a')})")
        for u in (dv.get("unavailable") or [])[:6]:
            lines.append(f"- {u}: unavailable")
    except Exception as exc:  # noqa: BLE001 — fail-soft
        lines.append(f"- drivers: unavailable ({type(exc).__name__})")
    lines.append("")
    return lines


def eod_report_md(data_root, date: str) -> str:
    """The branded EOD report for `date`, from the journal alone."""
    from .replay import replay_day
    report = replay_day(data_root, date)
    bars = report.get("bars") or []
    hist = report.get("histogram") or {}
    tickets = report.get("tickets") or []
    n_events = sum(hist.values()) if hist else len(bars)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"# {_BRAND} — END OF DAY",
        f"**{date}** · generated {now} · from journal alone "
        f"({n_events} journaled bar decisions)",
        "",
        "## Reason-code histogram",
        "",
    ]
    if hist:
        n_max = max(hist.values())
        for code, n in sorted(hist.items(), key=lambda kv: -kv[1]):
            lines.append(f"- `{code:24s}` **{n:4d}**  {_bar(n, n_max)}")
    else:
        lines.append("- no journal events for this date")
    lines.append("")

    lines += ["## Tickets", ""]
    if tickets:
        for t in tickets:
            lines.append(f"- {t.get('ticket_id', 'n/a')} "
                         f"{t.get('side', '?')} status=**{t.get('status', '?')}**"
                         + (f" · {t.get('note')}" if t.get("note") else ""))
    else:
        lines.append("- no tickets issued (the desk stayed flat — "
                     "check the histogram above for which filters ate "
                     "the session)")
    lines.append("")

    lines += _market_state(data_root)

    lines += [
        "## Desk view",
        "",
        "- phase-1 loop: deterministic, no LLM in the loop (L10)",
        "- every bar above carries exactly one terminal reason code",
        "- tickets require human confirmation — the desk never executes "
        "on its own",
    ]
    return "\n".join(lines) + "\n"
