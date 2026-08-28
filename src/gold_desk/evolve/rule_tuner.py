"""R5 BUILD 5 — watch-rule threshold tuning (transactional self-modification).

FUNDAMENTAL (docs/SELF_EVOLUTION_RESEARCH.md §3.5): the Gödel Agent's
contribution is TRANSACTIONAL self-modification — propose a change,
MEASURE it, commit only if better, otherwise roll back. This module
applies exactly that to watch-rule thresholds:

    champion (the rule's current threshold)
      → challengers (grid + jittered probes over bounds)
      → each measured by an injected score_fn with a MIN-FIRE gate
      → challenger must beat champion by `margin` to be promoted
      → the champion is always retained (rollback = keep the old value)

WHY score_fn IS INJECTED: the search machinery must not know what it is
optimizing (separation of mechanism and policy). The policy — what makes
an alert GOOD — is supplied by the caller. This module ships one honest
policy for pct_move-style rules:

    information_content(closes, window) =
        E[|next-window move| | alert fired] − E[|next-window move|]

i.e. "does the alert carry information about subsequent movement?" —
positive means the alert is an early warning, ~0 means noise, negative
means the move is already over when it fires. This is a likelihood-ratio
flavored score, not PnL theater: an alert can be informative and still
untradeable, and the tuner reports the number without dressing it up.

Determinism: seeded candidate generation, pure score functions, `now`
passed in. A rule that cannot gather `min_fires` fires at ANY threshold
is reported untunable — never force-tuned (the honest-default law).
"""
from __future__ import annotations

import random
import statistics
from dataclasses import dataclass

DEFAULT_MARGIN = 0.05
DEFAULT_MIN_FIRES = 5
DEFAULT_GRID = 9


@dataclass
class ProbeResult:
    """One threshold measured once — the atom of the audit trail."""
    value: float
    score: float | None        # None ⇒ min-fire gate tripped
    n_fires: int

    def to_dict(self) -> dict:
        return {"value": self.value, "score": self.score,
                "n_fires": self.n_fires}


@dataclass
class TuneConfig:
    lo: float
    hi: float
    margin: float = DEFAULT_MARGIN
    min_fires: int = DEFAULT_MIN_FIRES
    grid: int = DEFAULT_GRID        # grid probe count between lo..hi
    jitter_probes: int = 4          # extra seeded jitter probes
    grain: float | None = None      # snap values to this step (e.g. 0.25)


def _snap(v: float, cfg: TuneConfig) -> float:
    v = min(cfg.hi, max(cfg.lo, v))
    if cfg.grain:
        v = round(round(v / cfg.grain) * cfg.grain, 10)
        v = min(cfg.hi, max(cfg.lo, v))
    return v


def _probe(score_fn, value: float, cfg: TuneConfig) -> ProbeResult:
    score, n_fires = score_fn(value)
    ok = n_fires >= cfg.min_fires
    return ProbeResult(value=round(value, 10),
                       score=(float(score) if ok else None),
                       n_fires=int(n_fires))


