# ROADMAP — From v1 Desk to a World-Class Trading Harness

You said the ambition out loud: harnesses like DeepSeek's, ReasonX, ZAI
Code, Hermes — but wrapped around trading. This file maps that ambition
onto the v1 architecture **without smuggling back what the plan deleted**
(§18: agent pyramids, LLM tools, memory retrieval, live broker, RL,
dashboards...).

The uncomfortable truth that makes those harnesses great: their power is
not agent count. It is **evaluation, evidence, and guardrails**. A coding
harness is world-class because its tests are ruthless, not because 12
agents debate. The trading analogue:

| Coding harness | This harness |
|---|---|
| Test suite / CI | Doc 1.5 simulator battery + 74 pinned invariants |
| Sandboxed execution | Fail-closed constitution + human-only execution |
| Reproducible runs | Append-only journal + `replay --date` |
| Prompt/engineering hygiene (hashes, versions) | constitution_hash / spec_hash / prompt_hash on every event |
| No silent model misbehavior | Blindfold, binary veto, gate re-check |

## The growth ladder (each rung needs the one below it)

### Rung 1 — Earn the right to more setups (Phase 1.5)
Freeze your numbers, drop real H1 history into `sim/runner.py`, freeze kill
criteria in `sim/contract.md`, and put the GUESS through the battery.
Expected and correct outcome: `KILLED`. Writing hypothesis #2 is the first
real act of research. A harness that cannot kill hypotheses cheaply is a
narrative machine.

### Rung 2 — The veto, one completion (Phase 2)
Flip `identity.phase: 2` only after a `FROZEN_LIVE_CANDIDATE` exists.
Context pack is already built and blindfold-tested; the prompt file gets
hashed like everything else. This is the LLM's entire territory: one
binary decision, zero tools, blind to score.

### Rung 3 — The candidate zoo (research, offline)
More `SetupSpec`s — each one a falsifiable claim, each one facing the same
frozen battery, each one versioned and hashed. The desk still runs ONE
live setup at a time. This is the "Hermes-scale" moment: dozens of
hypotheses being examined in parallel by the *simulator*, not by chatter.
Nothing here touches the live loop, so scope creep is structurally safe.

### Rung 4 — Research telemetry
The journal is already an event log; add offline analytics on top
(reason-code drift, filter-eats-session analysis, veto calibration on
recorded packs). Still write-only in the live loop (L7). Sunday review
becomes data-driven instead of memory-driven.

### Rung 5 — Broader instruments / execution assist
Multi-symbol, locked-cBot parameter filling — only after the process has
weeks of journaled paper behavior you actually read. Each extension is a
constitution commit, hashed, never a runtime mutation.

## What stays dead forever (not "later")

- LLM proposing or editing entries/stops/targets/sizes
- Remaining-budget or score anywhere near a model
- Kelly, reduce_size, retry-into-fill, chase
- Memory retrieval inside the live bar loop
- Promotion of any setup by anything except the frozen battery + holdout

## The one-line version

The world's best trading harness is the one where **every idea dies
cheaply in the simulator, every survivor is promoted only by evidence, and
the live loop is too blind, dumb, and fail-closed to blow up on its own.**
v1 is that sentence, running.
