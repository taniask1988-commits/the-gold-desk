# Self-Evolving AI Agents — Deep Research & the Gold Desk R5 Design

> Research round: 2026-08-28. Primary sources: arXiv abstracts fetched directly,
> vendor sites, survey papers. This document is the design justification for
> Round 5 (SELF-EVOLVING DESK) of the Gauntlet Loop. Every claim about a
> third-party system cites its source. Every claim about this desk is either
> measured by a test in this repo or marked UNPROVEN.

---

## 1. The finding: a new agent paradigm

Apodex (https://www.apodex.ai) markets itself as a "Self-Evolving Heavy-Duty
Solver": a deep-research product whose pitch is *step-level reasoning traces*,
*citations in every report*, and the line that defines the category —

> "Apodex reasons through it step by step — verifying every conclusion before
> moving to the next. Not a chat reply. A verified brief."

The product is one instance of a research movement that matured in 2024-2026.
Two survey papers organize it:

- **"A Survey of Self-Evolving Agents: What, When, How, and Where"**
  (Gao et al., arXiv:2507.21046, ~253 citations) — the field's framework:
  organize by **WHAT** evolves (prompts, memory, tools, agent architecture,
  model weights), **WHEN** evolution happens (intra-test-time = inside one
  task; inter-test-time = across tasks/episodes), and **HOW** evolution is
  driven (scalar rewards, textual feedback, single- vs multi-agent).
- **"A Comprehensive Survey of Self-Evolving AI Agents: A New Paradigm
  Bridging Foundation Models and Lifelong Agentic Systems"** (Fang et al.,
  2025, ~186 citations).

The surveys' core observation: LLMs are **static** — frozen weights cannot
adapt to novel tasks, evolving domains, or changing contexts. Self-evolving
agents move the learning OUT of the weights and into an external, inspectable
substrate (memory, code, strategy library, prompt). That single move changes
the economics of adaptation.

---

## 2. The fundamental pattern (the one thing to understand)

Every system below — Reflexion, DGM, AlphaEvolve, ADAS, Gödel Agent, Zep,
AgentEvolver, QuantEvolve — is the SAME loop with different parts:

```
   archive / population / memory        (the substrate: what carries learning)
        │  variation                    (mutation, crossover, reflection,
        ▼                               LLM-rewrites-code — the creative step)
   candidate pool
        │  evaluation                   (benchmark, real outcome, backtest,
        ▼                               verifier — THE MEASUREMENT GATE)
   measured candidates
        │  selection                    (keep what measures better; retire
        ▼                               what doesn't; NEVER discard ancestors)
   updated archive ──► loop
```

Three load-bearing insights, each counter-intuitive:

1. **The LLM is not the learner — it is the variation operator.** In
   AlphaEvolve and DGM the model proposes diffs (the genetics-inspired
   "mutation"); the actual learning lives in the loop + evaluator + archive.
   This is why a frozen, cheap, keyless model can still power a system that
   improves: the improvement is stored outside the model.

2. **The evaluator is the whole game.** Evolution optimizes WHATEVER the
   evaluator measures — including its bugs. If the evaluator can be gamed
   (lookahead bias, survivorship, reward hacking), evolution will find the
   exploit faster than any human. This is why honest measurement (out-of-sample
   splits, costs, pessimistic fills, min-trade gates) is not a nicety but the
   load-bearing wall of every system in this document. It is also exactly what
   this desk's Gauntlet discipline already enforces on the ENGINEERING process
   — R5 applies it to the STRATEGY itself.

3. **Ancestors are never deleted.** DGM keeps an expanding archive (a tree of
   agents); AlphaEvolve keeps a population database; ADAS keeps "an
   ever-growing archive of previous discoveries." Why: greedy selection
   collapses diversity and strands you on a local optimum; keeping ancestors
   makes every promotion reversible (rollback is a first-class operation, not
   an apology).

**Why this is better than the incumbent alternatives:**

| Approach | Why it loses to the evolution loop |
|---|---|
| Fine-tuning weights | GPU-expensive, needs gradients + data pipelines, changes are opaque (no readable diff), not revertible per-step, catastrophic forgetting |
| Hand-tuned parameters | Human search is a few dozen trials, unlogged, biased by recency; evolution runs thousands of seeded trials with a full lineage audit |
| Grid/random search | No lineage, no selection pressure (each trial independent), no archive reuse; walk-forward evolution recombines winners |
| Single-shot LLM "pick me parameters" | No measurement gate — the model asserts, never verifies; exactly the "chat reply, not a verified brief" failure Apodex mocks |
| Static rule libraries | Never adapt when the regime shifts; temporal-memory systems retire contradicted facts automatically |

