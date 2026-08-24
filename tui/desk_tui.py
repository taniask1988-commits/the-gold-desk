#!/usr/bin/env python3
"""GOLD DESK COMMAND — TUI. Read-only telemetry over the harness journal.

Pure stdlib (curses). Mirrors the web command deck: driver board, journal
wire, reason histogram, tickets, constitution. Cannot trade, cannot mutate.

Keys:
    ← / →     prev / next journal day          ↑ / ↓  scroll wire
    a         all-time histogram toggle        d      force driver drift
    r         refresh from disk                q      quit

Run:  python3 tui/desk_tui.py [--data-root DIR]
"""
from __future__ import annotations

import argparse
import curses
import json
import os
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_DATA = Path(os.environ.get("GOLD_DESK_DATA") or REPO / "data")

# ---------------------------------------------------------------- palette ids
C_GOLD, C_GREEN, C_RED, C_AMBER, C_DIM, C_TEXT, C_CYAN = range(7)
REASON_COLOR = {
    "FILL": C_GREEN, "HUMAN_SKIP": C_DIM, "TICKET_EXPIRED": C_AMBER,
    "TICKET_SENT": C_GOLD, "NO_SETUP": C_DIM, "SESSION": C_DIM,
    "SPREAD": C_AMBER, "NEWS_BLACKOUT": C_RED, "NEWS_UNAVAILABLE": C_RED,
    "STALE_DATA": C_RED, "OUTLIER_PRICE": C_RED, "CONSEC_LOSS": C_RED,
    "OPEN_POSITION": C_CYAN, "BUDGET": C_RED, "MAX_TRADES": C_AMBER,
    "GATE_REJECT": C_AMBER, "CONSTITUTION_BLOCKED": C_RED,
    "IGNORED_LATE_RESPONSE": C_AMBER, "LLM_VETO": C_RED,
}

# ------------------------------------------- driver taxonomy (docs/MARKET_DRIVERS.md)
# (id, tier, name, base, vol, unit, lo, hi, high_stance)
DRIVERS = [
    ("D1", 1, "10y Real Yield", 1.74, 0.028, "%", 1.6, 1.9, "HEADWIND"),
    ("D2", 1, "DXY Dollar", 99.4, 0.34, "", 98.5, 100.5, "HEADWIND"),
    ("D3", 1, "Fed Path OIS", 3.90, 0.05, "%", 3.75, 4.05, "HEADWIND"),
    ("D4", 1, "Breakeven Infl", 2.34, 0.03, "%", 2.25, 2.45, "TAILWIND"),
    ("D5", 2, "COT Mgr Money", 196.0, 9.0, "k", 150, 240, "HEADWIND"),
    ("D6", 2, "ETF Flow 30d", 12.0, 8.0, "t", -20, 40, "TAILWIND"),
    ("D7", 2, "Central Banks", 222.0, 6.0, "t", 180, 260, "TAILWIND"),
    ("D8", 2, "COMEX-LBMA EFP", 14.5, 2.2, "$", 8, 22, "TAILWIND"),
    ("D9", 3, "Hours To Print", 19.0, 3.5, "h", 8, 999, "HEADWIND"),
    ("D10", 3, "VIX Regime", 16.8, 1.2, "", 14, 20, "TAILWIND"),
    ("D11", 4, "Session Liquidity", 7.0, 1.4, "/10", 5, 11, "TAILWIND"),
    ("D12", 4, "Dealer Gamma", 12.0, 12.0, "", -25, 25, "HEADWIND"),
    ("D13", 4, "Spread x Min", 1.1, 0.16, "x", 0, 1.4, "HEADWIND"),
]


class _Rng:
    def __init__(self, seed: int):
        self.state = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state / 0xFFFFFFFF


def _fnv(s: str) -> int:
    h = 2166136261
    for c in s:
        h = ((h ^ ord(c)) * 16777619) & 0xFFFFFFFF
    return h


def simulate(day: str, tick: int):
    out = []
    for did, tier, name, base, vol, unit, lo, hi, high_stance in DRIVERS:
        g = _Rng(_fnv(f"{day}:{did}:{tick // 6}"))
        v = base
        for _ in range(24):
            v += (g.next() - 0.5) * vol
            v = v * 0.92 + base * 0.08
        if lo <= v <= hi:
            stance = "NEUTRAL"
        elif v > hi:
            stance = high_stance
        else:
            stance = "TAILWIND" if high_stance == "HEADWIND" else "HEADWIND"
        out.append((did, tier, name, v, stance, unit))
    return out


# ---------------------------------------------------------------- data access
def read_day(root: Path, day: str):
    path = root / "events" / f"{day}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def read_days(root: Path):
    d = root / "events"
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.jsonl"))


def read_all(root: Path):
    out = []
    for day in read_days(root):
        out.extend(read_day(root, day))
    return out


