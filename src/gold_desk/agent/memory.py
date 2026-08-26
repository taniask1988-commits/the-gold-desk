"""ReflectiveMemory — outcome-labeled decision log + structured lessons.

R2-4 — judged vs TradingAgents v0.3.1's TradingMemoryLog
(``tradingagents/agents/utils/memory.py``, 299 lines) + Reflector
(``tradingagents/graph/reflection.py``, 57 lines). The bar's pattern:

  Phase A (store_decision):  append a pending entry to a markdown log
                              tagged ``[date | ticker | rating | pending]``;
  Phase B (reflect_on_final_decision): single LLM call produces 2-4
                              sentences of plain prose; the entry's tag
                              is updated to ``[date | ticker | rating |
                              raw +X% | alpha +Y% | Nd]`` and the prose
                              is appended under ``REFLECTION:``;
  Re-inject: ``get_past_context`` reads the last n same-ticker +
                              n cross-ticker reflections and injects
                              them as in-context examples for the next
                              analyst run.

OURS EXTENDS the bar with:
  - STRUCTURED lessons, not 2-4 sentences of plain prose: each
    reflection is a JSON object with 6 fields
    (directional_call_correct / alpha_pct / what_held / what_failed /
    lesson / applicable_signals) so the PM's re-injector can
    mechanically format the block without re-parsing prose;
  - per-symbol files (TA uses ONE global log → O(N) scan on every
    lookup) — ``{symbol}.md`` for O(1) lookup + a global ``index.md``
    for cross-symbol regime peers;
  - explicit regime tag on every entry so ``recent_lessons(symbol,
    regime, k)`` can deterministically fall back to same-regime peers
    when the symbol has < k of its own lessons;
  - idempotency on ``run_id`` (TA is idempotent on (date, ticker,
    rating) — date + ticker + rating is not unique across re-runs of
    the same day; ours uses the run_id ULID for true idempotency).

Storage: ``<cache_dir>/memory/{symbol}.md`` (per-symbol) +
``<cache_dir>/memory/index.md`` (cross-symbol). The cache dir is
``.gitignored`` — runtime artifacts, not source.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# ----------------------------------------------------------------- constants

# HTML comment delimiter — same pattern as TA's TradingMemoryLog
# (cannot appear in LLM prose output, safe as a hard block delimiter).
SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"

# Pending entry tag shape:
#   [run_id | symbol | action | pending | regime=... | benchmark=...]
# Reflected entry tag shape:
#   [run_id | symbol | action | reflected | raw +X.XX% | alpha +Y.YY% |
#    5d | regime=... | benchmark=...]
#
# Mirror TA's ``[date | ticker | rating | pending]`` discipline but use
# run_id (ULID) for true cross-run idempotency (date + ticker + rating
# collides when the same ticker is judged twice on the same day).
#
# The regex uses ``\s*\|\s*`` as the separator pattern (matches pipe
# with optional whitespace on both sides) so the tags remain
# human-readable (``[a | b | c]`` not ``[a|b|c]``) while the parser
# stays whitespace-tolerant.
_PENDING_TAG_RE = re.compile(
    r"^\[\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|"
    r"\s*pending\s*\|\s*regime=([^\|]*?)\s*\|\s*benchmark=([^\]]*?)\s*\]$"
)
_REFLECTED_TAG_RE = re.compile(
    r"^\[\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|\s*([^\|]+?)\s*\|"
    r"\s*reflected\s*\|\s*raw\s+([+-]?[0-9.]+)\s*%\s*\|"
    r"\s*alpha\s+([+-]?[0-9.]+)\s*%\s*\|\s*(\d+)d\s*\|"
    r"\s*regime=([^\|]*?)\s*\|\s*benchmark=([^\]]*?)\s*\]$"
)

# Lesson block: lives between ``REFLECTION:\n`` and the SEPARATOR.
# Stored as a single JSON object so the parser doesn't have to re-parse
# prose (the structured-lessons edge over TA's plain prose).
_REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)(?=" + re.escape(SEPARATOR) + r"|\Z)", re.DOTALL)
_DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=REFLECTION:|" + re.escape(SEPARATOR) + r"|\Z)", re.DOTALL)

# Rotation caps (mirror TA's ``memory_log_max_entries`` but applied per
# symbol + globally on the index, not on one file). Pending entries are
# always kept (they represent un-processed Phase B work); only reflected
# entries are rotated out.
MAX_PER_SYMBOL = 100
MAX_GLOBAL = 500


# ----------------------------------------------------------------- the system prompt

REFLECTION_SYSTEM = """You are a trading analyst reviewing your own past decision now that the outcome is known.

