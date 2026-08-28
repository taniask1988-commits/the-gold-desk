"""R5 — tests for the self-evolving desk layer (evolve/).

All bar series are SYNTHETIC (hand-built Bar objects) — no network.
Every test pins the determinism law: same inputs → same outputs.

Coverage map (mirrors docs/SELF_EVOLUTION_RESEARCH.md §4):
  genome.py       bounds/repair/validity, spec round-trip, mutation ops,
                  crossover, Individual lineage serialization
  walkforward.py  day-aligned splits, segment scoring, min-activity gate,
                  consistency penalty, OOS evaluation, overfit gap
  engine.py       full runs (deterministic, byte-identical archives),
                  champion/challenger verdict paths (PROMOTE /
                  KEEP_INCUMBENT / NO_VIABLE_CANDIDATE), archive
                  persistence + reload, safety (never writes live spec)
  lessons.py      validity windows, evidence counters, contradiction
                  retirement, decayed confidence, expiry, persistence
  rule_tuner.py   min-fire gate, margin gate, UNTUNABLE, dormant-rule
                  refusal, information-content score functions
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gold_desk.data.model import Bar
from gold_desk.evolve import genome as G
from gold_desk.evolve import lessons as L
from gold_desk.evolve import rule_tuner as RT
from gold_desk.evolve.engine import EvolutionEngine, load_archive
from gold_desk.evolve.genome import (DEFAULT_GENOME, GENE_NAMES,
                                     Individual, crossover, genome_from_spec,
                                     genome_hash, is_valid, mutate, repair,
                                     spec_from_genome)
from gold_desk.evolve.lessons import (LessonRecord, TemporalLessonStore)
from gold_desk.evolve.rule_tuner import (TuneConfig, atr_spike_score_fn,
                                         pct_move_score_fn, tune_threshold)
from gold_desk.evolve.walkforward import (evaluate_genome, evaluate_oos,
                                          overfit_gap, split_segments,
                                          split_train_test)
from gold_desk.setup.spec import SetupSpec

MONDAY = datetime(2026, 6, 1, tzinfo=timezone.utc)   # 2026-06-01 is a Monday


# ------------------------------------------------------------ bar builders
def _bar(day: datetime, hour: int, o: float, c: float,
         h: float | None = None, l: float | None = None) -> Bar:
    open_dt = day.replace(hour=0, minute=0, second=0, microsecond=0,
                          tzinfo=timezone.utc) + timedelta(hours=hour)
    return Bar(
        ts_open=open_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
        ts_close=(open_dt + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        open=o, high=h if h is not None else max(o, c) + 1.0,
        low=l if l is not None else min(o, c) - 1.0,
        close=c, volume=100.0,
    )


def _flat(day: datetime, h: int, p: float) -> Bar:
    return _bar(day, h, p, p, h=p + 1.0, l=p - 1.0)


def _flat_day(day: datetime, p: float) -> list[Bar]:
    return [_flat(day, h, p) for h in range(24)]


def _breakout_day(day: datetime, p: float, win: bool = True) -> list[Bar]:
    bars = [_flat(day, h, p) for h in range(0, 8)]
    bars.append(_bar(day, 8, p, p + 2.0, h=p + 3.0, l=p - 1.0))
    if win:
        bars.append(_bar(day, 9, p + 2, p + 2, h=p + 102.0, l=p + 1.0))
        bars += [_flat(day, h, p + 2) for h in range(10, 24)]
    else:
        bars.append(_bar(day, 9, p + 2, p + 2, h=p + 3.0, l=p - 102.0))
        bars += [_flat(day, h, p + 2) for h in range(10, 24)]
    return bars


def _series(n_days: int, every_other: bool = True,
            win: bool = True) -> list[Bar]:
    """n_days of bars; breakout signal day on even days (default)."""
    bars: list[Bar] = []
    for d in range(n_days):
        day = MONDAY + timedelta(days=d)
        p = 100.0 + d * 0.1
        if d % 2 == 0 or not every_other:
            bars += _breakout_day(day, p, win=win)
        else:
            bars += _flat_day(day, p)
    return bars


# ================================================================ genome.py
class TestGenome:
    def test_default_genome_is_valid_and_matches_spec(self):
        assert is_valid(DEFAULT_GENOME)
        spec = SetupSpec()
        for name in GENE_NAMES:
            assert DEFAULT_GENOME[name] == getattr(spec, name)

    def test_repair_clips_out_of_bounds(self):
        bad = dict(DEFAULT_GENOME, atr_period=999, stop_atr_mult=-5.0,
                   target_r_multiple=99.0)
        g = repair(bad)
        assert 7 <= g["atr_period"] <= 28
        assert 0.8 <= g["stop_atr_mult"] <= 3.0
        assert 1.0 <= g["target_r_multiple"] <= 4.0

    def test_repair_restores_ordered_pairs(self):
        bad = dict(DEFAULT_GENOME, pre_range_start_hour=9,
                   pre_range_end_hour=4)
        g = repair(bad)
        assert g["pre_range_start_hour"] < g["pre_range_end_hour"]
        assert is_valid(g)

    def test_is_valid_rejects_bad(self):
        assert not is_valid(dict(DEFAULT_GENOME, atr_period=5))
        assert not is_valid({"atr_period": 14})          # missing genes
        assert not is_valid(dict(DEFAULT_GENOME,
                                 pre_range_start_hour=8,
                                 pre_range_end_hour=8))

    def test_spec_round_trip(self):
        g = dict(DEFAULT_GENOME, atr_period=20, stop_atr_mult=2.2)
        spec = spec_from_genome(g)
        assert spec.atr_period == 20 and spec.stop_atr_mult == 2.2
        back = genome_from_spec(spec)
        assert back == g

    def test_spec_from_genome_rejects_invalid(self):
        with pytest.raises(ValueError):
            spec_from_genome(dict(DEFAULT_GENOME, atr_period=99))

    def test_genome_hash_stable_and_sensitive(self):
        a = dict(DEFAULT_GENOME)
        b = dict(DEFAULT_GENOME, atr_period=15)
        assert genome_hash(a) == genome_hash(dict(a))
        assert genome_hash(a) != genome_hash(b)

    def test_mutate_always_valid_and_deterministic(self):
        import random
        rng = random.Random(42)
        child, op = mutate(DEFAULT_GENOME, rng)
        assert op in G.MUTATION_OPS
        assert is_valid(child)
        # determinism: fresh rng, same seed → same child
        rng2 = random.Random(42)
        child2, op2 = mutate(DEFAULT_GENOME, rng2)
        assert child2 == child and op2 == op

    def test_mutate_unknown_op_raises(self):
        import random
        with pytest.raises(ValueError):
            mutate(DEFAULT_GENOME, random.Random(1), op="nope")

    def test_boundary_probe_hits_a_bound(self):
        import random
        rng = random.Random(3)
        # run enough probes that at least one gene sits exactly on a bound
        seen_bound = False
        for _ in range(50):
            child, _ = mutate(DEFAULT_GENOME, rng, op="boundary_probe")
            for name, _, lo, hi in G.GENES:
                if child[name] in (lo, hi):
                    seen_bound = True
        assert seen_bound

    def test_crossover_children_valid_and_gene_partition(self):
        import random
        rng = random.Random(11)
        a = dict(DEFAULT_GENOME, atr_period=10)
        b = dict(DEFAULT_GENOME, atr_period=20)
        c1, c2 = crossover(a, b, rng)
        assert is_valid(c1) and is_valid(c2)
        # every gene of c1 comes from a or b
        for name in GENE_NAMES:
            assert c1[name] in (a[name], b[name])
            assert c2[name] in (a[name], b[name])

    def test_individual_lineage_round_trip(self):
        ind = Individual(genome=dict(DEFAULT_GENOME), parent="abc",
                         generation=3, birth_op="crossover",
                         second_parent="def")
        d = ind.to_dict()
        back = Individual.from_dict(json.loads(json.dumps(d)))
        assert back.parent == "abc" and back.generation == 3
        assert back.birth_op == "crossover" and back.second_parent == "def"
        assert back.genome == ind.genome and back.ident == ind.ident


# ========================================================= walkforward.py
class TestWalkForward:
    def test_split_train_test_day_aligned_no_overlap(self):
        bars = _series(30)
        train, test = split_train_test(bars, 0.35)
        assert train and test
        train_days = {b.ts_close[:10] for b in train}
        test_days = {b.ts_close[:10] for b in test}
        assert not (train_days & test_days)
        assert train + test  # all bars accounted for
        assert len(train) + len(test) == len(bars)
        # test is the LAST days (contiguous tail)
        assert max(train_days) < min(test_days)

    def test_split_train_test_fraction_validation(self):
        with pytest.raises(ValueError):
            split_train_test(_series(10), 0.01)
        with pytest.raises(ValueError):
            split_train_test(_series(10), 0.9)

    def test_split_segments_day_atomic(self):
        bars = _series(24)
        segs = split_segments(bars, 3)
        assert len(segs) == 3
        all_days = [b.ts_close[:10] for s in segs for b in s]
        assert sorted(all_days) == sorted(b.ts_close[:10] for b in bars)
        # segments are contiguous, ordered
        for s1, s2 in zip(segs, segs[1:]):
            assert max(b.ts_close[:10] for b in s1) < \
                   min(b.ts_close[:10] for b in s2)

    def test_split_segments_too_few_days(self):
        # NOTE: the 23:00 bar CLOSES at next-day midnight, so a 1-day
        # series carries 2 day keys (close-time indexing, the repo
        # convention). Still < 3 → no segments.
        assert split_segments(_series(1), 3) == []

    def test_evaluate_genome_min_trades_gate(self):
        # flat-only series: no genome can trade → rejected
        bars = [b for d in range(12)
                for b in _flat_day(MONDAY + timedelta(days=d), 100.0)]
        fit = evaluate_genome(DEFAULT_GENOME, bars, min_trades=3)
        assert fit.rejected and fit.fitness is None
        assert "min_trades" in fit.reject_reason
        assert fit.n_trades_total == 0

    def test_evaluate_genome_viable_and_consistent(self):
        bars = _series(20)
        fit = evaluate_genome(DEFAULT_GENOME, bars, n_segments=3,
                              min_trades=3)
        assert not fit.rejected
        assert fit.fitness is not None
        assert fit.n_trades_total >= 3
        assert len(fit.segments) == 3
        # consistency: fitness <= mean (penalty only reduces)
        if fit.mean_score is not None:
            assert fit.fitness <= fit.mean_score + 1e-9

    def test_evaluate_genome_deterministic(self):
        bars = _series(20)
        f1 = evaluate_genome(DEFAULT_GENOME, bars, min_trades=3)
        f2 = evaluate_genome(DEFAULT_GENOME, bars, min_trades=3)
        assert f1.to_dict() == f2.to_dict()

    def test_evaluate_oos_gates(self):
        bars = _series(30)
        train, test = split_train_test(bars, 0.35)
        # flat test tail → zero trades → rejected
        flat_tail = [b for d in range(10)
                     for b in _flat_day(MONDAY + timedelta(days=100 + d),
                                        100.0)]
        fit = evaluate_oos(DEFAULT_GENOME, flat_tail, min_trades=2)
        assert fit.rejected and "zero_trades" in fit.reject_reason
        # real test tail trades
        fit2 = evaluate_oos(DEFAULT_GENOME, test, min_trades=2)
        assert not fit2.rejected
        assert fit2.fitness is not None

    def test_overfit_gap(self):
        assert overfit_gap(1.0, 0.5) == 0.5
        assert overfit_gap(None, 0.5) is None
        assert overfit_gap(1.0, None) is None


# ============================================================== engine.py
class TestEvolutionEngine:
    def test_full_run_deterministic_byte_identical(self, tmp_path: Path):
        bars = _series(30)
        r1 = EvolutionEngine(bars, seed=7, population=8, generations=3,
                             min_trades=3).run(
                                 archive_path=tmp_path / "a.jsonl")
        r2 = EvolutionEngine(bars, seed=7, population=8, generations=3,
                             min_trades=3).run(
                                 archive_path=tmp_path / "b.jsonl")
        assert r1 == r2
        assert (tmp_path / "a.jsonl").read_bytes() == \
               (tmp_path / "b.jsonl").read_bytes()

    def test_incumbent_always_evaluated_head_to_head(self):
        bars = _series(30)
        res = EvolutionEngine(bars, seed=7, population=8, generations=2,
                              min_trades=3).run()
        assert res["incumbent"]["oos_fitness"] is not None or \
               res["incumbent"]["oos_rejected"]
        assert res["incumbent"]["notes"] == "shipped GUESS incumbent"

    def test_verdict_promote_when_champion_beats_incumbent_oos(self):
        bars = _series(30)
        res = EvolutionEngine(bars, seed=7, population=8, generations=3,
                              min_trades=3).run()
        assert res["verdict"] in ("PROMOTE", "KEEP_INCUMBENT",
                                  "NO_VIABLE_CANDIDATE")
        if res["verdict"] == "PROMOTE":
            ch, inc = res["champion"], res["incumbent"]
            assert ch["oos_fitness"] > inc["oos_fitness"] + 0.05 - 1e-9
            assert ch["ident"] != inc["ident"]

    def test_keep_incumbent_when_margin_not_met(self):
        # all-win series: incumbents are near-perfect; force a big margin
        bars = _series(30)
        res = EvolutionEngine(bars, seed=7, population=8, generations=2,
                              min_trades=3,
                              promotion_margin=100.0).run()
        # nothing can beat by 100 Sharpe → KEEP_INCUMBENT (or champion
        # == incumbent → also KEEP_INCUMBENT via no-beat path)
        assert res["verdict"] in ("KEEP_INCUMBENT", "NO_VIABLE_CANDIDATE")

    def test_no_viable_candidate_on_flat_series(self):
        bars = [b for d in range(20)
                for b in _flat_day(MONDAY + timedelta(days=d), 100.0)]
        res = EvolutionEngine(bars, seed=7, population=6, generations=2,
                              min_trades=3).run()
        assert res["verdict"] == "NO_VIABLE_CANDIDATE"
        assert res["champion"] is None

    def test_overfit_gap_gate_blocks_promotion(self):
        bars = _series(30)
        # gap gate ~0: any positive gap blocks → verdict must not be a
        # promotion driven by a bigger IS than OOS
        res = EvolutionEngine(bars, seed=7, population=8, generations=2,
                              min_trades=3,
                              max_overfit_gap=-1.0).run()
        if res.get("champion") and res["champion"]["is_fitness"] is not None:
            # with an impossible gap gate, PROMOTE can only happen when
            # gap is None or <= -1 (OOS > IS by 1+) — verify the gate logic
            if res["verdict"] == "PROMOTE":
                assert res["overfit_gap"] is None or \
                       res["overfit_gap"] <= -1.0

    def test_archive_persistence_and_reload(self, tmp_path: Path):
        bars = _series(24)
        path = tmp_path / "arch.jsonl"
        res = EvolutionEngine(bars, seed=7, population=6, generations=2,
                              min_trades=3).run(archive_path=path)
        inds, result = load_archive(path)
        assert result == res
        assert len(inds) == res["archive_size"]
        # every individual has full lineage fields
        for ind in inds:
            assert is_valid(ind.genome)
            assert ind.ident == genome_hash(ind.genome)
            assert ind.generation >= 0
            assert ind.birth_op in ("seed", "crossover", *G.MUTATION_OPS)

    def test_engine_never_mutates_input_bars(self):
        bars = _series(20)
        before = [b.ts_close for b in bars]
        EvolutionEngine(bars, seed=7, population=6, generations=2,
                        min_trades=3).run()
        assert [b.ts_close for b in bars] == before

    def test_engine_rejects_bad_config(self):
        with pytest.raises(ValueError):
            EvolutionEngine(_series(10), population=2)
        with pytest.raises(ValueError):
            EvolutionEngine(_series(10), generations=0)

    def test_not_enough_bars_fail_soft(self):
        res = EvolutionEngine(_series(1), seed=1, population=6,
                              generations=1).run()
        assert res["ok"] is False
        assert res["error"] == "not_enough_bars"

    def test_lineage_parents_exist(self, tmp_path: Path):
        bars = _series(24)
        path = tmp_path / "lineage.jsonl"
        EvolutionEngine(bars, seed=7, population=6, generations=2,
                        min_trades=3).run(archive_path=path)
        inds, _ = load_archive(path)
        idents = {i.ident for i in inds}
        for ind in inds:
            if ind.parent is not None:
                assert ind.parent in idents
            if ind.second_parent is not None:
                assert ind.second_parent in idents


# =============================================================== lessons.py
class TestTemporalLessons:
    def test_born_with_validity_window_and_zero_confidence(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("gold fades after NY open", "GC=F", now)
        assert rec.status == "active"
        assert rec.valid_from == now and rec.valid_to is None
        # no evidence → no pull
        assert rec.confidence(now) == 0.0

    def test_evidence_monotone_support(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("lesson one", "GC=F", now)
        c = []
        for i in range(5):
            store.add_evidence(rec.lesson_id, "support", now)
            c.append(rec.confidence(now))
        assert all(x > 0 for x in c)
        assert c == sorted(c)                    # monotone non-decreasing
        # Laplace smoothing: 1 support scores 1/3 — direction, not proof
        assert abs(c[0] - 1.0 / 3.0) < 1e-5      # confidence rounds to 6dp
        assert abs(c[3] - 4.0 / 6.0) < 1e-5      # 4 of 6

    def test_contradiction_retirement_rule(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("lesson two", "SI=F", now)
        store.add_evidence(rec.lesson_id, "support", now)
        store.add_evidence(rec.lesson_id, "support", now)
        # 2 support, 1 contradict → still active (needs s>=2 AND c>=s)
        t = store.add_evidence(rec.lesson_id, "contradict", now)
        assert not t["retired"] and rec.status == "active"
        # 2 support, 2 contradict → RETIRED
        t2 = store.add_evidence(rec.lesson_id, "contradict", now)
        assert t2["retired"] and rec.status == "contradicted"
        assert rec.valid_to == now
        # retired lessons carry zero confidence and are not active
        assert rec.confidence(now) == 0.0
        assert store.active_lessons(now) == []

    def test_evidence_on_retired_lesson_fails_closed(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("lesson three", "GC=F", now)
        # NOTE: the contradiction rule needs support >= 2 — with zero
        # support, three contradicts leave the lesson active-but-negative
        for _ in range(3):
            store.add_evidence(rec.lesson_id, "contradict", now)
        assert rec.status == "active" and rec.contradict == 3
        # now push support to 2 — with 3 contradicts already banked, the
        # contradiction rule (c>=s AND s>=2) trips on THIS evidence call
        store.add_evidence(rec.lesson_id, "support", now)
        t = store.add_evidence(rec.lesson_id, "support", now)
        assert t["retired"] is True and rec.status == "contradicted"
        after = store.add_evidence(rec.lesson_id, "support", now + 1)
        assert after["ok"] is False and after["error"] == "lesson_contradicted"

    def test_unknown_lesson_and_bad_outcome_fail_closed(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        assert store.add_evidence("ghost", "support", now)["ok"] is False
        rec = store.add_lesson("l", "GC=F", now)
        assert store.add_evidence(rec.lesson_id, "sideways", now)["ok"] is False

    def test_decay_and_expiry(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("old lesson", "GC=F", now,
                               halflife_days=10, max_age_days=30)
        store.add_evidence(rec.lesson_id, "support", now)
        fresh = rec.confidence(now)
        older = rec.confidence(now + 10 * 86_400)     # one halflife
        assert 0 < older < fresh
        assert abs(older - fresh / 2) < 0.01          # exp decay, half at hl
        # past max_age → expired + zero
        rows = store.active_lessons(now + 31 * 86_400)
        assert rows == [] and rec.status == "expired"

    def test_symbol_regime_filtering_and_confidence_order(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        a = store.add_lesson("a", "GC=F", now, regime="low-vol")
        b = store.add_lesson("b", "SI=F", now, regime="low-vol")
        c = store.add_lesson("c", "SI=F", now, regime="high-vol")
        for _ in range(4):
            store.add_evidence(b.lesson_id, "support", now)
        for _ in range(1):
            store.add_evidence(a.lesson_id, "support", now)
        store.add_evidence(c.lesson_id, "support", now)
        syms = store.active_lessons(now, symbol="SI=F")
        assert [r["lesson_id"] for r in syms] == [b.lesson_id, c.lesson_id]
        assert syms[0]["confidence"] > syms[1]["confidence"]
        regime_rows = store.active_lessons(now, regime="low-vol")
        assert {r["lesson_id"] for r in regime_rows} == \
               {a.lesson_id, b.lesson_id}

    def test_idempotent_readd_keeps_evidence(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("persist", "GC=F", now)
        store.add_evidence(rec.lesson_id, "support", now)
        again = store.add_lesson("persist", "GC=F", now + 999)
        assert again is rec                      # same object, counters kept
        assert again.support == 1

    def test_manual_retire(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("kill me", "GC=F", now)
        assert store.retire(rec.lesson_id, now)["ok"] is True
        assert rec.status == "retired" and rec.valid_to == now
        assert store.retire("ghost", now)["ok"] is False

    def test_persistence_round_trip(self, tmp_path: Path):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("saved lesson", "GC=F", now, regime="x")
        store.add_evidence(rec.lesson_id, "support", now)
        store.add_evidence(rec.lesson_id, "support", now)
        store.add_evidence(rec.lesson_id, "contradict", now)
        p = tmp_path / "lessons.jsonl"
        store.save(p)
        loaded = TemporalLessonStore.load(p)
        r2 = loaded.get(rec.lesson_id)
        assert r2 is not None
        assert (r2.support, r2.contradict) == (2, 1)
        assert r2.status == "active"
        assert r2.confidence(now) == rec.confidence(now)

    def test_journal_records_transitions(self):
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson("journaled", "GC=F", now)
        store.add_evidence(rec.lesson_id, "support", now)
        store.retire(rec.lesson_id, now)
        j = "\n".join(store.journal_lines())
        assert "BORN" in j and "EVIDENCE" in j and "RETIRED" in j


# ============================================================ rule_tuner.py
class TestRuleTuner:
    def _sine_closes(self, n: int = 200) -> list[float]:
        import math
        closes = [100.0]
        for i in range(n):
            spike = 0.02 if i % 17 == 0 else 0.0
            closes.append(closes[-1] * (1 + 0.01 * math.sin(i / 3.0) + spike))
        return closes

    def test_min_fire_gate_untunable(self):
        # thresholds so high nothing fires → UNTUNABLE
        closes = self._sine_closes()
        sfn = pct_move_score_fn(closes, 1)
        res = tune_threshold(sfn, 0.005, TuneConfig(lo=0.5, hi=0.9,
                                                    min_fires=5))
        assert res["verdict"] == "UNTUNABLE"
        assert all(p["score"] is None for p in res["probes"])

    def test_dormant_incumbent_refuses_promotion(self):
        # incumbent far too high (never fires); grid has viable values —
        # the honest answer is KEEP, not promote-on-unmeasured-ground
        closes = self._sine_closes()
        sfn = pct_move_score_fn(closes, 1)
        res = tune_threshold(sfn, 0.9, TuneConfig(lo=0.001, hi=0.9,
                                                  min_fires=5))
        assert res["verdict"] == "KEEP_INCUMBENT"
        assert "dormant" in res["reason"]
        assert res["champion_score"] is None

    def test_margin_gate_keeps_incumbent(self):
        closes = self._sine_closes()
        sfn = pct_move_score_fn(closes, 1)
        res = tune_threshold(sfn, 0.005, TuneConfig(lo=0.001, hi=0.03,
                                                    min_fires=5,
                                                    grain=0.001,
                                                    margin=100.0))
        assert res["verdict"] == "KEEP_INCUMBENT"

    def test_promotion_when_margin_met(self):
        # two event classes: informative big moves (2% → next +5%) and
        # noise spikes (1.5% → next flat). A threshold above 1.5% but
        # below 2% selects ONLY the informative events → higher
        # information content → the margin gate opens.
        closes = [100.0]
        for i in range(120):
            if i % 20 == 19:
                closes.append(closes[-1] * 1.02)   # informative trigger
            elif i % 20 == 0 and i > 0:
                closes.append(closes[-1] * 1.05)   # follow-through
            elif i % 10 == 4:
                closes.append(closes[-1] * 1.015)  # noise spike
            elif i % 10 == 5:
                closes.append(closes[-1] * 1.0005)  # noise dies
            else:
                closes.append(closes[-1] * (1 + 0.001 *
                                            ((i % 5) - 2) / 5))
        sfn = pct_move_score_fn(closes, 1)
        low_score, low_fires = sfn(0.012)     # catches noise + signal
        high_score, high_fires = sfn(0.018)   # signal only
        assert high_score > low_score and high_fires < low_fires
        res = tune_threshold(sfn, 0.012, TuneConfig(lo=0.008, hi=0.025,
                                                    min_fires=3,
                                                    grain=0.002,
                                                    margin=0.005))
        assert res["verdict"] == "PROMOTE"
        assert res["best_score"] > res["champion_score"] + 0.005 - 1e-9

    def test_probes_recorded_and_deterministic(self):
        closes = self._sine_closes()
        sfn = pct_move_score_fn(closes, 1)
        cfg = TuneConfig(lo=0.001, hi=0.03, min_fires=5, grain=0.001)
        r1 = tune_threshold(sfn, 0.005, cfg, seed=9)
        r2 = tune_threshold(sfn, 0.005, cfg, seed=9)
        assert r1 == r2 and r1["n_probes"] == len(r1["probes"])
        vals = [p["value"] for p in r1["probes"]]
        assert len(vals) == len(set(vals))          # deduped

    def test_grain_snapping(self):
        closes = self._sine_closes()
        sfn = pct_move_score_fn(closes, 1)
        res = tune_threshold(sfn, 0.006, TuneConfig(lo=0.005, hi=0.03,
                                                    min_fires=5,
                                                    grain=0.005))
        for p in res["probes"]:
            assert abs(p["value"] / 0.005 - round(p["value"] / 0.005)) < 1e-9

    def test_bad_bounds_raise(self):
        sfn = pct_move_score_fn([1.0, 2.0, 3.0], 1)
        with pytest.raises(ValueError):
            tune_threshold(sfn, 0.5, TuneConfig(lo=1.0, hi=0.1))

    def test_information_content_score_direction(self):
        # persistence world: after a 2% move, next move is 5%
        closes = [100.0]
        for i in range(60):
            if i % 10 == 9:
                closes.append(closes[-1] * 1.02)
                closes.append(closes[-1] * 1.05)   # next-bar follow-through
            else:
                closes.append(closes[-1] * 1.0005)
        sfn = pct_move_score_fn(closes, 1)
        score_high_threshold, n_fires = sfn(0.015)  # catches the 2% moves
        score_everything, n_all = sfn(0.0)          # fires on every bar
        assert n_fires < n_all
        assert score_high_threshold > score_everything  # info > base

    def test_atr_spike_score_fn(self):
        atrs = [1.0] * 40 + [3.0, 3.2] + [1.0] * 20 + [2.5, 2.6] + [1.0] * 10
        sfn = atr_spike_score_fn(atrs, 20)
        score, fires = sfn(2.0)
        assert fires == 4          # both spikes' two bars each exceed 2x
        assert score > 0          # spikes predict elevated next ATR

    def test_score_fns_short_series_safe(self):
        assert pct_move_score_fn([1.0], 1)(0.01) == (0.0, 0)
        assert atr_spike_score_fn([1.0, 2.0], 20)(1.0) == (0.0, 0)


# ====================================================== integration layers
class TestEvolutionIntegration:
    def test_champion_spec_materializable(self):
        """The promotion path: champion genome → SetupSpec → the SAME
        backtest engine verifies the OOS number (no parallel truth)."""
        bars = _series(30)
        res = EvolutionEngine(bars, seed=7, population=8, generations=3,
                              min_trades=3).run()
        if res.get("champion"):
            spec = spec_from_genome(res["champion"]["genome"])
            assert isinstance(spec, SetupSpec)
            # re-running the OOS evaluation reproduces the number
            fit = evaluate_oos(res["champion"]["genome"], res_test_bars(bars),
                               min_trades=3)
            assert fit.fitness == res["champion"]["oos_fitness"]

    def test_lessons_into_evolution_audit(self):
        """Lessons and evolution coexist: the lesson store can record a
        KEEP_INCUMBENT verdict as evidence for the 'incumbent is hard to
        beat' belief — the Reflexion wiring (offline, operator-driven)."""
        bars = _series(24)
        res = EvolutionEngine(bars, seed=7, population=6, generations=2,
                              min_trades=3).run()
        store = TemporalLessonStore()
        now = 1_700_000_000.0
        rec = store.add_lesson(
            f"evolution verdict {res['verdict']} seed 7", "GC=F", now)
        store.add_evidence(rec.lesson_id, "support", now)
        assert store.active_lessons(now, symbol="GC=F")[0]["support"] == 1


