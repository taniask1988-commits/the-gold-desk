"""R5 BUILD 2 — the walk-forward evaluation gate (the anti-overfit wall).

FUNDAMENTAL (docs/SELF_EVOLUTION_RESEARCH.md §2, insight #2): the
evaluator is the whole game. Evolution optimizes WHATEVER the evaluator
measures — including its biases. The classic failure is evolving
parameters against the FULL history: you get a beautiful in-sample
curve and a dead strategy (the overfit trap that killed a decade of
retail "optimized" systems). This module makes that failure structurally
hard:

  1. WALK-FORWARD SEGMENTATION: the train split is cut into K contiguous
     day-aligned segments; a genome's fitness is the MEAN of its
     per-segment scores minus a consistency penalty (λ·std). A genome
     that wins big in one regime and dies in the next scores BELOW a
     genome that wins modestly everywhere — which is exactly what we
     want a live book to look like.
  2. MIN-ACTIVITY GATE: fewer than `min_trades_total` trades across the
     train split → fitness None + rejected=True. A genome that never
     trades never loses — and must never be allowed to "win" evolution
     by discovering inactivity (the honest-costs law applied to search).
  3. UNTOUCHED TEST TAIL: the engine never sees the last
     `test_fraction` of bars during selection; the champion is scored
     there once, at the end. overfit_gap = is_fitness − oos_fitness is
     REPORTED, never hidden, and gates promotion.

Everything is deterministic: same bars + same genome → same scores.
Scores run through the SAME BacktestEngine the desk ships (R3-2) — the
evaluator is not a parallel implementation that can drift from reality.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from ..data.model import Bar
from ..risk.backtest import BacktestEngine
from ..setup.spec import SetupSpec
from .genome import genome_from_spec, spec_from_genome

DEFAULT_N_SEGMENTS = 3
DEFAULT_MIN_TRADES = 8
DEFAULT_CONSISTENCY_LAMBDA = 0.25
DEFAULT_TEST_FRACTION = 0.35


# ------------------------------------------------------------- segmentation
def split_train_test(bars: list[Bar],
                     test_fraction: float = DEFAULT_TEST_FRACTION
                     ) -> tuple[list[Bar], list[Bar]]:
    """Contiguous split at a DAY boundary: train = the first
    (1−test_fraction) of days, test = the untouched tail. Day alignment
    matters — cutting mid-day would leak a session into both sides."""
    if not bars:
        return [], []
    if not (0.05 <= test_fraction <= 0.6):
        raise ValueError("test_fraction must be in [0.05, 0.6]")
    days = sorted({b.ts_close[:10] for b in bars})
    n_test_days = max(1, int(round(len(days) * test_fraction)))
    test_days = set(days[-n_test_days:])
    train = [b for b in bars if b.ts_close[:10] not in test_days]
    test = [b for b in bars if b.ts_close[:10] in test_days]
    return train, test


def split_segments(bars: list[Bar], k: int = DEFAULT_N_SEGMENTS) -> list[list[Bar]]:
    """Cut `bars` into k contiguous day-aligned segments (roughly equal
    by day count). Days are atomic units — a segment never starts or
    ends mid-session. Returns [] when there is not ≥1 day per segment."""
    if k < 1:
        raise ValueError("k must be >= 1")
    days = sorted({b.ts_close[:10] for b in bars})
    if len(days) < k:
        return []
    seg_days: list[list[str]] = [[] for _ in range(k)]
    for i, d in enumerate(days):
        seg_days[min(i * k // len(days), k - 1)].append(d)
    out: list[list[Bar]] = []
    for days_of_seg in seg_days:
        dset = set(days_of_seg)
        out.append([b for b in bars if b.ts_close[:10] in dset])
    return [s for s in out if s]


# ----------------------------------------------------------------- scoring
@dataclass
class SegmentScore:
    """One genome measured on one bar segment — the atom of evidence."""
    n_bars: int
    n_days: int
    n_trades: int
    total_return: float | None
    sharpe: float | None
    max_drawdown: float | None
    score: float | None          # None ⇒ segment had zero trades
    buy_hold_return: float | None

    def to_dict(self) -> dict:
        return {
            "n_bars": self.n_bars, "n_days": self.n_days,
            "n_trades": self.n_trades, "total_return": self.total_return,
            "sharpe": self.sharpe, "max_drawdown": self.max_drawdown,
            "score": self.score, "buy_hold_return": self.buy_hold_return,
        }


def score_spec(spec: SetupSpec, bars: list[Bar], seed: int = 7,
               **engine_kwargs) -> SegmentScore:
    """Run the shipped backtest engine ONCE over `bars` with `spec` and
    reduce it to a scalar score. Score = daily Sharpe (rf=0.05, the
    engine's own metric); n_trades is carried alongside because a score
    without its sample size is not evidence."""
    if not bars:
        return SegmentScore(0, 0, 0, None, None, None, None, None)
    res = BacktestEngine(bars, spec=spec, seed=seed, **engine_kwargs).run()
    n_trades = int(res["n_trades"])
    sharpe = res["sharpe"]
    score = None if n_trades == 0 else (0.0 if sharpe is None else float(sharpe))
    return SegmentScore(
        n_bars=int(res["n_bars"]),
        n_days=int(res["n_days"]),
        n_trades=n_trades,
        total_return=(None if res["total_return"] is None
                      else float(res["total_return"])),
        sharpe=(None if sharpe is None else float(sharpe)),
        max_drawdown=(None if res["max_drawdown"] is None
                      else float(res["max_drawdown"])),
        score=score,
        buy_hold_return=(None if res["buy_hold_return"] is None
                         else float(res["buy_hold_return"])),
    )


@dataclass
class GenomeFitness:
    """The full evaluation record for one genome on one dataset.

    `fitness` is the selection driver (mean − λ·std over trading
    segments); None + rejected=True means the min-activity gate tripped.
    Per-segment detail is retained — the audit trail must show WHICH
    segments carried the genome, not just the average."""
    fitness: float | None
    rejected: bool
    reject_reason: str
    n_trades_total: int
    segments: list[SegmentScore] = field(default_factory=list)
    segment_scores: list[float | None] = field(default_factory=list)
    mean_score: float | None = None
    std_score: float | None = None

    def to_dict(self) -> dict:
        return {
            "fitness": self.fitness, "rejected": self.rejected,
            "reject_reason": self.reject_reason,
            "n_trades_total": self.n_trades_total,
            "segments": [s.to_dict() for s in self.segments],
            "segment_scores": self.segment_scores,
            "mean_score": self.mean_score, "std_score": self.std_score,
        }


def evaluate_genome(genome: dict, bars: list[Bar],
                    n_segments: int = DEFAULT_N_SEGMENTS,
                    min_trades: int = DEFAULT_MIN_TRADES,
                    consistency_lambda: float = DEFAULT_CONSISTENCY_LAMBDA,
                    seed: int = 7,
                    **engine_kwargs) -> GenomeFitness:
    """Walk-forward fitness of one genome over `bars`.

    The bars are cut into k day-aligned segments; the genome scores on
    each independently (fresh engine, fresh window — no state leaks
    across segments); fitness = mean − λ·std of the trading segments'
    Sharpe scores, gated by the total-trade minimum. Deterministic.
    """
    spec = spec_from_genome(genome)
    segs = split_segments(bars, n_segments)
    if not segs:
        return GenomeFitness(None, True, "not_enough_days", 0)
    scores: list[SegmentScore] = [score_spec(spec, s, seed=seed,
                                             **engine_kwargs) for s in segs]
    n_total = sum(s.n_trades for s in scores)
    if n_total < min_trades:
        return GenomeFitness(
            None, True, f"min_trades({n_total}<{min_trades})", n_total,
            segments=scores, segment_scores=[s.score for s in scores])
    traded = [s.score for s in scores if s.score is not None]
    if not traded:
        return GenomeFitness(None, True, "no_trading_segments", n_total,
                             segments=scores,
                             segment_scores=[s.score for s in scores])
    mean = statistics.fmean(traded)
    std = statistics.pstdev(traded) if len(traded) > 1 else 0.0
    return GenomeFitness(
        round(mean - consistency_lambda * std, 6), False, "", n_total,
        segments=scores, segment_scores=[s.score for s in scores],
        mean_score=round(mean, 6), std_score=round(std, 6))


def evaluate_oos(genome: dict, test_bars: list[Bar],
                 min_trades: int = DEFAULT_MIN_TRADES,
                 seed: int = 7,
                 **engine_kwargs) -> GenomeFitness:
    """Champion validation on the untouched tail: ONE engine pass, the
    min-activity gate, and the raw Sharpe as fitness. This is the number
    the promotion gate compares — measured once, after selection, on
    data the search never saw."""
    if not test_bars:
        return GenomeFitness(None, True, "no_test_bars", 0)
    sc = score_spec(spec_from_genome(genome), test_bars, seed=seed,
                    **engine_kwargs)
    if sc.n_trades == 0:
        return GenomeFitness(None, True, "oos_zero_trades", 0,
                             segments=[sc], segment_scores=[sc.score])
    if sc.n_trades < min_trades:
        return GenomeFitness(
            None, True, f"oos_min_trades({sc.n_trades}<{min_trades})",
            sc.n_trades, segments=[sc], segment_scores=[sc.score])
    return GenomeFitness(round((0.0 if sc.score is None else sc.score), 6),
                         False, "", sc.n_trades, segments=[sc],
                         segment_scores=[sc.score])


# ----------------------------------------------------------------- helpers
def overfit_gap(is_fitness: float | None,
                oos_fitness: float | None) -> float | None:
    """is − oos. Positive ⇒ the genome lost performance off-sample.
    None when either side is unmeasured (never fabricate a gap)."""
    if is_fitness is None or oos_fitness is None:
        return None
    return round(is_fitness - oos_fitness, 6)


def incumbent_genome(spec: SetupSpec | None = None) -> dict:
    """The shipped GUESS spec as a genome — the incumbent every
    challenger must beat out-of-sample. Evolution starts from here, not
    from a random point: respect the audited baseline (DGM archives its
    parent for the same reason)."""
    return genome_from_spec(spec or SetupSpec())
