"""Multi-analyst desk engine (MARKET GAUNTLET piece 4).

    run_desk(symbol) -> desk report dict

Five personas (personas.py) judge ANY Yahoo symbol in parallel, then a
sixth call — The Portfolio Manager — synthesizes them into a consensus.
The discipline is ported from ai-hedge-fund's LLMAgent failure contract:

  - context-gather errors PROPAGATE (fail loud): a broken market plane
    must never silently become five neutral views. fetch_detail /
    fetch_board / fetch_market_movers returning {ok: False} is treated
    as a raise (DeskContextError) — the markets plane is fail-soft by
    design, the desk is not allowed to be.
  - per-persona LLM call/parse failures ABSTAIN: {signal: neutral,
    confidence: 0, abstained: True, thesis: "abstained: <error>"} — the
    desk NEVER dies because one model call failed. Model fall-through
    (resolve_models) happens first; abstention is the last resort.
  - the PM call gets the same fall-through; if it still fails, the desk
    falls back to a MECHANICAL majority vote, labeled as such in the
    summary and risk_flags — never a silent invention.

One run = 1 context gather (3 markets-plane calls, cached by the plane)
+ 5 persona completions in parallel + 1 PM completion. Everything is
journaled: AgentRunStarted / AgentStep (one per persona + the PM) /
AgentRunFinished / DeskReport (the full report, one event).

L12 blindfold holds: nothing in the context touches the paper account —
the desk reads market data only, and the persona prompts are test-pinned
to contain no account/balance keys.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ...events import Journal
from ..journal_util import default_journal
from ...llm.zen_client import LLMInvalidJSON, LLMUnavailable, complete_json
from ...markets.board import (
    fetch_board,
    fetch_detail,
    fetch_market_movers,
)
from ...ulid import new_ulid
from ..budgets import Budget, BudgetExceeded
from ..loop import resolve_models
from .personas import DESK_TOOLS, PERSONAS, Persona

DEFAULT_TIMEOUT_S = 60.0          # per-persona / PM wall clock
VALID_SIGNALS = ("bullish", "bearish", "neutral")
VALID_CONSENSUS = ("bullish", "bearish", "neutral", "mixed")


class DeskContextError(RuntimeError):
    """Context gather failed — the desk refuses to run (fail loud)."""


# --------------------------------------------------------------------- PM

PM_SYSTEM = """You are The Portfolio Manager of a five-analyst market desk
(a technician, a macro strategist, a news analyst, a sentiment reader
and a risk manager). Each analyst has just returned a signal on one
symbol, and you have the market context they judged.

Your job:
1. Weigh the five signals — a high-confidence specialist outweighs a
   low-confidence one; an abstained analyst carries no weight.
2. Name the consensus: bullish, bearish, neutral, or mixed when the
   desk genuinely splits.
3. Set conviction honestly: 0-100, where abstentions and conflicting
   specialists must drag it down.
4. Say where the desk splits, and flag the concrete risks the desk
   should watch.

Hard rules:
- Reason ONLY from the analyst signals and market context provided.
- Do not invent quotes, prices, or analyst views.
- summary is 2-3 sentences; disagreements is one sentence.

