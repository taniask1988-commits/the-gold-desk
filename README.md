# Gold Decision Harness v1

A silent, fail-closed, human-in-the-loop **XAUUSD H1 desk**.

Usually it does nothing. When one explicit Python rule fires, a risk gate
sizes or rejects, you get a Telegram ticket, you paste into cTrader or you
skip. Everything is journaled. Nothing is promoted by narrative. The
simulator precedes any LLM.

```
bar_close → quality → filters → setup → (veto: Phase 2 only) → gate → ticket → human
```

**What this is not:** an AI that trades, a 12-agent trading floor, a live
broker bot, a strategy-search engine, a dashboard, or an unbreakable money
machine. The harness prevents process death and forces evidence. Making
money is a property of a setup — after it survives the exam.

---

## Status

| Phase | What | State |
|---|---|---|
| 0 | Constitution + simulator contract | ✅ built, numbers **BLOCKED** (yours to commit) |
| 1 | Deterministic desk: pipeline, one GUESS setup, gate, journal, Telegram tickets, recovery, replay, EOD | ✅ built, 74 tests green |
| 1.5 | Frozen simulator battery on the setup | 🔶 skeleton (`sim/`) — refuses verdicts while numbers are BLOCKED |
| 2 | Single-shot context veto (LLM) | 🔒 stub only (raises); unlocks at `identity.phase: 2` |
| 3+ | Not in this plan | — |

The canonical `trading_constitution.yaml` is **fail-closed**: 30 numeric
fields are `BLOCKED` pending your broker/limits answers, so every bar ends
with reason code `CONSTITUTION_BLOCKED` and no ticket can ever exist. That
is by design — the demo overlay is the only thing that exercises trading
behavior, and everything it emits is watermarked `DEMO`.


## Install — one command

```bash
curl -fsSL https://raw.githubusercontent.com/taniask1988-commits/the-gold-desk/main/install.sh | bash
```

Then **restart the terminal** and type:

```bash
gold-desk          # launches the web command deck (Hermes-style global command)
gold-desk tui      # terminal UI instead
gold-desk doctor   # verify the installation
gold-desk help     # every command
```

The installer clones to `~/gold-desk`, creates an isolated venv, installs
dependencies, **runs the 92-test matrix as self-verification**, generates the
90-day demo journal, installs web-deck dependencies, and puts the global
`gold-desk` command on your PATH. No accounts, no keys, completely free.
(Manual alternative: `git clone <repo> && cd the-gold-desk && bash install.sh`)

## Repository layout

```
the-gold-desk/
├── trading_constitution.yaml   # Doc 1 — BLOCKED numbers, hashed, human-owned
├── src/gold_desk/              # the deterministic desk (Python)
├── sim/  prompts/  config/     # Doc 1.5 exam, Doc 4 prompts, templates
├── tui/desk_tui.py             # terminal UI (pure stdlib curses)
├── tests/                      # 92 tests = the §16 matrix
├── web/                        # GOLD DESK COMMAND — frosted-glass web deck
│   └── (Next.js 16 + TypeScript + Tailwind 4)
└── docs/                       # market-driver research + verification screenshots
```

### Web deck quickstart

```bash
# 1. populate the journal (the UI reads data/, which is gitignored)
python -m gold_desk.cli demo --days 90 --seed 42

# 2. run the deck (auto-finds ../data; or set GOLD_DESK_DATA)
cd web
bun install
bun run dev          # http://localhost:3000
```

### The `gold-desk` command

| Command | Does |
|---|---|
| `gold-desk` | web command deck (auto port, opens browser) |
| `gold-desk tui` | terminal UI |
| `gold-desk demo [days] [seed]` | regenerate the demo journal |
| `gold-desk test` | run the 92-test matrix |
| `gold-desk zen` | sync free OpenCode Zen models |
| `gold-desk doctor` | installation health check |
| `gold-desk update` | pull latest + refresh launcher |
| `gold-desk <anything else>` | pass-through to the harness CLI |

Env overrides: `GOLD_DESK_PORT` (web port, default 3000), `GOLD_DESK_DATA` (journal path), `GOLD_DESK_ROOT`
(harness root for the veto bench), `GOLD_DESK_PYTHON` (python with PyYAML).

## Quickstart

```bash
cd gold_desk_v1
pip install -e ".[dev]"        # or just: pip install pyyaml pytest

python -m gold_desk.cli validate          # what's BLOCKED and what to paste
python -m gold_desk.cli demo --days 30    # synthetic end-to-end run
python -m gold_desk.cli replay --date 2026-06-10
python -m gold_desk.cli eod --date 2026-06-10
python -m pytest                           # 74 tests, <1s
```

(If you didn't install the package, prefix commands with
`PYTHONPATH=src`.)

