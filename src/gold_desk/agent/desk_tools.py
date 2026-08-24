"""Tools v1 — the desk's own market, wired as agent tools (P2 §4).

Every tool is a ~10-30 line wrapper over a function that already exists
and is tested: feeds (spot/bars), indicators, news, calendar, drivers,
journal, paper account (SCRUBBED — L12 blindfold).

LAWS:
  - READ-ONLY. None of these can write tickets, touch the constitution,
    or mutate account state. Enforced by tests/test_agent_tools.py which
    walks the registry and asserts every tool's module is in the allowlist.
  - paper_account() output passes the context_pack scrubber: no equity/
    balance/PnL ever leaves the machine (L12).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..context_pack import _scrub
from ..data.feeds import fetch_bars, fetch_news, fetch_spot
from ..data.driver_feeds import fetch_driver_values
from ..events import Journal
from .tools import ToolRegistry, tool

REPO_ROOT = Path(__file__).resolve().parents[3]

# The allowlist: modules a desk tool may live in (test-pinned). Anything
# that can mutate (ticket.py, account.py writers, constitution) is absent
# by construction — importing it here would fail the allowlist test.
ALLOWED_MODULES = {
    "gold_desk.agent.desk_tools",
    "gold_desk.agent.tools",
    "gold_desk.data.feeds",
    "gold_desk.data.driver_feeds",
    "gold_desk.data.calendar",
    "gold_desk.features.indicators",
    "gold_desk.events",
}


# ------------------------------------------------------------------ market


@tool("Live spot price. symbol: 'XAUUSD' (gold) or a coingecko id like "
      "'bitcoin' for crypto. Returns {ok, price, source, ts}.",
      returns="dict")
def get_spot(symbol: str = "XAUUSD") -> dict:
    sym = (symbol or "XAUUSD").strip().upper()
    if sym in ("", "XAUUSD", "GOLD", "AU"):
        out = fetch_spot(REPO_ROOT / "data")
        return {"ok": bool(out.get("ok")), "price": out.get("price"),
                "source": out.get("source"),
                "market_time": out.get("market_time"),
                "prev_close": out.get("prev_close")}
    # crypto via coingecko (P3 asset registry path)
    from .assets import spot_for
    return spot_for(symbol)


@tool("Recent H1 OHLC bars for gold (or a crypto symbol like 'BTC'). "
      "bars: how many (max 200). Returns list of {ts,o,h,l,c} bars.",
      returns="dict")
def get_ohlc(symbol: str = "XAUUSD", bars: int = 48) -> dict:
    sym = (symbol or "XAUUSD").strip().upper()
    n = max(1, min(int(bars), 200))
    if sym in ("", "XAUUSD", "GOLD", "AU"):
        out = fetch_bars(REPO_ROOT / "data", limit=n)
        return {"ok": bool(out.get("ok")), "source": out.get("source"),
                "interval": out.get("interval"),
                "bars": out.get("bars", [])[-n:]}
    from .assets import ohlc_for
    return ohlc_for(symbol, n)


@tool("Technical indicators over H1 bars: 'atr' (Wilder ATR), "
      "'range' (high/low of last N bars). Returns numeric dict.",
      returns="dict")
def indicators(symbol: str = "XAUUSD", kind: str = "atr",
               period: int = 14) -> dict:
    data = get_ohlc(symbol, max(period + 2, 40))
    if not data.get("ok"):
        return {"ok": False, "error": "no bars for indicators"}
    rows = data.get("bars") or []
    if len(rows) < period + 1:
        return {"ok": False, "error": f"need {period + 1} bars, have {len(rows)}"}
    k = (kind or "atr").strip().lower()
    if k == "atr":
        trs = []
        for i in range(1, len(rows)):
            prev_c = rows[i - 1]["c"]
            b = rows[i]
            trs.append(max(b["h"] - b["l"], abs(b["h"] - prev_c),
                           abs(b["l"] - prev_c)))
        value = sum(trs[:period]) / period
        for tr in trs[period:]:
            value = (value * (period - 1) + tr) / period
        return {"ok": True, "atr14": round(value, 4), "bars_used": len(rows)}
    if k == "range":
        window = rows[-period:]
        hi = max(b["h"] for b in window)
        lo = min(b["l"] for b in window)
        return {"ok": True, "high": hi, "low": lo,
                "span": round(hi - lo, 2), "bars_used": len(window)}
    return {"ok": False, "error": f"unknown indicator kind: {kind}"}


# -------------------------------------------------------------------- news


@tool("Recent gold/macro news headlines (Yahoo Finance RSS). "
      "limit: 1-20. Returns list of {title, published, link}.",
      returns="dict")
def get_news(limit: int = 8) -> dict:
    n = max(1, min(int(limit), 20))
    out = fetch_news(REPO_ROOT / "data", limit=n)
    if not out.get("ok"):
        return {"ok": False, "error": out.get("error", "news unreachable")}
    items = [{"title": i["title"], "published": i.get("published"),
              "link": i.get("link", "")} for i in (out.get("items") or [])[:n]]
    return {"ok": True, "count": len(items), "items": items}


@tool("Macro driver readings the desk tracks: DXY, US yields, VIX, "
      "CFTC positioning, NFP — real free-feed values where available.",
      returns="dict")
def get_drivers() -> dict:
    out = fetch_driver_values(REPO_ROOT / "data")
    live = out.get("live") or {}
    rows = {}
    for did, v in live.items():
        rows[did] = {"value": v.get("value"), "unit": v.get("unit"),
                     "source": v.get("source")}
    return {"ok": bool(out.get("ok")), "live": rows,
            "unavailable": out.get("unavailable", [])}


# ----------------------------------------------------------------- journal


@tool("Read the desk's own journal: recent events filtered by kind "
      "(e.g. 'Fill', 'GateDecision', 'TicketEvent') or reason code.",
      returns="dict")
def read_journal(kind: str = "", reason_code: str = "",
                 limit: int = 20) -> dict:
    n = max(1, min(int(limit), 100))
    events = Journal.read_events(REPO_ROOT / "data")
    out = []
    for e in reversed(events):          # newest first
        if kind and e.get("kind") != kind:
            continue
        if reason_code and e.get("reason_code") != reason_code:
            continue
        out.append({"ts": e.get("ts"), "kind": e.get("kind"),
                    "reason_code": e.get("reason_code"),
                    "payload": _slim(e.get("payload") or {})})
        if len(out) >= n:
            break
    return {"ok": True, "count": len(out), "events": out}


def _slim(payload: dict, max_chars: int = 300) -> dict:
    """Journal payloads can be huge (tickets, bars) — clip for the model."""
    s = json.dumps(payload, ensure_ascii=False, default=str)
    if len(s) <= max_chars:
        return payload
    return {"_clipped": True, "head": s[:max_chars]}


# ----------------------------------------------------------------- account


@tool("Paper-account statistics — SCRUBBED (L12 blindfold): win/loss "
      "counts, trade counts and day keys only. No balances, equity, PnL "
      "or position details ever leave the machine.",
      returns="dict")
def paper_account() -> dict:
    path = REPO_ROOT / "data" / "account.json"
    if not path.exists():
        return {"ok": True, "note": "no paper account yet (no demo run)"}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        return {"ok": False, "error": f"account unreadable: {e}"}
    # L12: scrub BEFORE shaping — the scrubber removes equity/balance/pnl/
    # positions/trades_today and anything containing those substrings.
    safe = _scrub(raw)
    closed = raw.get("closed_trades") or []
    wins = sum(1 for t in closed if (t.get("pnl") or 0) > 0)
    return {
        "ok": True,
        "closed_trades": len(closed),
        "wins": wins,
        "losses": len(closed) - wins,
        "day_key": safe.get("day_key", ""),
        "note": "money figures are scrubbed from agent payloads (L12)",
    }


# ---------------------------------------------------------------- registry


def desk_registry() -> ToolRegistry:
    """The P2 tool registry — read-only desk tools."""
    reg = ToolRegistry()
    for t in (get_spot, get_ohlc, indicators, get_news, get_drivers,
              read_journal, paper_account):
        reg.register(t)
    return reg
