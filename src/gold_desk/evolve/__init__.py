"""R5 — the self-evolving desk layer.

What/When/How (the survey framework, docs/SELF_EVOLUTION_RESEARCH.md §4):
  WHAT evolves   strategy parameters (genome+engine), watch-rule
                 thresholds (rule_tuner), memory lessons (lessons)
  WHEN           inter-test-time only — EOD/on-demand CLI; the live
                 decision loop stays deterministic and zero-LLM
  HOW            seeded variation + walk-forward evaluation +
                 champion/challenger selection with full lineage audit;
                 promotion is explicit, never automatic
"""
from .genome import (DEFAULT_GENOME, GENES, GENE_NAMES, MUTATION_OPS,
                     Individual, crossover, genome_from_spec,
                     genome_hash, is_valid, mutate, repair,
                     spec_from_genome)
from .walkforward import (GenomeFitness, SegmentScore, evaluate_genome,
                          evaluate_oos, incumbent_genome, overfit_gap,
                          split_segments, split_train_test)
from .engine import EvolutionEngine, load_archive
from .lessons import LessonRecord, TemporalLessonStore
from .rule_tuner import (ProbeResult, TuneConfig, atr_spike_score_fn,
                         pct_move_score_fn, tune_threshold)

__all__ = [
    "DEFAULT_GENOME", "GENES", "GENE_NAMES", "MUTATION_OPS", "Individual",
    "crossover", "genome_from_spec", "genome_hash", "is_valid", "mutate",
    "repair", "spec_from_genome",
    "GenomeFitness", "SegmentScore", "evaluate_genome", "evaluate_oos",
    "incumbent_genome", "overfit_gap", "split_segments", "split_train_test",
    "EvolutionEngine", "load_archive",
    "LessonRecord", "TemporalLessonStore",
    "ProbeResult", "TuneConfig", "atr_spike_score_fn",
    "pct_move_score_fn", "tune_threshold",
]
