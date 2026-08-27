"""R4-1 — autonomous watch loop: poll → evaluate → fire → journal → push.

`WatchLoop.run_once(now)` performs one sweep:

  1. session-gate the poll — instruments whose exchange session is
     closed are NOT fetched (BTC 24/7 always is);
  2. fetch the live snapshot through MultiAssetMonitor (reuse — same
     keyless Yahoo path, same fail-soft per asset) and enrich each
     asset row with its raw OHLCV bars (the ATR/volume rules need
     them; the enrichment is additive to the dict the monitor returns);
  3. evaluate the rule pack through the PURE `evaluate_rules`;
  4. push each event through `AlertEngine.fire` (cooldown dedup) —
     survivors are journaled with reason code ALERT_FIRED, appended to
     the alert store's fired log, and delivered via Telegram when
     configured;
  5. persist loop state + refreshed correlation baselines.

Fail-soft everywhere: a fetch failure logs, marks the sweep failed and
returns [] — the daemon keeps ticking (one dead sweep never kills the
watch).

`is_session_open(calendar, now)` — UTC approximations, no DST chasing
(same documented philosophy as clock.py's fixed session windows):

    24/7            always open (BTC)
    24/5            Mon-Fri + Sun from 21:00 UTC (CME FX open ≈ Sun
                    17:00 ET); closed Saturday
    COMEX/CME/NYMEX nearly-23h futures: Mon-Fri, closed for the
                    21:00-22:00 UTC maintenance break, Friday closes at
                    21:00 UTC for the weekend
    US BOND/ICE/CBOE NY day session: Mon-Fri 14:00-21:00 UTC
    unknown         Mon-Fri 00:00-24:00 (conservative default)

`run_daemon(interval_seconds, max_ticks)` — stdlib scheduler: sleep
between sweeps (injectable sleeper for tests), KeyboardInterrupt exits
cleanly, max_ticks bounds the loop for tests/dry-runs.
"""
from __future__ import annotations

import time as _time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from ..agent.journal_util import default_journal
from ..clock import iso, utc_now
from ..markets.multi_asset import (INSTRUMENTS, INSTRUMENT_ORDER,
                                   MultiAssetMonitor)
from ..markets.registry import SESSION_CALENDARS
from .alerts import (AlertEngine, AlertEvent, AlertRule,
                     evaluate_rules, update_corr_baselines)
from .store import AlertStore

# Reason code journaled for every fired alert (additive to
# events.REASON_CODES — see events.py; never a BAR terminal code).
ALERT_FIRED = "ALERT_FIRED"

# ---------------------------------------------------------------- sessions
NY_DAY_OPEN_UTC = 14      # ≈ 08:00 ET (winter) — bond/ICE/CBOE day session
NY_DAY_CLOSE_UTC = 21     # ≈ 16:00 ET
FUT_BREAK_START_UTC = 21  # 17:00 ET maintenance break start
FUT_BREAK_END_UTC = 22    # 18:00 ET reopen (Sun reopen ≈ 22:00 UTC too)
FX_SUNDAY_OPEN_UTC = 21   # CME FX Sun 17:00 ET ≈ 21:00/22:00 UTC

_ALWAYS_OPEN = {"24/7"}
_NEAR_23H = {"COMEX", "CME", "NYMEX"}
_NY_DAY = {"US BOND", "ICE", "CBOE"}


def _calendar_name(calendar) -> str | None:
    """Accept a registry SESSION_CALENDARS entry (dict with "calendar"),
    a MultiAssetMonitor INSTRUMENTS entry, a bare calendar-name string,
    or None."""
    if isinstance(calendar, dict):
        return str(calendar.get("calendar") or "") or None
    if isinstance(calendar, str) and calendar.strip():
        return calendar.strip().upper()
    return None


