# GAUNTLET RUN — MARKET GAUNTLET
## The locked prompt (run charter)

Build MARKET GAUNTLET — a next-generation multi-market intelligence
terminal that beats the best of TradingView, ai-hedge-fund, and
Bloomberg's public surface in one system: live cross-market monitoring
(forex, crypto, Indian/NSE, US equities, indices, commodities) with a
heatmap/movers surface, per-asset deep research, and a multi-analyst
agent desk (technician, macro, sentiment, news, risk) that runs on any
asset.

The bar is three real things, judged separately and blind:
1. TradingView's /markets page screenshotted at desktop+mobile for the
   surface — can a user see the whole market's state at a glance and
   drill into any asset in one click?
2. virattt/ai-hedge-fund's analyst personas for the agent layer — every
   persona does distinct, evidence-backed work like theirs do.
3. Bloomberg's documented public feature checklist (command palette,
   monitor lists, alerts, news search, economic calendar) for coverage.
Get the real things first and compare against them directly, not
descriptions of them.

Break this into the smallest pieces that can be improved and judged on
their own. For each piece, fan out a builder and a separate critic with
fresh context (opus/sonnet only — haiku never gets work). The critic
inspects the actual output, puts it next to the bar blind with the
labels stripped, says which one is better, and names the single biggest
remaining gap. Then it goes back to the builder.

The critic should be a harsh critic. Praise is not useful. If ours does
not win, it keeps going.

Loop on each piece until the critic picks ours blind. Do not stop before
that. Exit is winning the comparison or the owner stopping the run —
never a round count.

Everything must be verified end-to-end. Keep this progress page updated
live. Budgets and safety laws L11–L14 of the host repo apply unchanged.

## Execution constraints (owner-mandated)

- Subagent model policy: opus preferred, sonnet acceptable, haiku never.
  Opus has lower output limits → give subagents focused bounded tasks.
- No workflow compression. If the session dies, the next session resumes
  from the last journal entry in /home/z/my-project/worklog.md.
- End-to-end verification of every piece before it counts as done.

## Piece decomposition (smallest judgeable units)

| # | Piece | Judgeable against | Status |
|---|---|---|---|
| 0 | Bars fetched (TradingView screenshots, ai-hedge-fund clone, Bloomberg checklist) | — | DONE |
| 1 | Multi-market data plane: symbol registry v2 + feeds for forex/crypto/NSE/US/indices/commodities | TradingView coverage of those markets | ROUND 3 BUILT (awaiting round-3 critic) |
| 2 | Markets surface: cross-asset heatmap, movers, watchlists, search | TradingView /markets screenshot (blind) | WON (round 1) |
| 3 | Asset drill-down: chart + news + drivers + research for ANY asset | TradingView symbol page | WON (4/4 blind + defect round) |
| 4 | Analyst desk: personas (technician, macro, sentiment, news, risk) + PM synthesis, any asset | ai-hedge-fund personas (distinct evidence-backed work) | WON (critic: ours) |
| 5 | Bloomberg layer: command palette, monitor lists, alerts, news search, economic calendar | Bloomberg public feature checklist | BUILT (GAUNTLET-P13-BUILDER, awaiting critic) |
| 6 | Chat AGENT MODE → analyst desk entry | — | DONE (bridge live-verified) |
| 7 | End-to-end verification: every surface live-tested | — | DONE (19/20 + sync fix → 20/20) |
| 8 | Critic gauntlet: blind comparisons per piece vs the fetched bars | all three bars | EXIT_WON |

## Round log

(rounds append here as builder→critic cycles complete)

### Round 0 — bars fetched (COMPLETE)
- TradingView: 5 screenshots (desktop/full/crypto/forex/mobile) in scripts/gauntlet-bars/
- ai-hedge-fund: cloned; persona pattern = LLMAgent base + checklist prompt → signal + confidence; abstain-on-failure contract
- Bloomberg: markets screenshot + 8-feature public checklist (bloomberg-checklist.md)

### Piece 1 — multi-market data plane (BUILT, GAUNTLET-P1-BUILDER)
- `src/gold_desk/markets/registry.py`: 6 sectors / 47 verified-live Yahoo symbols
  (crypto 6, forex 8, commodities 6, indices 10, India 10, US 7) + `normalize()`
  human input → canonical symbol (btc/bitcoin, gold/XAUUSD, nifty/NIFTY50,
  reliance, eur/usd, aapl, tatamotors …)
- `src/gold_desk/markets/board.py`: threaded v8/chart fan-out (12 workers,
  full board in ~0.3s), per-symbol fail-soft, TTL-120s file cache
  (data/cache/markets_board.json, stale-serve), sparkline points, magnitude-aware
  rounding (2dp ≥1, 4dp <1); `fetch_detail()` reuses the chart fetch for 5d×30m OHLC
