"""Autonomy ladder (P5 §7) — governed, opt-in, journaled, fail-closed.

    L2  GOLD_DESK_AUTONOMY=L2  scheduled watchlist research
    L3  GOLD_DESK_AUTONOMY=L3  agent may DRAFT proposals through the same
                                gate/sizing/ticket path (human still approves)
    L4  separate sign-off env   paper auto-exec — default OFF forever

The scheduler is deliberately ~50 LOC reading config/watch.yaml (assets,
cadence, quiet hours). No framework. Crontab works equally well:

    15 8,16 * * 1-5  cd ~/gold-desk && python -m gold_desk.cli watch --once
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import yaml

from ..events import Journal
from .journal_util import default_journal
from ..llm.zen_client import LLMUnavailable
from .loop import resolve_models

REPO_ROOT = Path(__file__).resolve().parents[3]

DEFAULT_WATCH = {
    "assets": ["XAUUSD"],
    "interval_hours": 8,
    "quiet_hours_utc": [21, 7],       # no runs inside this window
    "depth": 1,
}


def autonomy_level() -> int:
    raw = (os.environ.get("GOLD_DESK_AUTONOMY") or "").strip().upper()
    if raw in ("L2", "2"):
        return 2
    if raw in ("L3", "3"):
        return 3
    if raw in ("L4", "4"):
        return 4          # still gated by the separate sign-off env below
    return 1


def l4_paper_execution_enabled() -> bool:
    """L4 requires an ADDITIONAL explicit owner sign-off env var."""
    return (os.environ.get("GOLD_DESK_L4_PAPER_SIGNOFF") or "").strip().lower() \
        in ("i-own-this", "yes", "true", "1") and autonomy_level() >= 4


def load_watch_config() -> dict:
    path = REPO_ROOT / "config" / "watch.yaml"
    if path.exists():
        try:
            cfg = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError:
            cfg = {}
        merged = dict(DEFAULT_WATCH)
        merged.update({k: v for k, v in cfg.items() if v is not None})
        return merged
    return dict(DEFAULT_WATCH)


def in_quiet_hours(hour_utc: int | None = None) -> bool:
    cfg = load_watch_config()
    lo, hi = cfg.get("quiet_hours_utc") or [21, 7]
    h = hour_utc if hour_utc is not None else time.gmtime().tm_hour
    if lo <= hi:
        return lo <= h < hi
    return h >= lo or h < hi          # window wraps midnight


def watch_once(*, data_root: str | Path = "data",
               journal: Journal | None = None,
               force: bool = False) -> dict:
    """One watch pass over the configured assets. L2 behavior: research
    each asset at the configured depth; emit a delta brief only when
    something material moved. Returns a summary dict (never raises)."""
    from .journal_util import default_journal
    jr = journal or default_journal(data_root)
    cfg = load_watch_config()
    level = autonomy_level()

    if level < 2 and not force:
        return {"ok": False, "status": "autonomy_below_l2",
                "detail": "set GOLD_DESK_AUTONOMY=L2 (or pass --force)"}
    if not force and in_quiet_hours():
        jr.emit("AgentRunFinished", {
            "run_id": "watch", "status": "quiet_hours",
            "detail": "watch pass skipped (quiet hours)",
        })
        return {"ok": True, "status": "quiet_hours", "assets": []}

    from .research import research
    results = []
    assets = cfg.get("assets") or ["XAUUSD"]
    depth = int(cfg.get("depth") or 1)
    for asset in assets[:6]:                 # hard cap: 6 assets per pass
        try:
            out = research(asset, data_root=data_root, depth=depth,
                           journal=jr)
            results.append({"asset": asset, "ok": bool(out.get("ok")),
                            "path": out.get("report_path", ""),
                            "status": out.get("status", "")})
        except Exception as e:  # noqa: BLE001 — one asset failing must not
            results.append({"asset": asset, "ok": False,   # kill the pass
                            "status": "error",
                            "detail": f"{type(e).__name__}: {e}"})
    jr.emit("AgentRunFinished", {
        "run_id": "watch", "status": "watch_pass_complete",
        "assets": [r["asset"] for r in results],
        "ok_count": sum(1 for r in results if r.get("ok")),
    })
    return {"ok": any(r.get("ok") for r in results), "assets": results,
            "autonomy": f"L{level}"}


# --------------------------------------------------------------------------
# L3 — proposal drafting through the EXISTING pipeline (L14: proposals are
# not tickets; only ticket.py mints IDs, and only the human FILLs).
# --------------------------------------------------------------------------

def draft_proposal(asset: str, *, data_root: str | Path = "data",
                   journal: Journal | None = None) -> dict:
    """L3: ask the analyst to shape a setup-engine-compatible candidate.
    The draft goes through the SAME filters -> sizing -> gate path; a
    surviving draft becomes a normal Telegram ticket requiring human
    approval (origin-tagged so histograms can track agent-origin rates).

    XAUUSD only until the constitution changes — research covers any asset,
    proposals do not (non-goal §14)."""
    from .journal_util import default_journal
    jr = journal or default_journal(data_root)
    if autonomy_level() < 3:
        return {"ok": False, "status": "autonomy_below_l3",
                "detail": "set GOLD_DESK_AUTONOMY=L3 to allow drafting"}
    if asset.strip().upper() not in ("XAUUSD", "GOLD", "AU"):
        return {"ok": False, "status": "asset_not_proposable",
                "detail": "proposals are XAUUSD-only until the "
                          "constitution changes (non-goal §14)"}

    try:
        from .loop import run_agent
        from .desk_tools import desk_registry
        reg = desk_registry()
        from .browse import browse_tools
        for t in browse_tools():
            reg.register(t)
        result = run_agent(
            f"Draft a trading PROPOSAL (not a trade) for XAUUSD H1 using the "
            f"GUESS london range breakout spec: check the current pre-London "
            f"range and ATR via tools, and state entry/stop/target/time-stop "
            f"levels the setup would imply IF a signal bar closed now. "
            f"Clearly mark this as a draft proposal for the human.",
            reg, data_root=data_root, journal=jr, max_steps=8,
            model=(resolve_models(None, data_root) or [None])[0],
        )
        jr.emit("ProposalDrafted", {
            "run_id": result.run_id, "asset": "XAUUSD",
            "origin": f"agent:{result.run_id}",
            "ok": result.ok, "answer_head": result.answer[:400],
        }, model_id=result.model)
        return {"ok": result.ok, "run_id": result.run_id,
                "answer": result.answer, "origin": f"agent:{result.run_id}",
                "status": result.status}
    except LLMUnavailable as e:
        return {"ok": False, "status": "provider_error", "detail": str(e)}
