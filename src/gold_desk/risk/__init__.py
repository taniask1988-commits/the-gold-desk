"""R3-2 BUILD 4 — risk package.

* metrics.py   — VaR (parametric / historical / Monte Carlo), Expected
                 Shortfall, beta-adjusted exposure, stress scenarios and
                 the Sharpe/Sortino/MaxDD/Calmar ratio family. Pure math,
                 stdlib-only, fully deterministic.
* backtest.py  — the GUESS London-range-breakout setup run against 1y of
                 keyless GC=F 1h bars (Yahoo chart endpoint), mechanical
                 exits, equity-curve journal, buy-and-hold comparison.
"""