- CLI: `gold-desk markets [SECTOR] [--symbol ALIAS] [--json]`; web API
  `/api/desk/markets[?symbol=]` (news-route harness/python resolution pattern)
- Live verification: 47/47 symbols resolve with real data, 0 errors,
  8 currencies (USD EUR GBP JPY INR HKD CHF CAD)
- Symbol note: TATAMOTORS.NS 404s on Yahoo since the Oct-2025 demerger → registry
  uses TMCV.NS (the continuing "Tata Motors Ltd"), alias tatamotors→TMCV.NS
- Tests: tests/test_markets.py (14) — full suite 173 green (159 + 14)

### Piece 1 round 2 — defects fixed + coverage expanded (GAUNTLET-P2-BUILDER)
Round-1 critic verdict: PICK TRADINGVIEW. All defects fixed:

1. **FX pip precision** — `_round()` is now symbol-aware: "=X" pairs publish
   price/prev/change/points at 5dp below 10 / 3dp at-or-above 10 (tenth-of-a-pip
   both regimes; EURUSD=X now 1.16591 vs round-1 "1.17", USDJPY 159.262).
   Published change/change_pct derive from the published price/prev so a row
   never contradicts itself (round-1 USDCAD row: price=1.39/prev=1.38/change=0.002).
2. **Detail dual-range** — `fetch_detail()` now makes two chart calls:
   range=1d&interval=15m for the daily fields (price/prev_close/change/
   change_pct — chartPreviousClose of a 1d fetch IS yesterday's close) and
   range=5d&interval=30m for the OHLC bars + a new honestly-labeled
   `range_5d_change_pct`. Gold detail: −0.33% daily (matches board −0.34%,
   was mislabeled +3.75% in round 1) with +3.67% 5d alongside.
3. **Sparkline fallback** — when a 1d/15m fetch yields <8 points (GC=F/SI=F/CL=F
   gave 3, ^NSEI 5, KC=F/SB=F 0) the row refetchs range=5d&interval=60m and
   sparks off those closes, labeled `points_source: "1d"|"5d"` (fail-soft).
   Live: 28 symbols now carry 5d sparklines instead of degenerate 3–5 point ones.
4. **Coverage (BIGGEST_GAP)** — 9 sectors / 67 verified-live symbols
   (+20 round-2, every one probed live, none dropped):
   volatility ^VIX · rates ^TNX ^FVX ^IRX + DX-Y.NYB (DXY) ·
   ETFs SPY QQQ IWM GLD SLV EEM VXX · commodities +PL=F PA=F ALI=F
   ZC=F ZW=F ZS=F KC=F SB=F (platinum-group, base metals, agriculture)
   · MOVERS: top-5 gainers/losers by daily change_pct across the whole
   board, computed locally from board rows (no extra fetches), returned as
   `{movers: {gainers, losers}}` and printed in the CLI output.
5. **normalize()** covers the new universe: vix, dxy, dollar index, 10y/us 10 year,
   5y, 13w/tbill, spy, qqq, iwm, gld, slv, eem, vxx, russell 2000, platinum,
   palladium, aluminum/aluminium, corn, wheat, soybeans, coffee, sugar …
- CLI: board prints all 9 sectors + a MOVERS section; detail prints change (1d)
  and change (5d) separately; `_fmt_price()` is FX-aware (5dp/3dp pip resolution).
- Live E2E (2026-08-25): full board 67/67 rows, errors=[], ~0.8s cold;
  `--symbol gold` daily −0.33% matches board −0.34%; `--symbol eurusd`
  1.16591 (5dp). Suite: 186 green (173 + 13 new markets tests).
- Known carry-over (NOT in round-2 brief): the running web app's
  /api/desk/markets route still needs the lead's deploy step (runtime harness
  at download/gold_desk_v1 has no gold_desk.markets package yet).

### Piece 1 round 3 — the round-2 critic's 3 defects fixed (GAUNTLET-P4-BUILDER)
Round-2 critic verdict: PICK TRADINGVIEW narrowly, on coverage only (quality
certified). Its 3 new defects, all fixed:

