# MARKET DRIVERS — What Really Moves Gold, and What Institutions Watch

*Desk research compiled 2026-08 (sources: Chicago Fed working papers, CFTC,
LBMA/CME documentation, World Gold Council, market-microstructure literature).
This file powers the Driver Board in both the web command deck and the TUI.*

---

## Tier 1 — Macro regime (moves gold for weeks/months)

### D1 · US Real Yields (10y TIPS) — the gravity well
The single most cited driver. Gold pays no coupon, so its opportunity cost
is the real (inflation-adjusted) yield on the alternative (Treasuries).
Historical correlation ≈ **−0.7** (weaker in strong central-bank-buying
regimes, e.g. 2025's −0.45 while gold surged ~65% — flows can override
yield gravity, which is itself signal). Institutions watch 10y TIPS yield
daily; breakouts in real yields are the classic gold headwind.

### D2 · US Dollar (DXY) — the denominator
Gold is priced in USD. Strong dollar = cheaper gold for the world =
headwind, and vice versa. Watch DXY alongside real yields: when both align
against gold (yields up + DXY up), downtrends are credible; when they
diverge, gold usually sides with real yields.

### D3 · Fed policy path & rate expectations
FOMC decisions, dot plots, speeches. What matters is the *expected* path:
Fed funds futures / SOFR strip repricing moves real yields instantly.
A dovish repricing = gold tailwind.

### D4 · Inflation & breakevens
CPI/PCE prints and 5y/10y breakevens. Rising inflation with flat nominal
yields = falling real yields = gold tailwind. Stagflationary mixes
(inflation up + growth down) are historically gold's best regime.

## Tier 2 — Positioning & flows (daily/weekly)

### D5 · CFTC COT — Managed Money net position
Weekly (Fri data, Tue snapshot) breakdown of futures open interest.
Extreme net-long positioning = crowded trade = vulnerable to liquidation
cascades even in intact uptrends. Institutions read Money Manager net lots
vs 1y percentile; flips from net-long to net-short mark regime turns.

### D6 · ETF flows (GLD / global gold ETF complex)
Daily visible demand. Tonne flows in/out of gold ETFs are the cleanest
public real-time demand signal. Sustained inflows confirm trend; inflow
exhaustion near highs warns of momentum decay (2026 note: ~298t of ETF gold
underwater while central banks kept buying — flows diverging = regime info).

### D7 · Central bank buying (WGC quarterly + PBoC monthly)
The structural bid of the 2022-2026 era (de-dollarization reserve
diversification). ~1,000t+/yr official-sector demand compresses available
float and decouples gold from real yields. PBoC's monthly reserve update
is a watched print.

### D8 · COMEX–LBMA EFP spread — the stress gauge
Exchange-for-Physical spread = COMEX futures price minus LBMA spot.
Normally a few dollars (vaulting/financing cost). EFP blowouts = physical
shortage or futures-side squeeze (echoes of 2020 March). Widening EFP with
rising futures open interest = real scarcity, not paper speculation.

## Tier 3 — Event risk (minutes/hours)

### D9 · The big three prints (blackout-window events)
**NFP** (first Friday, 13:30 UTC), **CPI** (~13:30 UTC), **FOMC** (19:00 UTC
statement + presser). These account for the largest single-bar gold moves;
the harness constitution enforces ±30-min blackout windows around them.
Medium tier: PPI, retail sales, GDP, jobless claims, Fed speakers.

### D10 · Geopolitical risk & VIX
Safe-haven bids on escalation. VIX > 20 regimes lift gold bid depth; gold
responds positively to VIX spikes (safe-haven literature, Sokhanvar 2024).
Fading VIX with held gold gains = sticky re-pricing (stronger signal).

## Tier 4 — Microstructure (intraday, harness-visible)

### D11 · Session liquidity map
24/5 market, but liquidity concentrates: London open (07:00–09:00 UTC)
sets the day's range framework; **London–NY overlap (13:00–17:00 UTC) is
the deepest, most volatile window**; rollover (22:00–23:00 UTC) has the
widest spreads — the harness fails closed there (SPREAD/SESSION codes).

### D12 · Dealer gamma / options walls (COMEX options)
Net dealer gamma determines whether hedging flows *absorb* (positive gamma,
pinned near call walls) or *amplify* (negative gamma past gamma-flip) moves.
Call wall overhead / put wall below frame expected ranges into expiry.
Institutions track the GEX flip level like equity desks do for SPX.

### D13 · Spread & rollover discipline (harness-enforced)
XAUUSD spreads triple at rollover and London pre-open on thin books. The
desk's SPREAD filter and pessimistic cost model (max(live, typical, min))
exist because of D13 — this is where retail bleeds.

---

## Signal synthesis used by the Driver Board

Each driver renders: current value, Δ, stance (TAILWIND / HEADWIND /
NEUTRAL for gold), one-line "why". Tier-1 macro sets the regime; Tier-2
flows confirm or diverge; Tier-3 events gate timing (blackouts); Tier-4
microstructure decides whether *this hour* is tradeable at all — which is
exactly the layer the harness's filters encode into reason codes.

**Sources**: Chicago Fed (Barsky) on real rates & gold; CFTC COT
definitions; CME/LBMA EFP documentation; World Gold Council Gold Demand
Trends; session studies (13:00–17:00 UTC overlap); SpotGamma-style GEX
framing; Sokhanvar (2024) VIX/safe-haven study.

*Values shown in the UI demo are simulated and clearly watermarked; wiring
real feeds is a data-plane task, not a UI task.*