### The 30-day demo proves the Phase-1 done-when

- 720 closed bars → **exactly one terminal reason code each**
- ~8 tickets in 30 days (the desk is biased to no-trade)
- tickets persist to disk **before** send; recovery reuses the same id
- late human FILL after expiry → ignored + journalled, never chased
- zero LLM anywhere ($0.00 spend, `ENDORSE_BYPASS` journalled per candidate)
- replay answers "why this ticket" from the journal alone
- the GUESS setup *lost slightly* on synthetic data after costs — which is
  exactly why it is marked GUESS and can never be promoted without the exam

## Committing your numbers (the five questions)

1. **Firm** — you declared a personal account: `firm.enabled: false`. Paste
   `firm.account_size` + your news/weekend rules.
2. **Broker/contract** — replace every `BLOCKED` under `broker:`/`costs:`
   (contract size, digits, tick, lot step/min/max, London-open spread,
   commission, slippage). `config/constitution.example.yaml` is a prefilled
   template to copy from — verify every number against your broker's spec.
3. **Internal limits** — risk % per trade, sizing basis, max trades/day,
   loss stand-down, sessions, blackout minutes, spread cap, stop floors,
   RR floor, internal daily stop.
4. **Execution** — Telegram text → manual paste (default). Set
   `GOLD_DESK_TG_TOKEN` and `GOLD_DESK_TG_CHAT_ID` env vars; without them
   tickets print to console.
5. **Setup** — the engine holds a clearly-marked GUESS (London pre-range
   breakout, ATR stop/target, time-stop). Replace Doc 2 (`src/gold_desk/
   setup/spec.py`) only with a hypothesis that then faces Doc 1.5.

Then re-run `python -m gold_desk.cli validate` — the desk stays fail-closed
until zero BLOCKED fields remain.

## Layout

```
trading_constitution.yaml      # Doc 1 — BLOCKED numbers, hashed, human-owned
config/constitution.example.yaml  # prefilled template (typical gold CFD)
config/demo.yaml               # DEMO overlay — synthetic numbers, watermarked
src/gold_desk/
  constitution.py clock.py ulid.py events.py     # constitution, sessions, ids, journal
  data/      bars.py quality.py calendar.py news.py asof.py model.py
  features/  indicators.py     # closed-bar ATR/range (forming-bar firewall)
  setup/     spec.py engine.py # Doc 2 GUESS hypothesis → complete package or None
  filters.py sizing.py risk_gate.py   # §4.2 filters, §7.3 lots, §7.2 gate table
  context_pack.py veto.py      # blind pack (tested), Phase-2 stub (raises)
  ticket.py telegram_io.py account.py recover.py orchestrator.py
  replay.py eod.py demo.py cli.py
sim/contract.md runner.py report.py   # Doc 1.5 — the exam, offline, INCOMPLETE
prompts/                       # empty until Phase 2 (law)
tests/                         # 74 tests = §16 matrix + loop invariants
data/                          # runtime journal (gitignored)
```

## Reason codes (every bar ends with one)

`NO_SETUP SESSION SPREAD NEWS_BLACKOUT NEWS_UNAVAILABLE STALE_DATA
MISSING_BAR OUTLIER_PRICE BUDGET MAX_TRADES CONSEC_LOSS OPEN_POSITION
STOP_TOO_TIGHT RR_FLOOR SIZE_INVALID KILL_SWITCH LLM_VETO LLM_INVALID_JSON
LLM_UNAVAILABLE GATE_REJECT TICKET_EXPIRED HUMAN_SKIP FILL
CONSTITUTION_BLOCKED DEGRADED TZ_MISALIGN SOURCE_MISMATCH
IGNORED_LATE_RESPONSE SPREAD_BLOWOUT` — plus one additive code,
`TICKET_SENT` (ticket issued, awaiting the human), so the one-code-per-bar
invariant stays truthful while a live human has the ticket.

Histograms of these codes are how you learn "no edge" vs "spread filter ate
London open." `python -m gold_desk.cli eod --date ...` prints them.

## Telegram

Tickets, expiry/skip confirmations, kill-switch acks, EOD summaries —
nothing else. Never `NO_SETUP` noise. Sends are idempotent by `ticket_id`;
Telegram-down means retry the same id until expiry, then `TICKET_EXPIRED`.

```bash
export GOLD_DESK_TG_TOKEN="123:abc"
export GOLD_DESK_TG_CHAT_ID="42"
```

## Laws (enforced in code, hashed into every event)

Blindfold · Python owns the package · Veto is binary · Fixed fraction ·
Fail closed · Endorsement ≠ fill · Write-only memory · Telegram quiet,
journal loud · Human is the only agent · Simulator precedes LLM · No market
facts from LLM memory · Constitution is human-owned · Fail closed on time ·
Challenge survival beats return · Scope is v1. See `§1` of the frozen plan;
the full plan text is the design contract for this repo.

