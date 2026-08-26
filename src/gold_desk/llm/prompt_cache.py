"""PromptCache — sha256 content-addressed prompt/response cache.

R2-4 — judged vs ai-hedge-fund v2.2.0's hedge_fund/llm/cache.py (48
lines). AHF's cache is three things at once:
  1. a cache: a backtest re-running an agent over an unchanged snapshot
     costs $0;
  2. the persistence record: the EXACT prompt + response behind every
     decision, for replay and audit;
  3. the debug trail: failed parses keep the raw response on disk.

OURS extends the bar with:
  - explicit ``put_failure(key, raw_response, error)`` so failed parses
    are persisted as structured records (parse_ok=False) the audit trail
    can iterate, not just "the raw response happens to be on disk";
  - optional TTL so a live desk can avoid stale-market-data cache
    poisoning (a 24h TTL caps how long a cached reasoning survives
    before the markets plane re-asserts);
  - thread-safe atomic writes (tempfile + os.replace) so the 6 parallel
    persona threads can each put their own key without corrupting each
    other's file or the directory.

Files live under ``<cache_dir>/{key}.json`` — one JSON per call, keyed
by a 24-char sha256 prefix of (persona_name | model | system | user).
The cache key matches AHF's shape (``agent | model | system | user``)
with ``persona_name`` substituted for AHF's ``agent`` (OURS' personas
are the equivalent unit).
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def prompt_key(persona_name: str, model: str, system_prompt: str,
               user_msg: str) -> str:
    """Cache key for one (persona, model, prompt) combination.

    Mirrors AHF's ``prompt_key(agent, model, system, user)`` shape with
    ``persona_name`` in the agent slot. Returns the first 24 hex chars
    of the sha256 — same length as AHF so the on-disk filenames stay
    short enough to ``ls`` by eye.
    """
    payload = f"{persona_name}|{model}|{system_prompt}|{user_msg}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")


class PromptCache:
    """One JSON file per LLM call, content-addressed by sha256 of the
    prompt.

    Three roles (mirrors AHF cache.py:1-11):
      1. cache: a re-run over an unchanged prompt returns the cached
         parsed result with $0 LLM cost;
      2. persistence record: the parsed JSON + raw response + model
         that produced every decision, for replay and audit;
      3. debug trail: failed parses are persisted as ``parse_ok=False``
         records so the operator can grep the cache dir for the broken
         responses and fix the prompt / parser without re-running the
         desk.

    Thread-safety: writes are atomic via tempfile + os.replace (POSIX
    rename is atomic; last writer wins, no corruption). The 6 parallel
    persona threads each compute a different key (different
    persona_name) so they write different files — no contention. Two
    threads computing the same key (e.g. a backtest re-running the same
    persona) race harmlessly: both writes are atomic, one wins.

    TTL: optional ``ttl_seconds`` (default None = forever). When set,
    ``get`` returns None for records older than TTL — a live desk can
    set 24h to avoid stale-market-data cache poisoning (a cached
    reasoning about yesterday's tape shouldn't survive into today's
    session).
    """

    def __init__(self, cache_dir: Path | str,
                 ttl_seconds: float | None = None) -> None:
        self._dir = Path(cache_dir)
        # TTL is per-instance so different callers (backtest forever vs
        # live desk 24h) can share the same code path with different
        # policies.
        self._ttl_seconds = ttl_seconds

    # ----------------------------------------------------------- key shape

    @staticmethod
    def key_for(persona_name: str, model: str, system_prompt: str,
                user_msg: str) -> str:
        """Static-method form of ``prompt_key`` for callers that want
        the key without an instance (e.g. a CLI that lists cached
        keys)."""
        return prompt_key(persona_name, model, system_prompt, user_msg)

    # ----------------------------------------------------------------- read

    def get(self, key: str) -> dict | None:
        """Return the cached record for ``key`` or None on miss.

        A corrupt JSON file is treated as a miss (returns None) — the
        next ``put`` will overwrite it cleanly. A record older than the
        instance TTL is treated as a miss (returns None) but is NOT
        deleted here; the operator may want to inspect the stale record
        before it's overwritten. Call ``evict_stale`` to delete expired
        records.
        """
        path = self._dir / f"{key}.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None  # corrupt cache entry → miss, will be rewritten
        if not isinstance(record, dict):
            return None
        # TTL check — a stale record is a miss (don't return its
        # parsed payload to a live caller; they'll re-call the LLM and
        # the next put will overwrite the stale record).
        if self._is_stale(record):
            return None
        return record

    # ---------------------------------------------------------------- write

    def put(self, key: str, record: dict) -> None:
        """Persist a successful parse record.

        ``record`` should carry: {response, parsed, parse_ok=True,
        model_used, persona}. The ``created_at`` timestamp is added
        here (mirrors AHF cache.py:46 — put adds created_at). Writes
        are atomic (tempfile + os.replace) so concurrent writers can't
        corrupt each other's file.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        full = {
            "persona": record.get("persona", ""),
            "model_used": record.get("model_used", ""),
            "response": record.get("response"),
            "parsed": record.get("parsed"),
            "parse_ok": True,
            "error": None,
            "created_at": _now_iso(),
        }
        self._atomic_write(key, full)

    def put_failure(self, key: str, raw_response: str | None,
                    error: str) -> None:
        """Persist a failed-parse (or transport-failure) record.

        The AHF bar's debug trail concept: failed parses keep the raw
        response on disk. OURS extends it with an explicit structured
        record so the audit trail can iterate ``parse_ok=False``
        entries and grep for the broken responses without having to
        diff every cached file.

        For transport failures (LLMUnavailable — no response was
        returned), ``raw_response`` is None and only the error string
        is persisted. For parse failures (LLMInvalidJSON — the model
        returned text we couldn't extract JSON from), ``raw_response``
        is the unparseable text so the operator can see what the model
        actually said.
        """
        self._dir.mkdir(parents=True, exist_ok=True)
        full = {
            "persona": "",   # filled by the caller via put()'s record
            "model_used": "",
            "response": raw_response,
            "parsed": None,
            "parse_ok": False,
            "error": str(error)[:1000],
            "created_at": _now_iso(),
        }
        self._atomic_write(key, full)

    def put_failure_with_meta(self, key: str, raw_response: str | None,
                              error: str, *, persona: str = "",
                              model_used: str = "") -> None:
        """``put_failure`` variant that preserves the persona + model
        metadata alongside the failure record. The persona/model
        context is what makes the audit trail useful — the operator
        can grep "all fundamentalist failures" or "all gpt-4o-mini
        transport failures" without re-deriving the key."""
        self._dir.mkdir(parents=True, exist_ok=True)
        full = {
            "persona": persona,
            "model_used": model_used,
            "response": raw_response,
            "parsed": None,
            "parse_ok": False,
            "error": str(error)[:1000],
            "created_at": _now_iso(),
        }
        self._atomic_write(key, full)

    # ----------------------------------------------------------------- utils

    def evict_stale(self) -> int:
        """Delete all records older than TTL. Returns the count
        evicted. No-op when TTL is None (forever). Useful for a daily
        cron on a live desk to keep the cache dir from growing
        unbounded."""
        if self._ttl_seconds is None:
            return 0
        n = 0
        if not self._dir.exists():
            return 0
        for p in self._dir.glob("*.json"):
            try:
                record = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if self._is_stale(record):
                try:
                    p.unlink()
                    n += 1
                except OSError:
                    pass
        return n

    def list_records(self) -> list[dict]:
        """List every record in the cache dir (sorted by created_at).
        For the audit trail / CLI inspection."""
        out: list[dict] = []
        if not self._dir.exists():
            return out
        for p in self._dir.glob("*.json"):
            try:
                record = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(record, dict):
                    record["_key"] = p.stem
                    record["_path"] = str(p)
                    out.append(record)
            except (json.JSONDecodeError, OSError):
                continue
        out.sort(key=lambda r: r.get("created_at") or "")
        return out

    # ----------------------------------------------------------- internals

    def _is_stale(self, record: dict) -> bool:
        """True if the record is older than TTL. No-op when TTL is None."""
        if self._ttl_seconds is None:
            return False
        created = record.get("created_at")
        if not created:
            return False  # no timestamp → treat as fresh (legacy record)
        try:
            ts = datetime.fromisoformat(
                created.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return False
        now = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age = (now - ts).total_seconds()
        return age > float(self._ttl_seconds)

    def _atomic_write(self, key: str, record: dict[str, Any]) -> None:
        """Atomic write: tempfile in the same dir, then os.replace.

        POSIX rename is atomic — a crash mid-write leaves either the
        old file or the new file, never a half-written file. Two
        concurrent writers racing on the same key: both create temp
        files, both call os.replace; one rename "wins" last and the
        other's temp file is orphaned (cleaned up by the OS on
        process exit or a future gc).
        """
        path = self._dir / f"{key}.json"
        # NamedTemporaryFile in the SAME directory so os.replace stays
        # within one filesystem (rename across filesystems raises).
        fd, tmp_path = tempfile.mkstemp(
            prefix=f".{key}.", suffix=".tmp", dir=str(self._dir))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, indent=2, ensure_ascii=False))
            os.replace(tmp_path, path)
        except Exception:
            # cleanup the orphaned temp file on any failure
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise


# ------------------------------------------------------------ module default

# The default cache dir resolves to <repo>/cache/llm/ (sibling of the
# data/ dir). The cache/ root is .gitignored — runtime artifacts, not
# source. Tests pass an explicit tmp_path; production picks up the
# repo-relative default. Resolved lazily so import-time never creates
# directories.
def default_cache_dir(data_root: str | Path | None = None) -> Path:
    """Resolve the default cache dir as a sibling of data_root.

    For ``data_root="data"`` → ``Path("cache") / "llm"``.
    For ``data_root="/abs/path/data"`` → ``/abs/path/cache/llm``.
    The cache root (``/abs/path/cache``) is the same shape as the
    brief's ``cache/memory/`` and ``cache/llm/`` subdirs.
    """
    if data_root is None:
        return Path("cache") / "llm"
    dr = Path(data_root)
    # parent of data_root is the repo root; cache/ lives next to data/
    parent = dr.parent if dr.name else dr
    return parent / "cache" / "llm"