Return ONLY JSON: {"consensus": "bullish"|"bearish"|"neutral"|"mixed",
"conviction": 0-100, "summary": "2-3 sentences",
"disagreements": "one sentence on where the desk splits",
"risk_flags": ["up to 5 short concrete flags"]}"""


# ------------------------------------------------------------- entry point

def run_desk(
    symbol: str,
    *,
    data_root: str | Path = "data",
    journal: Journal | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_S,
    max_model_fallbacks: int = 2,
    on_event: Callable[[dict], None] | None = None,
    personas: tuple[Persona, ...] | list[Persona] | None = None,
) -> dict:
    """Run the 5-persona desk + PM synthesis for one symbol.

    Raises DeskContextError (or whatever the markets plane raised) when
    the context gather fails — fail loud, never five silent neutrals.
    Never raises for LLM failures: personas abstain, the PM falls back
    to a mechanical vote.
    """
    jr = journal or default_journal(data_root)
    run_id = new_ulid()
    started = time.monotonic()
    persona_list = list(personas or PERSONAS)
    models = resolve_models(model, data_root)
    chain = models[: 1 + max_model_fallbacks]
    primary = models[0]

    budget = Budget(data_root, max_steps=len(persona_list) + 2,
                    max_minutes=10.0, max_tool_calls=8)

    def _emit(kind: str, payload: dict) -> None:
        try:
            if on_event is not None:
                on_event({"kind": kind, **payload})
        except Exception:
            pass

    try:
        budget.check_run_start()
    except BudgetExceeded as e:
        jr.emit("BudgetExceeded", {"run_id": run_id, "reason": str(e)})
        jr.emit("AgentRunFinished", {"run_id": run_id, "steps": 0,
                                     "tool_calls": 0, "elapsed_ms": 0,
                                     "status": "budget", "detail": str(e)})
        return {"ok": False, "symbol": str(symbol), "status": "budget",
                "error": str(e), "run_id": run_id}

    all_tools = sorted({t for p in persona_list for t in p.tools}
                       | {"pm_synthesis"})
    jr.emit("AgentRunStarted", {
        "run_id": run_id,
        "task": f"analyst desk {symbol}",
        "model": primary,
        "tools": all_tools,
        "personas": [p.name for p in persona_list],
    }, model_id=primary, prompt_hash=_sha16(PM_SYSTEM))

    # ---- 1. context gather — ONCE, fail loud (ai-hedge-fund contract) --
    try:
        detail = fetch_detail(symbol, data_root)
        if not detail.get("ok"):
            raise DeskContextError(
                f"detail: {detail.get('error', 'unknown error')}")
        board = fetch_board(data_root)
        if not board.get("ok"):
            raise DeskContextError(
                f"board: {board.get('error', 'unknown error')}")
        movers = fetch_market_movers(data_root)
        if not movers.get("ok"):
            raise DeskContextError(
                f"movers: {movers.get('error', 'unknown error')}")
    except BudgetExceeded:
        raise
    except BaseException:  # noqa: BLE001 — context errors PROPAGATE
        # journal the failed start first, then re-raise (fail loud)
        jr.emit("AgentRunFinished", {
            "run_id": run_id, "steps": 0, "tool_calls": 0,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "status": "context_error", "detail": "context gather failed",
        })
        raise
    _emit("context", {"symbol": detail.get("symbol"), "bars":
                      len(detail.get("bars") or [])})

    context = _build_context(detail, board, movers)
    base_block = _base_block(detail, board)

    # ---- 2. the five personas, in PARALLEL (one completion_json each) --
    personas_out: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=len(persona_list)) as ex:
            futures = {}
            for p in persona_list:
                user = _persona_user_msg(p, context, detail)
                futures[ex.submit(_run_persona, p, user, chain, budget,
                                  timeout)] = p
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    out = fut.result()
                except BudgetExceeded:
                    raise
                except Exception as e:  # noqa: BLE001 — belt and braces
                    out = _abstain_result(p, "", e)
                personas_out.append(out)
                jr.emit("AgentStep", {
                    "run_id": run_id, "step": len(personas_out),
                    "persona": p.name, "signal": out["signal"],
                    "confidence": out["confidence"],
                    "abstained": out["abstained"],
                    "model": out.get("model") or "", "ms": out["latency_ms"],
                })
                _emit("persona", {"name": p.name, "signal": out["signal"],
                                  "confidence": out["confidence"],
                                  "abstained": out["abstained"]})
    except BudgetExceeded as e:
        jr.emit("BudgetExceeded", {"run_id": run_id, "reason": str(e)})
        jr.emit("AgentRunFinished", {
            "run_id": run_id, "steps": budget.steps,
            "tool_calls": 3, "elapsed_ms": int(
                (time.monotonic() - started) * 1000),
            "status": "budget", "detail": str(e)})
        return {"ok": False, "symbol": str(symbol), "status": "budget",
                "error": str(e), "run_id": run_id}
    # stable display order: the declared persona order
    order = {p.name: i for i, p in enumerate(persona_list)}
    personas_out.sort(key=lambda r: order.get(r["name"], 99))

    # ---- 3. PM synthesis (one more call; mechanical fallback on failure)
    try:
        pm = _run_pm(personas_out, base_block, detail, chain, budget,
                     timeout)
    except BudgetExceeded as e:
        jr.emit("BudgetExceeded", {"run_id": run_id, "reason": str(e)})
        jr.emit("AgentRunFinished", {
            "run_id": run_id, "steps": budget.steps,
            "tool_calls": 3, "elapsed_ms": int(
                (time.monotonic() - started) * 1000),
            "status": "budget", "detail": str(e)})
        return {"ok": False, "symbol": str(symbol), "status": "budget",
                "error": str(e), "run_id": run_id}
    jr.emit("AgentStep", {
        "run_id": run_id, "step": "pm",
        "consensus": pm["consensus"], "conviction": pm["conviction"],
        "model": pm.get("model") or "", "ms": pm.get("latency_ms", 0),
    })
    _emit("pm", {"consensus": pm["consensus"],
                 "conviction": pm["conviction"]})

    elapsed_ms = int((time.monotonic() - started) * 1000)
    report = {
        "ok": True,
        "symbol": detail.get("symbol") or str(symbol),
        "requested": str(symbol),
        "name": detail.get("name"),
        "sector": detail.get("sector"),
        "as_of": _now_iso(),
        "price": detail.get("price"),
        "change_pct": detail.get("change_pct"),
        "range_5d_change_pct": detail.get("range_5d_change_pct"),
        "personas": personas_out,
        "pm": pm,
        "abstained": sum(1 for r in personas_out if r["abstained"]),
        "model": primary,
        "run_id": run_id,
        "elapsed_ms": elapsed_ms,
    }

    jr.emit("DeskReport", {
        "run_id": run_id,
        "symbol": report["symbol"],
        "name": report["name"],
        "price": report["price"],
        "personas": [
            {"name": r["name"], "signal": r["signal"],
             "confidence": r["confidence"], "abstained": r["abstained"],
             "model": r.get("model") or ""}
            for r in personas_out
        ],
        "pm": {"consensus": pm["consensus"], "conviction": pm["conviction"],
               "mechanical": pm.get("mechanical", False)},
        "elapsed_ms": elapsed_ms,
    }, model_id=primary)
    jr.emit("AgentRunFinished", {
        "run_id": run_id, "steps": len(persona_list) + 1,
        "tool_calls": 3, "elapsed_ms": elapsed_ms, "status": "ok",
        "detail": f"{report['abstained']} abstention(s)",
    })
    return report


# ------------------------------------------------------------ persona call

def _run_persona(persona: Persona, user_msg: str, models: list[str],
                 budget: Budget, timeout: float) -> dict:
    """One persona = one complete_json call (with model fall-through).

    Never raises: any LLM/parse failure becomes an abstention.
    """
    t0 = time.monotonic()
    messages = [
        {"role": "system", "content": persona.system},
        {"role": "user", "content": user_msg},
    ]
    model_used = ""
    try:
        budget.check_step()
        parsed, model_used = _complete_json_with_fallback(
            messages, models, timeout)
        budget.record_step(time.monotonic() - t0)
        return _persona_result(persona, parsed, model_used,
                               time.monotonic() - t0)
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 — abstain, never die
        try:
            budget.record_step(time.monotonic() - t0)
        except Exception:
            pass
        return _abstain_result(persona, model_used, e,
                               time.monotonic() - t0)


def _complete_json_with_fallback(messages: list[dict], models: list[str],
                                 timeout: float,
                                 max_tokens: int = 2400) -> tuple[dict, str]:
    """complete_json over the model chain, honoring a wall clock.

    max_tokens is generous (2400): reasoning-first free models burn
    hundreds of tokens thinking before the JSON lands — a tight cap
    truncates the answer away and every persona abstains for no reason
    (observed live on x-preview-f-free: a 5/5-abstention desk run at
    max_tokens=700).

    Parse-failure rescue (P12-CRITIC defect 1 — 40% abstention rate):
    when a model returns text that complete_json cannot parse, ONE
    re-prompt is attempted on the SAME model with a JSON-only ultimatum
    before falling through to the next model. Transport failures skip
    straight to the next model. This converts most reasoning-burn
    abstentions into parsed signals without retry-storming.

    Returns (parsed, model_used). Raises the last LLM error when every
    model in the chain failed — the caller decides what that means
    (persona → abstain; PM → mechanical fallback).
    """
    deadline = time.monotonic() + timeout
    last: Exception | None = None
    for i, m in enumerate(models):
        remaining = deadline - time.monotonic()
        if i > 0 and remaining <= 0:
            break
        # every attempt is capped by the REMAINING wall clock, so one
        # persona can never outrun its timeout by stacking retries
        attempt_timeout = max(10.0, min(timeout, remaining))
        try:
            parsed = complete_json(
                messages, m, timeout=attempt_timeout,
                temperature=0.2, max_tokens=max_tokens, retries=2)
            return parsed, m
        except LLMInvalidJSON as e:
            last = e
            # one JSON-only re-prompt on the same model (cheap rescue:
            # often the model just wrapped the JSON in prose)
            remaining = deadline - time.monotonic()
            if remaining <= 5:
                continue
            try:
                rescue = [
                    {"role": "system",
                     "content": "Output ONLY a single JSON object. No prose, "
                                "no reasoning, no markdown fences. Start "
                                "with { and end with }."},
                    {"role": "user", "content": messages[-1]["content"]},
                ]
                parsed = complete_json(
                    rescue, m, timeout=max(10.0, min(timeout, remaining)),
                    temperature=0.0, max_tokens=max_tokens, retries=1)
                return parsed, m
            except (LLMUnavailable, LLMInvalidJSON) as e2:
                last = e2
                continue
        except LLMUnavailable as e:
            last = e
            continue
    raise last or LLMUnavailable("desk: model chain exhausted")


def _persona_result(persona: Persona, parsed: dict, model: str,
                    elapsed: float) -> dict:
    """Validate the persona JSON (ai-hedge-fund _parse discipline)."""
    signal = str(parsed.get("signal", "")).strip().lower()
    if signal not in VALID_SIGNALS:
        raise ValueError(f"invalid signal {parsed.get('signal')!r}")
    try:
        confidence = float(parsed.get("confidence", 0))
    except (TypeError, ValueError):
        raise ValueError(f"confidence not numeric: "
                         f"{parsed.get('confidence')!r}") from None
    if not 0 <= confidence <= 100:
        raise ValueError(f"confidence out of range: {confidence}")
    thesis = str(parsed.get("thesis", "")).strip()[:400]
    if not thesis:
        raise ValueError("empty thesis")
    evidence = parsed.get("key_evidence")
    if evidence is None:
        evidence = []
    if not isinstance(evidence, list):
        raise ValueError("key_evidence must be a list")
    ev = [str(e).strip()[:160] for e in evidence if str(e).strip()][:3]
    return {
        "name": persona.name,
        "role": persona.role,
        "signal": signal,
        "confidence": int(round(confidence)),
        "thesis": thesis,
        "key_evidence": ev,
        "abstained": False,
        "model": model,
        "latency_ms": int(elapsed * 1000),
    }


def _abstain_result(persona: Persona, model: str, error: Exception,
                    elapsed: float) -> dict:
    """The abstention contract — mirrors LLMAgent._abstain."""
    return {
        "name": persona.name,
        "role": persona.role,
        "signal": "neutral",
        "confidence": 0,
        "thesis": f"abstained: {error}"[:400],
        "key_evidence": [],
        "abstained": True,
        "model": model,
        "latency_ms": int(elapsed * 1000),
    }


# ----------------------------------------------------------------- the PM

def _run_pm(personas_out: list[dict], base_block: dict, detail: dict,
            models: list[str], budget: Budget, timeout: float) -> dict:
    """PM synthesis with the mechanical-vote fallback."""
    t0 = time.monotonic()
    signals = [
        {"name": r["name"], "role": r["role"], "signal": r["signal"],
         "confidence": r["confidence"], "thesis": r["thesis"],
         "key_evidence": r["key_evidence"], "abstained": r["abstained"]}
        for r in personas_out
    ]
    user = (
        f"Analyst desk report for {detail.get('symbol')} "
        f"({detail.get('name')}).\n\n"
        f"MARKET CONTEXT (JSON):\n{json.dumps(base_block, default=str)}\n\n"
        f"ANALYST SIGNALS (JSON):\n"
        f"{json.dumps(signals, ensure_ascii=False, default=str)}\n\n"
        "Produce the desk consensus now."
    )
    messages = [{"role": "system", "content": PM_SYSTEM},
                {"role": "user", "content": user}]
    try:
        budget.check_step()
        parsed, model_used = _complete_json_with_fallback(
            messages, models, timeout, max_tokens=2000)
        budget.record_step(time.monotonic() - t0)
        pm = _validate_pm(parsed)
        pm["model"] = model_used
        pm["mechanical"] = False
        pm["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return pm
    except BudgetExceeded:
        raise
    except Exception as e:  # noqa: BLE001 — mechanical fallback
        try:
            budget.record_step(time.monotonic() - t0)
        except Exception:
            pass
        pm = _mechanical_pm(personas_out, e)
        pm["model"] = ""
        pm["mechanical"] = True
        pm["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return pm


def _validate_pm(parsed: dict) -> dict:
    consensus = str(parsed.get("consensus", "")).strip().lower()
    if consensus not in VALID_CONSENSUS:
        raise ValueError(f"invalid consensus {parsed.get('consensus')!r}")
    try:
        conviction = float(parsed.get("conviction", 0))
    except (TypeError, ValueError):
        raise ValueError(f"conviction not numeric: "
                         f"{parsed.get('conviction')!r}") from None
    if not 0 <= conviction <= 100:
        raise ValueError(f"conviction out of range: {conviction}")
    summary = str(parsed.get("summary", "")).strip()[:800]
    if not summary:
        raise ValueError("empty summary")
    flags = parsed.get("risk_flags")
    if flags is None:
        flags = []
    if not isinstance(flags, list):
        raise ValueError("risk_flags must be a list")
    rf = [str(f).strip()[:160] for f in flags if str(f).strip()][:5]
    return {
        "consensus": consensus,
        "conviction": int(round(conviction)),
        "summary": summary,
        "disagreements": str(parsed.get("disagreements", "")).strip()[:400],
        "risk_flags": rf,
    }


def _mechanical_pm(personas_out: list[dict], error: Exception) -> dict:
    """Majority vote when the PM model is unreachable — labeled, never
    silent: the summary says it is mechanical, the error lands in
    risk_flags."""
    live = [r for r in personas_out if not r["abstained"]]
    counts: dict[str, int] = {}
    for r in live:
        counts[r["signal"]] = counts.get(r["signal"], 0) + 1
    if not live:
        consensus, conviction = "neutral", 0
    else:
        best = max(counts.items(), key=lambda kv: kv[1])
        consensus = best[0] if best[1] * 2 > len(live) else "mixed"
        conviction = int(round(
            sum(r["confidence"] for r in live) / len(live)))
    spread = ", ".join(
        f"{r['name']}:{r['signal']}({r['confidence']}%)"
        for r in personas_out)
    return {
        "consensus": consensus,
        "conviction": conviction,
        "summary": (f"PM synthesis unavailable ({error}); this is a "
                    f"mechanical majority vote over the desk's live "
                    f"signals."),
        "disagreements": "not assessed (PM unavailable) — desk split: "
                         + spread,
        "risk_flags": [f"pm synthesis unavailable: {error}"[:160]],
    }


# ------------------------------------------------------- context slicing

def _build_context(detail: dict, board: dict, movers: dict) -> dict:
    """The desk's whole context, keyed by DESK_TOOLS names so each
    persona slice is just its tools' blocks (+ a small shared header)."""
    return {
        "market_ohlc": _slice_ohlc(detail),
        "market_indicators": _slice_indicators(detail),
        "board_sectors": _slice_board(board),
        "symbol_news": _slice_news(detail),
        "market_movers": _slice_movers(movers, board),
    }