1. **range_5d_change_pct is now BAR-DERIVED** (was: Yahoo's 5d
   meta.chartPreviousClose, which anchors near *yesterday* for 24/7 assets).
   Now (last_close − first_close)/first_close over the served 5d bars — the
   CLOSE of the first bar, not the open, not meta. Live `markets --symbol
   btc`: bars 73,699.43 → 80,743.90, 5d change **+9.56%** (was +2.74%; the
   lying 5d cp 78,982.27 is byte-identical to the 1d prev close — the
   critic's diagnosis confirmed). Daily 1d fields untouched (+2.23%).
   Gold sanity: +0.03% 1d / +3.34% 5d. Empty-OHLC fallback → raw closes.
2. **Whole-market movers** — live-probed the keyless Yahoo predefined
   screeners: `query1`/`query2` …/v1/finance/screener/predefined/saved?
   scrIds=day_gainers&count=12 → **HTTP 200** (12 quotes each for
   day_gainers/day_losers, standard UA, no crumb); the combined comma form
   → 400 "Can only have 1 scrId currently" (so two calls). Added
   `fetch_market_movers()` (cached 120s, fail-soft, per-side fail-soft);
   the board merges BOTH strips: `market_movers` (whole market, 12/side —
   live TOP +14.93%, GENB +9.93% … AAOI −13.77%, all 24 symbols outside
   our registry) + `watchlist_movers` (our 67, the round-2 "movers"
   renamed; `movers` kept as back-compat alias). CLI prints both sections;
   if the screener is ever unreachable the footer notes the constraint.
   Merged post-cache so a screener outage never poisons the board cache.
3. **Inverse FX pairs** — `inr/usd` resolves to the reciprocal registry pair
   USDINR=X and serves the INVERTED quote: price=1/price, prev=1/prev,
   change recomputed, change_pct=(1/p−1/q)/(1/q)·100 exactly, named
   "INR/USD (derived)" with derived/derived_from flags, 6dp derived
   precision (Yahoo's native INRUSD=X carries only 4dp) and inverted OHLC
   bars (high/low swap under 1/x). Pairs with no registry side (jpy/eur)
   resolve AD-HOC against Yahoo: both directions fetched in parallel,
   anchored on whichever side Yahoo quotes at higher precision (inverse
   pairs publish ~4dp: JPYEUR=X 0.0054 vs EURJPY=X 185.687) — live
   `markets --symbol jpy/eur` → EURJPY=X-derived 0.005385 EUR. normalize()
   accepts slash/dash/AABBB forms; aliases still beat the pair heuristic
   (xauusd → GC=F, silver → SI=F).
- Tests: tests/test_markets.py 27 → 43 (16 new: lying-meta 5d, closes
  fallback, screener parse/cache/fail-soft, board merge + screener-down,
  CLI both-sections + footer note + derived pair, inverse/ad-hoc pair math
  + clean failure). Full suite **202 passed** (186 kept green + 16 new),
  9s, still fully offline (autouse fixture fails the screener seam).
- Live E2E (2026-08-25): board 67/67 rows, errors=[], market_movers 24/24
  non-registry symbols, movers==watchlist_movers alias; btc 5d +9.56%;
  inr/usd, usd/eur, jpy/eur derived; gold/xauusd unchanged.
- Still the lead's deploy step (unchanged from round 2): the running web
  app's /api/desk/markets route needs the repo_stage markets module
  redeployed to pick up market_movers/watchlist_movers and the fixes.

### Piece 1 — WON (3 rounds)
R1: TRADINGVIEW (FX pips, detail mislabel, sparklines, coverage). R2: TRADINGVIEW narrowly (5d wrong on 24/7, self-universe movers, inverse FX). R3: OURS — zero defects reproduced, ^GSPC/^DJI byte-identical to the TV screenshot, India ours alone, whole-market movers 12/side.

### Piece 2 — WON (round 1)
Blind VLM: "Page B wins by a landslide" (at-a-glance). ~60 instruments/viewport vs TV ~4. Cosmetic defects (ragged orphans, NSE truncation, microtext contrast) folded into Piece 3.

### Piece 3 — WON (4/4 blind VLM + defect round)
Ad-hoc resolution closed the biggest gap: ANY Yahoo ticker in one click. News above fold, decoded breadcrumbs, 24/24 mover links live.

### Piece 4 — WON
Critic: data-plane persona separation structurally stronger than the bar; zero fabricated numbers; abstain contract + JSON rescue (GC=F 4/5 substantive after fix). 238/238 tests.

### Piece 5 — the Bloomberg layer (BUILT, GAUNTLET-P13-BUILDER)
All 5 remaining checklist features, judged against bloomberg-checklist.md items 1-5
(6-8 were already won in pieces 1-3):

1. **COMMAND ENTRY** — `CommandPalette.tsx`: ⌘K / Ctrl+K / "/" (when not
   typing) from anywhere on /markets and /markets/[...symbol]; fuzzy search
   over the 67 registry symbols (symbol+name+alias), the commands
   (go markets/deck/chat · movers · eco · news <q> · alert <sym> · mon ·
   run desk <sym>), and FREE-TEXT passthrough ("search: xyz" or any
   unmatched query → /markets/<query> drill-down). Arrow keys + Enter,
   relevance-ranked results, score-sorted so "alert" ranks ALERTS above
   loose subsequence hits.
2. **ECO** — `markets/calendar.py`: ForexFactory mirror
   (nfs.faireconomy.media/ff_calendar_thisweek.json, live-probed: HTTP 200
   JSON, but rate-limits bursts → the 30min cache keeps the deployed
   surface far under the limit; an HTML rate-limit body parses as
   failure and falls through). 4-step fail-soft chain: fresh cache →
   live → stale cache → STATIC date-math schedule (FOMC 2026 published
   days, NFP first-Friday, CPI mid-month, ECB/BoE/BoJ 6-week cadences,
   RBI even-month first-Fridays) with a "static schedule" badge — ECO
   always works. CLI `markets-eco`, API `/api/desk/eco`, modal grouped
   by day with impact dots + ISO country chips + fcst/prev.
3. **NSE news search** — `markets/news_search.py`: query fuzzy-matches
   the registry (symbol/name/alias/sector + FX pair resolution + multi-
   token fallback), merges per-symbol Yahoo RSS feeds (≤12 symbols) with
   the gold general stream, entity-normalized title dedupe, recency
   rank, cap 20. CLI `markets-news Q`, API `/api/desk/news-search?q=`,
   modal from palette "news <q>" + NEWS header chip.
4. **ALERTS** — localStorage-persisted (max 20), checked against the
   board on every 30s refresh (no extra network); one-shot trips fire a
   bottom-right gold-border toast (8s auto-dismiss, CSS transition),
   then stay listed as "FIRED @ price". Quick-add "+ ALERT" on every
   drill-down prefills current price ±2%. ALERTS header chip carries an
   armed-count badge.
5. **MON** — localStorage monitor lists (max 5 × 30) seeded with MY
   WATCH [BTC-USD, GC=F, ^NSEI, EURUSD=X, SPY]; the MONITORS strip on
   /markets renders the active list as a compact quote table
   (price/chg%/sparkline fed by the board, ad-hoc symbols show "—");
   manager modal (create/rename/delete/add symbols); "+ MONITOR" list
   picker on every drill-down.
Also: `?desk=1` auto-triggers the analyst desk (palette "run desk
<sym>"), `#movers` deep-link scrolls from drill-downs, keyboard-safe
"/" (skips inputs/textareas).
Perf budget kept: no backdrop-filter/blur anywhere, solid overlay
colors, transform/opacity transitions only, memoized modal rows, 1
infinite animation on the steady-state board (the header live-dot).
Verification: 267/267 python tests (29 new: calendar 13 + news-search
16, all offline), tsc 0 / lint 0 / build green (new routes
/api/desk/eco + /api/desk/news-search emitted), repo↔runtime
byte-identical (web src + python markets/cli/tests), live E2E 15/15
(palette→BTC-USD drill-down, ECO modal 69 events live feed, news
results, alert toast + persistence across reload, monitors strip,
?desk=1 API call), 3 screenshots >150KB each VLM-verified.

### Piece 5 — WON
Critic 25/25 live checks; ECO judged BEAT; command entry edge-BEAT. Defect round: Google News topic search, palette hint fix, lint clean both trees. 277/277.

### Piece 6 — DONE
run_analyst_desk bridge tool in both agent registries; live-verified on GC=F (5 personas, PM bullish 56).


## RUN CLOSED — EXIT_WON

Final critic (fresh context, harshest seat): PICK_OVERALL **OURS**.
- vs TradingView (fresh blind VLM, both orders): ours 9/10 vs 3/10 and 9/10 vs 2/10
- vs ai-hedge-fund: discipline ported faithfully, structurally exceeded (data-plane persona entitlements)
- vs Bloomberg checklist: 8/8 live-verified
- The hybrid: TV's density + Bloomberg's command surface + the fund's persona desk + the constitution/journal/governor spine none of the three have — one keyless system.
- E2E 19/20 → 20/20 after the runtime re-sync fix (gold_desk.data restored).

### Round-2 targets (if the owner re-opens the gauntlet)
1. Runtime-sync guard in gold-desk doctor (prevent drift between repo and deployed harness)
2. PromptCache analogue + persona abstention polish
3. Hero focal element on /markets + cosmetic polish (truncated mover names, orphan HSI tile, sub-label contrast)

---

# ROUND 2 — THE INTELLIGENCE ROUND (hedge-fund-grade research depth)

Charter locked from deep research (2026-08-25): make the desk's research output
hedge-fund-grade in DEPTH and USEFULNESS — beat a hybrid bar of:
- BAR A: TradingAgents v0.3.1 @ a33fd4c (cloned scripts/tradingagents) — 12-agent debate
  firm: analysts→bull/bear debate→RM→Trader→3-way risk debate→PM + reflective memory.
  Runnable against our zen endpoint via openai_compatible for a true blind artifact fight.
- BAR B: ai-hedge-fund v2.2.0 (cloned scripts/ai-hedge-fund) — PIT fundamentals (20Q×14),
  earnings surprises, PromptCache. Its data plane is PAYWALLED — keyless superset is our
  permanent edge.
- BAR C: the institutional investment memo (thesis/citations/scenarios/risks/sizing/
  kill-criteria/conviction).

Keyless feed inventory (all probed live): SEC XBRL companyconcept (PIT fundamentals, MORE
history than AHF's paid vendor), Yahoo fundamentals-timeseries (13 quarterly series),
SEC 13F infotables (institutional positioning — Berkshire 89 positions/$299.3B parsed),
EDGAR full-text + submissions, Treasury.gov yield curve (162d), alternative.me F&G,
blockchain.info, CoinGecko, Reddit RSS. Dead: quoteSummary/options/StockTwits/Stooq/NSE.

Gap (forensic): desk context = price+news only. Missing: fundamentals, earnings events,
13F, macro series, social/on-chain, debate, trader execution, reflective memory,
PromptCache, verification pass, memo shape.

Piece table:
| # | Piece | Bar |
|---|-------|-----|
| R2-1 | Institutional data plane (XBRL fundamentals, 13F, curve, F&G, on-chain, social + fundamentalist persona) | AHF data depth, keyless superset |
| R2-2 | Quant toolkit + deterministic verified snapshot | TA market-data validation + stockstats |
| R2-3 | Adversarial debate + trader execution + risk panel | TA debate architecture |
| R2-4 | Reflective memory + PromptCache analogue | TA memory + AHF PromptCache |
| R2-5 | Memo product + mechanical evidence-checker | Institutional memo + deep-research verification |
| R2-6 | Run TradingAgents live as bar artifact + E2E + final critic | Blind: their report vs our memo |

Research artifacts: scripts/gauntlet2-research/ (DEEP_RESEARCH.md, tradingagents-brief.md,
ai-hedge-fund-brief.md, probe_feeds*.py + results).

Round log:
- R0 (2026-08-25): deep research complete. Both bars forensically briefed with file:line
  citations; 10 keyless institutional feeds probed live; gap analysis locked. AWAITING
  USER BAR LOCK before builder dispatch.

## Round 2 progress (live commits)
- 9f1596c — R0 deep research (bars set up)
- 9965507 — R2-1 builder (institutional data plane)
- bb74690 — R2-1 fix (fundamentalist prompt trim + OCF/FCF) — R2-1 CLOSED WON, 354 tests
- 8f6c58e — R2-2 builder (quant toolkit + verified snapshot + claim-conflict journaling), 407 tests
- GAUNTLET2-R2-CRITIC verdict: PICK BAR — 7 defects reproduced (D1-D7). Resume: see worklog.md entry GAUNTLET2-R2-RESUME-HANDOFF for the R2-2 fix dispatch brief.

## Round 3 progress (live commits) — DESK LIVE
- 0642b93 — R3-1 builder (8-asset monitor + Alpaca paper execution)
- 205d003 — R3-1 fix (D1-D6: WS auth, date-aligned correlation w/ BTC sign-flip fixed)
- b2000ae — R3-2 builder (news NLP sentiment + VaR/ES/stress risk engine + backtest), 765 tests
- f24c07a — R3-3 builder (MV/RP/HRP portfolio + P&L attribution + critic gap-fixes), 856 tests
- R3 EXIT vs OpenBB + Alpaca + Refinitiv: EXIT_WON 6/6 sub-rounds, 8/12 dimensions — SHIP

## Round 4 progress (live commits) — DESK AUTONOMY
- 65ceace — R4-1 builder (autonomous watch loop + alert engine + Telegram), 933 tests
- b52400f — R4-2 builder (24-instrument UNIVERSE + Ledoit-Wolf + exact projected-gradient MV), 964 tests
- 34b3517 — R4-3 builder (semantic news intelligence + historical stress replay + Brinson-Fachler), 1021 tests
- c1a0f5b — R4 exit-fix (D1-D8: FLAT_ASSETS whitelist, FISTA MV 1.5e-15, :3000 sync, rule-symbol sweep, domain synonyms, taxonomy arbitration)
- 73a4987 — D5/D6 close (entity-substitution synonyms + crypto taxonomy markers)
- 45c224e — R4-5 (covariance estimator zoo OAS/EWMA/semi + two-level Brinson w/ exact nesting), 1042 tests
- R4 FINAL GATE: EXIT_WON 9/12 — SHIP (trajectory 5/12 → 7/12 → 9/12; the gate HELD twice before shipping)

## Round 5 progress (live commits) — SELF-EVOLVING DESK
- Deep research (user-found Apodex.ai + the field): 11 primary sources — Reflexion,
  Darwin Gödel Machine, AlphaEvolve, ADAS, Gödel Agent, Zep/Graphiti, AgentEvolver,
  surveys 2507.21046/2505.22954/2506.13131/2408.08435/2501.13956/2303.11366;
  synthesized in docs/SELF_EVOLUTION_RESEARCH.md (the fundamental pattern: variation →
  evaluation → selection → archive; the evaluator is the whole game)
- 80e9211 — R5-1 builder (evolve/ package: genome + walkforward gate + engine w/ lineage
  archive + temporal lessons + rule tuner; CLI evolve-run/evolve-status/lessons/tune-rule;
  EvolutionPanel + 2 read-only API routes live on :3000), 61 new tests (1103 total)
- 934bd2e — R5 critic-fix D1 (archive self-describing: reject reasons persisted), 2
  regression tests (1105 total both repos, byte-identical)
- R5 CRITIC: WON 12/12 — 8 independent probe suites incl. the lookahead killer (test-tail
  mutation leaves every IS record byte-stable) and live-claim reproduction from the archive
- Headline: the 1y live run's champion WON in-sample (+0.244 vs −1.970) and LOST
  out-of-sample (0.677 vs 1.872) → the gate said KEEP_INCUMBENT. The system's most
  important output is a refusal.

## Round 6 progress (live commits) — EVERYWHERE DESK (device portability)
- Trigger: owner installs on a second device — install.sh step 4 (frozen
  matrix, fail-closed) refused: 7 sync-guard tests hard-coded the build
  machine's absolute deployment roots (/home/z/my-project/...) →
  FileNotFoundError on any other machine → installer blocked.
- Root cause class: a verification gate whose verdict depends on WHICH
  machine runs it is an environment probe, not a gate.
- Fix (R6-0): conftest mirror_root / runtime_web_root session fixtures —
  env-overridable (GOLD_DESK_MIRROR_ROOT / GOLD_DESK_RUNTIME_WEB_ROOT),
  SKIP when the root is absent on this device, HARD-fail when present
  (anti-drift discipline unchanged); all 7 guards rewritten onto them;
  repo root always derived from the test file itself; tests/test_portability.py
  (3 new guards): repo-root derivation, META-GUARD forbidding absolute host
  paths in test sources (conftest DEFAULT_* constants the only sanctioned
  exceptions), and the fresh-device contract — all 7 guards re-run in a
  subprocess with absent roots must SKIP, never fail.
- 1108/1108 both repos (1105 + 3). Build machine: guards run HARD. Fresh
  device: guards skip, 1101 pass — the installer proceeds.

## Round 7 progress (live commits) — THE BLOOMBERG WEDGE
Mission: Bloomberg-grade surfaces on the 100% free/keyless stack — pro
charting, options analytics, screener, macro/FRED depth, pair analytics,
a receipts-backed AI copilot, workflow glue — fused into the observation
layer while the deterministic live desk stayed byte-for-byte untouched.

- Piece 0 — bars captured to docs/screenshots/bars/ (TradingView GC1! +
  lightweight-charts gallery, Hull worked example, CBOE GLD chain, Finviz/
  stockanalysis/TradingView/Yahoo screeners, FRED DGS10 + curve history,
  TradingView GLD/SLV, Bloomberg command markdown). Bar-artifact honesty
  noted by the critic: the lightweight-charts capture was a 404, two
  screeners were Cloudflare challenges, and the Hull doc's own printed
  Greeks are arithmetically wrong against its own formulas (its prices
  4.76/0.81 are not — those bind).
- Piece 1 — ProChart (lightweight-charts v5.2.1, MIT) on the home deck +
  symbol drill-down: 15m/1h/1d switching, SMA20/50 + BB + RSI14 own pane,
  volume, sourced footer, no blur/backdrop-filter. tests/test_chart.py.
- Piece 2 — options plane: closed-form Black-Scholes Greeks in pure
  stdlib (src/gold_desk/risk/options.py) + chain fetcher (Yahoo options,
  Deribit for crypto; fail-soft + stale-serve) + IV surface/skew, OI
  wall, max pain, put/call (volume + OI), IV rank honestly labeled
  "this-chain basis". Math oracle: 0.0 deviation vs closed form on the
  critic's grid; Hull S=42/K=40/r=10%/sigma=20%/T=0.5 -> c=4.759422,
  p=0.808599. tests/test_options.py.
- Piece 3 — screener: safe expression DSL over technical + SEC-XBRL
  fundamental fields, registry/universe/sp500 universes, presets, TTL
  cache; verified live `rsi < 65 and revenue_growth > 0` -> AAPL rev_g
  16.4% keyless. /screener page + cli screen. tests/test_screener.py.
- Piece 4 — macro plane (src/gold_desk/macro/fred.py): FRED key path +
  keyless fredgraph.csv fallback + Treasury.gov keyless everything-else;
  curve history heatmap, macro dashboard, gold-vs-real-yields rolling
  correlation. ROADMAP.md carries the sanctioned FRED-key exception note
  (env var only, fail-closed to keyless). tests/test_macro.py.
- Piece 5 — pair plane (markets/pairs.py): spread/ratio, rolling
  z-score +/-2sigma bands, Engle-Granger 2-step zero-lag ADF (exploratory,
  never a gate). PairPanel on the drill-down with a non-identical default
  leg + identical-pair guard. tests/test_pairs.py.
- Piece 6 — receipts-backed copilot (agent/copilot.py + cli copilot +
  /api/desk/copilot + CopilotPanel): deterministic planner executes the
  desk's own READ-ONLY CLI subcommands (allowlist AST/test-pinned, no
  mutator reachable), every number [R#]-cited to a receipt, mechanical
  evidence gate (half-last-decimal tolerance, Q-echo excluded, negative
  numbers included). Probes: BAR question -> 28 checked / 0 untraceable /
  published; critic's own probe caught a question-echo number and
  refused to publish BEFORE the gate-scope fix (fail-closed proven).
  tests/test_copilot.py (25).
- Piece 7 — workflow glue: canonical CSV builder (export_csv.py,
  RFC-4180 round-trip pinned) + cli export-csv (journal/screener/pair/
  macro/options) + /api/desk/export + ExportCsvButton on every G7 table;
  command palette reaches SCREENER/MACRO/COPILOT/PAIR/OPTIONS/EXPORT
  (+ export <kind> prefix); branded EOD report (eod_report.py, cli
  eod-report) from the journal alone; Telegram alerts deep-link the
  drill-down (GOLD_DESK_DECK_URL override, symbolHref-encoded).
  tests/test_workflow_glue.py (12).
- Piece 8 — E2E: 1206 passed / 21 skipped (baseline 1087/21 this device;
  same 21 device-portability skips; +119 new tests); doctor healthy
  (venv/PyYAML/code/journal/zen/feeds/bun/web-deps all ok after the
  launcher learned to self-derive GOLD_DESK_ROOT on unsubstituted
  clones); demo --days 30 on a clean root: clean, synthetic-feed
  disclaimer intact.
- Bonus fixes swept in: argparse %-format crashes that killed root
  --help since R2 (ATR/ATR%%, %% moves, relative %%), eod --json for
  receipts, harness .venv symlink robustness via explicit env launch.

## Round 7 critic verdicts
- FINAL CRITIC (fresh context, blind, both orders, vs captured bars):
  PIECE1 OURS 8.0 vs 9.0 (ours literally wraps the bar's own engine,
  keyless) | PIECE2 OURS 7.7 vs 8.3 (math oracle exact; deeper analytics
  than the CBOE bar's own surface) | PIECE3 OURS 7.7 vs 6.7 (bar
  artifacts were 3/4 challenge pages) | PIECE4 BAR 7.0 vs 8.7 (D3:
  gold-vs-real failed keyless) | PIECE5 BAR 3.3 vs 7.7 (D1: self-pair
  GC=F/GC=F default) | PIECE6 OURS 8.3 (fail-closed proven twice) |
  PIECE7 OURS 7.0 vs 9.7 (D2: palette not evidenced in artifact) |
  BAR-E2E GREEN (1206/21, doctor, demo). DEFECTS D1-D7.
  PICK_OVERALL: OURS (SHIP-CONDITIONAL).
- Fix loop: D1 non-identical default leg + identical-pair guard;
  D2 palette artifact captured with G7 commands; D3 keyless Treasury
  real-yield fallback (190 aligned days, degraded-labeled) + latent
  b["t"]->b["ts"] fix; D4 commit (this push); D5 rate_assumption
  disclosed in payload + UI; D6 stale `next dev` killed, prod standalone
  serves, dev badge gone; D7 gate excludes question echo + one indicator
  leg per mentioned symbol (multi-symbol probes now covered).
- RE-CRITIC (fresh context, mechanical, three-way verification:
  source + live execution + OCR'd artifacts): D1 FIXED, D2 FIXED,
  D3 FIXED, D5 FIXED, D6 FIXED, D7 FIXED. PIECE4 REVERSED TO OURS,
  PIECE5 REVERSED TO OURS, PIECE7 HELD OURS. Residuals: one minor
  labeling drop in the dashboard projection (closed in this push),
  palette artifact filters MACRO/COPILOT out of frame (code-verified),
  GC=F futures honestly have no Yahoo options group (disclosed).
  PICK_OVERALL: OURS — 7/7 pieces. EXIT.

## Round 8 progress (live commits) — THE CRITIC'S LEDGER

Mission: clear the external audit — 3 CRITICAL, 7 MAJOR, 7 MINOR defects
(D-001…D-017) — without regressing the frozen matrix or the 15 Design
Laws. Five mechanical bars: BAR-INSTALL, BAR-HERMETIC, BAR-INTEGRITY,
BAR-PORTABILITY, BAR-POLISH. Each piece shipped only after a
fresh-context harsh critic re-verified the bar by execution.

Round log:

- Baseline (fresh clone @ gauntlet7, Sunday): 4 failed / 1202 passed /
  21 skipped — the 4 = D-002 reproducing live on a weekend run.
- Piece 1 (D-001, D-003, D-016): the installer's `sed g` rewrote the
  launcher's OWN substituted? comparison → self-satisfying if → every
  installed launcher re-derived its root from ~/.local/bin (proven by
  rendering + doctor). Fix: sed-immune prefix-strip check; the exit
  critic then caught the same g-flag re-writing `do_update`'s sed
  pattern into a no-op that bricked `gold-desk update` — split-sentinel
  literal + behavioral update test. allowedDevOrigins
  (localhost/127.0.0.1/0.0.0.0); launcher serves the production
  standalone build by default (GOLD_DESK_DEV=1 escape); `npm start`
  falls back to node when bun is absent. Render→doctor contract pinned
  in tests/test_launcher_portability.py.
- Piece 2 (D-002): injectable `now` threaded through run_daemon (frozen
  datetime OR per-tick callable) and watch_status; 4 tests frozen to
  WED 15:00 UTC; TestHermeticity META-GUARDs: source scan (any
  `.now(/.utcnow(/.today(/time.time(/utc_now(` shape — alias-dodge
  hardened after the P1+P2 critic), hostile-clock tripwire (utc_now
  monkeypatched to RAISE — injected now must drive everything),
  signature introspection, verbatim Sunday-03:00 sweep. Full matrix
  green ON A LIVE SUNDAY (critic re-ran: 1238 results, exit 0, and
  re-introduced a wall read to prove the tripwire trips).
- Piece 3 (D-004…D-007, D-009): sidecar journal stamps the REAL
  constitution content hash (same loader as demo/validate; existing
  root hash preserved; missing YAML → honest non-hex marker) and the
  overview hash now comes from the constitution loader, never the last
  event; macro live DXY/VIX read dv["live"]["D2"]/["D10"] (verified
  live: 102.23 / 14.84 keyless); FRED: no-key fail-fast (no wasted API
  attempt), 4s keyless CSV timeout, 10-min NEGATIVE cache tombstones,
  gold_vs_real parallelized — dashboard warm 0.54–0.65s (was ~17s),
  cold bounded ~4.9s; screener sp500 parse tolerant to the LIVE Parsoid
  markup (ticker = anchor TEXT; validated against the real page: 503
  symbols, real fixture committed) + cmd_screen fail-closed (403
  bot-wall renders clean {ok:false}, zero traceback); fetch_daily_bars:
  bounded retry (2 retries), no-bars vs fetch-failed distinction via
  fetch_daily_bars_ex, stale-serve, negative tombstones.
- Piece 4 (D-008, D-010, D-014): ONE shared portable resolver
  (web/src/lib/desk/resolve.ts) replaces 39 duplicated route-local
  copies + portable journal data-root — /home/z/ hits in web/src+bin
  now ZERO; R6 META-GUARD extended to scan web/src/** (all source
  shapes, after the critic flagged .js/.jsx/.mjs + bin-subdir holes)
  and bin/**; desk engine flips an all-personas-abstain run to
  ok:false + reason "provider unavailable" (+ hint naming
  GOLD_DESK_LLM_BASE_URL; UI surfaces it; CLI exits 1 — proven
  end-to-end against a dead endpoint); ignoreBuildErrors:false —
  tsc --noEmit + next build green (one pruned type import fixed).
- Piece 5 (D-011…D-013, D-015, D-017): demo counts DISTINCT ticket ids
  (8) with the lifecycle-event count disclosed (24) + a live
  PENDING_SEND snapshot line; eod market_time epoch → ISO-8601 UTC
  (seconds and ms); README status table carries R3–R8 with real counts
  (1,269 collected / 1,248 passing / 21 device skips · 45 routes · 5
  pages) re-pinned after the critic caught 4-test drift; --font-sans
  wired to Inter (the old value pointed at an undefined variable —
  computed body font now Inter, verified live); "real-time-ish" hedge
  replaced by honest "delayed (intraday)" badges; 390px overflow fixed
  at the component (responsive polarity gauge, wrapping ProChart
  toolbar) — measured 0 on /, /markets, /screener AND /chat.
- Final E2E: matrix 1269 tests → 1248 passed / 21 skipped / 0 failed;
  tsc --noEmit clean; next build green with the types gate live;
  clean-room install.sh (skip-web for speed; the web-inclusive path
  was proven in Piece 1) → doctor green, launcher substitution
  verified.

Per-piece critic verdicts (fresh context, blind vs the bar):
  P1+P2 (BAR-INSTALL + BAR-HERMETIC): PICK OURS (carry-forward:
    update self-corruption — fixed same round; alias-dodge — fixed).
  P3 (BAR-INTEGRITY): PICK OURS (carry-forward: gold_vs_real honest
    failure labels + stale docstring — fixed same round).
  P4 (BAR-PORTABILITY): PICK OURS (carry-forward: META-GUARD scope —
    widened same round).
  P5 (BAR-POLISH): PICK OURS (carry-forward: README count re-pin +
    launcher help staleness — fixed same round).