Produce a STRUCTURED lesson in JSON (no prose, no markdown, no markdown fences). Cover:
- directional_call_correct: true if the action's direction matched the realized return's sign (BUY + positive raw return, SELL + negative raw return, HOLD/ABSTAIN + any return — HOLD/ABSTAIN is "correct" iff the realized move was small enough that staying out was right).
- alpha_pct: the alpha vs the benchmark (provided in the user message).
- what_held: ONE sentence on which part of the original thesis held up.
- what_failed: ONE sentence on which part of the original thesis failed.
- lesson: ONE concrete sentence to apply to the next similar analysis (cite a specific signal or level).
- applicable_signals: list of "persona:signal" strings that were right OR wrong (e.g. ["technician:bullish", "fundamentalist:bearish"]). The PM will re-inject these as a checklist of which signals to weight or discount next time.

Be specific and terse. Every word must earn its place — your output is stored verbatim and re-read by future analysts.

Return ONLY JSON: {"directional_call_correct": true|false, "alpha_pct": <float>, "what_held": "<one sentence>", "what_failed": "<one sentence>", "lesson": "<one concrete sentence>", "applicable_signals": ["persona:signal", ...]}"""


# ----------------------------------------------------------------- the class

class ReflectiveMemory:
    """Append-only reflective memory log — outcome-labeled decisions +
    structured lessons.

    Mirrors TA's TradingMemoryLog discipline but EXTENDS it with
    structured lessons (JSON, 6 fields) instead of 2-4 sentences of
    plain prose. Per-symbol files give O(1) lookup on the symbol the
    desk is about to judge; the global ``index.md`` carries cross-
    symbol lessons for the regime-peer fallback. Idempotency on
    run_id (ULID) is stricter than TA's (date, ticker, rating) tuple
    — the same symbol judged twice in one day produces two distinct
    entries, not a silent overwrite.
    """

    def __init__(self, memory_dir: Path | str,
                 *, llm_call: Callable | None = None,
                 model: str = "x-preview-f-free",
                 timeout: float = 60.0,
                 max_tokens: int = 1200) -> None:
        self._dir = Path(memory_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        # The LLM call function — defaults to gold_desk.llm.zen_client.
        # complete_json. Tests inject a fake (the constraint: "All LLM
        # calls go through the existing _run_persona / complete_json
        # infrastructure — no direct LLM calls in new code").
        if llm_call is None:
            from ..llm.zen_client import complete_json as _cj
            llm_call = _cj
        self._llm_call = llm_call
        self._model = model
        self._timeout = timeout
        self._max_tokens = max_tokens

    # ----------------------------------------------------------- Phase A

    def store_decision(self, run_id: str, symbol: str, action: str, *,
                       entry_price: float | None = None,
                       stop_price: float | None = None,
                       target_price: float | None = None,
                       position_size_pct: float | None = None,
                       conviction_label: str = "LOW",
                       kill_criteria: list[str] | None = None,
                       evidence_cited: list | None = None,
                       transcript_ref: str = "",
                       regime: str = "unknown",
                       benchmark: str = "SPY") -> None:
        """Append a pending entry to ``{symbol}.md`` (Phase A).

        Idempotent on (run_id, symbol, action): a second call with the
        same triple is a no-op (the entry is already pending). Mirror
        TA's idempotency guard but on run_id, not (date, ticker,
        rating) — the run_id is the unique cross-run key.

        After appending to the per-symbol file, ALSO append a one-line
        summary to ``index.md`` so the regime-peer fallback in
        ``recent_lessons`` can find this entry without scanning every
        symbol file.
        """
        symbol = (symbol or "").upper().strip()
        if not symbol or not run_id:
            return
        path = self._symbol_path(symbol)
        # idempotency guard — fast raw scan for an existing pending tag
        # with this (run_id, symbol, action).
        if path.exists():
            raw = path.read_text(encoding="utf-8")
            pending_marker = (f"[{run_id} | {symbol} | {action} | "
                              f"pending |")
            for line in raw.splitlines():
                line = line.strip()
                if line.startswith(pending_marker):
                    return  # already pending — no-op
        # build the entry
        decision = {
            "run_id": run_id,
            "symbol": symbol,
            "action": action,
            "entry_price": entry_price,
            "stop_price": stop_price,
            "target_price": target_price,
            "position_size_pct": position_size_pct,
            "conviction_label": conviction_label,
            "kill_criteria": list(kill_criteria or []),
            "evidence_cited": [
                (e if isinstance(e, dict)
                 else {"persona": "", "claim": str(e)[:200],
                       "source": ""})
                for e in (evidence_cited or [])
            ][:5],
            "transcript_ref": transcript_ref,
            "regime": regime,
            "benchmark": benchmark,
        }
        tag = (f"[{run_id} | {symbol} | {action} | pending | "
               f"regime={regime} | benchmark={benchmark}]")
        entry = (f"{tag}\n\nDECISION:\n"
                 f"{json.dumps(decision, indent=2, ensure_ascii=False)}"
                 f"{SEPARATOR}")
        # atomic append — file is opened in append mode; POSIX append is
        # atomic for writes < PIPE_BUF (4KB) so two concurrent writers
        # don't interleave. Our entries can exceed 4KB; for safety we
        # also use a per-symbol in-process lock.
        with _SYMBOL_LOCKS.lock_for(symbol):
            with open(path, "a", encoding="utf-8") as f:
                f.write(entry)
        # also append a one-line summary to index.md for the regime
        # peer fallback lookup. The summary line carries: run_id,
        # symbol, action, regime, status=pending. After Phase B
        # reflects, the index line is updated to status=reflected with
        # the alpha figure (see _update_index_for_reflection).
        self._append_index_line(symbol, run_id, action, regime,
                                benchmark, status="pending")

    # ----------------------------------------------------------- Phase B

    def reflect_on_decision(self, run_id: str, symbol: str,
                           realized_5d_return: float,
                           alpha_vs_benchmark: float,
                           benchmark_name: str = "SPY") -> dict | None:
        """Phase B deferred reflection — find the pending entry for
        (run_id, symbol), call the LLM ONCE, produce a structured
        lesson, update the entry's tag from pending → reflected, and
        persist the lesson in the entry's REFLECTION section.

        Idempotent on run_id: a second call for an already-reflected
        entry is a no-op (returns the existing lesson). Returns the
        structured lesson dict on success, or None on LLM failure (the
        entry stays pending so the operator can re-run reflection).

        The LLM call is a single ``complete_json`` over
        REFLECTION_SYSTEM + a user message carrying the original
        decision context + the realized 5d return + alpha. No re-runs,
        no retry-storms — a failure leaves the entry pending and the
        operator decides whether to re-run.
        """
        symbol = (symbol or "").upper().strip()
        if not symbol or not run_id:
            return None
        path = self._symbol_path(symbol)
        if not path.exists():
            return None
        # find the pending entry for this run_id
        with _SYMBOL_LOCKS.lock_for(symbol):
            text = path.read_text(encoding="utf-8")
            blocks = text.split(SEPARATOR)
            target_idx = None
            target_block = None
            for i, block in enumerate(blocks):
                stripped = block.strip()
                if not stripped:
                    continue
                first_line = stripped.splitlines()[0].strip()
                if (first_line.startswith(f"[{run_id} | {symbol} |")
                        and "| pending |" in first_line):
                    target_idx = i
                    target_block = stripped
                    break
            if target_idx is None:
                # entry may already be reflected — return existing lesson
                for block in blocks:
                    stripped = block.strip()
                    if not stripped:
                        continue
                    first_line = stripped.splitlines()[0].strip()
                    if (first_line.startswith(f"[{run_id} | {symbol} |")
                            and "| reflected |" in first_line):
                        m = _REFLECTION_RE.search(stripped)
                        if m:
                            try:
                                return json.loads(m.group(1).strip())
                            except json.JSONDecodeError:
                                return None
                return None
            # parse the decision context to feed the LLM
            decision_match = _DECISION_RE.search(target_block)
            decision_json_str = ""
            if decision_match:
                decision_json_str = decision_match.group(1).strip()
            try:
                decision = json.loads(decision_json_str) if decision_json_str else {}
            except json.JSONDecodeError:
                decision = {}
            # build the LLM prompt — single call, structured JSON output
            user_msg = self._reflection_user_msg(
                decision, realized_5d_return, alpha_vs_benchmark,
                benchmark_name)
            try:
                lesson = self._llm_call(
                    [{"role": "system", "content": REFLECTION_SYSTEM},
                     {"role": "user", "content": user_msg}],
                    self._model,
                    timeout=self._timeout,
                    temperature=0.0,
                    max_tokens=self._max_tokens,
                    retries=1)
            except Exception:
                # LLM failed — leave the entry pending so the operator
                # can re-run reflection. Don't cache a half-lesson.
                return None
            # validate the lesson shape (all 6 fields, correct types)
            lesson = self._validate_lesson(lesson, alpha_vs_benchmark)
            # update the entry: tag pending → reflected; append REFLECTION
            # block. Use atomic write (tempfile + os.replace) — same
            # discipline as TA's update_with_outcome.
            new_block = self._update_block_for_reflection(
                target_block, run_id, symbol, decision,
                realized_5d_return, alpha_vs_benchmark, lesson)
            blocks[target_idx] = new_block
            # apply per-symbol rotation cap
            blocks = self._apply_rotation(blocks, MAX_PER_SYMBOL)
            new_text = SEPARATOR.join(blocks)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(new_text, encoding="utf-8")
            tmp.replace(path)
        # update the index.md line for this entry (pending → reflected)
        self._update_index_for_reflection(symbol, run_id,
                                          alpha_vs_benchmark)
        return lesson

    # ----------------------------------------------------------- Re-inject

    def recent_lessons(self, symbol: str, regime: str | None = None,
                       k: int = 5) -> list[dict]:
        """Return up to k most recent reflected lessons for the symbol,
        falling back to same-regime peers if the symbol has < k of its
        own lessons.

        The PM's re-injector calls this with k=3 to prepend a
        "RECENT LESSONS" block to the PM's user_msg. The returned
        lessons are sorted most-recent-first.

        Each returned dict carries: date (the entry's run_id-derived
        date tag — we use the run_id's first 10 chars as the date
        prefix; ULIDs sort lexically by time so this is monotonic),
        action, alpha_pct, lesson, directional_call_correct,
        applicable_signals, regime, symbol.
        """
        symbol = (symbol or "").upper().strip()
        k = max(1, int(k))
        same = self._load_symbol_lessons(symbol)
        if len(same) >= k:
            return same[:k]
        # fallback — same-regime peers from the global index
        regime = (regime or "").strip() or "unknown"
        peers = self._load_regime_peer_lessons(symbol, regime,
                                                limit=k - len(same))
        combined = same + peers
        return combined[:k]

    # ----------------------------------------------------------- internals

    def _symbol_path(self, symbol: str) -> Path:
        # sanitize symbol for filesystem (BTC-USD → BTC-USD is fine on
        # POSIX; EURUSD=X → EURUSD=X is fine too). Keep the raw symbol
        # so the audit trail stays readable.
        safe = re.sub(r"[^A-Za-z0-9._\-=^]", "_", symbol)
        return self._dir / f"{safe}.md"

    def _index_path(self) -> Path:
        return self._dir / "index.md"

    def _append_index_line(self, symbol: str, run_id: str, action: str,
                           regime: str, benchmark: str,
                           status: str = "pending",
                           alpha: float | None = None) -> None:
        """Append a one-line summary to index.md as a JSON object.

        Format: one JSON object per line:
        ``{"run_id":..., "symbol":..., "action":..., "regime":...,
        "benchmark":..., "status":..., "alpha":..., "ts":...}``

        JSON-lines (not pipe-separated) so the regime tag — which
        ITSELF contains pipes (``trend:up|vol:calm``) — doesn't break
        parsing. The regime tag is a pipe-joined sorted-keys string
        from ``_regime_tag``; using JSON lets the parser find it
        without ambiguity. Used by the regime-peer fallback to find
        lessons without scanning every symbol file.
        """
        path = self._index_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        alpha_val = float(alpha) if isinstance(alpha, (int, float)) else None
        line = json.dumps({
            "run_id": run_id,
            "symbol": symbol,
            "action": action,
            "regime": regime,
            "benchmark": benchmark,
            "status": status,
            "alpha": alpha_val,
            "ts": _now_iso(),
        }, ensure_ascii=False)
        with _INDEX_LOCK:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def _update_index_for_reflection(self, symbol: str, run_id: str,
                                      alpha: float) -> None:
        """Update the index.md line for an entry from pending → reflected.

        Reads the index, finds the matching JSON line by run_id, sets
        status="reflected" + alpha=<float>, and atomic-writes the file
        back. Idempotent — a second call for the same run_id updates
        the alpha figure but doesn't duplicate the line.
        """
        path = self._index_path()
        if not path.exists():
            self._append_index_line(symbol, run_id, "?", "unknown", "SPY",
                                    status="reflected", alpha=alpha)
            return
        with _INDEX_LOCK:
            text = path.read_text(encoding="utf-8")
            new_lines: list[str] = []
            updated = False
            for line in text.splitlines():
                if not line.strip():
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    new_lines.append(line)  # preserve non-JSON lines
                    continue
                if entry.get("run_id") == run_id:
                    entry["status"] = "reflected"
                    entry["alpha"] = float(alpha)
                    entry["symbol"] = symbol
                    entry["ts"] = _now_iso()
                    new_lines.append(json.dumps(entry, ensure_ascii=False))
                    updated = True
                else:
                    new_lines.append(line)
            if not updated:
                new_lines.append(json.dumps({
                    "run_id": run_id, "symbol": symbol, "action": "?",
                    "regime": "unknown", "benchmark": "SPY",
                    "status": "reflected", "alpha": float(alpha),
                    "ts": _now_iso(),
                }, ensure_ascii=False))
            tmp = path.with_suffix(".tmp")
            tmp.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
            tmp.replace(path)

    def _load_symbol_lessons(self, symbol: str) -> list[dict]:
        """Load all reflected lessons for a symbol from its per-symbol
        file. Returns the lessons sorted most-recent-first (by run_id,
        which is a ULID and sorts lexically by time)."""
        path = self._symbol_path(symbol)
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        blocks = [b.strip() for b in text.split(SEPARATOR) if b.strip()]
        lessons: list[dict] = []
        for block in blocks:
            parsed = self._parse_entry(block)
            if parsed and parsed.get("status") == "reflected":
                lessons.append(parsed)
        # sort by run_id desc (ULID → most-recent first)
        lessons.sort(key=lambda L: L.get("run_id", ""), reverse=True)
        return lessons

    def _load_regime_peer_lessons(self, exclude_symbol: str,
                                   regime: str, limit: int) -> list[dict]:
        """Load up to `limit` reflected lessons from OTHER symbols in
        the same regime. Uses the global index.md (JSON-lines) to find
        candidate (symbol, run_id) pairs, then loads each symbol's file
        to pull the full structured lesson. Caps the work at 2*limit
        symbol files scanned."""
        if limit <= 0:
            return []
        path = self._index_path()
        if not path.exists():
            return []
        text = path.read_text(encoding="utf-8")
        # find JSON-line entries with status=reflected + regime=<regime>
        # + symbol != exclude_symbol. JSON-lines (R2-4 fix) so the
        # regime tag's embedded pipes don't break parsing.
        candidates: list[tuple[str, str]] = []  # (symbol, run_id)
        for line in text.splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(entry, dict):
                continue
            if entry.get("status") != "reflected":
                continue
            sym = entry.get("symbol", "")
            if sym == exclude_symbol:
                continue
            if entry.get("regime") != regime:
                continue
            run_id = entry.get("run_id", "")
            if run_id:
                candidates.append((sym, run_id))
        # sort candidates by run_id desc (most recent first)
        candidates.sort(key=lambda c: c[1], reverse=True)
        out: list[dict] = []
        for sym, run_id in candidates:
            if len(out) >= limit:
                break
            lessons = self._load_symbol_lessons(sym)
            for L in lessons:
                if L.get("run_id") == run_id:
                    out.append(L)
                    break
            if len(out) >= limit:
                break
        return out

    def _parse_entry(self, block: str) -> dict | None:
        """Parse a single entry block (tag + DECISION + optional
        REFLECTION). Returns a dict with the structured lesson fields
        if reflected, or the pending fields if pending."""
        lines = block.strip().splitlines()
        if not lines:
            return None
        tag_line = lines[0].strip()
        # try reflected first (more specific)
        m_ref = _REFLECTED_TAG_RE.match(tag_line)
        if m_ref:
            run_id, sym, action, raw_pct, alpha_pct, days, regime, \
                bench = [g.strip() for g in m_ref.groups()]
            decision_match = _DECISION_RE.search(block)
            reflection_match = _REFLECTION_RE.search(block)
            try:
                decision = json.loads(decision_match.group(1).strip()) \
                    if decision_match else {}
            except json.JSONDecodeError:
                decision = {}
            try:
                lesson = json.loads(reflection_match.group(1).strip()) \
                    if reflection_match else {}
            except json.JSONDecodeError:
                lesson = {}
            return {
                "run_id": run_id,
                "symbol": sym,
                "action": action,
                "status": "reflected",
                "raw_pct": raw_pct,
                "alpha_pct": _parse_pct(alpha_pct),
                "holding_days": int(days) if days.isdigit() else None,
                "regime": regime or "unknown",
                "benchmark": bench or "SPY",
                "date": _ulid_date(run_id),
                "decision": decision,
                "lesson": lesson,
                # convenience fields for the re-injector
                "directional_call_correct": lesson.get(
                    "directional_call_correct"),
                "what_held": lesson.get("what_held", ""),
                "what_failed": lesson.get("what_failed", ""),
                "lesson_text": lesson.get("lesson", ""),
                "applicable_signals": lesson.get(
                    "applicable_signals", []),
            }
        # try pending
        m_pen = _PENDING_TAG_RE.match(tag_line)
        if m_pen:
            run_id, sym, action, regime, bench = \
                [g.strip() for g in m_pen.groups()]
            decision_match = _DECISION_RE.search(block)
            try:
                decision = json.loads(decision_match.group(1).strip()) \
                    if decision_match else {}
            except json.JSONDecodeError:
                decision = {}
            return {
                "run_id": run_id,
                "symbol": sym,
                "action": action,
                "status": "pending",
                "regime": regime or "unknown",
                "benchmark": bench or "SPY",
                "date": _ulid_date(run_id),
                "decision": decision,
            }
        return None

    def _apply_rotation(self, blocks: list[str], max_entries: int) -> list[str]:
        """Drop oldest reflected blocks when their count exceeds
        max_entries. Pending blocks are always kept."""
        if max_entries <= 0:
            return blocks
        reflected_count = 0
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                continue
            first = stripped.splitlines()[0].strip()
            if "| reflected |" in first:
                reflected_count += 1
        if reflected_count <= max_entries:
            return blocks
        to_drop = reflected_count - max_entries
        kept: list[str] = []
        for block in blocks:
            stripped = block.strip()
            if not stripped:
                kept.append(block)
                continue
            first = stripped.splitlines()[0].strip()
            if ("| reflected |" in first) and to_drop > 0:
                to_drop -= 1
                continue
            kept.append(block)
        return kept

    def _update_block_for_reflection(self, target_block: str,
                                      run_id: str, symbol: str,
                                      decision: dict,
                                      raw_return: float,
                                      alpha: float,
                                      lesson: dict) -> str:
        """Rewrite a pending block as reflected: update the tag line,
        preserve the DECISION block, append a REFLECTION block with the
        structured lesson JSON."""
        lines = target_block.splitlines()
        old_tag = lines[0].strip()
        # parse regime + benchmark from the old pending tag
        regime = "unknown"
        benchmark = "SPY"
        m = _PENDING_TAG_RE.match(old_tag)
        if m:
            regime = m.group(4).strip() or "unknown"
            benchmark = m.group(5).strip() or "SPY"
        action = decision.get("action", "")
        raw_pct = f"{raw_return:+.4f}%"
        alpha_pct = f"{alpha:+.4f}%"
        new_tag = (f"[{run_id} | {symbol} | {action} | reflected | "
                   f"raw {raw_pct} | alpha {alpha_pct} | 5d | "
                   f"regime={regime} | benchmark={benchmark}]")
        # preserve the DECISION block (everything between DECISION: and
        # the end of the block, minus the old tag line)
        body = "\n".join(lines[1:]).strip()
        # body now starts with "DECISION:\n{json}"
        new_block = (f"{new_tag}\n\n{body}\n\nREFLECTION:\n"
                     f"{json.dumps(lesson, indent=2, ensure_ascii=False)}")
        return new_block

    def _reflection_user_msg(self, decision: dict,
                              raw_return: float, alpha: float,
                              benchmark_name: str) -> str:
        """Build the user message for the reflection LLM call.

        Includes:
          - the original action + entry/stop/target/conviction/
            kill_criteria (so the LLM sees what was actually decided);
          - the realized 5d return + alpha vs benchmark (the outcome
            that the LLM must judge the decision against);
          - the original evidence_cited + transcript_ref (so the LLM
            can name which signals were right/wrong).
        """
        action = decision.get("action", "?")
        conviction = decision.get("conviction_label", "?")
        kill = decision.get("kill_criteria") or []
        ev = decision.get("evidence_cited") or []
        ev_lines = []
        for e in ev[:5]:
            if isinstance(e, dict):
                ev_lines.append(
                    f"  - {e.get('persona', '?')}: {e.get('claim', '')}"
                    f" (source: {e.get('source', '?')})")
            else:
                ev_lines.append(f"  - {e}")
        ev_str = "\n".join(ev_lines) or "  (no evidence cited)"
        kill_str = "\n".join(f"  - {k}" for k in kill) or "  (none)"
        return (
            f"Original decision (action={action}, "
            f"conviction={conviction}):\n"
            f"  entry={decision.get('entry_price')}, "
            f"stop={decision.get('stop_price')}, "
            f"target={decision.get('target_price')}\n"
            f"  kill_criteria:\n{kill_str}\n"
            f"  evidence_cited:\n{ev_str}\n\n"
            f"Realized 5-day return: {raw_return:+.4f}%\n"
            f"Alpha vs {benchmark_name}: {alpha:+.4f}%\n\n"
            f"Produce the structured lesson now."
        )

    def _validate_lesson(self, lesson: dict | None,
                         alpha_default: float) -> dict:
        """Validate the LLM's structured lesson has all 6 fields with
        the right types. Falls back to a minimal-shape lesson on
        missing fields (never raises — Phase B never breaks the desk).
        """
        if not isinstance(lesson, dict):
            return {
                "directional_call_correct": False,
                "alpha_pct": float(alpha_default),
                "what_held": "",
                "what_failed": "",
                "lesson": "(reflection unavailable — LLM returned non-dict)",
                "applicable_signals": [],
            }
        # directional_call_correct: bool (coerce loosely)
        dcc_raw = lesson.get("directional_call_correct")
        if isinstance(dcc_raw, bool):
            dcc = dcc_raw
        elif isinstance(dcc_raw, str):
            dcc = dcc_raw.strip().lower() in ("true", "yes", "1", "correct")
        else:
            dcc = bool(dcc_raw)
        # alpha_pct: float (fall back to the provided alpha)
        try:
            alpha = float(lesson.get("alpha_pct", alpha_default))
        except (TypeError, ValueError):
            alpha = float(alpha_default)
        # what_held / what_failed / lesson: str (truncate to 400 chars)
        what_held = str(lesson.get("what_held", ""))[:400]
        what_failed = str(lesson.get("what_failed", ""))[:400]
        lesson_text = str(lesson.get("lesson", ""))[:400]
        # applicable_signals: list[str]
        sigs_raw = lesson.get("applicable_signals", [])
        if not isinstance(sigs_raw, list):
            sigs_raw = []
        sigs = [str(s).strip()[:80] for s in sigs_raw
                if str(s).strip()][:10]
        return {
            "directional_call_correct": dcc,
            "alpha_pct": round(alpha, 4),
            "what_held": what_held,
            "what_failed": what_failed,
            "lesson": lesson_text,
            "applicable_signals": sigs,
        }


# ----------------------------------------------------------------- helpers

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _parse_pct(s: str) -> float | None:
    """Parse "+1.2345%" into 1.2345. None on failure."""
    if not s:
        return None
    s = s.strip().rstrip("%")
    try:
        return float(s)
    except ValueError:
        return None


def _ulid_date(run_id: str) -> str:
    """Extract a date prefix from a ULID. ULIDs encode time in their
    first 10 chars (Crockford base32); for display purposes we just
    take the first 10 chars of the run_id as a sortable date proxy.
    A real ULID → ISO date conversion is overkill for the re-injector's
    display block."""
    if not run_id:
        return "?"
    return run_id[:10]


class _PerSymbolLocks:
    """In-process per-symbol lock dict so concurrent writes to the same
    symbol file don't interleave. Guarded by a global lock so the dict
    itself is thread-safe."""

    def __init__(self) -> None:
        import threading
        self._guards: dict[str, threading.Lock] = {}
        self._meta = threading.Lock()

    def lock_for(self, symbol: str) -> "threading.Lock":
        import threading
        with self._meta:
            if symbol not in self._guards:
                self._guards[symbol] = threading.Lock()
            return self._guards[symbol]


_SYMBOL_LOCKS = _PerSymbolLocks()

import threading as _threading
_INDEX_LOCK = _threading.Lock()


def default_memory_dir(data_root: str | Path | None = None) -> Path:
    """Resolve the default memory dir as a sibling of data_root.

    For ``data_root="data"`` → ``Path("cache") / "memory"``.
    For ``data_root="/abs/path/data"`` → ``/abs/path/cache/memory``.
    """
    if data_root is None:
        return Path("cache") / "memory"
    dr = Path(data_root)
    parent = dr.parent if dr.name else dr
    return parent / "cache" / "memory"
