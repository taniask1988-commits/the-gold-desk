# BAR CAPTURE NOTES — GAUNTLET 7 (Piece 0)

Captured 2026-10-06, headless Chromium, 1600×900 desktop width, into `docs/screenshots/bars/`.

| Bar ID | Named target | Artifact | Status |
|---|---|---|---|
| BAR-CHART (primary) | TradingView COMEX:GC1! live chart page | `BAR-CHART-tradingview-gc1.png` | CAPTURED (GC1! 4,197.9 live) |
| BAR-CHART (secondary) | TradingView official lightweight-charts demos gallery | `BAR-CHART-lightweight-charts-demos.png` | CAPTURED |
| BAR-OPT-SURFACE | OptionStrat / MarketChameleon GLD | — | **NOT FETCHABLE headless**: OptionStrat chains URL 404s; MarketChameleon returns "Access Denied" (bot wall, captured as evidence then deleted); Barchart returns CloudFront 403. |
| BAR-OPT-SURFACE (substitute) | **CBOE delayed quotes — GLD options chain** (IV, Delta, Gamma, Vol, Open Int, delayed-labeled) | `BAR-OPT-SURFACE-cboe-gld-chain.png` | CAPTURED (chain w/ filters, 90 records, "Delayed" badge visible) |
| BAR-SCREEN | Finviz screener | — | **NOT FETCHABLE headless**: Cloudflare Turnstile challenge ("Verify you are human") never passes headless; evidence captured then deleted. |
| BAR-SCREEN (substitute 1) | TradingView Stock Screener | `BAR-SCREEN-tradingview-screener.png` | CAPTURED — filter rail visible (14+ dimensions) but result table is login-gated for anonymous headless traffic ("No symbols match your filters"). Kept as partial evidence. |
| BAR-SCREEN (substitute 2, primary) | **Yahoo Finance predefined screen — Top Stock Gainers Today** (dense table, presets, table/heatmap views) | `BAR-SCREEN-yahoo-gainers.png` | CAPTURED (dense 10-column table, live data) |
| BAR-MACRO (primary) | FRED DGS10 series page | `BAR-MACRO-fred-dgs10.png` | CAPTURED |
| BAR-MACRO (secondary) | FRED multi-tenor curve graph (DGS1/2/5/10/30) | `BAR-MACRO-fred-curve-history.png` | CAPTURED |
| BAR-PAIR | TradingView GLD/SLV ratio chart | `BAR-PAIR-tradingview-gld-slv.png` | CAPTURED (GLD/SLV 6.88 live) |
| BAR-COPILOT | This repo's own /chat AGENT MODE (Round 0 piece 6, live-verified) | `BAR-COPILOT-*.png` | Captured later in the round — requires the local web app running; it IS ours, so the artifact is produced by running the app (see round log). |
| BAR-WORKFLOW | Bloomberg's documented command-palette breadth for features we now have | `BAR-WORKFLOW-bloomberg-commands.md` | Documented checklist (Bloomberg's UI is terminal-only; a screenshot is not fetchable — the doc method matches Round 1's bloomberg-checklist.md precedent). |
| BAR-E2E | This repo's frozen test matrix + doctor + demo | — | Not a screenshot; verified by running (see Piece 8). |
| BAR-OPT-MATH | Closed-form Black-Scholes + Greeks vs a published worked example (Hull) | `BAR-OPT-MATH-hull-example.md` | Documented worked example (math bar — the oracle is the formula + the textbook numbers, not a page). |

Substitution discipline: every substitution is same-certification (options depth, screener density), named, fetchable, and recorded here. The critic is shown these notes so the comparison is honest, not hallucinated.
