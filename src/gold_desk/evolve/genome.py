"""R5 BUILD 1 — the evolvable genome: GUESS spec parameters as genes.

FUNDAMENTAL (see docs/SELF_EVOLUTION_RESEARCH.md §2): in a self-evolving
agent the model is NOT the learner — it is the VARIATION OPERATOR. The
learning lives in the loop: variation → evaluation → selection → archive.
This module is the variation layer: a bounded, domain-constrained gene
space (the 9 numeric parameters of the desk's GUESS SetupSpec) plus
seeded mutation operators and crossover.

WHY A BOUNDED GENE SPACE (not free-form code, contra DGM/AlphaEvolve):
DGM evolves whole codebases and pays for it with a sandbox + human
oversight on every step. We evolve 9 bounded parameters with ordering
constraints — the search space where evolution is provably safe to run
unattended, byte-reproducible from a seed, and diffable by a human in
one glance. Conscious divergence, documented in the research doc §4.

Determinism law: every operator takes an injected `random.Random`.
Same genome + same seed → same child, always. No wall-clock, no
os-randomness, no ordering dependence on dict iteration beyond the
frozen GENES tuple.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass

from ..setup.spec import SetupSpec

# gene → (kind, lo, hi) — kind "int" steps by 1, kind "float" is continuous.
# Bounds chosen around the shipped GUESS defaults (always inside) with room
# to explore; the ordering constraint pairs are enforced after mutation.
GENES: tuple[tuple[str, str, float, float], ...] = (
    ("pre_range_start_hour", "int", 0, 5),
    ("pre_range_end_hour", "int", 4, 9),
    ("signal_start_hour", "int", 6, 10),
    ("signal_end_hour", "int", 9, 14),
    ("atr_period", "int", 7, 28),
    ("stop_atr_mult", "float", 0.8, 3.0),
    ("target_r_multiple", "float", 1.0, 4.0),
    ("time_stop_bars", "int", 3, 12),
    ("buffer_atr_mult", "float", 0.0, 0.5),
)
GENE_NAMES: tuple[str, ...] = tuple(g[0] for g in GENES)
_BOUNDS = {g[0]: (g[1], float(g[2]), float(g[3])) for g in GENES}

# after mutation these pairs must satisfy start < end (half-open windows)
_ORDERED_PAIRS = (
    ("pre_range_start_hour", "pre_range_end_hour"),
    ("signal_start_hour", "signal_end_hour"),
)

# defaults = the shipped GUESS spec verbatim (the incumbent every
# challenger must beat on OUT-OF-SAMPLE evidence, not vibes)
DEFAULT_GENOME: dict = {
    "pre_range_start_hour": 2,
    "pre_range_end_hour": 7,
    "signal_start_hour": 8,
    "signal_end_hour": 11,
    "atr_period": 14,
    "stop_atr_mult": 1.5,
    "target_r_multiple": 2.0,
    "time_stop_bars": 6,
    "buffer_atr_mult": 0.10,
}

MUTATION_OPS = ("gaussian_jitter", "step_walk", "boundary_probe",
                "uniform_reset")


# --------------------------------------------------------------- validation
def _clip(name: str, value: float) -> float | int:
    kind, lo, hi = _BOUNDS[name]
    v = lo if value < lo else (hi if value > hi else value)
    if kind == "int":
        v = int(round(v))          # canonical int — never 2.0 (hash stability)
    return v


def repair(genome: dict) -> dict:
    """Clip every gene into bounds and restore the ordered-pair
    constraints. Used after every mutation/crossover so a child is
    always a legal spec. Deterministic, pure."""
    g = {name: _clip(name, genome.get(name, DEFAULT_GENOME[name]))
         for name in GENE_NAMES}
    for start, end in _ORDERED_PAIRS:
        if g[start] >= g[end]:
            # pull start down to end-1 (clipped) — never widen past bounds
            g[start] = _clip(start, g[end] - 1)
            if g[start] >= g[end]:  # bounds left no room: push end up
                g[end] = _clip(end, g[start] + 1)
    return g


def is_valid(genome: dict) -> bool:
    """True iff every gene is in bounds (and int-typed where declared)
    and ordered pairs hold. `repair` output always satisfies this."""
    for name in GENE_NAMES:
        if name not in genome:
            return False
        kind, lo, hi = _BOUNDS[name]
        v = genome[name]
        if kind == "int" and not (isinstance(v, int) or
                                  (isinstance(v, float) and v.is_integer())):
            return False
        if not (lo <= float(v) <= hi):
            return False
    return all(genome[s] < genome[e] for s, e in _ORDERED_PAIRS)


# ------------------------------------------------------------ spec bridging
def genome_from_spec(spec: SetupSpec) -> dict:
    """Extract the 9 genes from a SetupSpec (incumbent seeding)."""
    return repair({name: getattr(spec, name) for name in GENE_NAMES})


def spec_from_genome(genome: dict, base: SetupSpec | None = None) -> SetupSpec:
    """Materialize a SetupSpec carrying the genome's genes. Non-gene
    fields (id/version/status/expiry...) come from `base` (default: the
    shipped spec) so only the evolvable surface changes. Raises on an
    invalid genome — callers never get a half-legal spec."""
    if not is_valid(genome):
        raise ValueError(f"invalid genome: {genome!r}")
    import dataclasses
    base = base or SetupSpec()
    patch = {name: (int(genome[name]) if _BOUNDS[name][0] == "int"
                    else float(genome[name])) for name in GENE_NAMES}
    return dataclasses.replace(base, **patch)


def genome_hash(genome: dict) -> str:
    """Content hash of the gene values (lineage identity).

    Canonicalizes types first (int genes → int, float genes → float)
    so {atr_period: 14} and {atr_period: 14.0} hash IDENTICALLY — the
    ident is about the gene VALUES, never their JSON spelling."""
    canon: dict = {}
    for name in GENE_NAMES:
        v = genome.get(name)
        if _BOUNDS[name][0] == "int":
            canon[name] = int(round(float(v)))
        else:
            canon[name] = float(v)
    canon_json = json.dumps(canon, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canon_json.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------- mutation
def _gaussian_jitter(g: dict, rng: random.Random, sigma_scale: float = 0.1) -> dict:
    """Continuous genes: N(0, sigma_scale * gene-range) around the parent.
    Int genes fall through to step semantics (jitter + round)."""
    child = dict(g)
    for name in GENE_NAMES:
        kind, lo, hi = _BOUNDS[name]
        span = hi - lo
        if rng.random() < 0.35:            # per-gene activation
            if kind == "float":
                child[name] = _clip(name, g[name] + rng.gauss(0.0, sigma_scale * span))
            else:
                step = max(1, int(round(abs(rng.gauss(0.0, sigma_scale * span)))))
                child[name] = _clip(name, g[name] + (step if rng.random() < 0.5 else -step))
    return child


def _step_walk(g: dict, rng: random.Random) -> dict:
    """Integer genes: ±1..3 steps on a random subset."""
    child = dict(g)
    for name in GENE_NAMES:
        if _BOUNDS[name][0] == "int" and rng.random() < 0.4:
            step = rng.randint(1, 3) * (1 if rng.random() < 0.5 else -1)
            child[name] = _clip(name, g[name] + step)
    return child


def _boundary_probe(g: dict, rng: random.Random) -> dict:
    """Exploration: push ONE random gene to a bound (lo or hi). Keeps
    the population from drifting into the middle and staying there."""
    child = dict(g)
    name = rng.choice(GENE_NAMES)
    _, lo, hi = _BOUNDS[name]
    child[name] = _clip(name, lo if rng.random() < 0.5 else hi)
    return child


def _uniform_reset(g: dict, rng: random.Random) -> dict:
    """Fresh blood: reset ONE random gene uniformly in bounds. The
    diversity-injection operator (anti premature-convergence)."""
    child = dict(g)
    name = rng.choice(GENE_NAMES)
    kind, lo, hi = _BOUNDS[name]
    child[name] = _clip(name, rng.uniform(lo, hi))
    return child


_OPERATORS = {
    "gaussian_jitter": _gaussian_jitter,
    "step_walk": _step_walk,
    "boundary_probe": _boundary_probe,
    "uniform_reset": _uniform_reset,
}


def mutate(genome: dict, rng: random.Random,
           op: str | None = None) -> tuple[dict, str]:
    """Apply ONE mutation operator (chosen by rng when `op` is None) and
    repair constraints. Returns (child, op_name). The op name is carried
    in the lineage record — the audit trail must say HOW each candidate
    was born, not just that it was."""
    if op is None:
        op = rng.choice(MUTATION_OPS)
    if op not in _OPERATORS:
        raise ValueError(f"unknown mutation op: {op!r}")
    child = repair(_OPERATORS[op](dict(genome), rng))
    return child, op


def crossover(a: dict, b: dict, rng: random.Random) -> tuple[dict, dict]:
    """Uniform per-gene crossover of two parents (two children, the
    complement pair). Recombination is what distinguishes evolution from
    random search — winners' building blocks get mixed."""
    c1, c2 = {}, {}
    for name in GENE_NAMES:
        if rng.random() < 0.5:
            c1[name], c2[name] = a[name], b[name]
        else:
            c1[name], c2[name] = b[name], a[name]
    return repair(c1), repair(c2)


