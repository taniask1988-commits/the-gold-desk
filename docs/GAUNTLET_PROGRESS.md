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
| 6 | Chat AGENT MODE → analyst desk entry | — | PENDING |
| 7 | End-to-end verification: every surface live-tested | — | PENDING |
| 8 | Critic gauntlet: blind comparisons per piece vs the fetched bars | all three bars | PENDING |

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