def res_test_bars(bars: list[Bar]) -> list[Bar]:
    """Recompute the engine's test tail for cross-verification."""
    train, test = split_train_test(bars, 0.35)
    return test


# ================================================================ CLI layer
def _chart_body_breakout(n_days: int = 30) -> str:
    """Yahoo chart JSON with n_days of GUESS-breakout days (all WIN) —
    trades guaranteed, so the CLI path exercises the full engine."""
    ts: list[int] = []
    quote = {"open": [], "high": [], "low": [], "close": [], "volume": []}
    t0 = MONDAY
    for d in range(n_days):
        day = t0 + timedelta(days=d)
        p = 100.0 + d * 0.1
        breakout = d % 2 == 0
        for h in range(24):
            open_dt = day.replace(hour=0, minute=0, second=0,
                                  microsecond=0) + timedelta(hours=h)
            ts.append(int(open_dt.timestamp()))
            if h < 8:
                o = c = p
                hi, lo = p + 1.0, p - 1.0
            elif h == 8 and breakout:
                o, c = p, p + 2.0
                hi, lo = p + 3.0, p - 1.0
            elif h == 9 and breakout:
                o, c = p + 2.0, p + 2.0
                hi, lo = p + 102.0, p + 1.0
            else:
                base = p + 2.0 if (breakout and h >= 8) else p
                o = c = base
                hi, lo = base + 1.0, base - 1.0
            quote["open"].append(round(o, 2))
            quote["high"].append(round(hi, 2))
            quote["low"].append(round(lo, 2))
            quote["close"].append(round(c, 2))
            quote["volume"].append(100)
    body = {"chart": {"result": [{
        "meta": {"symbol": "GC=F"},
        "timestamp": ts,
        "indicators": {"quote": [quote]},
    }]}}
    return json.dumps(body)


