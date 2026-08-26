"""Desk-tool bridge: exposes the multi-analyst desk (piece 4) to the chat
window's AGENT MODE as one tool — so the research agent can run the full
5-persona + PM analysis on any symbol mid-conversation.

Read-only (L13/L14): run_desk only reads market data and calls LLMs; it
never mutates desk state, mints tickets, or touches the constitution.
"""
from __future__ import annotations

from ..markets.board import fetch_detail
from .tools import tool

_REPO = None


def _repo_root():
    global _REPO
    if _REPO is None:
        from pathlib import Path
        _REPO = Path(__file__).resolve().parents[3]
    return _REPO


@tool("Run the multi-analyst desk on a symbol: 5 personas (technician, "
      "macro, news, sentiment, risk) judge it in parallel, then a PM "
      "synthesizes consensus, conviction, disagreements and risk flags. "
      "Use for 'what does the desk think about X' questions. Takes "
      "60-150 seconds.",
      returns="dict")
def run_analyst_desk(symbol: str) -> dict:
    # Validate the symbol resolves before paying for the LLM calls
    detail = fetch_detail(symbol, data_root=_repo_root() / "data")
    if not detail.get("ok"):
        return {"ok": False,
                "error": f"unknown symbol: {symbol}"}

    from .desk.engine import run_desk
    out = run_desk(symbol, data_root=_repo_root() / "data")
    if not out.get("ok"):
        return {"ok": False,
                "error": f"desk run failed: {out.get('detail', 'unknown')}"}

    # Compact view for the chat transcript: the PM verdict + one line per
    # persona. Full output stays in the journal/transcript.
    pm = out.get("pm") or {}
    personas = []
    for p in out.get("personas") or []:
        personas.append({
            "role": p.get("role"),
            "signal": p.get("signal"),
            "confidence": p.get("confidence"),
            "thesis": p.get("thesis"),
            "abstained": bool(p.get("abstained")),
        })
    return {
        "ok": True,
        "symbol": out.get("symbol"),
        "price": out.get("price"),
        "pm": {
            "consensus": pm.get("consensus"),
            "conviction": pm.get("conviction"),
            "summary": pm.get("summary"),
            "disagreements": pm.get("disagreements"),
            "risk_flags": pm.get("risk_flags") or [],
        },
        "personas": personas,
        "elapsed_ms": out.get("elapsed_ms"),
        "note": "full report journalled; this is the compact view",
    }


def desk_bridge_tools() -> list:
    return [run_analyst_desk]