def _base_block(detail: dict, board: dict) -> dict:
    """The compact context the PM sees (no bars, no full board)."""
    return {
        "symbol": detail.get("symbol"),
        "name": detail.get("name"),
        "sector": detail.get("sector"),
        "price": detail.get("price"),
        "change_pct_1d": detail.get("change_pct"),
        "change_pct_5d": detail.get("range_5d_change_pct"),
        "as_of": detail.get("news", {}).get("as_of") or _now_iso(),
        "board_headline": [
            {"sector": s.get("key"),
             "avg_change_pct": _avg(
                 [r.get("change_pct") for r in s.get("rows") or []])}
            for s in (board.get("sectors") or [])
        ],
    }


def _slice_ohlc(detail: dict) -> dict:
    bars = detail.get("bars") or []
    rows = [[_r4(b.get("o")), _r4(b.get("h")), _r4(b.get("l")), _r4(b.get("c"))]
            for b in bars]
    return {
        "note": "5d of 30m bars, rows are [open, high, low, close]",
        "bar_count": len(bars),
        "first_bar_ts": bars[0]["ts"] if bars else None,
        "last_bar_ts": bars[-1]["ts"] if bars else None,
        "price": detail.get("price"),
        "change_pct_1d": detail.get("change_pct"),
        "change_pct_5d": detail.get("range_5d_change_pct"),
        "bars": rows,
    }


