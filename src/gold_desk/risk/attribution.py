"""R3-3 BUILD 5b — P&L attribution over a trade ledger.

A ledger row is a plain dict:
    {"symbol": "XAUUSD", "side": "buy"|"sell"|"long"|"short",
     "qty": 0.5, "entry": 2400.0, "exit": 2412.0,
     "timestamp": "2026-06-01T08:00:00Z", "setup_tag": "GUESS_..."}

P&L convention (unit-agnostic, equities-style): long → qty·(exit−entry),
short → qty·(entry−exit) — so a short entered at 100 exited at 90 books
+10·qty. No commission/point-value assumptions live here; the paper
account's own dollar accounting stays in account.py.

Views (`attribute`):
* by_asset  — Σ P&L, % of total, trade count, win rate per symbol
* by_setup  — Σ P&L, win rate, trade count per setup_tag
* by_hour   — ALL 24 UTC buckets (always present, zero-filled) with the
  session label: hour < 8 → Asia, 8 ≤ hour ≤ 12 → London, hour ≥ 13 → NY
* totals    — total P&L, wins/losses, win rate, gross profit/loss,
  profit factor

Conservation is structural: every view aggregates the SAME parsed rows,
so Σ by_asset == Σ by_setup == Σ by_hour == total to float precision
(the test suite pins 1e-9).

Two ledger sources:
* `ledger_from_journal(events)` — reconstructs closed trades from the
  desk's JSONL event journal: TicketEvent payloads supply symbol/setup,
  entry Fill events (payload.status == "paper-position-opened") supply
  entry price/lots/side/decision_ts, exit Fill events (payload.phase in
  {"paper-exit", "forced-close"} with a resolution record) supply exit
  price and the account's own pnl. Entries still open and exits without
  entries are honestly counted, never silently dropped.
* `synthetic_ledger(seed, n_trades)` — a deterministic demo ledger
  (mixed symbols/setups/sides/hours) so the CLI works out of the box.

Degenerate inputs fail gracefully: an empty (or entirely-invalid) ledger
returns an all-zeros report with ok=True and the skipped rows surfaced.
Pure stdlib, no I/O of its own.
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from pathlib import Path

LONG_SIDES = {"buy", "long", "b", "l", ""}
SHORT_SIDES = {"sell", "short", "s"}

SYNTHETIC_SYMBOLS = ["XAUUSD", "SPY", "BTC-USD"]
SYNTHETIC_SETUPS = ["GUESS_london_range_breakout", "momentum_breakdown",
                    "ny_reversal", "asia_fade"]
SYNTHETIC_BASE = {"XAUUSD": 2400.0, "SPY": 450.0, "BTC-USD": 80000.0}


# ------------------------------------------------------------------ sessions
def session_for_hour(hour: int) -> str:
    """UTC hour → trading-session label. Boundaries pinned by test:
    7 → Asia, 8 → London, 12 → London, 13 → NY."""
    if hour < 8:
        return "Asia"
    if hour < 13:
        return "London"
    return "NY"


def parse_timestamp(ts) -> datetime | None:
    """Best-effort timestamp → aware UTC datetime. Accepts ISO 8601
    (trailing Z tolerated, naive assumed UTC) and epoch seconds/millis
    (int/float or numeric string). None when unparseable."""
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        return _epoch_to_utc(float(ts))
    if isinstance(ts, str):
        raw = ts.strip()
        if not raw:
            return None
        try:
            iso = raw.replace("Z", "+00:00").replace("z", "+00:00")
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
        try:
            return _epoch_to_utc(float(raw))
        except ValueError:
            return None
    return None


def _epoch_to_utc(seconds: float) -> datetime:
    # heuristics: > 1e11 means the number is in milliseconds
    if abs(seconds) > 1e11:
        seconds /= 1000.0
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


# ------------------------------------------------------------------ rows
def trade_pnl(row: dict) -> float:
    """Signed P&L of one trade row (spec convention; side case-insensitive
    so raw un-normalized rows can be scored directly)."""
    qty = float(row["qty"])
    entry = float(row["entry"])
    exit_ = float(row["exit"])
    if str(row.get("side", "")).strip().lower() in SHORT_SIDES:
        return qty * (entry - exit_)
    return qty * (exit_ - entry)


def normalize_ledger(rows: list) -> tuple[list[dict], list[dict]]:
    """Coerce raw ledger rows to the canonical schema. Returns
    (normalized rows, skipped rows with a reason). A row is skipped when
    symbol/qty/entry/exit are missing or non-numeric, or the side is not
    a recognizable long/short marker — never a crash."""
    out: list[dict] = []
    skipped: list[dict] = []
    for i, raw in enumerate(rows or []):
        if not isinstance(raw, dict):
            skipped.append({"index": i, "reason": "not an object",
                            "row": str(raw)[:80]})
            continue
        symbol = str(raw.get("symbol", "") or "").strip().upper()
        side = str(raw.get("side", "") or "").strip().lower()
        qty, entry, exit_ = raw.get("qty"), raw.get("entry"), raw.get("exit")
        try:
            qty_f, entry_f, exit_f = float(qty), float(entry), float(exit_)
        except (TypeError, ValueError):
            skipped.append({"index": i, "reason": "qty/entry/exit not numeric",
                            "row": dict(raw)})
            continue
        if not symbol or side not in (LONG_SIDES | SHORT_SIDES):
            skipped.append({"index": i,
                            "reason": f"unrecognized symbol/side "
                                      f"({symbol!r}/{side!r})",
                            "row": dict(raw)})
            continue
        ts_raw = raw.get("timestamp", raw.get("ts"))
        dt = parse_timestamp(ts_raw)
        out.append({
            "symbol": symbol,
            "side": side,
            "qty": qty_f,
            "entry": entry_f,
            "exit": exit_f,
            "timestamp": (dt.astimezone(timezone.utc).isoformat()
                          if dt else str(ts_raw or "")),
            "setup_tag": str(raw.get("setup_tag", "") or "untagged"),
            "hour": dt.hour if dt else None,
        })
    return out, skipped


# ------------------------------------------------------------------ views
def _agg_bucket():
    return {"pnl": 0.0, "n_trades": 0, "n_wins": 0, "n_losses": 0}


def attribute(ledger: list) -> dict:
    """The full attribution report. Empty ledger → all-zero views, ok."""
    rows, skipped = normalize_ledger(ledger)
    timed = [r for r in rows if r["hour"] is not None]

    by_asset: dict[str, dict] = {}
    by_setup: dict[str, dict] = {}
    by_hour = {h: _agg_bucket() for h in range(24)}
    gross_profit = 0.0
    gross_loss = 0.0
    for r in rows:
        pnl = trade_pnl(r)
        for book, key in ((by_asset, r["symbol"]), (by_setup, r["setup_tag"])):
            bucket = book.setdefault(key, _agg_bucket())
            bucket["pnl"] += pnl
            bucket["n_trades"] += 1
            if pnl >= 0:
                bucket["n_wins"] += 1
            else:
                bucket["n_losses"] += 1
        if r["hour"] is not None:
            bucket = by_hour[r["hour"]]
            bucket["pnl"] += pnl
            bucket["n_trades"] += 1
            if pnl >= 0:
                bucket["n_wins"] += 1
            else:
                bucket["n_losses"] += 1
        if pnl >= 0:
            gross_profit += pnl
        else:
            gross_loss += -pnl

    total = sum(b["pnl"] for b in by_asset.values())
    n_trades = len(rows)
    n_wins = sum(1 for r in rows if trade_pnl(r) >= 0)

    def view(book: dict, key_name: str) -> list[dict]:
        out = []
        for key in sorted(book, key=lambda k: (-book[k]["pnl"], k)):
            b = book[key]
            out.append({
                key_name: key,
                "pnl": b["pnl"],
                "pct_of_total": (b["pnl"] / total) if total else 0.0,
                "n_trades": b["n_trades"],
                "n_wins": b["n_wins"],
                "n_losses": b["n_losses"],
                "win_rate": (b["n_wins"] / b["n_trades"])
                            if b["n_trades"] else 0.0,
            })
        return out

    hourly = []
    for h in range(24):
        b = by_hour[h]
        hourly.append({
            "hour": h,
            "session": session_for_hour(h),
            "pnl": b["pnl"],
            "n_trades": b["n_trades"],
            "win_rate": (b["n_wins"] / b["n_trades"]) if b["n_trades"] else 0.0,
        })

    return {
        "ok": True,
        "n_trades": n_trades,
        "n_wins": n_wins,
        "n_losses": n_trades - n_wins,
        "win_rate": (n_wins / n_trades) if n_trades else 0.0,
        "total_pnl": total,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (gross_profit / gross_loss) if gross_loss else None,
        "by_asset": view(by_asset, "symbol"),
        "by_setup": view(by_setup, "setup"),
        "by_hour": hourly,
        "n_unparsed_timestamps": len(rows) - len(timed),
        "n_skipped_rows": len(skipped),
        "skipped_rows": skipped[:20],
    }


# ------------------------------------------------------------------ sources
def ledger_from_journal(events: list[dict]) -> dict:
    """Reconstruct closed trades from the desk's event journal.

    Joins (in journal order): TicketEvent payloads (symbol, setup_id,
    lots, side), entry Fill events (payload.status ==
    "paper-position-opened" → fill price + decision_ts) and exit Fill
    events (payload.resolution → exit price + account pnl). Returns
    {"ledger": rows, "n_entries": .., "n_exits": .., "matched": ..,
    "open_or_unmatched": .., "unmatched_exits": ..} — honesty counters
    first, no silent drops."""
    tickets: dict[str, dict] = {}
    entries: dict[str, dict] = {}
    exits: dict[str, dict] = {}
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        kind = ev.get("kind")
        payload = ev.get("payload") or {}
        if not isinstance(payload, dict):
            continue
        if kind == "TicketEvent":
            tid = payload.get("ticket_id")
            if tid:
                tickets[tid] = payload
        elif kind == "Fill":
            tid = payload.get("ticket_id")
            resolution = payload.get("resolution")
            if isinstance(resolution, dict):
                rid = resolution.get("ticket_id")
                if rid:
                    exits[rid] = {"exit": resolution.get("exit"),
                                  "closed_ts": resolution.get("closed_ts"),
                                  "reason": resolution.get("reason"),
                                  "account_pnl": resolution.get("pnl"),
                                  "ts": ev.get("ts")}
            elif tid and payload.get("price") is not None:
                entries[tid] = {"price": payload.get("price"),
                                "lots": payload.get("lots"),
                                "side": payload.get("side"),
                                "ts": ev.get("decision_ts") or ev.get("ts")}
    ledger: list[dict] = []
    matched = set(exits) & set(entries)
    for tid, entry in entries.items():
        if tid not in exits:
            continue                     # position still open — not a trade
        exit_rec = exits[tid]
        ticket = tickets.get(tid, {})
        ledger.append({
            "symbol": str(ticket.get("symbol") or "XAUUSD"),
            "side": str(entry.get("side") or ticket.get("side") or "buy"),
            "qty": float(entry.get("lots") or 0.0),
            "entry": float(entry.get("price")),
            "exit": float(exit_rec["exit"]),
            "timestamp": entry.get("ts") or exit_rec.get("closed_ts") or "",
            "setup_tag": str(ticket.get("setup_id") or "untagged"),
        })
    # stable, replay-friendly order: by entry timestamp then ticket id
    ledger.sort(key=lambda r: (str(r["timestamp"]), str(r["setup_tag"])))
    return {
        "ledger": ledger,
        "n_entry_fills": len(entries),
        "n_exit_fills": len(exits),
        "matched": len(ledger),
        "open_or_unmatched": len(entries) - len(ledger),
        "unmatched_exits": len([t for t in exits if t not in entries]),
    }


def load_journal_ledger(data_root) -> dict:
    """Read <data_root>/events/*.jsonl (the desk journal) and reconstruct."""
    from ..events import Journal           # local import: no I/O at module load
    events = Journal.read_events(data_root)   # read_events appends /events
    return ledger_from_journal(events)


def read_ledger_file(path) -> list:
    """A ledger file: JSONL (one row per line) or a JSON array."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    stripped = text.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        data = json.loads(stripped)
        return data if isinstance(data, list) else [data]
    rows = []
    for line in stripped.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append(json.loads(line))
    return rows


def synthetic_ledger(seed: int = 11, n_trades: int = 24) -> list[dict]:
    """Deterministic demo ledger: mixed symbols, setups, sides and hours
    spread over the three sessions (Asia/London/NY). Same seed → the
    identical ledger, so the CLI demo and its tests are reproducible."""
    rng = random.Random(seed)
    hours = [2, 3, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 18, 20, 21]
    rows: list[dict] = []
    day = 1
    for i in range(max(1, n_trades)):
        if i and i % 4 == 0:
            day += 1
        symbol = SYNTHETIC_SYMBOLS[i % len(SYNTHETIC_SYMBOLS)]
        setup = SYNTHETIC_SETUPS[i % len(SYNTHETIC_SETUPS)]
        hour = hours[i % len(hours)]
        side = "short" if rng.random() < 0.35 else "buy"
        entry = SYNTHETIC_BASE[symbol] * (1.0 + 0.01 * rng.uniform(-1, 1))
        risk = 0.004 * SYNTHETIC_BASE[symbol]
        win = rng.random() < 0.45
        move = risk * (rng.uniform(1.0, 2.2) if win
                       else -rng.uniform(0.6, 1.3))
        exit_ = entry + move if side == "buy" else entry - move
        qty = round(rng.choice([0.5, 1.0, 2.0, 3.0]), 2)
        rows.append({
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "entry": round(entry, 2),
            "exit": round(exit_, 2),
            "timestamp": f"2026-06-{day:02d}T{hour:02d}:{(i * 17) % 60:02d}:00Z",
            "setup_tag": setup,
        })
    return rows


# ------------------------------------------------------------------ report
def attribution_report(ledger: list | None = None, source: str = "ledger"
                       ) -> dict:
    """Convenience wrapper: attribute() + the source echoed."""
    out = attribute(ledger or [])
    out["source"] = source
    return out
