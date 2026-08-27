"""R3-2/R3-3 — risk package.

* metrics.py     — VaR (parametric / historical / Monte Carlo), Expected
                   Shortfall, beta-adjusted exposure, stress scenarios
                   (GFC/COVID/2022 incl. gold+BTC vectors) and the
                   Sharpe/Sortino/MaxDD/Calmar ratio family. Pure math,
                   stdlib-only, fully deterministic.
* backtest.py    — the GUESS London-range-breakout setup run against 1y
                   of keyless GC=F 1h bars (Yahoo chart endpoint),
                   mechanical exits, equity-curve journal, buy-and-hold
                   comparison.
* portfolio.py   — R3-3 portfolio construction: mean-variance
                   (seed-pinned random/grid search), risk parity / ERC
                   (Spinu coordinate descent) and hierarchical risk
                   parity (López de Prado), each returning weights +
                   portfolio vol + per-asset risk contributions +
                   diversification ratio. Pure stdlib.
* attribution.py — R3-3 P&L attribution over a trade ledger: by asset,
                   by setup (win rates) and by hour-of-day with
                   Asia/London/NY session labels; journal-ledger
                   reconstruction + deterministic synthetic ledger
                   source. Pure stdlib.
"""