def _slice_indicators(detail: dict) -> dict:
    """Bar-derived technicals, computed the same way as the desk tools
    (Wilder ATR(14), ranges, swings, last-8 momentum stats)."""
    bars = detail.get("bars") or []
    price = detail.get("price")
    out: dict = {"note": "derived from the 5d 30m bars by the harness",
                 "bar_count": len(bars)}
    if len(bars) < 2:
        out["error"] = "insufficient bars for indicators"
        return out
    trs = []
    for i in range(1, len(bars)):
        prev_c = bars[i - 1]["c"]
        b = bars[i]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - prev_c),
                       abs(b["l"] - prev_c)))
    period = min(14, len(trs))
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    out["atr14"] = _r4(atr)
    if isinstance(price, (int, float)) and price:
        out["atr14_pct_of_price"] = round(atr / price * 100.0, 3)

    last_ts = bars[-1]["ts"]
    day_bars = [b for b in bars if b["ts"] >= last_ts - 24 * 3600 * 1000]
    def _rng(bs):
        if not bs:
            return None
        return {"low": _r4(min(b["l"] for b in bs)),
                "high": _r4(max(b["h"] for b in bs))}
    day_rng, week_rng = _rng(day_bars), _rng(bars)

    def _pos(rng):
        if not rng or not isinstance(price, (int, float)):
            return None
        lo, hi = rng["low"], rng["high"]
        if hi is None or lo is None or hi <= lo:
            return None
        return round((price - lo) / (hi - lo) * 100.0, 1)
    if day_rng:
        day_rng["price_position_pct"] = _pos(day_rng)
    if week_rng:
        week_rng["price_position_pct"] = _pos(week_rng)
    out["day_range_24h"] = day_rng
    out["range_5d"] = week_rng

    # recent swings: last 48 bars (~24h) — the levels a stop cares about
    recent = bars[-48:]
    out["swing_high_24h"] = _r4(max(b["h"] for b in recent))
    out["swing_low_24h"] = _r4(min(b["l"] for b in recent))

    last8 = bars[-8:]
    if isinstance(price, (int, float)) and len(last8) >= 2:
        first_c = last8[0]["c"]
        out["last_8_bars"] = {
            "net_change_pct": _r4((price - first_c) / first_c * 100.0)
            if first_c else None,
            "up_bars": sum(1 for i in range(1, len(last8))
                           if last8[i]["c"] > last8[i - 1]["c"]),
            "down_bars": sum(1 for i in range(1, len(last8))
                             if last8[i]["c"] < last8[i - 1]["c"]),
            "closes": [_r4(b["c"]) for b in last8],
        }
    return out


