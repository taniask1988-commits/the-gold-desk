"""§6 — the setup engine. One rule. Full package or None. Never a story.

GUESS rule (all closed bars, broker-aligned H1, UTC hours):
  1. Build today's pre-London range from bars opening 02:00..06:59 UTC
     (needs >= 4 of the 5 expected bars, else no candidate).
  2. Look at the signal bar (the bar that just closed). It qualifies iff its
     open hour is 08, 09 or 10 UTC and its close breaks a range edge by more
     than buffer = 0.10 * ATR(14).
  3. Side: buy on upside break, sell on downside break. Entry = signal close.
     Stop = entry -/+ 1.5*ATR. Target = entry +/- 2.0 * stop distance.
     Time-stop = decision + 6 bars. Expiry = decision + 10 min.
     Invalidation (mechanical): "H1 close back inside pre-London range".
  4. At most one candidate per day.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from ..clock import iso
from ..data.model import Bar
from ..features.indicators import assert_closed, atr, range_stats
from .spec import SetupSpec


@dataclass
class SetupCandidate:
    schema: str = "setup_candidate.v1"
    setup_id: str = ""
    setup_version: str = ""
    symbol: str = "XAUUSD"
    timeframe: str = "H1"
    decision_ts: str = ""
    side: str = ""                # buy | sell
    entry_type: str = "market"    # human pastes right after bar close
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    time_stop_ts: str = ""
    expiry_ts: str = ""
    invalidation: str = ""
    stop_distance: float = 0.0
    features_used: dict = field(default_factory=dict)
    data_hash: str = ""
    spec_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _hash_bars(bars: list[Bar]) -> str:
    canon = "|".join(b.canonical() for b in bars)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class SetupEngine:
    def __init__(self, spec: SetupSpec | None = None):
        self.spec = spec or SetupSpec()
        self.spec_hash = self.spec.hash()
        self._last_candidate_day: str | None = None

    def evaluate(self, bars: list[Bar], decision_ts: datetime) -> SetupCandidate | None:
        """bars: the trailing window of CLOSED bars ending with the signal bar."""
        if not bars:
            return None
        assert_closed(bars, decision_ts)      # forming-bar firewall
        signal = bars[-1]
        decision_iso = iso(decision_ts)

        day = signal.open_dt.date()
        day_key = f"{day}"
        if self._last_candidate_day == day_key:
            return None                        # one candidate per day

        sig_hour = signal.open_dt.hour
        if not (self.spec.signal_start_hour <= sig_hour < self.spec.signal_end_hour):
            return None

        atr14 = atr(bars, self.spec.atr_period, decision_ts)
        if atr14 is None or atr14 <= 0:
            return None                        # not enough history yet

        pre = [b for b in bars
               if b.open_dt.date() == day
               and self.spec.pre_range_start_hour <= b.open_dt.hour < self.spec.pre_range_end_hour]
        if len(pre) < 4:
            return None                        # incomplete pre-range

        stats = range_stats(pre, decision_ts)
        if stats is None:
            return None
        r_high, r_low = stats
        buffer = self.spec.buffer_atr_mult * atr14

        side = None
        if signal.close > r_high + buffer:
            side = "buy"
        elif signal.close < r_low - buffer:
            side = "sell"
        if side is None:
            return None

        entry = signal.close
        stop_dist = round(self.spec.stop_atr_mult * atr14, 2)
        if side == "buy":
            stop = round(entry - stop_dist, 2)
            target = round(entry + self.spec.target_r_multiple * stop_dist, 2)
        else:
            stop = round(entry + stop_dist, 2)
            target = round(entry - self.spec.target_r_multiple * stop_dist, 2)

        expiry_dt = decision_ts + timedelta(minutes=self.spec.expiry_minutes)
        time_stop_dt = decision_ts + timedelta(hours=self.spec.time_stop_bars)

        cand = SetupCandidate(
            setup_id=self.spec.setup_id,
            setup_version=self.spec.setup_version,
            decision_ts=decision_iso,
            side=side,
            entry_type="market",
            entry=entry,
            stop=stop,
            target=target,
            time_stop_ts=iso(time_stop_dt),
            expiry_ts=iso(expiry_dt),
            invalidation="H1 close back inside pre-London range",
            stop_distance=stop_dist,
            features_used={
                "atr14": atr14,
                "range_high": r_high,
                "range_low": r_low,
                "range_bars": len(pre),
                "signal_hour_utc": sig_hour,
                "buffer": round(buffer, 2),
            },
            data_hash=_hash_bars(bars[-40:]),
            spec_hash=self.spec_hash,
        )
        self._last_candidate_day = day_key
        return cand

    def notify_outcome(self, day_key: str) -> None:
        """Called by the orchestrator when a day's candidate resolves, so a
        next signal on the same day stays suppressed and a new day is clean."""
        self._last_candidate_day = day_key