---

## 3. The technology tree (each: what / how / why it matters)

### 3.1 Reflexion — verbal reinforcement learning
*(Shinn et al., NeurIPS 2023, arXiv:2303.11366 — ~7,000+ citations, the most
cited self-improvement mechanism in AI)*

- **What**: an agent that fails a task converts the environmental feedback into
  natural-language self-reflection, stores it in episodic memory, and the next
  trial conditions on that reflection. No weight updates anywhere.
- **How**: act → observe outcome → verbalize *why* it failed → store → retry
  with the lesson injected. Performance compounds across EPISODES
  (inter-test-time), not within one.
- **Why it matters**: proved that *linguistic* feedback is a sufficient
  training signal for agentic improvement. Every "lessons learned" subsystem
  since (including ours, R2-4) descends from it.
- **Beats**: plain retry (no learning), RL (needs no reward model or
  gradients), RAG-over-history (retrieves raw events, not distilled causes).

### 3.2 Darwin Gödel Machine — empirical self-modification with an archive
*(Sakana AI + UBC, arXiv:2505.22954, May 2025)*

- **What**: a coding agent that rewrites its OWN codebase, then must prove the
  rewrite is better by running benchmarks. SWE-bench 20.0% → 50.0%,
  Polyglot 14.2% → 30.7%.
- **How**: sample an agent from an archive → foundation model creates "a new,
  interesting version" (variation) → empirical validation on held-out problems
  (evaluation) → if better, add to archive (selection). Open-ended exploration
  keeps a growing TREE of diverse agents, not one champion.
- **The deep idea**: Schmidhuber's original Gödel machine required *provably*
  beneficial self-modification — impossible in practice. DGM's move: replace
  proof with **empirical validation on held-out tasks**. That substitution is
  the entire trick, and it is exactly the trade this repo already makes
  (measured backtests, never narrative).
- **Beats**: greedy self-prompting (no validation), pure RL in code space
  (no open-ended archive), human architecture search (slower, narrower).

### 3.3 AlphaEvolve — evolution + LLMs for algorithm discovery
*(Google DeepMind, arXiv:2506.13131, May 2025)*

- **What**: an evolutionary coding agent that beat a 56-year-old record: a
  4×4 complex-matrix multiplication algorithm using **48** scalar
  multiplications (Strassen 1969: 49). Also improved Google data-center
  scheduling, simplified accelerator circuitry, and sped up the training of
  the very LLM that powers it.
- **How**: a **population database** of programs + fitness scores; evaluators
  that check correctness and score quality; the LLM as mutation/crossover
  operator over the best programs; island-model diversity. Every candidate is
  VERIFIED before it can enter the population.
- **Why it matters**: demonstrates the loop at industrial scale with
  correctness-guaranteed evaluation. The architecture — *population DB +
  evaluator + LLM mutation* — is the blueprint R5 follows (with seeded
  statistical mutation instead of an LLM, because our live code is stdlib-only
  and deterministic by law).
- **Beats**: FunSearch (Romera-Paredes 2023 — single-island, smaller scope),
  human experts on this class of problem (56 years of them).

### 3.4 ADAS / Meta Agent Search — agents that design agents
*(Hu et al., arXiv:2408.08435, ~630 citations)*

- **What**: a meta-agent that PROGRAMS new agent architectures in code,
  evaluated automatically, kept in "an ever-growing archive of previous
  discoveries."
- **How**: because agents are defined in Turing-complete code, the search
  space is all possible agent designs — prompts, tool use, workflows, and
  combinations. Discovered agents transferred across domains AND models.
- **Why it matters**: the ML-historical claim — "hand-designed solutions are
  eventually replaced by learned solutions" — extended to agent systems
  themselves. R5's analog: the desk's own decision parameters become a
  searched space rather than a frozen guess.
- **Beats**: hand-designed pipelines (us, before R5), prompt-tuning (searches
  tokens, not structure).

### 3.5 Gödel Agent — runtime self-reference with rollback
*(Yin et al., arXiv:2410.04444, ACL 2025)*

- **What**: an agent that inspects itself at runtime (self-awareness via
  code/object introspection), modifies its own decision logic mid-run, and
  rolls back any self-modification that does not improve measured utility.
- **How**: the utility-guarded write: propose change → measure → keep or
  revert. Self-modification is a transaction, not a leap of faith.
