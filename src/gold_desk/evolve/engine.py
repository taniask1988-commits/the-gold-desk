"""R5 BUILD 3 — the evolution engine: population → evaluation → selection
→ archive, with a champion/challenger promotion gate.

FUNDAMENTAL (docs/SELF_EVOLUTION_RESEARCH.md §2): the loop is the
learner. This engine is AlphaEvolve's architecture rendered deterministic
and keyless:

    population DB      → the JSONL archive (every individual ever born,
                          with parent ids, birth operator, both fitnesses)
    evaluator          → evolve.walkforward (the shipped backtest engine,
                          segmented, min-activity gated)
    mutation/crossover → evolve.genome operators (seeded; the LLM-as-
                          variation-operator role is played OFFLINE by the
                          gauntlet itself, because the live engine is
                          stdlib + deterministic by law)

SAFETY MODEL (DGM/ Gödel Agent): the engine NEVER writes the live spec.
It produces a measured candidate + full evidence; `verdict` is a
recommendation. Promotion is an explicit operator action (the system
proposes, the operator disposes). Ancestors are never deleted — rollback
is archive lookup.

HONESTY MODEL: the incumbent (shipped GUESS genome) is always evaluated
head-to-head on the SAME untouched test tail. If evolution cannot beat
it out-of-sample, the verdict says KEEP_INCUMBENT — a failed search is
a result, not an embarrassment. R3 already established the baseline is
hard to beat (GUESS < buy-and-hold on 1y); the engine is forbidden from
forgetting that.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from ..data.model import Bar
from .genome import (DEFAULT_GENOME, Individual, crossover, genome_hash,
                     mutate)
from .walkforward import (DEFAULT_CONSISTENCY_LAMBDA, DEFAULT_MIN_TRADES,
                          DEFAULT_N_SEGMENTS, DEFAULT_TEST_FRACTION,
                          GenomeFitness, evaluate_genome, evaluate_oos,
                          overfit_gap, split_train_test)

DEFAULT_POPULATION = 10
DEFAULT_GENERATIONS = 6
DEFAULT_ELITE = 2
DEFAULT_TOURNAMENT = 3
DEFAULT_CROSSOVER_P = 0.3
DEFAULT_PROMOTION_MARGIN = 0.05
DEFAULT_MAX_OVERFIT_GAP = 1.0

VERDICTS = ("PROMOTE", "KEEP_INCUMBENT", "NO_VIABLE_CANDIDATE")


class EvolutionEngine:
    """Deterministic strategy-parameter evolution over closed bars.

    Parameters
    ----------
    bars            : closed Bar series (train/test split is day-aligned;
                      the test tail is NEVER seen during selection).
    seed            : pins the whole run — same bars + same seed → the
                      same archive, byte-identical.
    population      : individuals per generation (incumbent included).
    generations     : selection→variation rounds after the seed round.
    """

    def __init__(self, bars: list[Bar], seed: int = 7,
                 population: int = DEFAULT_POPULATION,
                 generations: int = DEFAULT_GENERATIONS,
                 elite: int = DEFAULT_ELITE,
                 tournament: int = DEFAULT_TOURNAMENT,
                 crossover_p: float = DEFAULT_CROSSOVER_P,
                 n_segments: int = DEFAULT_N_SEGMENTS,
                 min_trades: int = DEFAULT_MIN_TRADES,
                 consistency_lambda: float = DEFAULT_CONSISTENCY_LAMBDA,
                 test_fraction: float = DEFAULT_TEST_FRACTION,
                 promotion_margin: float = DEFAULT_PROMOTION_MARGIN,
                 max_overfit_gap: float = DEFAULT_MAX_OVERFIT_GAP):
        if population < 4:
            raise ValueError("population must be >= 4")
        if generations < 1:
            raise ValueError("generations must be >= 1")
        self.bars = sorted(bars, key=lambda b: b.ts_close)
        self.seed = int(seed)
        self.population_size = int(population)
        self.generations = int(generations)
        self.elite = int(elite)
        self.tournament = int(tournament)
        self.crossover_p = float(crossover_p)
        self.n_segments = int(n_segments)
        self.min_trades = int(min_trades)
        self.consistency_lambda = float(consistency_lambda)
        self.test_fraction = float(test_fraction)
        self.promotion_margin = float(promotion_margin)
        self.max_overfit_gap = float(max_overfit_gap)
        self.train, self.test = split_train_test(self.bars, self.test_fraction)
        # evaluation cache: ident → GenomeFitness (identical genome never
        # re-measured — the archive IS the memory of this run)
        self._fit_cache: dict[str, GenomeFitness] = {}

    # ------------------------------------------------------------ evaluate
    def _fitness(self, ind: Individual) -> GenomeFitness:
        key = ind.ident
        if key in self._fit_cache:
            return self._fit_cache[key]
        fit = evaluate_genome(ind.genome, self.train,
                              n_segments=self.n_segments,
                              min_trades=self.min_trades,
                              consistency_lambda=self.consistency_lambda,
                              seed=self.seed)
        self._fit_cache[key] = fit
        # backfill the measured record onto the individual so the
        # PERSISTED archive carries what each genome measured (the
        # population DB must be self-contained evidence, not a name
        # list). Rejected genomes retire at birth (stillborn) WITH the
        # reject reason — the archive must say WHY, not just that.
        ind.is_fitness = fit.fitness
        ind.is_reject_reason = fit.reject_reason
        ind.is_trades = fit.n_trades_total
        ind.overfit_gap = None          # OOS measured for finalists only
        if fit.rejected:
            ind.status = "retired"
        return fit

    def _score(self, ind: Individual) -> float:
        """Selection score: viable fitness, else −inf (rejected
        individuals are stillborn — archived, never selected)."""
        fit = self._fitness(ind)
        if fit.rejected or fit.fitness is None:
            return float("-inf")
        return fit.fitness

    # ------------------------------------------------------------- helpers
    def _tournament_winner(self, pool: list[Individual],
                           rng: random.Random) -> Individual:
        contenders = rng.sample(pool, min(self.tournament, len(pool)))
        return max(contenders, key=self._score)

    # ----------------------------------------------------------------- run
    def run(self, archive_path: str | Path | None = None) -> dict:
        """Run the full evolution. Returns the audit dict (also persisted
        to `archive_path` as JSONL when given). Deterministic."""
        if len(self.train) < 40 or len(self.test) < 24:
            return {
                "ok": False, "error": "not_enough_bars",
                "n_bars": len(self.bars), "train_bars": len(self.train),
                "test_bars": len(self.test),
                "note": "need >= 40 train bars across >=2 days and >= 24 test bars",
            }
        rng = random.Random(self.seed)
        archive: list[Individual] = []
        history: list[dict] = []

        # ---- seed population: incumbent + its mutations (never random
        # strangers: respect the audited baseline, explore around it)
        incumbent = Individual(genome=dict(DEFAULT_GENOME), ident=genome_hash(DEFAULT_GENOME),
                               generation=0, birth_op="seed",
                               notes="shipped GUESS incumbent")
        archive.append(incumbent)
        for _ in range(self.population_size - 1):
            child, op = mutate(incumbent.genome, rng)
            ind = Individual(genome=child, parent=incumbent.ident,
                             generation=0, birth_op=op)
            if all(ind.ident != a.ident for a in archive):
                archive.append(ind)

        # ---- generations: evaluate → select → vary
        for gen in range(1, self.generations + 1):
            viable = [a for a in archive
                      if not self._fitness(a).rejected
                      and self._fitness(a).fitness is not None]
            if not viable:
                break
            viable.sort(key=self._score, reverse=True)
            parents = viable
            children: list[Individual] = []
            n_children = max(0, self.population_size - self.elite)
            while len(children) < n_children:
                if len(parents) >= 2 and rng.random() < self.crossover_p:
                    p1 = self._tournament_winner(parents, rng)
                    p2 = self._tournament_winner(parents, rng)
                    c1, c2 = crossover(p1.genome, p2.genome, rng)
                    for c in (c1, c2):
                        if len(children) >= n_children:
                            break
                        ind = Individual(genome=c, parent=p1.ident,
                                         second_parent=p2.ident,
                                         generation=gen, birth_op="crossover")
                        if all(ind.ident != a.ident for a in archive) and \
                           all(ind.ident != ch.ident for ch in children):
                            children.append(ind)
                else:
                    p = self._tournament_winner(parents, rng)
                    child, op = mutate(p.genome, rng)
                    ind = Individual(genome=child, parent=p.ident,
                                     generation=gen, birth_op=op)
                    if all(ind.ident != a.ident for a in archive) and \
                       all(ind.ident != ch.ident for ch in children):
                        children.append(ind)
            archive.extend(children)
            best = max(archive, key=self._score)
            bf = self._fitness(best)
            history.append({
                "generation": gen, "best_ident": best.ident,
                "best_is_fitness": bf.fitness,
                "best_trades": bf.n_trades_total,
                "born": len(children), "archive": len(archive),
            })

        # ---- champion selection (in-sample) + OOS validation
        # NOTE: the test tail is measured ONLY for the two finalists
        # (champion + incumbent). Measuring every candidate there would
        # be selection-on-the-test-set — the multiple-comparisons trap
        # walk-forward exists to prevent.
        viable = [a for a in archive
                  if not self._fitness(a).rejected
                  and self._fitness(a).fitness is not None]
        champion = max(viable, key=self._score) if viable else None
        if champion is not None:
            champion.status = "champion"
        if incumbent.status == "candidate":
            incumbent.status = "retired" if \
                self._fitness(incumbent).rejected else "candidate"

        # incumbent always gets the same OOS treatment (head-to-head)
        inc_is = self._fitness(incumbent)
        inc_oos = evaluate_oos(incumbent.genome, self.test,
                               min_trades=self.min_trades, seed=self.seed)
        incumbent.oos_fitness = (None if inc_oos.rejected
                                 else inc_oos.fitness)
        incumbent.oos_trades = inc_oos.n_trades_total

        result: dict = {
            "ok": True,
            "seed": self.seed,
            "config": {
                "population": self.population_size,
                "generations": self.generations,
                "elite": self.elite, "tournament": self.tournament,
                "crossover_p": self.crossover_p,
                "n_segments": self.n_segments, "min_trades": self.min_trades,
                "consistency_lambda": self.consistency_lambda,
                "test_fraction": self.test_fraction,
                "promotion_margin": self.promotion_margin,
                "max_overfit_gap": self.max_overfit_gap,
            },
            "n_bars": len(self.bars),
            "train_bars": len(self.train), "test_bars": len(self.test),
            "train_days": len({b.ts_close[:10] for b in self.train}),
            "test_days": len({b.ts_close[:10] for b in self.test}),
            "generations_run": len(history),
            "archive_size": len(archive),
            "history": history,
            "incumbent": self._head(incumbent, inc_is, inc_oos),
        }

        if champion is None:
            result.update({
                "champion": None,
                "verdict": "NO_VIABLE_CANDIDATE",
                "verdict_reason":
                    "no genome passed the min-activity gate on train",
            })
        else:
            ch_is = self._fitness(champion)          # cached (already measured)
            ch_oos = evaluate_oos(champion.genome, self.test,
                                  min_trades=self.min_trades,
                                  seed=self.seed)
            gap = overfit_gap(ch_is.fitness, ch_oos.fitness)
            # backfill the finalists' OOS onto the persisted individuals
            champion.oos_fitness = (None if ch_oos.rejected
                                    else ch_oos.fitness)
            champion.oos_trades = ch_oos.n_trades_total
            champion.overfit_gap = gap
            result["champion"] = self._head(champion, ch_is, ch_oos)
            result["overfit_gap"] = gap
            result["verdict"], result["verdict_reason"] = self._verdict(
                champion, ch_is, ch_oos, gap, incumbent, inc_oos)

        if archive_path is not None:
            self._write_archive(archive_path, archive, result)
        return result

    # ------------------------------------------------------------ internals
    @staticmethod
    def _head(ind: Individual, is_fit: GenomeFitness,
              oos_fit: GenomeFitness) -> dict:
        d = ind.to_dict()
        d["is_fitness"] = (is_fit.fitness if is_fit else None)
        d["is_rejected"] = (is_fit.rejected if is_fit else None)
        d["is_reject_reason"] = (is_fit.reject_reason if is_fit else None)
        d["is_trades"] = (is_fit.n_trades_total if is_fit else 0)
        d["oos_fitness"] = (oos_fit.fitness if oos_fit else None)
        d["oos_rejected"] = (oos_fit.rejected if oos_fit else None)
        d["oos_reject_reason"] = (oos_fit.reject_reason if oos_fit else None)
        d["oos_trades"] = (oos_fit.n_trades_total if oos_fit else 0)
        d["overfit_gap"] = overfit_gap(
            is_fit.fitness if is_fit else None,
            oos_fit.fitness if oos_fit else None)
        d["is_segments"] = ([s.to_dict() for s in is_fit.segments]
                            if is_fit else [])
        d["oos_segments"] = ([s.to_dict() for s in oos_fit.segments]
                             if oos_fit else [])
        return d

    def _verdict(self, champion: Individual, ch_is: GenomeFitness,
                 ch_oos: GenomeFitness, gap: float | None,
                 incumbent: Individual,
                 inc_oos: GenomeFitness) -> tuple[str, str]:
        """The promotion gate — measured OOS evidence only:

        1. challenger must be OOS-viable (min-activity gate on test tail);
        2. challenger OOS must beat incumbent OOS by promotion_margin;
        3. challenger overfit gap must be <= max_overfit_gap.

        The incumbent is the default winner of every tie — a search that
        cannot beat the audited baseline out-of-sample must say so."""
        if ch_oos.rejected or ch_oos.fitness is None:
            return ("KEEP_INCUMBENT",
                    f"champion failed OOS gate: {ch_oos.reject_reason}")
        if gap is not None and gap > self.max_overfit_gap:
            return ("KEEP_INCUMBENT",
                    f"overfit gap {gap:.3f} > {self.max_overfit_gap}")
        inc_fit = inc_oos.fitness if not inc_oos.rejected else None
        if inc_fit is None:
            return ("PROMOTE",
                    "champion OOS-viable; incumbent not OOS-viable "
                    f"({inc_oos.reject_reason or 'unmeasured'})")
        if ch_oos.fitness > inc_fit + self.promotion_margin:
            return ("PROMOTE",
                    f"champion OOS {ch_oos.fitness:.3f} beats incumbent "
                    f"{inc_fit:.3f} by more than {self.promotion_margin}")
        return ("KEEP_INCUMBENT",
                f"champion OOS {ch_oos.fitness:.3f} does not beat incumbent "
                f"{inc_fit:.3f} by margin {self.promotion_margin}")

    @staticmethod
    def _write_archive(path: str | Path, archive: list[Individual],
                       result: dict) -> None:
        """JSONL archive: one Individual per line, then a final `result`
        line. Loadable by `load_archive` — the population DB persists
        across runs (learning that survives process restarts)."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = [json.dumps(ind.to_dict(), sort_keys=True)
                 for ind in archive]
        lines.append(json.dumps({"_result": result}, sort_keys=True,
                                default=str))
        p.write_text("\n".join(lines) + "\n")


def load_archive(path: str | Path) -> tuple[list[Individual], dict | None]:
    """Read a JSONL archive written by EvolutionEngine.run. Returns
    (individuals, result_or_None). Malformed lines are skipped — the
    archive is append-history, never a load-bearing parser."""
    p = Path(path)
    if not p.exists():
        return [], None
    inds: list[Individual] = []
    result: dict | None = None
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if "_result" in d:
            result = d["_result"]
        else:
            try:
                inds.append(Individual.from_dict(d))
            except (KeyError, TypeError, ValueError):
                continue
    return inds, result