def hist_of(events):
    h = {}
    for e in events:
        rc = e.get("reason_code")
        if rc:
            h[rc] = h.get(rc, 0) + 1
    return sorted(h.items(), key=lambda kv: -kv[1])


def session_of(dt: datetime) -> str:
    h = dt.hour
    if h < 7:
        return "ASIA"
    if h < 12:
        return "LONDON"
    if h < 16:
        return "LDN-NY OVERLAP"
    if h < 21:
        return "NEW YORK"
    return "OFF"


# ---------------------------------------------------------------- UI helpers
def ch(win, y, x, text, color=C_DIM, attr=0):
    try:
        win.addstr(y, x, text, curses.color_pair(color) | attr)
    except curses.error:
        pass


def main(stdscr, data_root: Path):
    curses.curs_set(0)
    stdscr.timeout(1000)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(C_GOLD, 214, -1)
    curses.init_pair(C_GREEN, 71, -1)
    curses.init_pair(C_RED, 203, -1)
    curses.init_pair(C_AMBER, 179, -1)
    curses.init_pair(C_DIM, 245, -1)
    curses.init_pair(C_TEXT, 252, -1)
    curses.init_pair(C_CYAN, 80, -1)

    days = read_days(data_root)
    if not days:
        stdscr.addstr(0, 0, "no journal found under " + str(data_root))
        stdscr.refresh()
        stdscr.getch()
        return
    all_events = read_all(data_root)
    ticket_days = sorted({(e.get("decision_ts") or e.get("ts", ""))[:10]
                          for e in all_events if e.get("kind") == "TicketEvent"})
    idx = (days.index(ticket_days[-1])
           if ticket_days and ticket_days[-1] in days else len(days) - 1)
    all_hist = hist_of(all_events)

    scroll = 0
    show_all = False
    tick = 0

    while True:
        H, W = stdscr.getmaxyx()
        if H < 20 or W < 110:
            stdscr.erase()
            stdscr.addstr(0, 0, f"terminal too small ({W}x{H}); need >= 110x20")
            stdscr.refresh()
            key = stdscr.getch()
            if key in (ord("q"), 27):
                return
            continue

        day = days[idx]
        events = read_day(data_root, day)
        hist = all_hist if show_all else hist_of(events)
        tick += 1
        drivers = simulate(day, tick)
        now = datetime.now(timezone.utc)

        stdscr.erase()
        # ------------------------------------------------ header
        ch(stdscr, 0, 1, " Au ", C_GOLD, curses.A_BOLD | curses.A_REVERSE)
        ch(stdscr, 0, 6, "GOLD DESK COMMAND", C_GOLD, curses.A_BOLD)
        ch(stdscr, 0, 25, "· XAUUSD H1 · FAIL-CLOSED · PHASE 1 · NO LLM", C_DIM)
        try:
            ch(stdscr, 0, max(25, W - 36),
               f"{now.strftime('%H:%M:%S')} UTC  {session_of(now)}", C_TEXT)
        except Exception:
            pass
        try:
            ch(stdscr, 1, 0, "─" * W, C_DIM)
        except curses.error:
            pass

        LW, RW = 37, 34
        MW = max(20, W - LW - RW)
        top = 2
        body_h = H - top - 3

        # ------------------------------------------------ drivers (left)
        ch(stdscr, top, 1, "MARKET DRIVERS · SIM", C_TEXT, curses.A_BOLD)
        y = top + 1
        tier_names = {1: "T1 MACRO REGIME", 2: "T2 POSITIONING/FLOWS",
                      3: "T3 EVENT RISK", 4: "T4 MICROSTRUCTURE"}
        cur_tier = 0
        for did, tier, name, v, stance, unit in drivers:
            if y >= H - 5:
                break
            if tier != cur_tier:
                cur_tier = tier
                ch(stdscr, y, 1, tier_names[tier], C_GOLD, curses.A_BOLD)
                y += 1
            col = {"TAILWIND": C_GREEN, "HEADWIND": C_RED, "NEUTRAL": C_DIM}[stance]
            mark = {"TAILWIND": "▲", "HEADWIND": "▼", "NEUTRAL": "•"}[stance]
            val = f"{v:,.2f}{unit}"
            ch(stdscr, y, 1, did, C_DIM)
            ch(stdscr, y, 5, name[:16].ljust(16), C_TEXT)
            ch(stdscr, y, 22, val[:11].rjust(11), col, curses.A_BOLD)
            ch(stdscr, y, 34, mark, col)
            y += 1
        bias = sum({"TAILWIND": 1, "HEADWIND": -1, "NEUTRAL": 0}[d[4]] for d in drivers)
        ch(stdscr, y + 1, 1, f"raw bias {bias:+d}/13 drivers", C_CYAN)
        ch(stdscr, y + 2, 1, "docs/MARKET_DRIVERS.md", C_DIM)

        # ------------------------------------------------ wire (middle)
        mx = LW + 1
        ch(stdscr, top, mx, f"JOURNAL WIRE · {day}", C_TEXT, curses.A_BOLD)
        ch(stdscr, top, mx + 30, "←→ day · ↑↓ scroll", C_DIM)
        wire_h = body_h - 2
        y = top + 1
        for e in events[scroll: scroll + wire_h]:
            if y >= H - 3:
                break
            t = (e.get("decision_ts") or e.get("ts", ""))[11:19]
            kind = e.get("kind", "?")
            rc = e.get("reason_code")
            kcol = {"TicketEvent": C_GOLD, "Fill": C_GREEN,
                    "SetupCandidate": C_CYAN, "GateDecision": C_GOLD,
                    "FilterReject": C_AMBER, "DataQualityFailed": C_RED}.get(kind, C_DIM)
            ch(stdscr, y, mx, t, C_DIM)
            ch(stdscr, y, mx + 9, kind[:18].ljust(18), kcol)
            if rc:
                col = REASON_COLOR.get(rc, C_DIM)
                ch(stdscr, y, mx + 28, f"[{rc}]", col, curses.A_BOLD)
            p = e.get("payload", {})
            det = p.get("detail") or p.get("code") or ""
            if p.get("bar"):
                det = f"O:{p['bar']['o']} C:{p['bar']['c']}"
            off = mx + 28 + (len(rc) + 3 if rc else 1)
            ch(stdscr, y, off, str(det)[: max(1, W - RW - off - 2)], C_DIM)
            y += 1
        ch(stdscr, top, W - RW - 10, f"{scroll}/{len(events)}", C_DIM)

        # ------------------------------------------------ histogram (right)
        rx = W - RW + 1
        scope_lbl = "ALL-TIME" if show_all else "THIS DAY"
        ch(stdscr, top, rx, f"REASON CODES · {scope_lbl} [a]", C_TEXT, curses.A_BOLD)
        maxn = max((n for _, n in hist), default=1)
        y = top + 1
        for code, n in hist[: body_h - 2]:
            col = REASON_COLOR.get(code, C_DIM)
            ch(stdscr, y, rx, code[:22].ljust(22), col)
            bar_w = int((n / maxn) * (RW - 14))
            ch(stdscr, y, rx + 23, "█" * max(0, bar_w), col)
            ch(stdscr, y, rx + 23 + max(0, bar_w) + 1, str(n), C_TEXT)
            y += 1

        # ------------------------------------------------ status line
        tickets = [e for e in events if e.get("kind") == "TicketEvent"]
        fills = sum(1 for e in all_events if e.get("reason_code") == "FILL")
        try:
            acct = json.loads((data_root / "account.json").read_text())
        except Exception:
            acct = {}
        bal = acct.get("balance", 0)
        zen = ""
        try:
            zc = json.loads((data_root / "zen-catalog.json").read_text())
            zen = f"  ·  ZEN {zc.get('default', '?')} ({len(zc.get('models', {}))} free)"
        except Exception:
            pass
        ch(stdscr, H - 2, 1,
           f"TICKETS {len(tickets)} this day · FILLS {fills} total · PAPER BAL {bal:,.2f}{zen}",
           C_GOLD)

        # ------------------------------------------------ footer
        try:
            ch(stdscr, H - 1, 0, "─" * W, C_DIM)
        except curses.error:
            pass
        ch(stdscr, H - 1, 1,
           "q quit · ← → day · ↑↓ scroll · a all-time · d drift · r refresh  —  read-only telemetry · nothing is promoted by narrative",
           C_DIM)

        stdscr.refresh()
        key = stdscr.getch()
        if key in (ord("q"), 27):
            return
        elif key == curses.KEY_RIGHT:
            idx = min(len(days) - 1, idx + 1)
            scroll = 0
        elif key == curses.KEY_LEFT:
            idx = max(0, idx - 1)
            scroll = 0
        elif key == curses.KEY_DOWN:
            scroll = min(max(0, len(events) - 3), scroll + 5)
        elif key == curses.KEY_UP:
            scroll = max(0, scroll - 5)
        elif key == ord("a"):
            show_all = not show_all
        elif key == ord("r"):
            days = read_days(data_root)
            all_events = read_all(data_root)
            all_hist = hist_of(all_events)
        elif key == ord("d"):
            tick += 6


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Gold Desk Command TUI")
    ap.add_argument("--data-root", default=str(DEFAULT_DATA))
    args = ap.parse_args()
    try:
        curses.wrapper(main, Path(args.data_root))
    except KeyboardInterrupt:
        # Ctrl+C at the getch() prompt — clean exit, no traceback
        print("\n  desk closed.")