- **Why it matters**: introduces TRANSACTIONAL semantics (propose → measure →
  commit-or-rollback) into self-modification. R5 adopts exactly this for rule
  tuning: a challenger threshold is promoted only on measured out-of-sample
  evidence, and the incumbent is always retained for rollback.

### 3.6 Zep / Graphiti — memory with validity windows
*(arXiv:2501.13956, ~375 citations)*

- **What**: a memory layer for agents built on a TEMPORAL knowledge graph:
  every fact carries a validity window; contradicted facts are INVALIDATED,
  not overwritten. Beat MemGPT on the Deep Memory Retrieval benchmark (94.8%
  vs 93.4%); +18.5% accuracy and −90% latency on LongMemEval.
- **How**: facts are edges stamped valid_from/valid_to. New contradicting
  evidence closes the old edge's window and opens a new one — history is
  preserved, current truth is queryable, staleness is a first-class concept.
- **Why it matters**: memory stops being a pile and becomes a *belief system
  with expiry dates*. For a trading desk this is precisely right: a lesson
  like "breakouts fail in low-ATR regimes" is TRUE of a regime, not of the
  universe — regimes end, and the memory must know when.
- **Beats**: MemGPT/Letta (blocks and files, no temporal semantics), Mem0
  (vector similarity, no invalidation), naive append-only logs (stale lessons
  poison future decisions forever).

### 3.7 AgentEvolver — the efficiency mechanisms
*(arXiv:2511.10395)*

Three named mechanisms worth stealing by name: **self-questioning**
(curiosity-driven task generation — the system invents its own practice
problems), **self-navigating** (experience reuse + hybrid policy guidance),
**self-attributing** (credit assignment to the states/actions that actually
caused the outcome, not just the final reward). R5's analog: attribute lesson
evidence to the SPECIFIC setup/parameter context that produced it, and let
the evolution engine generate its own candidate space (self-questioning =
mutation proposal distribution).

### 3.8 Deep-research agents (the Apodex family)

