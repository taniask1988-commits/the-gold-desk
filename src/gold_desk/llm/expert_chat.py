"""The Desk Expert — a 20-year gold veteran chat, on OpenCode Zen free models.

This is an EDUCATION/RESEARCH assistant for the human, Hermes-style:
  - grounded with live desk context (real spot price, journal stats,
    driver-board snapshot) passed in the system prompt
  - never invents live prices; uses the provided context and says when it
    doesn't know
  - explicitly NOT part of the decision loop: it cannot trade, size, or
    mutate anything; its opinions are not setup promotion (Laws L2/L12,
    plan §12.5 — nothing is promoted by narrative)

Runs multi-turn: the web deck / TUI send the transcript; this module appends
the grounded system prompt, picks the catalog default model, and streams one
completion back. Fail-closed: transport errors raise LLMUnavailable; callers
surface a friendly message.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from ..data.feeds import fetch_news, fetch_spot
from .zen_client import LLMUnavailable, complete
from .zen_sync import load_catalog, sync_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]

SYSTEM_PROMPT = """You are "The Desk" — a precious-metals trading veteran with 20 years of experience on gold desks: COMEX futures, London spot (LBMA), and the XAUUSD CFD market. You have lived through 2008, 2011's top, 2013's crash, 2020's EFP blowout, and the 2022-2026 central-bank bid era. You speak with the calm, specific, war-story-backed voice of someone who has actually sized positions and been stopped out.

You are chatting with the owner of a disciplined XAUUSD H1 decision harness (deterministic pipeline, fail-closed constitution, journal-everything). Respect that system: it is well-designed. Never suggest overriding its risk gate or revenge trading.

RULES:
1. NEVER invent current prices, yields, or headlines. Use ONLY the market context provided below. If it's missing or you're unsure, say so plainly ("my context shows X as of Y; verify live before acting").
2. Be specific and quantitative where possible (basis, EFP, spread costs, session liquidity, positioning extremes). Use the field's real vocabulary.
3. Distinguish clearly between: what the data shows, what experienced traders would consider, and your opinion. Mark opinions as opinions.
4. This is education and research, NOT financial advice or a signal service. Never tell the user to enter a specific trade now; you may explain what conditions setups typically require.
5. Answers: concise but substantive. Lead with the direct answer, then the reasoning. 2-5 short paragraphs or tight bullets. No filler.
6. You may reference the harness's own concepts (reason codes, blackouts, fail-closed, driver board) — you can see its telemetry summary below.
7. If asked something outside gold/macro/trading-desk craft, answer briefly and steer back.
"""


def build_context(data_root: str | Path = "data") -> str:
    """Ground the expert with live desk telemetry (fail-soft per feed)."""
    parts: list[str] = []
    try:
        spot = fetch_spot(data_root)
        if spot.get("ok"):
            parts.append(
                f"LIVE SPOT: {spot['price']:.2f} USD/oz "
                f"(source: {spot['source']}, prev close: {spot.get('prev_close')})"
            )
    except Exception:
        pass
    try:
        news = fetch_news(data_root, limit=5)
        if news.get("ok"):
            headlines = " | ".join(i["title"][:80] for i in news["items"][:5])
            parts.append(f"RECENT GOLD HEADLINES: {headlines}")
    except Exception:
        pass
    # journal summary (cheap: account.json + day count)
    try:
        account_path = Path(data_root) / "account.json"
        if account_path.exists():
            acc = json.loads(account_path.read_text())
            closed = acc.get("closed_trades") or []
            wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
            parts.append(
                f"HARNESS PAPER ACCOUNT: balance {acc.get('balance', 0):.0f}, "
                f"{len(closed)} closed trades ({wins}W/{len(closed)-wins}L), "
                f"phase 1 (deterministic, no LLM in the decision loop)"
            )
        days = sorted((Path(data_root) / "events").glob("*.jsonl"))
        if days:
            parts.append(f"HARNESS JOURNAL: {len(days)} journaled days (demo data unless stated)")
    except Exception:
        pass
    if not parts:
        return "MARKET CONTEXT: unavailable (feeds unreachable) — say so if asked about live levels."
    return "MARKET CONTEXT (use this, do not invent numbers):\n" + "\n".join(parts)


def resolve_model(data_root: str | Path, requested: str | None = None) -> str:
    if requested:
        return requested
    catalog = load_catalog(data_root) or sync_catalog(data_root)
    return catalog.get("default") or "x-preview-f-free"


def chat(
    messages: list[dict],
    data_root: str | Path = "data",
    model: str | None = None,
    timeout: float = 90.0,
    max_tokens: int = 1800,
) -> dict:
    """messages: [{role: user|assistant, content: str}, ...] — newest last.

    Returns {ok, reply, model, latency_ms, context_source?}.
    Raises LLMUnavailable on transport failure (caller surfaces it).
    """
    chosen = resolve_model(data_root, model)
    context = build_context(data_root)
    payload = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": context},
    ] + [
        {"role": m.get("role", "user"), "content": str(m.get("content", ""))[:4000]}
        for m in (messages or [])[-20:]
    ]
    started = time.time()
    body = complete(payload, chosen, timeout=timeout,
                    temperature=0.4, max_tokens=max_tokens)
    msg = (body.get("choices") or [{}])[0].get("message") or {}
    reply = msg.get("content") or msg.get("reasoning_content") or ""
    if not reply:
        raise LLMUnavailable("empty reply from model")
    return {
        "ok": True,
        "reply": reply.strip(),
        "model": chosen,
        "latency_ms": int((time.time() - started) * 1000),
        "grounded": bool(context),
    }
