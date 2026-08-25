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
from ...features.quant import compute_indicators as _compute_indicators
from ...features.verified_snapshot import (
    build_verified_snapshot as _build_verified_snapshot,
    flag_claim_conflicts as _flag_claim_conflicts,
)
from ...llm.zen_client import LLMInvalidJSON, LLMUnavailable, complete_json
from ...markets.board import (
    fetch_board,
    fetch_daily_bars,
    fetch_detail,
    fetch_market_movers,
)
from ...markets.institutional import (
    gather_institutional_context,
)
from ...ulid import new_ulid
from ..budgets import Budget, BudgetExceeded
from ..loop import resolve_models
from .personas import DESK_TOOLS, PERSONAS, Persona

DEFAULT_TIMEOUT_S = 60.0          # per-persona / PM wall clock
VALID_SIGNALS = ("bullish", "bearish", "neutral")
VALID_CONSENSUS = ("bullish", "bearish", "neutral", "mixed")

# R2-1 fix — defect 1: per-persona max_tokens override. The desk-wide
# default 2400 fits every persona EXCEPT the fundamentalist, whose
# institutional slice (8Q XBRL + top-10 13F + EPS path) is the largest
# single payload. Bumping it to 4800 gives the reasoning-first free-tier
# model headroom to emit JSON without truncating — combined with the
# 13F top-10 trim in _slice_institutional (drops the prompt from
# ~16,260 chars to ~6,000 chars), the fundamentalist's call lands
# within the model's response budget on DEFAULT engine settings (no
# CLI max_tokens knob required for normal runs — verified live).
PERSONA_DEFAULT_MAX_TOKENS = 2400
PERSONA_MAX_TOKENS: dict[str, int] = {
    "fundamentalist": 4800,
}


class DeskContextError(RuntimeError):
    """Context gather failed — the desk refuses to run (fail loud)."""


# --------------------------------------------------------------------- PM