def is_session_open(calendar, now: datetime | None = None) -> bool:
    """Is the instrument's exchange session open at `now` (UTC)?"""
    now = now or utc_now()
    now = now.astimezone(timezone.utc)
    name = _calendar_name(calendar)
    if name is None:
        # unknown symbol → conservative Mon-Fri default (never polls
        # weekend tapes that would only serve stale quotes)
        return now.weekday() < 5
    if name in _ALWAYS_OPEN:
        return True
    wd = now.weekday()  # Mon=0 … Sun=6
    if name == "24/5":
        if wd == 5:
            return False                     # Saturday
        if wd == 6:
            return now.hour >= FX_SUNDAY_OPEN_UTC  # Sunday reopen
        return True
    if name in _NEAR_23H:                    # nearly-23h futures
        if wd >= 5:
            return False                     # weekend (Fri 21:00 close
        hour = now.hour                      # approximated by wd check)
        if wd == 4 and hour >= FUT_BREAK_START_UTC:
            return False                     # Friday evening close
        return not (FUT_BREAK_START_UTC <= hour < FUT_BREAK_END_UTC)
    if name in _NY_DAY:                      # NY day session
        return wd < 5 and NY_DAY_OPEN_UTC <= now.hour < NY_DAY_CLOSE_UTC
    return now.weekday() < 5                 # unknown name → Mon-Fri


def calendar_for(symbol: str) -> str | None:
    """Session-calendar NAME for a symbol (registry first, then the
    monitor's INSTRUMENTS table; None when unknown)."""
    sym = str(symbol or "")
    entry = SESSION_CALENDARS.get(sym) or SESSION_CALENDARS.get(sym.upper())
    if entry:
        return entry.get("calendar")
    meta = INSTRUMENTS.get(sym) or INSTRUMENTS.get(sym.upper())
    if meta:
        return meta.get("calendar")
    return None


def session_map(now: datetime | None = None) -> dict[str, bool]:
    """{symbol: session_open} for the 8 monitored instruments."""
    now = now or utc_now()
    return {sym: is_session_open(calendar_for(sym), now)
            for sym in INSTRUMENT_ORDER}


# ------------------------------------------------------------ default pack
def default_rules() -> list[AlertRule]:
    """Sensible defaults for the 8 instruments (the store's seed pack):

    * GC=F ±1.5% daily move (vs prior close) + the charter's GC↔DXY
      30d correlation-flip watch;
    * BTC ATR spike 2.5× (24/7 tape always evaluates) + ±4% daily move;
    * ES=F ±1%, ^TNX ±2% (yield % move), VIX above 30, WTI volume
      4× the 20-bar mean.
    """
    return [
        AlertRule("GC=F:pct_move:1", "GC=F", "pct_move",
                  {"threshold": 1.5, "window_bars": 1},
                  cooldown_minutes=240, note="gold daily ±1.5% move"),
        AlertRule("ES=F:pct_move:1", "ES=F", "pct_move",
                  {"threshold": 1.0, "window_bars": 1},
                  cooldown_minutes=240, note="S&P e-mini daily ±1% move"),
        AlertRule("^TNX:pct_move:1", "^TNX", "pct_move",
                  {"threshold": 2.0, "window_bars": 1},
                  cooldown_minutes=240, note="10y yield daily ±2% move"),
        AlertRule("BTC-USD:pct_move:1", "BTC-USD", "pct_move",
                  {"threshold": 4.0, "window_bars": 1},
                  cooldown_minutes=180, note="bitcoin daily ±4% move"),
        AlertRule("BTC-USD:atr_spike:1", "BTC-USD", "atr_spike",
                  {"k": 2.5}, cooldown_minutes=120,
                  note="bitcoin ATR > 2.5× 20-bar mean"),
        AlertRule("CL=F:volume_spike:1", "CL=F", "volume_spike",
                  {"k": 4.0}, cooldown_minutes=120,
                  note="WTI volume > 4× 20-bar mean"),
        AlertRule("^VIX:price_above:1", "^VIX", "price_above",
                  {"level": 30.0}, cooldown_minutes=360,
                  note="VIX stress level 30"),
        AlertRule("GC=F:corr_flip:1", "GC=F", "corr_flip",
                  {"other": "DX-Y.NYB"}, cooldown_minutes=360,
                  note="gold↔dollar 30d corr sign flip"),
        AlertRule("EURUSD=X:pct_move:1", "EURUSD=X", "pct_move",
                  {"threshold": 1.0, "window_bars": 1},
                  cooldown_minutes=240, note="EUR/USD daily ±1% move"),
    ]