## Notes

- Sizing math in unit tests uses a **fake contract only** (Doc 5 rule); the
  live path reads the constitution and fails closed while BLOCKED.
- `prompts/` is empty by law. `veto.py` raises if touched. CI pins that the
  orchestrator's veto import lives inside the `phase >= 2` branch only.
- Scope deleted by the plan (agent pyramids, Kelly, reduce_size, memory
  retrieval, dashboards, live broker, cBot codegen, RL) is not hidden
  anywhere in this codebase. See `ROADMAP.md` for how the harness grows
  without smuggling them back.

---

## GOLD DESK COMMAND — the observation layer (owner-approved scope extension)

Read-only telemetry over the journal (ROADMAP rung 4, pulled forward by
owner decision). It cannot size, trade, mutate the constitution, or enter
the decision loop — it only reads `data/` and renders.

### 1. Web command deck — Hermes/DeepSeek-style terminal interface
- Price chart with London/overlap session bands, live ticker strip, paper
  equity curve
- **Market Driver Board** — the 13-driver institutional taxonomy from
  `docs/MARKET_DRIVERS.md` (real yields, DXY, Fed path, breakevens, COT,
  ETF flows, central banks, EFP, event risk, VIX, sessions, dealer gamma,
  spread discipline) with stances and a tier-weighted bias composite
  (values simulated + watermarked; real feeds are a data-plane task)
- Journal wire with live-replay engine (play/pause/1×/4×/16×), kind filters
- Reason-code histograms (day / all-time), ticket lifecycle pipelines,
  day replay with per-bar "why this bar" stories, constitution + laws

### 2. TUI — same deck in your terminal
```bash
python3 tui/desk_tui.py            # ← → day, ↑↓ scroll, a all-time, q quit
```

### Research that powers both
`docs/MARKET_DRIVERS.md` — what really moves gold and what institutions
watch, tiered by horizon (macro regime → positioning/flows → event risk →
microstructure), compiled from Chicago Fed / CFTC / LBMA-CME / WGC /
safe-haven literature sources.

---

## OpenCode Zen — free models inside the project (owner-approved)

Ported from the owner's `OPENCODE_HERMES_SETUP.md` (Hermes Agent deployment).
`https://opencode.ai/zen/v1` is a **keyless, completely free** OpenAI-compatible
endpoint; the Authorization header is never sent (paid-only on Zen) and the
official `opencode/1.18.18` client identity is used.

### What was added
| Piece | Role |
|---|---|
| `src/gold_desk/llm/zen_sync.py` | auto-discovery: Zen `/v1/models` ∩ models.dev, free-only (cost 0/0), tool-calling, deprecated-flagged; 6h cache with ID-diff fast path; bundled static fallback; preference-order default (falls through when Zen removes a model) |
| `src/gold_desk/llm/zen_client.py` | keyless client; balanced-brace JSON extractor (handles reasoning dumps); typed fail-closed errors |
| `src/gold_desk/llm/veto_llm.py` | the §8.3 context veto over Zen; timeout → no ticket, invalid JSON/schema → VETO |
| `src/gold_desk/llm/veto_bench.py` | OFFLINE research bench (clean/news/stale scenarios), journals to `data/veto_bench.jsonl` |
| `prompts/veto_system.v1.txt` + `veto_schema.json` | Doc 4 files, sha256-hashed into every veto result |

### Laws preserved
- **L10**: live bar loop still zero-LLM at `identity.phase: 1` — the
  orchestrator imports the veto only inside its `phase >= 2` branch
  (pinned by tests). The bench is offline research, never the live loop.
- **L3/L5**: binary ENDORSE|VETO; every failure mode fails closed.
- **§8.2**: provider stays OUT of the constitution (env: `OPENCODE_ZEN_BASE_URL`,
  `OPENCODE_ZEN_API_KEY=placeholder`).
- Nothing here can issue tickets, size trades, or promote a setup.

### Usage
```bash
python -m gold_desk.cli zen                    # sync + show free catalog
python -m gold_desk.cli veto-bench --scenario news   # live free-model veto test
```
The web deck's **OPENCODE ZEN panel** shows the catalog and runs the dry veto
from the UI; the TUI status line shows `ZEN <default> (N free)`.

### Early bench findings (honest)
The heavyweight default (`x-preview-f-free`) reasons deeply — it caught the
"intrabar spike = chase" failure mode — but often exhausts token budgets before
emitting JSON (fail-closed VETO, 25–75s). `hy3-free` answers fast but missed the
CPI-in-12-minutes veto. This is exactly the data the bench exists to collect
before any model is trusted at Phase 2.
