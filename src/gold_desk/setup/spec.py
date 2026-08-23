"""Document 2 — the one setup hypothesis (spec object + content hash).

STATUS: GUESS. This spec exercises the pipeline. It can NEVER be promoted to
FROZEN_LIVE_CANDIDATE by narrative — only the Doc 1.5 exam battery can, and
its kill numbers are still BLOCKED. Treat every candidate it produces as a
paper hypothesis.

Hypothesis (falsifiable claim):
  In the London session, when an H1 close breaks out of the pre-London range
  (02:00-07:00 UTC) with ATR-normalised expansion, entering at that close
  with a 1.5*ATR(14) stop, 2.0R target and a 6-bar time-stop has positive
  expectancy after pessimistic costs. Doc 1.5 exists to kill this claim.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict

SPEC_ID = "GUESS_london_range_breakout"
SPEC_VERSION = "0.1.0"
STATUS = "GUESS"


@dataclass
class SetupSpec:
    setup_id: str = SPEC_ID
    setup_version: str = SPEC_VERSION
    status: str = STATUS
    # session window (UTC): breakout evaluated on bars closing 08:00..11:00
    pre_range_start_hour: int = 2       # range bars open 02:00..06:59
    pre_range_end_hour: int = 7
    signal_start_hour: int = 8          # signal bars open 08,09,10 (close 09..11)
    signal_end_hour: int = 11
    atr_period: int = 14
    stop_atr_mult: float = 1.5
    target_r_multiple: float = 2.0
    time_stop_bars: int = 6
    expiry_minutes: int = 10
    buffer_atr_mult: float = 0.10       # breakout buffer beyond range edge
    max_candidates_per_day: int = 1
    expected_trades_per_week: str = "~1-3 (sanity figure; Doc 1.5 must verify)"
    known_failure_modes: list[str] = field(default_factory=lambda: [
        "quiet ranging London with false breaks",
        "news spike already consumed by breakout bar",
        "Monday gap makes range meaningless",
    ])
    forbidden_patches: list[str] = field(default_factory=lambda: [
        "adding an LLM to rescue losers",
        "adding 12 indicators",
        "tuning parameters after seeing the holdout",
    ])

    def to_dict(self) -> dict:
        return asdict(self)

    def hash(self) -> str:
        canon = json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()


DEFAULT_SPEC = SetupSpec()