class _SweepFetchError(Exception):
    """Internal: the monitor's fail-soft wrapper reported a dead sweep
    (fetch raised, no cache to stale-serve). Message is already clean —
    no double 'RuntimeError:' wrapping."""


def telegram_configured(telegram) -> bool:
    """Mock-friendly: a sender counts as configured when it carries
    truthy token+chat_id attributes (TelegramIO does; test fakes set
    them). None / console-only → delivery is skipped silently."""
    if telegram is None:
        return False
    return bool(getattr(telegram, "token", None)
                and getattr(telegram, "chat_id", None))


def _watch_journal(data_root: Path):
    """Journal that PRESERVES the data root's existing constitution
    hash (the desk's own stamp when any validate/demo run happened
    there); a fresh root gets the agent-sidecar marker hash (same
    pattern as agent/journal_util — the journal format is identical,
    only the provenance stamp differs)."""
    from ..events import Journal
    hash_path = data_root / "hashes" / "constitution.sha256"
    if hash_path.exists():
        try:
            existing = hash_path.read_text(encoding="utf-8").strip()
        except OSError:
            existing = ""
        if existing:
            return Journal(data_root, existing)
    return default_journal(data_root)


# ---------------------------------------------------------------- the loop
class WatchLoop:
    """One watch loop instance = one sweep engine over one data root."""

    def __init__(self, data_root: str | Path = "data",
                 rules: list[AlertRule] | None = None,
                 monitor: MultiAssetMonitor | None = None,
                 fetcher: Callable[[list[str]], dict] | None = None,
                 journal=None, telegram=None, engine: AlertEngine | None = None,
                 store: AlertStore | None = None,
                 correlation_provider: Callable[[], dict] | None = None,
                 sleeper: Callable[[float], None] | None = None,
                 logger: Callable[[str], None] | None = None):
        self.data_root = Path(data_root)
        self.store = store or AlertStore(self.data_root)
        self._explicit_rules = rules
        self._rules: list[AlertRule] | None = rules
        self.telegram = telegram
        self.journal = journal if journal is not None \
            else _watch_journal(self.data_root)
        self.correlation_provider = correlation_provider
        self.sleeper = sleeper or _time.sleep
        self.logger = logger or (lambda msg: None)
        self.engine = engine or AlertEngine(
            self._effective_rules(),
            last_fired=self.store.load_last_fired())
        self.engine.set_cooldowns(self._effective_rules())
        # poll-gated monitor: wrap the base fetcher so closed-session
        # instruments are not polled at all (their snapshot rows are
        # re-labelled "session_closed" below)
        self._raw_quotes: dict[str, dict] = {}
        self._polled: list[str] = []
        if monitor is not None:
            self.monitor = monitor
            base = fetcher if fetcher is not None else monitor._fetcher
            monitor._fetcher = self._gated_fetcher(base)
        else:
            from ..markets.multi_asset import fetch_multi_quote
            base = fetcher if fetcher is not None else fetch_multi_quote
            self.monitor = MultiAssetMonitor(
                data_root=self.data_root,
                fetcher=self._gated_fetcher(base))
        # loop state (status surface for /api/desk/watch/status)
        self.interval_seconds: int = 300
        self.ticks = 0
        self.last_sweep_at: str | None = None
        self.next_sweep_at: str | None = None
        self.last_error: str | None = None
        self.last_fired_count = 0

    # ----------------------------------------------------------- rules
    def _effective_rules(self) -> list[AlertRule]:
        if self._rules is not None:
            return self._rules
        stored = self.store.load_rules()
        self._rules = stored if stored else default_rules()
        return self._rules

    def rules(self) -> list[AlertRule]:
        return list(self._effective_rules())

    # ----------------------------------------------------------- fetch
    def _gated_fetcher(self, base_fetch):
        """Wrap the quote fetcher: only open-session symbols are polled
        (gated at the SWEEP's `now` — run_once stamps it so tests are
        wall-clock independent)."""
        def _fetch(symbols: list[str]) -> dict:
            now = getattr(self, "_sweep_now", None) or utc_now()
            open_syms = [s for s in symbols
                         if is_session_open(calendar_for(s), now)]
            self._polled = open_syms
            quotes = base_fetch(open_syms)
            self._raw_quotes = dict(quotes)
            return quotes
        return _fetch

    def _enrich(self, snap: dict) -> dict:
        """Add the raw OHLCV bars (when this sweep fetched fresh quotes)
        to each live asset row — the ATR/volume/pct_move rules read
        them. On a cache hit (monitor served its 60s cache) no bars are
        attached and those rules fail closed for that sweep."""
        assets = snap.get("assets") or {}
        for sym, row in assets.items():
            q = self._raw_quotes.get(sym) or {}
            if q.get("ok") and q.get("bars"):
                row["bars"] = q["bars"]
        return snap

    # ----------------------------------------------------------- sweep
    def run_once(self, now: datetime | None = None) -> list[AlertEvent]:
        """One sweep. Returns the events that FIRED (survived cooldown).
        Never raises — a fetch failure is logged, state-stamped and
        returns []."""
        now = now or utc_now()
        self.ticks += 1
        rules = self._effective_rules()
        self.engine.set_cooldowns(rules)

        # session gate at evaluation time (authoritative — a cached
        # snapshot fetched minutes ago must not fire closed sessions)
        gated = [r for r in rules
                 if r.enabled and is_session_open(calendar_for(r.symbol),
                                                  now)]

        correlation: dict | None = None
        self._sweep_now = now
        try:
            if any(r.kind == "corr_flip" for r in gated) \
                    and self.correlation_provider is not None:
                try:
                    correlation = self.correlation_provider() or None
                except Exception as e:  # noqa: BLE001 — corr is optional
                    self.logger(f"watch: correlation fetch failed: {e}")
            snap = self.monitor.snapshot()
            if snap.get("ok") is False:
                # monitor fail-soft wrapper (fetch raised, no cache)
                raise _SweepFetchError(str(snap.get("error")
                                            or "snapshot failed"))
            snap = self._enrich(snap)
        except _SweepFetchError as e:
            self.last_error = str(e)
            self.logger(f"watch: sweep skipped (fetch failed): "
                        f"{self.last_error}")
            self._stamp_state(now, error=self.last_error)
            self.journal.emit("AlertSweepFailed", {
                "error": self.last_error, "tick": self.ticks})
            return []
        except Exception as e:  # noqa: BLE001 — fail-soft sweep
            self.last_error = f"{type(e).__name__}: {e}"
            self.logger(f"watch: sweep skipped (fetch failed): "
                        f"{self.last_error}")
            self._stamp_state(now, error=self.last_error)
            self.journal.emit("AlertSweepFailed", {
                "error": self.last_error, "tick": self.ticks})
            return []

        candidates = evaluate_rules(gated, snap, correlation)
        fired: list[AlertEvent] = []
        for ev in candidates:
            if self.engine.fire(ev, now):
                fired.append(ev)
                row = self.store.append_fired(
                    ev, fired_at=iso(now), channel=self._channel())
                self.journal.emit(
                    "AlertFired",
                    {"rule_id": ev.rule_id, "symbol": ev.symbol,
                     "kind": ev.kind, "message": ev.message,
                     "value": ev.value, "threshold": ev.threshold,
                     "event_id": row.get("event_id")},
                    reason_code=ALERT_FIRED)
                self._deliver(ev)
        # refresh correlation baselines for the NEXT sweep (persisted
        # with the rules so restarts keep the flip detector armed)
        if correlation is not None:
            new_rules = update_corr_baselines(rules, correlation)
            if any((r.params or {}).get("prev_corr")
                    != (o.params or {}).get("prev_corr")
                    for r, o in zip(new_rules, rules)):
                self._rules = new_rules
                if self._explicit_rules is None:
                    self.store.save_rules(new_rules)
        self.last_fired_count = len(fired)
        self.last_error = None
        self._stamp_state(now)
        return fired

    # ----------------------------------------------------------- deliver
    def _channel(self) -> str:
        return "telegram" if telegram_configured(self.telegram) else "none"

    def _deliver(self, ev: AlertEvent) -> str:
        """Push one fired alert. No Telegram config → skipped silently
        (no console spam, no journal noise — the fired log + journal
        already carry the event)."""
        if not telegram_configured(self.telegram):
            return "skipped"
        try:
            return self.telegram.send_message(ev.message) or "telegram"
        except Exception as e:  # noqa: BLE001 — delivery is best-effort
            self.logger(f"watch: telegram delivery failed: {e}")
            return "failed"

    # ----------------------------------------------------------- state
    def _stamp_state(self, now: datetime, error: str | None = None) -> None:
        self.last_sweep_at = iso(now)
        self.next_sweep_at = iso(datetime.fromtimestamp(
            now.timestamp() + self.interval_seconds, tz=timezone.utc))
        state = {
            "last_sweep": self.last_sweep_at,
            "next_sweep": self.next_sweep_at,
            "interval_seconds": self.interval_seconds,
            "ticks": self.ticks,
            "rules_count": len(self._effective_rules()),
            "last_fired_count": self.last_fired_count,
            "last_error": error,
            "sessions": session_map(now),
        }
        self.store.save_state(state)
        self.store.save_last_fired(self.engine.to_dict())

    # ----------------------------------------------------------- daemon
    def run_daemon(self, interval_seconds: int = 300,
                   max_ticks: int | None = None) -> list[AlertEvent]:
        """Sweep forever (or until max_ticks / KeyboardInterrupt).
        Returns every event that fired across all ticks."""
        self.interval_seconds = int(interval_seconds)
        all_fired: list[AlertEvent] = []
        try:
            while True:
                all_fired.extend(self.run_once())
                if max_ticks is not None and self.ticks >= max_ticks:
                    break
                self.sleeper(float(interval_seconds))
        except KeyboardInterrupt:
            self.logger("watch: KeyboardInterrupt — clean exit")
        return all_fired


# ---------------------------------------------------------------- status
def watch_status(data_root: str | Path = "data") -> dict:
    """Loop status for GET /api/desk/watch/status: last/next sweep,
    rules count, per-instrument session open/closed. Reads the state
    the loop persists (a stopped loop reports its last sweep — the
    field names make that honest)."""
    store = AlertStore(data_root)
    rules = store.load_rules() or default_rules()
    state = store.load_state()
    now = utc_now()
    sessions = session_map(now)
    running = bool(state.get("last_sweep"))
    return {
        "ok": True,
        "running": running,
        "as_of": iso(now),
        "last_sweep": state.get("last_sweep"),
        "next_sweep": state.get("next_sweep"),
        "interval_seconds": state.get("interval_seconds"),
        "ticks": state.get("ticks", 0),
        "last_error": state.get("last_error"),
        "rules_count": len(rules),
        "sessions": sessions,
        "n_open_sessions": sum(1 for v in sessions.values() if v),
        "fired_logged": len(store.list_fired()),
    }