PM_SYSTEM = """You are The Portfolio Manager of a six-analyst market desk
(a technician, a macro strategist, a news analyst, a sentiment reader,
a risk manager, and a fundamentalist). Each analyst has just returned a
signal on one symbol, and you have the market context they judged.

Your job:
1. Weigh the six signals — a high-confidence specialist outweighs a
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

    # ---- 1b. institutional context gather — R2-1 data plane, fail-soft
    # per slice. A dead XBRL or 429'd CoinGecko degrades to ok:False on
    # its slice and never raises — the desk still runs with whatever
    # lived (mirrors the per-symbol fail-soft in markets/board.py).
    # The fundamentalist persona abstains if its slice is empty.
    try:
        inst = gather_institutional_context(str(detail.get("symbol")
                                              or symbol), data_root)
    except Exception:  # noqa: BLE001 — institutional slice fail-soft
        inst = {"ok": False, "slices": {}}
    inst_slices = (inst or {}).get("slices") or {}

    # ---- 1c. R2-2 quant toolkit + deterministic verified snapshot.
    # Built ONCE per desk run, fail-soft: a bars-less snapshot degrades
    # to {ok: False, "no_bars": True} so the desk still runs. The
    # technician persona gets both slices in its prompt; the PM base
    # block carries a compact headline so the synthesis weighs the
    # verified numbers against the other five voices.
    #
    # R2-2: the snapshot + quant_indicators are computed from DAILY
    # bars (fetch_daily_bars range=1y&interval=1d) so the indicator
    # windows (RSI14, MACD 26+9, BBands 20, ATR14, realized_vol_20d)
    # and the 5d/20d/63d change pct fields use the proper daily
    # resolution. The technician's market_ohlc + market_indicators
    # slices are SEPARATE — they still use the 5d/30m bars from
    # fetch_detail for the technician's chart-reading checklist.
    quant_indicators: dict = {"ok": False, "error": "no daily bars"}
    verified_snapshot: dict = {"ok": False, "no_bars": True,
                              "regime_labels": {}}
    try:
        canon_sym = str(detail.get("symbol") or symbol)
        daily_bars = fetch_daily_bars(canon_sym,
                                       data_root=data_root) or []
        if daily_bars:
            quant_indicators = _compute_indicators(daily_bars)
            # SPY benchmark bars for beta (fail-soft: any fetch failure
            # → benchmark_beta is None; the snapshot still ships).
            bench_bars: list[dict] = []
            try:
                bench_bars = fetch_daily_bars("SPY", data_root=data_root)
            except Exception:  # noqa: BLE001 — benchmark fail-soft
                bench_bars = []
            verified_snapshot = _build_verified_snapshot(
                canon_sym, daily_bars,
                indicators=quant_indicators,
                benchmark_bars=bench_bars)
    except Exception as e:  # noqa: BLE001 — quant slice fail-soft
        quant_indicators = {"ok": False,
                            "error": f"{type(e).__name__}: {e}"}
        verified_snapshot = {"ok": False, "no_bars": False,
                            "regime_labels": {},
                            "error": f"{type(e).__name__}: {e}"}

    context = _build_context(detail, board, movers, inst_slices,
                           quant_indicators=quant_indicators,
                           verified_snapshot=verified_snapshot)
    base_block = _base_block(detail, board, inst_slices,
                           verified_snapshot=verified_snapshot)

    # ---- 2. the five personas, in PARALLEL (one completion_json each) --
    personas_out: list[dict] = []
    try:
        with ThreadPoolExecutor(max_workers=len(persona_list)) as ex:
            futures = {}
            for p in persona_list:
                user = _persona_user_msg(p, context, detail)
                # R2-1 fix — defect 1: per-persona max_tokens (only the
                # fundamentalist's bumped to 4800; default stays 2400)
                max_tok = PERSONA_MAX_TOKENS.get(
                    p.name, PERSONA_DEFAULT_MAX_TOKENS)
                futures[ex.submit(_run_persona, p, user, chain, budget,
                                  timeout, max_tok,
                                  verified_snapshot)] = p
            for fut in as_completed(futures):
                p = futures[fut]
                try:
                    out = fut.result()
                except BudgetExceeded:
                    raise
                except Exception as e:  # noqa: BLE001 — belt and braces
                    out = _abstain_result(p, "", e)
                # R2-2 claim-conflict flag: extract numeric claims from
                # the technician's thesis and compare against the verified
                # snapshot (the deterministic ground-truth block). A
                # delta > 0.5% is journaled as a claim_conflicts array on
                # the AgentStep so the PM + downstream evidence-checker
                # (R2-5) can see the LLM's numeric drift. Only the
                # technician reads the verified_snapshot in its prompt;
                # other personas have no snapshot to claim against.
                claim_conflicts: list[dict] = []
                if (not out.get("abstained")
                        and verified_snapshot.get("ok")
                        and "verified_snapshot" in p.tools):
                    try:
                        claim_conflicts = _flag_claim_conflicts(
                            out.get("thesis", ""), verified_snapshot)
                    except Exception:  # noqa: BLE001 — flag is advisory
                        claim_conflicts = []
                if claim_conflicts:
                    out["claim_conflicts"] = claim_conflicts
                personas_out.append(out)
                step_payload = {
                    "run_id": run_id, "step": len(personas_out),
                    "persona": p.name, "signal": out["signal"],
                    "confidence": out["confidence"],
                    "abstained": out["abstained"],
                    "model": out.get("model") or "", "ms": out["latency_ms"],
                }
                if claim_conflicts:
                    step_payload["claim_conflicts"] = claim_conflicts
                jr.emit("AgentStep", step_payload)
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
        "quant_indicators": quant_indicators,
        "verified_snapshot": verified_snapshot,
        "claim_conflicts_count": sum(
            len(r.get("claim_conflicts") or [])
            for r in personas_out),
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
                 budget: Budget, timeout: float,
                 max_tokens: int = PERSONA_DEFAULT_MAX_TOKENS,
                 verified_snapshot: dict | None = None) -> dict:
    """One persona = one complete_json call (with model fall-through).

    Never raises: any LLM/parse failure becomes an abstention. The
    max_tokens override lets the fundamentalist (largest payload) emit
    JSON without truncating — the desk-wide default 2400 fits every
    other persona. The verified_snapshot arg is accepted for
    signature symmetry with the caller's submit loop; the claim-
    conflict flag is run by the caller (the loop has access to the
    persona's tools list and the snapshot) so this function stays
    LLM-pure."""
    t0 = time.monotonic()
    messages = [
        {"role": "system", "content": persona.system},
        {"role": "user", "content": user_msg},
    ]
    model_used = ""
    try:
        budget.check_step()
        parsed, model_used = _complete_json_with_fallback(
            messages, models, timeout, max_tokens=max_tokens)
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

def _build_context(detail: dict, board: dict, movers: dict,
                   inst_slices: dict | None = None,
                   quant_indicators: dict | None = None,
                   verified_snapshot: dict | None = None) -> dict:
    """The desk's whole context, keyed by DESK_TOOLS names so each
    persona slice is just its tools' blocks (+ a small shared header).

    R2-1: the institutional slices (fundamentals, earnings,
    institutional_top, macro_curve, crypto_sentiment, onchain, social)
    are merged in fail-soft — a slice returning {ok: False} is still
    passed to the persona, which knows to abstain when its data is
    empty. The fundamentalist's three entitlements come straight from
    these slices.

    R2-2: quant_indicators + verified_snapshot slices are additive —
    the technician reads them; the verified snapshot is the source of
    truth for any exact numeric claim in the technician's thesis
    (engine._run_persona flag-checks the thesis against the snapshot).
    Both slices are fail-soft so a bars-less detail degrades cleanly.
    """
    ctx = {
        "market_ohlc": _slice_ohlc(detail),
        "market_indicators": _slice_indicators(detail),
        "board_sectors": _slice_board(board),
        "symbol_news": _slice_news(detail),
        "market_movers": _slice_movers(movers, board),
    }
    inst_slices = inst_slices or {}
    # the 7 institutional slices — ok:False stays in the slice so the
    # fundamentalist can see "the XBRL feed is down" and abstain honestly
    ctx["fundamentals"] = _slice_fundamentals(
        inst_slices.get("fundamentals"))
    ctx["earnings"] = _slice_earnings(
        inst_slices.get("fundamentals"))
    ctx["institutional_top"] = _slice_institutional(
        inst_slices.get("institutional_top"))
    ctx["macro_curve"] = _slice_curve(inst_slices.get("macro_curve"))
    ctx["crypto_sentiment"] = inst_slices.get("crypto_sentiment") or \
        {"ok": False}
    ctx["onchain"] = inst_slices.get("onchain") or {"ok": False}
    ctx["social"] = _slice_social(inst_slices.get("social"))
    # R2-2 quant toolkit + verified snapshot — additive, fail-soft
    ctx["quant_indicators"] = quant_indicators or {"ok": False}
    ctx["verified_snapshot"] = verified_snapshot or {"ok": False}
    return ctx


def _base_block(detail: dict, board: dict,
                inst_slices: dict | None = None,
                verified_snapshot: dict | None = None) -> dict:
    """The compact context the PM sees (no bars, no full board).

    R2-1: the PM also sees a fundamentals headline — latest revenue +
    latest EPS + 8Q growth direction + top-3 13F positions + latest 10Y
    yield + F&G value + on-chain price — so the synthesis weighs the
    fundamentalist voice against the chart/macro/news voices without
    reading the full XBRL bundle. Each headline field is null when its
    slice was fail-soft.

    R2-2: the PM also sees a verified_snapshot_headline (last_close,
    regime_labels, realized_vol_20d, benchmark_beta) so the synthesis
    weighs the technician's verified numbers against the other five
    voices. The full snapshot stays in the technician's context slice;
    the PM only needs the headline."""
    inst = inst_slices or {}
    fund = (inst.get("fundamentals") or {})
    inst13 = (inst.get("institutional_top") or {})
    curve = (inst.get("macro_curve") or {})
    fng = (inst.get("crypto_sentiment") or {})
    onchain = (inst.get("onchain") or {})
    fund_head = _fundamentals_headline(fund) if fund.get("ok") else None
    inst_head = _institutional_headline(inst13) \
        if inst13.get("ok") else None
    curve_head = _curve_headline(curve) if curve.get("ok") else None
    fng_head = ((fng.get("latest") or {}) if fng.get("ok") else None)
    onchain_head = ({"price": onchain.get("market_price_usd")}
                    if onchain.get("ok") else None)
    snap = verified_snapshot or {}
    snap_head = (_verified_snapshot_headline(snap)
                 if snap.get("ok") else None)
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
        "fundamentals_headline": fund_head,
        "institutional_headline": inst_head,
        "macro_curve_headline": curve_head,
        "crypto_sentiment_headline": fng_head,
        "onchain_headline": onchain_head,
        "verified_snapshot_headline": snap_head,
    }


def _verified_snapshot_headline(snap: dict) -> dict:
    """Compact PM-facing verified snapshot: last close + regime + vol +
    beta. The full snapshot stays in the technician's context slice."""
    reg = snap.get("regime_labels") or {}
    return {
        "last_close": snap.get("last_close"),
        "last_change_pct": snap.get("last_change_pct"),
        "change_pct_5d": snap.get("change_pct_5d"),
        "change_pct_20d": snap.get("change_pct_20d"),
        "change_pct_63d": snap.get("change_pct_63d"),
        "atr14_value": snap.get("atr14_value"),
        "atr_pct": snap.get("atr_pct"),
        "realized_vol_20d": snap.get("realized_vol_20d"),
        "rsi14": snap.get("rsi14"),
        "macd_hist": snap.get("macd_hist"),
        "bb_pct_b": snap.get("bb_pct_b"),
        "regime": reg,
        "benchmark_beta": snap.get("benchmark_beta"),
    }


def _fundamentals_headline(fund: dict) -> dict:
    """Compact PM-facing fundamentals: latest revenue, latest EPS,
    8Q revenue growth direction, accession-cited."""
    periods = fund.get("periods") or []
    if not periods:
        return {"ok": False, "error": "no periods"}
    latest = periods[0]
    rev = latest.get("revenue")
    eps = latest.get("eps_diluted") or latest.get("eps_basic")
    # 8Q growth direction: compare latest vs oldest revenue
    oldest = periods[-1]
    growth = None
    if isinstance(rev, (int, float)) and \
            isinstance(oldest.get("revenue"), (int, float)) \
            and oldest["revenue"]:
        growth = round((rev - oldest["revenue"]) /
                      oldest["revenue"] * 100, 2)
    return {
        "ok": True, "n_quarters": len(periods),
        "latest_quarter": fund.get("latest_quarter"),
        "latest_revenue": rev,
        "latest_eps": eps,
        "latest_accession": latest.get("accn"),
        "latest_filed": latest.get("filed"),
        "revenue_growth_8q_pct": growth,
        "source": fund.get("source"),
    }


def _institutional_headline(inst: dict) -> dict:
    """Top-3 13F positions + total disclosed value + top10 %."""
    positions = inst.get("positions") or []
    top3 = sorted(positions, key=lambda p: p.get("value", 0),
                  reverse=True)[:3]
    return {
        "fund": inst.get("fund"),
        "filed": inst.get("filed"),
        "accession": inst.get("accession"),
        "total_value": inst.get("total_value"),
        "n_positions": inst.get("n_positions"),
        "top10_pct": inst.get("top10_pct"),
        "top3": [{"issuer": p.get("issuer"), "value": p.get("value"),
                  "type": p.get("type")} for p in top3],
    }


def _curve_headline(curve: dict) -> dict:
    """Latest 10Y yield + 2Y/10Y shape."""
    c = curve.get("curve") or {}
    return {
        "latest_date": curve.get("latest_date"),
        "yields": {k: c.get(k) for k in ("1M", "3M", "6M", "1Y", "2Y",
                                        "5Y", "10Y", "20Y", "30Y")
                   if k in c},
    }


def _slice_fundamentals(fund: dict | None) -> dict:
    """Pass through the fundamentals slice for the fundamentalist;
    None becomes {ok: False} so the persona can abstain cleanly."""
    if not fund or not isinstance(fund, dict):
        return {"ok": False, "error": "no fundamentals slice"}
    return fund


def _slice_earnings(fund: dict | None) -> dict:
    """EPS-only slice of the fundamentals — the fundamentalist's
    earnings entitlement. Pulls diluted + basic per-share across the
    8 quarters, accession-cited."""
    if not fund or not isinstance(fund, dict) or not fund.get("ok"):
        return {"ok": False, "error": "no fundamentals for earnings slice"}
    periods = fund.get("periods") or []
    eps_rows = [{"fy": p.get("fy"), "fp": p.get("fp"),
                 "filed": p.get("filed"), "accn": p.get("accn"),
                 "eps_diluted": p.get("eps_diluted"),
                 "eps_basic": p.get("eps_basic")}
                for p in periods]
    return {"ok": True, "symbol": fund.get("symbol"),
            "source": fund.get("source"), "periods": eps_rows,
            "latest_quarter": fund.get("latest_quarter"),
            "n_quarters": len(eps_rows)}


def _slice_institutional(inst: dict | None) -> dict:
    """The fundamentalist's 13F entitlement — the latest 13F-HR
    holdings for the default filer (Berkshire). The slice is fund-
    positioning, not per-symbol; the fundamentalist cites top
    holdings and concentration.

    R2-1 fix — defect 1: the FULL 89-position Berkshire 13F (live
    2026-08-14, ~16,260 chars serialized) blows the free-tier
    model's response budget — the persona reliably abstains with
    "zen timeout" before emitting JSON. TRIM the positions array
    to TOP 10 BY VALUE so the persona prompt drops to ~6,000 chars
    and the JSON lands within the model's response budget on DEFAULT
    engine settings (verified live — no max_tokens CLI knob required
    for normal runs). The full picture is preserved via total_value
    + n_positions + top10_pct so the persona still reasons about
    concentration honestly without reading all 89 rows."""
    if not inst or not isinstance(inst, dict):
        return {"ok": False, "error": "no institutional slice"}
    if not inst.get("ok"):
        # fail-soft pass-through (preserves {ok: False, error} shape
        # so the persona abstains cleanly when the 13F feed is down)
        return inst
    positions = inst.get("positions") or []
    top10 = sorted(positions, key=lambda p: p.get("value", 0),
                   reverse=True)[:10]
    return {
        "ok": True,
        "fund": inst.get("fund"),
        "cik": inst.get("cik"),
        "filed": inst.get("filed"),
        "accession": inst.get("accession"),
        "total_value": inst.get("total_value"),
        "n_positions": inst.get("n_positions"),
        "top10_pct": inst.get("top10_pct"),
        "positions": [{"issuer": p.get("issuer"),
                       "cusip": p.get("cusip"),
                       "value": p.get("value"),
                       "shares": p.get("shares"),
                       "type": p.get("type")} for p in top10],
        "n_positions_shown": len(top10),
        "note": ("top 10 by value; full picture in total_value / "
                 "n_positions / top10_pct"),
    }


def _slice_curve(curve: dict | None) -> dict:
    """The macro_curve slice — Treasury yield curve."""
    if not curve or not isinstance(curve, dict):
        return {"ok": False, "error": "no curve slice"}
    return curve


def _slice_social(soc: dict | None) -> dict:
    """The social slice — Reddit RSS by sub."""
    if not soc or not isinstance(soc, dict):
        return {"ok": False, "error": "no social slice"}
    return soc


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
        f"You are one of six analysts on this desk. Judge only from "
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
