"""R4-1 BUILD 1 — Autonomous alert engine (beats TradingView alerts).

Pure, deterministic rule evaluation over a live multi-asset snapshot.
TradingView's free tier caps active alerts and only watches prices; the
desk's engine evaluates six rule kinds — price levels, windowed %
moves, ATR spikes, volume spikes, and 30d correlation sign flips —
against the SAME keyless Yahoo data the monitor already fetches, with
per-rule cooldown dedup, JSONL journal filing and Telegram push.

Rule kinds (evaluated against a MultiAssetMonitor-style snapshot dict;
per-asset rows may carry a `bars` list of {ts,o,h,l,c,v} OHLCV bars
which the watch loop enriches from the raw quote fetch):

    price_above   params: {"level": float}            price >= level
    price_below   params: {"level": float}            price <= level
    pct_move      params: {"window_bars": int,        |pct move| >= threshold
                            "threshold": float}       window 1 → vs
                                                         prev_close (daily);
                                                         window N → vs the
                                                         close N bars back
    atr_spike     params: {"k": float}                ATR(now) > k × mean
                                                         of the prior 20
                                                         ATR values
    volume_spike  params: {"k": float}                volume(now) > k ×
                                                         mean of prior 20
                                                         bars' volume
    corr_flip     params: {"other": symbol,           30d correlation with
                            "prev_corr": float|None}   `other` flips sign
                                                         vs the last sweep
                                                         (prev_corr lives in
                                                         the rule and is
                                                         persisted by the
                                                         loop after every
                                                         sweep — the first
                                                         sweep only records
                                                         the baseline)

`evaluate_rules` is a PURE function: same (rules, snapshot, correlation)
in → same events out, no wall-clock reads, no mutation of its inputs
(pinned by tests). Firing time (`AlertEvent.fired_at`) comes from the
snapshot's `as_of` so the function stays deterministic; the sweep loop
stamps wall-clock time when it fires the event through the engine.

`AlertEngine.fire(event, now)` is the stateful gate: an event whose rule
fired within the rule's cooldown window is suppressed (returns False);
the last-fired map is persisted by the alert store so restarts don't
re-spam. Cooldown boundary: elapsed >= cooldown re-fires, elapsed <
cooldown suppresses.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from ..clock import iso, utc_now

RULE_KINDS = (
    "price_above",
    "price_below",
    "pct_move",
    "atr_spike",
    "volume_spike",
    "corr_flip",
)

# ATR / volume spike baselines: "20-bar mean" per the charter.
SPIKE_BASELINE_BARS = 20


# ----------------------------------------------------------------- datatypes
@dataclass
class AlertRule:
    """One alert rule. `params` shape depends on `kind` (see module doc).

    `id` is a stable string key (cooldown + persistence key). The
    default rule pack pins human-readable ids; user rules get
    `<symbol>:<kind>:<n>` ids from the store.
    """

    id: str
    symbol: str
    kind: str
    params: dict = field(default_factory=dict)
    enabled: bool = True
    cooldown_minutes: int = 60
    note: str = ""

    def __post_init__(self) -> None:
        if self.kind not in RULE_KINDS:
            raise ValueError(f"unknown rule kind: {self.kind!r}")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlertRule":
        return cls(
            id=str(d.get("id") or ""),
            symbol=str(d.get("symbol") or ""),
            kind=str(d.get("kind") or ""),
            params=dict(d.get("params") or {}),
            enabled=bool(d.get("enabled", True)),
            cooldown_minutes=int(d.get("cooldown_minutes") or 60),
            note=str(d.get("note") or ""),
        )


@dataclass
class AlertEvent:
    """One fired alert (the output of evaluate_rules).

    `fired_at` is the DATA time (snapshot `as_of`) — deterministic. The
    loop/engine stamp wall clock when the event survives cooldown.
    `snapshot_ref` carries a tiny data reference (as_of + the price the
    rule evaluated) so a fired alert is auditable against the sweep.
    """

    rule_id: str
    symbol: str
    kind: str
    message: str
    value: float | None
    threshold: float | None
    fired_at: str
    snapshot_ref: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlertEvent":
        return cls(
            rule_id=str(d.get("rule_id") or ""),
            symbol=str(d.get("symbol") or ""),
            kind=str(d.get("kind") or ""),
            message=str(d.get("message") or ""),
            value=d.get("value"),
            threshold=d.get("threshold"),
            fired_at=str(d.get("fired_at") or ""),
            snapshot_ref=dict(d.get("snapshot_ref") or {}),
        )


# ----------------------------------------------------------------- helpers
def _assets_of(snapshot: Any) -> dict:
    """Accept either a full monitor snapshot ({ok, assets, ...}) or a
    bare {symbol: row} map. Returns the per-asset rows dict."""
    if not isinstance(snapshot, dict):
        return {}
    if "assets" in snapshot and isinstance(snapshot["assets"], dict):
        return snapshot["assets"]
    return snapshot


def _as_of(snapshot: Any) -> str:
    if isinstance(snapshot, dict):
        a = snapshot.get("as_of")
        if isinstance(a, str) and a:
            return a
    return ""


def _fnum(v: Any) -> float | None:
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def _true_ranges(bars: list[dict]) -> list[float]:
    """True-range series from OHLCV bars (needs h/l/c; first bar's TR is
    its own h-l — standard seeding)."""
    trs: list[float] = []
    prev_c: float | None = None
    for b in bars:
        h, l, c = _fnum(b.get("h")), _fnum(b.get("l")), _fnum(b.get("c"))
        if h is None or l is None or c is None:
            prev_c = c if c is not None else prev_c
            continue
        if prev_c is None:
            tr = h - l
        else:
            tr = max(h - l, abs(h - prev_c), abs(l - prev_c))
        trs.append(tr)
        prev_c = c
    return trs


def atr_now_and_base(bars: list[dict],
                     window: int = SPIKE_BASELINE_BARS) -> tuple[float | None,
                                                                 float | None]:
    """(current ATR, baseline ATR) for an OHLCV bar list.

    Current ATR   = mean of the last `window` true ranges.
    Baseline ATR  = mean of the `window` ATR values BEFORE the current
                    one (each itself a `window`-TR mean) — "ATR > k ×
                    20-bar mean ATR" per the charter.
    Fail-closed: needs >= 2*window+1 bars; anything short → (None, None)
    (the rule simply does not evaluate — no alert on thin data).
    """
    trs = _true_ranges(bars)
    need = 2 * window + 1
    if len(trs) < need:
        return None, None
    atrs: list[float] = []
    for i in range(window - 1, len(trs)):
        atrs.append(sum(trs[i - window + 1:i + 1]) / window)
    now_atr = atrs[-1]
    base = atrs[:-1][-window:]
    return now_atr, (sum(base) / len(base) if base else None)


def volume_now_and_base(bars: list[dict],
                        window: int = SPIKE_BASELINE_BARS
                        ) -> tuple[float | None, float | None]:
    """(last bar volume, mean of the prior `window` bars' volume)."""
    vols = [v for v in (_fnum(b.get("v")) for b in bars) if v is not None]
    if len(vols) < window + 1:
        return None, None
    base = vols[-window - 1:-1]
    mean_base = sum(base) / len(base)
    if mean_base <= 0:
        # zero-volume tapes (^TNX) would make k×0 == 0 and fire on any
        # tick — fail closed instead
        return vols[-1], None
    return vols[-1], mean_base


def _pct_move(asset: dict, window_bars: int) -> float | None:
    """% move of `price` vs the close `window_bars` back. Window 1 uses
    prev_close (the true daily move from Yahoo's chart meta); windows
    > 1 walk the intraday closes in `bars`. None when data is missing
    (fail-closed — no event)."""
    price = _fnum(asset.get("price"))
    if price is None or price == 0:
        return None
    if window_bars <= 1:
        prev = _fnum(asset.get("prev_close"))
        if prev is None or prev == 0:
            return None
        return (price - prev) / prev * 100.0
    bars = asset.get("bars") or []
    closes = [c for c in (_fnum(b.get("c")) for b in bars) if c is not None]
    if len(closes) < window_bars + 1:
        return None
    ref = closes[-window_bars - 1]
    if ref == 0:
        return None
    return (price - ref) / ref * 100.0


def _corr_value(correlation: Any, sym: str, other: str) -> float | None:
    """Pull matrix[sym][other] from a compute_correlation-style result."""
    if not isinstance(correlation, dict):
        return None
    matrix = correlation.get("matrix")
    if not isinstance(matrix, dict):
        return None
    row = matrix.get(sym)
    if isinstance(row, dict):
        v = _fnum(row.get(other))
        if v is not None:
            return v
    row = matrix.get(other)  # symmetric fallback
    if isinstance(row, dict):
        return _fnum(row.get(sym))
    return None


# ----------------------------------------------------------------- evaluate
def _event(rule: AlertRule, asset: dict, as_of: str, message: str,
           value: float | None, threshold: float | None) -> AlertEvent:
    price = _fnum(asset.get("price"))
    return AlertEvent(
        rule_id=rule.id,
        symbol=rule.symbol,
        kind=rule.kind,
        message=message,
        value=None if value is None else round(value, 6),
        threshold=None if threshold is None else round(threshold, 6),
        fired_at=as_of,
        snapshot_ref={"as_of": as_of, "symbol": rule.symbol, "price": price},
    )


def evaluate_rules(rules: list[AlertRule], snapshot: Any,
                   correlation: Any = None) -> list[AlertEvent]:
    """Pure rule evaluation. Deterministic: same inputs → same events;
    inputs are never mutated (pinned by tests).

    * disabled rules and rules whose symbol is absent / has no price
      from the snapshot never fire (fail-closed, no exception);
    * ATR/volume/pct_move need enough `bars` on the asset row — short
      tapes simply don't evaluate;
    * corr_flip needs a live correlation matrix AND a recorded baseline
      (`params["prev_corr"]`) — the first sweep only records it.
    """
    assets = _assets_of(snapshot)
    as_of = _as_of(snapshot)
    events: list[AlertEvent] = []
    for rule in rules:
        if not rule.enabled:
            continue
        asset = assets.get(rule.symbol)
        if not isinstance(asset, dict):
            continue
        price = _fnum(asset.get("price"))
        p = rule.params or {}

        if rule.kind == "price_above":
            # accept both param spellings: `level` (canonical, used by the
            # default rule pack) and `threshold` (what alerts-add --threshold
            # and the web POST route write) — either arms the rule
            level = _fnum(p.get("level"))
            if level is None:
                level = _fnum(p.get("threshold"))
            if price is not None and level is not None and price >= level:
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol} price {price:g} >= {level:g}",
                    price, level))

        elif rule.kind == "price_below":
            level = _fnum(p.get("level"))
            if level is None:
                level = _fnum(p.get("threshold"))
            if price is not None and level is not None and price <= level:
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol} price {price:g} <= {level:g}",
                    price, level))

        elif rule.kind == "pct_move":
            threshold = _fnum(p.get("threshold"))
            window = int(p.get("window_bars") or 1)
            pct = _pct_move(asset, window)
            if pct is not None and threshold is not None \
                    and abs(pct) >= threshold:
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol} {pct:+.2f}% over {window} bar(s) "
                    f"(threshold ±{threshold:g}%)",
                    pct, threshold))

        elif rule.kind == "atr_spike":
            k = _fnum(p.get("k"))
            now_atr, base_atr = atr_now_and_base(asset.get("bars") or [])
            if k is not None and now_atr is not None and base_atr \
                    and base_atr > 0 and now_atr > k * base_atr:
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol} ATR {now_atr:.6g} > {k:g}× "
                    f"baseline {base_atr:.6g}",
                    now_atr / base_atr if base_atr else None, k))

        elif rule.kind == "volume_spike":
            k = _fnum(p.get("k"))
            v_now, v_base = volume_now_and_base(asset.get("bars") or [])
            if k is not None and v_now is not None and v_base \
                    and v_now > k * v_base:
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol} volume {v_now:.6g} > {k:g}× "
                    f"mean {v_base:.6g}",
                    v_now / v_base if v_base else None, k))

        elif rule.kind == "corr_flip":
            other = str(p.get("other") or "")
            prev = _fnum(p.get("prev_corr"))
            corr = _corr_value(correlation, rule.symbol, other) \
                if other else None
            # strict sign flip: both sides observed, opposite signs
            if prev is not None and corr is not None \
                    and prev != 0 and corr != 0 and (prev > 0) != (corr > 0):
                events.append(_event(
                    rule, asset, as_of,
                    f"{rule.symbol}↔{other} 30d corr flipped "
                    f"{prev:+.3f} → {corr:+.3f}",
                    corr, 0.0))

    return events