def tune_threshold(score_fn, incumbent: float, cfg: TuneConfig,
                   seed: int = 7) -> dict:
    """Champion/challenger search over a threshold.

    score_fn(value) → (score, n_fires) must be pure. Candidates: the
    incumbent, a uniform grid over [lo, hi], and seeded jitter probes
    around the incumbent (local refinement). Every probe is recorded.
    Verdict PROMOTE only when a viable challenger beats the incumbent's
    viable score by cfg.margin; ties and failures KEEP the incumbent —
    the current rule is the default winner of every tie.
    """
    if cfg.hi <= cfg.lo:
        raise ValueError("hi must be > lo")
    rng = random.Random(seed)
    champion_val = _snap(incumbent, cfg)
    champion = _probe(score_fn, champion_val, cfg)

    probes: list[ProbeResult] = [champion]
    for i in range(cfg.grid):
        probes.append(_probe(
            score_fn,
            _snap(cfg.lo + (cfg.hi - cfg.lo) * i / max(1, cfg.grid - 1), cfg),
            cfg))
    for _ in range(cfg.jitter_probes):
        span = (cfg.hi - cfg.lo) * 0.15
        probes.append(_probe(
            score_fn,
            _snap(champion_val + rng.uniform(-span, span), cfg), cfg))

    # dedupe by value (first probe wins — order is deterministic)
    seen: dict[float, ProbeResult] = {}
    for p in probes:
        if p.value not in seen:
            seen[p.value] = p
    probes = list(seen.values())

    viable = [p for p in probes if p.score is not None]
    best = max(viable, key=lambda p: p.score) if viable else None

    result = {
        "ok": True,
        "champion_value": champion_val,
        "champion_score": champion.score,
        "champion_fires": champion.n_fires,
        "best_value": best.value if best else None,
        "best_score": best.score if best else None,
        "probes": [p.to_dict() for p in probes],
        "n_probes": len(probes),
    }
    if champion.score is None and best is None:
        result.update({"verdict": "UNTUNABLE",
                       "reason": f"no threshold produced >= "
                                 f"{cfg.min_fires} fires"})
        return result
    if champion.score is None:
        # incumbent itself never fires enough — the rule is dormant;
        # promoting a challenger here changes live behaviour on
        # unmeasured ground. Report, do not promote.
        result.update({"verdict": "KEEP_INCUMBENT",
                       "reason": "incumbent below min-fire gate "
                                 "(rule dormant); refusing to promote "
                                 "on unmeasured ground"})
        return result
    if best is None or best.value == champion_val:
        result.update({"verdict": "KEEP_INCUMBENT",
                       "reason": "no viable challenger"})
        return result
    if best.score > champion.score + cfg.margin:
        result.update({"verdict": "PROMOTE",
                       "reason": f"challenger {best.value} scores "
                                 f"{best.score:.4f} vs champion "
                                 f"{champion.score:.4f} "
                                 f"(margin {cfg.margin})"})
        return result
    result.update({"verdict": "KEEP_INCUMBENT",
                   "reason": f"best challenger {best.value} scores "
                             f"{best.score:.4f}, does not beat champion "
                             f"{champion.score:.4f} by margin "
                             f"{cfg.margin}"})
    return result


# ------------------------------------------------------- shipped score fns
def pct_move_score_fn(closes: list[float], window_bars: int = 1):
    """Information-content scorer for pct_move-style alert thresholds.

    fire at t when |close_t / close_{t−window} − 1| >= θ
    score(θ)   = mean(|next-window move| after fires)
                 − mean(|next-window move| everywhere)

    Positive ⇒ fires precede elevated follow-through movement (the alert
    carries information); ~0 ⇒ noise; negative ⇒ the move is exhausted
    by the time the alert fires. Returns a pure closure score_fn(θ) →
    (score, n_fires). Deterministic."""
    if len(closes) < window_bars + 2:
        def _empty(theta: float):
            return (0.0, 0)
        return _empty
    n = len(closes)
    idx = list(range(window_bars, n - window_bars))
    past_move = [abs(closes[t] / closes[t - window_bars] - 1.0)
                 for t in idx]
    next_move = [abs(closes[t + window_bars] / closes[t] - 1.0)
                 for t in idx]
    base = statistics.fmean(next_move) if next_move else 0.0

    def _score(theta: float) -> tuple[float, int]:
        fires = [nm for pm, nm in zip(past_move, next_move) if pm >= theta]
        if not fires:
            return (0.0, 0)
        return (round(statistics.fmean(fires) - base, 8), len(fires))

    return _score


def atr_spike_score_fn(atr_series: list[float], k_window: int = 20):
    """Information-content scorer for ATR-spike thresholds: fire at t
    when ATR_t >= θ · mean(ATR_{t−k..t−1}); score = mean(next ATR)
    after fires − unconditional mean next ATR. Elevated-volatility
    persistence is the alert's informational payload."""
    if len(atr_series) < k_window + 2:
        def _empty(theta: float):
            return (0.0, 0)
        return _empty
    idx = list(range(k_window, len(atr_series) - 1))
    ratios = [atr_series[t] / max(1e-12, statistics.fmean(
        atr_series[t - k_window:t])) for t in idx]
    nxt = [atr_series[t + 1] for t in idx]
    base = statistics.fmean(nxt) if nxt else 0.0

    def _score(theta: float) -> tuple[float, int]:
        fires = [nv for r, nv in zip(ratios, nxt) if r >= theta]
        if not fires:
            return (0.0, 0)
        return (round(statistics.fmean(fires) - base, 8), len(fires))

    return _score