def _slice_board(board: dict) -> dict:
    sectors = []
    for s in board.get("sectors") or []:
        sectors.append({
            "sector": s.get("key"),
            "label": s.get("label"),
            "rows": [
                {"symbol": r.get("symbol"), "price": r.get("price"),
                 "change_pct": r.get("change_pct")}
                for r in (s.get("rows") or [])
                if isinstance(r.get("change_pct"), (int, float))
            ],
        })
    out = {"as_of": board.get("as_of"),
           "note": "daily change % per symbol, by sector",
           "sectors": sectors}
    watch = board.get("watchlist_movers") or {}
    if watch:
        out["watchlist_movers"] = watch
    return out


def _slice_news(detail: dict) -> dict:
    news = detail.get("news") or {}
    items = [
        {"title": (i.get("title") or "")[:200],
         "published": i.get("published")}
        for i in (news.get("items") or [])[:8]
    ]
    return {"ok": bool(news.get("ok", bool(items))),
            "count": len(items),
            "items": items}


def _slice_movers(movers: dict, board: dict) -> dict:
    out: dict = {"as_of": movers.get("as_of")}
    for side in ("gainers", "losers"):
        rows = movers.get(side) or []
        out[f"market_{side}"] = [
            {"symbol": m.get("symbol"), "name": (m.get("name") or "")[:60],
             "price": m.get("price"), "change_pct": m.get("change_pct")}
            for m in rows
        ]
    watch = board.get("watchlist_movers") or {}
    for side in ("gainers", "losers"):
        rows = watch.get(side) or []
        out[f"watchlist_{side}"] = [
            {"symbol": m.get("symbol"), "sector": m.get("sector"),
             "change_pct": m.get("change_pct")}
            for m in rows
        ]
    return out


def _persona_user_msg(persona: Persona, context: dict, detail: dict) -> str:
    blocks = {t: context.get(t) for t in persona.tools if t in context}
    return (
        f"Briefing for {detail.get('symbol')} "
        f"({detail.get('name')}), sector {detail.get('sector')}, "
        f"as of {_now_iso()}.\n\n"
        f"You are one of five analysts on this desk. Judge only from "
        f"your checklist and the data below.\n\n"
        f"DATA (JSON):\n{json.dumps(blocks, ensure_ascii=False, default=str)}"
    )


# --------------------------------------------------------------- helpers

def _avg(vals) -> float | None:
    nums = [v for v in vals if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 2) if nums else None


def _r4(v):
    if isinstance(v, (int, float)):
        return round(v, 4)
    return v


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _sha16(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