def update_corr_baselines(rules: list[AlertRule],
                          correlation: Any) -> list[AlertRule]:
    """Record this sweep's correlation into each corr_flip rule's
    `prev_corr` so the NEXT sweep can detect a flip. Returns the rules
    (mutated in place is avoided — fresh param dicts are installed so
    callers' inputs stay intact)."""
    out: list[AlertRule] = []
    for r in rules:
        if r.kind != "corr_flip":
            out.append(r)
            continue
        other = str((r.params or {}).get("other") or "")
        corr = _corr_value(correlation, r.symbol, other) if other else None
        if corr is None:
            out.append(r)
            continue
        nr = AlertRule.from_dict(r.to_dict())
        nr.params = dict(nr.params)
        nr.params["prev_corr"] = corr
        out.append(nr)
    return out


# ----------------------------------------------------------------- engine
class AlertEngine:
    """Cooldown gate — the ONLY stateful piece of the alert path.

    `fire(event, now)` returns True when the event's rule has NOT fired
    within its cooldown window (and records the firing); False when it
    is a suppressed duplicate. The last-fired map is exposed via
    `to_dict`/`from_dict` so the alert store can persist it across
    process restarts.
    """

    def __init__(self, rules: list[AlertRule] | None = None,
                 default_cooldown_minutes: int = 60,
                 last_fired: dict[str, str] | None = None):
        self.default_cooldown_minutes = int(default_cooldown_minutes)
        self._cooldowns: dict[str, int] = {
            r.id: int(r.cooldown_minutes) for r in (rules or [])}
        self._last_fired: dict[str, str] = dict(last_fired or {})

    # ------------------------------------------------------------ fire
    def fire(self, event: AlertEvent,
             now: datetime | None = None) -> bool:
        now = now or utc_now()
        rid = event.rule_id
        last_iso = self._last_fired.get(rid)
        if last_iso:
            try:
                last = datetime.fromisoformat(
                    last_iso.replace("Z", "+00:00"))
                if last.tzinfo is None:
                    last = last.replace(tzinfo=timezone.utc)
                elapsed_min = (now - last).total_seconds() / 60.0
                cooldown = self._cooldowns.get(
                    rid, self.default_cooldown_minutes)
                if elapsed_min < cooldown:
                    return False  # suppressed duplicate (within cooldown)
            except ValueError:
                pass  # unparsable stamp → treat as never fired
        self._last_fired[rid] = iso(now)
        return True

    # ------------------------------------------------------------ state
    def last_fired(self, rule_id: str) -> str | None:
        return self._last_fired.get(rule_id)

    def to_dict(self) -> dict[str, str]:
        return dict(self._last_fired)

    def from_dict(self, d: dict[str, str] | None) -> None:
        self._last_fired = {str(k): str(v) for k, v in (d or {}).items()}

    def set_cooldowns(self, rules: list[AlertRule]) -> None:
        self._cooldowns.update(
            {r.id: int(r.cooldown_minutes) for r in rules})