# ------------------------------------------------------------------ lineage
@dataclass
class Individual:
    """One archive member: genome + WHERE IT CAME FROM + what it measured.

    AlphaEvolve's population DB row / DGM's archive node, made
    deterministic. Fitness fields are filled by the evaluator (never by
    narrative); `status` is champion | candidate | retired.
    """
    genome: dict
    ident: str = ""                       # short hash, set at birth
    parent: str | None = None             # parent ident (None = seed)
    second_parent: str | None = None      # set for crossover children
    generation: int = 0
    birth_op: str = "seed"                # seed | mutation op | crossover
    is_fitness: float | None = None       # walk-forward in-sample score
    oos_fitness: float | None = None      # held-out score (the truth)
    is_trades: int = 0
    oos_trades: int = 0
    status: str = "candidate"
    overfit_gap: float | None = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.ident:
            self.ident = genome_hash(self.genome)

    def to_dict(self) -> dict:
        return {
            "ident": self.ident, "genome": {k: self.genome[k] for k in GENE_NAMES},
            "parent": self.parent, "second_parent": self.second_parent,
            "generation": self.generation, "birth_op": self.birth_op,
            "is_fitness": self.is_fitness, "oos_fitness": self.oos_fitness,
            "is_trades": self.is_trades, "oos_trades": self.oos_trades,
            "status": self.status, "overfit_gap": self.overfit_gap,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Individual":
        return cls(genome=repair(d["genome"]), ident=d.get("ident", ""),
                   parent=d.get("parent"), second_parent=d.get("second_parent"),
                   generation=d.get("generation", 0),
                   birth_op=d.get("birth_op", "seed"),
                   is_fitness=d.get("is_fitness"),
                   oos_fitness=d.get("oos_fitness"),
                   is_trades=d.get("is_trades", 0),
                   oos_trades=d.get("oos_trades", 0),
                   status=d.get("status", "candidate"),
                   overfit_gap=d.get("overfit_gap"),
                   notes=d.get("notes", ""))
