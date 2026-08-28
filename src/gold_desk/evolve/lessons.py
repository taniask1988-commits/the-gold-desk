"""R5 BUILD 4 — temporal lesson memory: beliefs with expiry dates.

FUNDAMENTAL (docs/SELF_EVOLUTION_RESEARCH.md §3.6): Zep/Graphiti's move
was to make memory TEMPORAL — every fact carries a validity window and
a NEW contradicting observation INVALIDATES the old fact instead of
silently overwriting it (94.8% vs MemGPT 93.4% on DMR; +18.5% accuracy
on LongMemEval). A trading desk needs exactly this: a lesson like
"breakouts fail in low-ATR regimes" is TRUE OF A REGIME, not of the
universe — regimes end, and memory must know when.

OURS (extends R2-4's ReflectiveMemory with the temporal semantics):

  validity window   every lesson is born with valid_from; retirement
                    stamps valid_to — history is preserved, current
                    truth is queryable (Graphiti's edge bi-temporality,
                    reduced to two timestamps)
  evidence counters a lesson carries `support` and `contradict` counts,
                    NEVER a narrative confidence
  contradiction     once contradict >= support AND support >= 2, the
  invalidation      lesson RETIRES (status=contradicted) — no prose can
                    rescue it
  decayed           confidence = 0.5^(age/halflife) · Laplace-smoothed
  confidence        (support − contradict)/(support + contradict + 2):
                    monotone in evidence volume, decaying in time,
                    sign-flippable by contradiction pressure
  determinism       every method takes `now` as a parameter — zero
                    wall-clock reads inside the logic (the alerts law)

Persistence: JSONL, one record per line, load/save round-trips. The
store is the substrate the Reflexion loop (R2-4) writes into; retiring
stale lessons is what R5 adds on top.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

STATUSES = ("active", "contradicted", "expired", "retired")
DAY_S = 86_400.0
DEFAULT_HALFLIFE_DAYS = 90.0
DEFAULT_MAX_AGE_DAYS = 365.0
DEFAULT_CONTRADICTION_MIN_SUPPORT = 2


def _lid_for(text: str, symbol: str) -> str:
    """Stable lesson id from content+symbol (idempotent re-adds)."""
    slug = re.sub(r"[^a-z0-9]+", "-", f"{symbol} {text}".lower()).strip("-")
    return slug[:64] or "lesson"


@dataclass
class LessonRecord:
    """One belief with a lifetime."""
    lesson_id: str
    text: str
    symbol: str
    regime: str = "unspecified"
    valid_from: float = 0.0            # epoch seconds
    valid_to: float | None = None      # None = open-ended until retired
    status: str = "active"
    support: int = 0
    contradict: int = 0
    halflife_days: float = DEFAULT_HALFLIFE_DAYS
    max_age_days: float = DEFAULT_MAX_AGE_DAYS
    created_note: str = ""

    def age_days(self, now: float) -> float:
        return max(0.0, (now - self.valid_from) / DAY_S)

    def confidence(self, now: float) -> float:
        """0.5^(age/halflife) · (support − contradict)/(support + contradict + 2).

        Halflife means HALF-LIFE: at age == halflife_days the decay
        weight is exactly 0.5 (at 2× it is 0.25). The evidence term is
        LAPLACE-SMOOTHED — the +2 prior keeps a 1-sample lesson at 1/3,
        not 1.0: one observation is direction, not proof (a 4-support
        lesson scores 4/6, 10-support 10/12, saturating toward 1 only
        with volume). Range (−1, 1): sign carries direction, magnitude
        carries decayed, shrunk evidence weight."""
        if self.status != "active":
            return 0.0
        age = self.age_days(now)
        if age > self.max_age_days:
            return 0.0
        decay = 0.5 ** (age / max(1e-9, self.halflife_days))
        net = self.support - self.contradict
        total = self.support + self.contradict + 2
        return round(decay * net / total, 6)

    def to_dict(self) -> dict:
        return {
            "lesson_id": self.lesson_id, "text": self.text,
            "symbol": self.symbol, "regime": self.regime,
            "valid_from": self.valid_from, "valid_to": self.valid_to,
            "status": self.status, "support": self.support,
            "contradict": self.contradict,
            "halflife_days": self.halflife_days,
            "max_age_days": self.max_age_days,
            "created_note": self.created_note,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LessonRecord":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__
                      if k in d})


class TemporalLessonStore:
    """The lesson belief system. All mutation methods take `now`
    (epoch seconds) explicitly — determinism law."""

    def __init__(self, contradiction_min_support: int =
                 DEFAULT_CONTRADICTION_MIN_SUPPORT):
        self._lessons: dict[str, LessonRecord] = {}
        self.contradiction_min_support = int(contradiction_min_support)
        self._journal: list[str] = []          # audit lines

    # ------------------------------------------------------------- queries
    def get(self, lesson_id: str) -> LessonRecord | None:
        return self._lessons.get(lesson_id)

    def all_lessons(self) -> list[LessonRecord]:
        return sorted(self._lessons.values(), key=lambda r: r.lesson_id)

    def _expire_stale(self, rec: LessonRecord, now: float) -> bool:
        """Transition active → expired when past max_age. Returns True
        when the status changed (the caller journals it)."""
        if rec.status == "active" and rec.age_days(now) > rec.max_age_days:
            rec.status = "expired"
            rec.valid_to = now
            return True
        return False

    def active_lessons(self, now: float, symbol: str | None = None,
                       regime: str | None = None,
                       min_confidence: float = -1.0) -> list[dict]:
        """Active, non-expired lessons — optionally filtered to a symbol
        (exact) and/or regime (exact), ordered by decayed confidence
        descending. Each row carries its confidence so the consumer can
        weigh, not just rank."""
        out: list[dict] = []
        for rec in self._lessons.values():
            if self._expire_stale(rec, now):
                self._journal.append(
                    f"{now}|EXPIRED|{rec.lesson_id}|age>{rec.max_age_days}d")
            if rec.status != "active":
                continue
            if symbol is not None and rec.symbol != symbol:
                continue
            if regime is not None and rec.regime != regime:
                continue
            conf = rec.confidence(now)
            if conf <= min_confidence:
                continue
            out.append({"lesson_id": rec.lesson_id, "text": rec.text,
                        "symbol": rec.symbol, "regime": rec.regime,
                        "confidence": conf, "support": rec.support,
                        "contradict": rec.contradict,
                        "age_days": round(rec.age_days(now), 2)})
        out.sort(key=lambda r: (-r["confidence"], r["lesson_id"]))
        return out

    # ------------------------------------------------------------ mutation
    def add_lesson(self, text: str, symbol: str, now: float,
                   regime: str = "unspecified",
                   halflife_days: float = DEFAULT_HALFLIFE_DAYS,
                   max_age_days: float = DEFAULT_MAX_AGE_DAYS,
                   note: str = "") -> LessonRecord:
        """Create (or refresh) a lesson. Re-adding an existing
        (lesson_id) text does NOT reset its evidence counters — beliefs
        keep their history (idempotent re-injection from the R2-4
        reflector)."""
        lid = _lid_for(text, symbol)
        existing = self._lessons.get(lid)
        if existing is not None:
            self._journal.append(f"{now}|RESEEN|{lid}")
            return existing
        rec = LessonRecord(lesson_id=lid, text=text, symbol=symbol,
                           regime=regime, valid_from=float(now),
                           halflife_days=float(halflife_days),
                           max_age_days=float(max_age_days),
                           created_note=note)
        self._lessons[lid] = rec
        self._journal.append(f"{now}|BORN|{lid}")
        return rec

    def add_evidence(self, lesson_id: str, outcome: str, now: float) -> \
            dict:
        """Record one outcome against a lesson. outcome ∈
        {"support", "contradict"}. Applies the contradiction rule:

            contradict >= support AND support >= min_support
              → status=contradicted (RETIRED, valid_to=now)

        Returns the transition dict (the audit record). Unknown ids and
        bad outcomes fail-closed as {"ok": False, ...} — never invent
        evidence for a lesson that does not exist."""
        rec = self._lessons.get(lesson_id)
        if rec is None:
            return {"ok": False, "error": "unknown_lesson", "lesson_id": lesson_id}
        if outcome not in ("support", "contradict"):
            return {"ok": False, "error": "bad_outcome", "outcome": outcome}
        if rec.status != "active":
            self._expire_stale(rec, now)
            if rec.status != "active":
                return {"ok": False, "error": f"lesson_{rec.status}",
                        "lesson_id": lesson_id, "status": rec.status}
        if outcome == "support":
            rec.support += 1
        else:
            rec.contradict += 1
        self._journal.append(f"{now}|EVIDENCE|{lesson_id}|{outcome}")
        transition = {"ok": True, "lesson_id": lesson_id,
                      "support": rec.support, "contradict": rec.contradict,
                      "retired": False}
        if (rec.contradict >= rec.support
                and rec.support >= self.contradiction_min_support):
            rec.status = "contradicted"
            rec.valid_to = now
            transition["retired"] = True
            self._journal.append(f"{now}|RETIRED|{lesson_id}|contradicted")
        transition["confidence"] = rec.confidence(now)
        return transition

    def retire(self, lesson_id: str, now: float) -> dict:
        """Manual retirement (the operator's kill switch)."""
        rec = self._lessons.get(lesson_id)
        if rec is None:
            return {"ok": False, "error": "unknown_lesson"}
        if rec.status == "active":
            rec.status = "retired"
            rec.valid_to = now
            self._journal.append(f"{now}|RETIRED|{lesson_id}|manual")
        return {"ok": True, "status": rec.status}

    # ---------------------------------------------------------- persistence
    def journal_lines(self) -> list[str]:
        return list(self._journal)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(r.to_dict(), sort_keys=True)
                 for r in self.all_lessons()]
        p.write_text("\n".join(lines) + ("\n" if lines else ""))

    @classmethod
    def load(cls, path: str | Path,
             contradiction_min_support: int =
             DEFAULT_CONTRADICTION_MIN_SUPPORT) -> "TemporalLessonStore":
        store = cls(contradiction_min_support=contradiction_min_support)
        p = Path(path)
        if not p.exists():
            return store
        for line in p.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = LessonRecord.from_dict(json.loads(line))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            store._lessons[rec.lesson_id] = rec
        return store