class TestEvolveCLI:
    def test_cli_evolve_run_and_status(self, monkeypatch, capsys, tmp_path):
        from gold_desk.cli import cmd_evolve_run, cmd_evolve_status
        from gold_desk.risk import backtest as bt_mod
        monkeypatch.setattr(bt_mod, "_TEST_BARS",
                            _chart_body_breakout(30))

        class _RunArgs:
            symbol = "GC=F"
            bars = "1y"
            seed = 7
            population = 8
            generations = 2
            min_trades = 3
            margin = 0.05
            max_gap = 10.0
            archive = str(tmp_path / "arch.jsonl")
            json = True
            data_root = str(tmp_path)

        rc = cmd_evolve_run(_RunArgs())
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["ok"] is True
        assert out["verdict"] in ("PROMOTE", "KEEP_INCUMBENT",
                                  "NO_VIABLE_CANDIDATE")
        assert out["archive_size"] >= 8
        assert Path(out["archive_path"]).exists()

        class _StatusArgs:
            archive = str(tmp_path / "arch.jsonl")
            json = True
            data_root = str(tmp_path)

        rc2 = cmd_evolve_status(_StatusArgs())
        out2 = json.loads(capsys.readouterr().out)
        assert rc2 == 0 and out2["ok"] is True
        assert out2["n_individuals"] == out["archive_size"]
        assert out2["last_verdict"] == out["verdict"]

    def test_cli_evolve_status_missing(self, capsys, tmp_path):
        from gold_desk.cli import cmd_evolve_status

        class _Args:
            archive = str(tmp_path / "nope.jsonl")
            json = True
            data_root = str(tmp_path)

        rc = cmd_evolve_status(_Args())
        out = json.loads(capsys.readouterr().out)
        assert rc == 1 and out["ok"] is False

    def test_cli_lessons_round_trip(self, monkeypatch, capsys, tmp_path):
        from gold_desk.cli import cmd_lessons
        monkeypatch.setattr("time.time", lambda: 1_700_000_000.0)

        class _AddArgs:
            lessons_cmd = "add"
            text = "cli lesson one"
            symbol = "GC=F"
            regime = "low-vol"
            lesson_id = None
            outcome = None
            halflife = 90.0
            max_age = 365.0
            store = str(tmp_path / "lessons.jsonl")
            json = True
            data_root = str(tmp_path)

        rc = cmd_lessons(_AddArgs())
        out = json.loads(capsys.readouterr().out)
        assert rc == 0 and out["ok"] is True
        lid = out["lesson"]["lesson_id"]

        class _EvArgs(_AddArgs):
            lessons_cmd = "evidence"
            text = None
            lesson_id = lid
            outcome = "support"

        rc2 = cmd_lessons(_EvArgs())
        out2 = json.loads(capsys.readouterr().out)
        assert rc2 == 0 and out2["transition"]["support"] == 1

        class _ListArgs(_AddArgs):
            lessons_cmd = "list"
            text = None
            symbol = "all"
            regime = "all"

        rc3 = cmd_lessons(_ListArgs())
        out3 = json.loads(capsys.readouterr().out)
        assert rc3 == 0 and len(out3["active"]) == 1
        assert out3["active"][0]["confidence"] == pytest.approx(1 / 3, abs=1e-5)

    def test_cli_lessons_validation(self, capsys, tmp_path):
        from gold_desk.cli import cmd_lessons

        class _Args:
            lessons_cmd = "add"
            text = None            # missing → error
            symbol = "GC=F"
            regime = "all"
            lesson_id = None
            outcome = None
            halflife = 90.0
            max_age = 365.0
            store = str(tmp_path / "lessons.jsonl")
            json = True
            data_root = str(tmp_path)

        rc = cmd_lessons(_Args())
        out = json.loads(capsys.readouterr().out)
        assert rc == 1 and out["ok"] is False

    def test_cli_tune_rule(self, monkeypatch, capsys, tmp_path):
        from gold_desk.cli import cmd_tune_rule
        from gold_desk.risk import backtest as bt_mod
        monkeypatch.setattr(bt_mod, "_TEST_BARS",
                            _chart_body_breakout(20))

        class _Args:
            rule = "pct_move"
            symbol = "GC=F"
            bars = "1y"
            window = 1
            incumbent = 0.005
            lo = 0.001
            hi = 0.03
            min_fires = 5
            margin = 0.005
            grain = 0.001
            seed = 7
            json = True
            data_root = str(tmp_path)

        rc = cmd_tune_rule(_Args())
        out = json.loads(capsys.readouterr().out)
        assert rc in (0, 1)  # UNTUNABLE is possible on synthetic data
        assert out["ok"] is True
        assert out["verdict"] in ("PROMOTE", "KEEP_INCUMBENT", "UNTUNABLE")
        assert out["n_closes"] == 20 * 24
        assert len(out["probes"]) == out["n_probes"]

    def test_cli_tune_rule_fetch_failure(self, monkeypatch, capsys, tmp_path):
        from gold_desk.cli import cmd_tune_rule
        from gold_desk.risk import backtest as bt_mod

        def _boom(*a, **k):
            raise RuntimeError("network down")

        monkeypatch.setattr(bt_mod, "fetch_hourly_bars", _boom)

        class _Args:
            rule = "pct_move"
            symbol = "GC=F"
            bars = "1y"
            window = 1
            incumbent = 0.005
            lo = 0.001
            hi = 0.03
            min_fires = 5
            margin = 0.005
            grain = 0.001
            seed = 7
            json = True
            data_root = str(tmp_path)

        rc = cmd_tune_rule(_Args())
        out = json.loads(capsys.readouterr().out)
        assert rc == 1 and out["ok"] is False
        assert "network down" in out["error"]