The architecture family Apodex belongs to (see the arXiv survey "Deep Research
Agents: A Systematic Examination", 2509.xxxx): a **search-reason loop** —
query → search → read → synthesize → VERIFY → re-query, with citations at
every step. Apodex's differentiators per their site: step-level reasoning
trace (every conclusion verified before the next step), branching off any
report, full-text search across threads. The category insight: research is a
*distributed-systems problem* (fan-out, verification, reconciliation), not a
chat problem. Our Gauntlet critics already run this loop for ENGINEERING
claims; R5 does not change that, it adds the same verification discipline to
the desk's own decision parameters.

### 3.9 Trading-domain prior art
- **QuantEvolve** (arXiv Oct 2025): multi-agent evolutionary framework for
  diverse, regime-adaptable trading strategies — validates the pattern for
  our domain.
- **EvoTS-Agent** (2026): self-evolving LLM agent for financial time-series
  change-point detection.
- **LLM-GA** (Zhang 2025): LLMs + genetic algorithms for strategy generation.

None of these publishes a full lineage-audited, walk-forward-gated, rollback-
capable engine that a retail operator can run keyless. That gap is R5's bar:
not "invent evolution" (we didn't) but "institutional measurement discipline
applied to evolution" (nobody hands you that).

---

## 4. The R5 design: what we build, mapped to the survey's WHAT/WHEN/HOW

### WHAT evolves (three substrates, one discipline)

| Layer | Module | Evolution mechanism | Prior art honored |
|---|---|---|---|
| Strategy parameters | `evolve/genome.py` + `evolve/engine.py` | seeded mutation + crossover over the 9 GUESS-spec genes; population + archive with full lineage | AlphaEvolve, DGM, ADAS |
| Watch-rule thresholds | `evolve/rule_tuner.py` | champion/challenger threshold search, min-fire gate, rollback by retention | Gödel Agent (transactional self-mod) |
| Memory lessons | `evolve/lessons.py` | validity windows, contradiction invalidation, evidence-weighted decayed confidence | Zep/Graphiti, Reflexion |

### WHEN evolution happens

**Inter-test-time only** (EOD / on-demand CLI), NEVER inside the live loop.
The live decision path stays deterministic and zero-LLM — an existing law.
Evolution results enter the live path only through an explicit, journaled
PROMOTE operation. This is the DGM safety principle (sandboxing, human
oversight) rendered as code: *the system proposes, the operator (or a
measured gate) disposes.*

### HOW evolution is driven — and the anti-reward-hacking stack

The evaluator is the desk's own deterministic backtest engine (R3-2). The R5
measurement gate, in order of importance:

1. **Walk-forward out-of-sample selection** (`evolve/walkforward.py`): bars
   split into K segments; fitness is computed on held-out segments the
   selection process never saw. The genome that wins in-sample MUST also win
   forward. This kills the classic evolve-until-overfit failure.
2. **Minimum-activity gate**: a genome that trades fewer than the minimum
   trade count scores −inf. A strategy that never trades can never lose
   money — and can never make any; evolution must not be allowed to
   "discover" inactivity. (Same honesty rule as the R3 backtest reporting.)
3. **Overfit gap report**: in-sample fitness minus out-of-sample fitness is
   REPORTED, not hidden. A champion whose OOS collapses vs IS is flagged
   OVERFIT and cannot be promoted. (The gap is the single most informative
   overfitting statistic in the walk-forward literature.)
4. **Full lineage audit**: every candidate records parent, generation,
   mutation operator, both fitnesses, trade counts. The archive is JSONL +
   content-hashed — byte-reproducible given the seed (DGM/AlphaEvolve
   archive, made deterministic).
5. **Ancestor retention**: retired champions stay in the archive; promotion
   stores the incumbent's genome; rollback is one command.
6. **Determinism**: seeded RNG everywhere; same bars + same seed → same
   archive, byte-identical (the repo's standing test discipline).

### Why each piece is the way it is (vs the obvious alternative)

- **Seeded statistical mutation, not LLM mutation, in the live engine.**
  AlphaEvolve uses an LLM as the mutation operator. We deliberately do NOT in
  the shipped engine: (a) stdlib-only law; (b) determinism law — a genome's
  lineage must reproduce byte-identically for audit; (c) $0 cost law. The LLM
  plays its variation role in the GAUNTLET (Builder proposes, Critic verifies)
  which is where non-determinism is acceptable because it is adversarially
  checked. This is a conscious, documented divergence, not an omission.
- **Genes are the spec's existing 9 parameters, not free-form code.** DGM
  evolves whole codebases; the safety cost is a sandbox and a human. We evolve
  a bounded parameter space with domain constraints (session hours ordered,
  ATR period sane, stop/target positive) — the search space where evolution
  is provably safe to run unattended.
- **Lessons carry evidence counters, not vibes.** A lesson's confidence is
  `exp(−age/halflife) · (support − contradict)/(support + contradict)` —
  monotone in evidence, decaying in time, sign-flippable by contradiction.
  When contradict ≥ support (with ≥2 support), the lesson RETIRES. No
  narrative can rescue it.

---

## 5. Honest limitations (unchanged gauntlet discipline)

- Evolution CANNOT conjure edge from nothing: it searches the GUESS
  parameter space; if the setup hypothesis itself is wrong, evolution
  honestly reports that (and the R3 finding — GUESS loses to buy-and-hold —
  is the baseline every evolved genome must beat, stated up front).
- 1y of keyless hourly bars is a small, regime-limited dataset; walk-forward
  over it is honest but statistically thin. The engine reports n_trades and
  segment counts so nobody mistakes a 9-trade champion for a law of nature.
- Rule tuning depends on realized fires; a rule that never fired has no
  evidence to tune from — the tuner reports that rather than inventing.
- English-only, rule-based NLP, 24 keyless instruments, 300s poll — all R4
  permanent gaps stand. R5 does not touch them.

## 6. Sources

- Apodex: https://www.apodex.ai (product page, fetched 2026-08-28)
- Gao et al., "A Survey of Self-Evolving Agents: What, When, How, and Where", arXiv:2507.21046
- Fang et al., "A Comprehensive Survey of Self-Evolving AI Agents", 2025
- Zhang et al., "Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents", arXiv:2505.22954
- Novikov et al., "AlphaEvolve: A coding agent for scientific and algorithmic discovery", arXiv:2506.13131
- Hu et al., "Automated Design of Agentic Systems", arXiv:2408.08435
- Yin et al., "Gödel Agent: A Self-Referential Agent Framework for Recursive Self-Improvement", arXiv:2410.04444, ACL 2025
- Rasmussen et al., "Zep: A Temporal Knowledge Graph Architecture for Agent Memory", arXiv:2501.13956
- Shinn et al., "Reflexion: Language Agents with Verbal Reinforcement Learning", NeurIPS 2023, arXiv:2303.11366
- "AgentEvolver: Towards Efficient Self-Evolving Agent System", arXiv:2511.10395
- QuantEvolve (arXiv, Oct 2025); EvoTS-Agent (2026); LLM-GA (Zhang 2025)
