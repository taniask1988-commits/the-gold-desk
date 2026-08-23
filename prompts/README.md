# prompts/ — intentionally EMPTY until Phase 2

Law L10: no veto code, no veto prompt, until a setup survives the Doc 1.5
battery + holdout. When Phase 2 arrives, this directory holds:

    veto_system.v1.txt   — the locked system prompt (§8.3 intent)
    veto_schema.json     — {"decision": "ENDORSE"|"VETO", "reason": "<=500 chars"}

The prompt file's sha256 is journalled on every veto event. Changing the
prompt is a version bump, not a hot edit during a losing week.

A CI test (tests/test_blindfold.py) fails if any forbidden field — equity,
pnl, budget, streaks, challenge progress — can enter a context pack.