class TestCriticFoundDefects:
    """Regressions for defects the R5 adversarial critic round found."""

    def test_retired_individuals_carry_reject_reason(self, tmp_path):
        """CRITIC D1: rejected genomes must persist WHY they retired —
        the archive is the audit trail; 'retired' without a reason is
        an unexplained verdict."""
        from gold_desk.evolve.engine import load_archive
        # flat series rejects everyone; the engine must still record why
        bars = [b for d in range(20)
                for b in _flat_day(MONDAY + timedelta(days=d), 100.0)]
        path = tmp_path / "why.jsonl"
        res = EvolutionEngine(bars, seed=7, population=6, generations=2,
                              min_trades=3).run(archive_path=path)
        assert res["verdict"] == "NO_VIABLE_CANDIDATE"
        inds, _ = load_archive(path)
        retired = [i for i in inds if i.status == "retired"]
        assert retired, "flat series should retire genomes at birth"
        for ind in retired:
            assert ind.is_reject_reason, \
                f"retired {ind.ident} lost its reject reason"
            assert "min_trades" in ind.is_reject_reason

    def test_archive_self_describing_round_trip(self, tmp_path):
        """Every persisted individual round-trips with its full audit
        record: measured fitness OR reject reason, never neither."""
        from gold_desk.evolve.engine import load_archive
        bars = _series(24)
        path = tmp_path / "selfdesc.jsonl"
        EvolutionEngine(bars, seed=7, population=6, generations=2,
                        min_trades=3).run(archive_path=path)
        inds, _ = load_archive(path)
        for ind in inds:
            measured = (ind.is_fitness is not None
                        or ind.is_reject_reason != "")
            assert measured, \
                f"{ind.ident}: neither a measurement nor a rejection — " \
                f"the archive must be self-describing"
