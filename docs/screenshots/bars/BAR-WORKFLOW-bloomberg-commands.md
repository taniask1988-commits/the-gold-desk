# BAR-WORKFLOW — Bloomberg's documented command breadth (for the features we now have)

Bloomberg Terminal's UI is a private, paid terminal — no screenshot is fetchable.
Precedent: Round 1 used Bloomberg's *documented public feature checklist* as the bar
(bloomberg-checklist.md). This bar does the same for workflow: the documented command
breadth of `<GO>`-style function codes mapped onto the surfaces GAUNTLET 7 builds.
Certification: our command palette must reach every feature below in ≤2 keystroke-bundled
actions, and CSV export must be byte-identical to what the table renders.

| Bloomberg function | What it does | Our surface (this round) |
|---|---|---|
| GP / GIP / D | price graph, intraday, daily | Pro chart (lightweight-charts): 15m/1h/1d switch, indicator overlays |
| OMON / OCV | option monitor / chain viewer | Options chain panel + Greeks + IV surface |
| SKEW / IVOL | volatility skew, implied vol analytics | IV skew curve + surface + IV rank |
| MAXP | max pain strikes | Max-pain computation + chart |
| EQS / FLDS | equity screening with field codes | `screen --where "rsi<30 and ..." ` screener + /screener page |
| GFV / GLCO | fixed income / curve analytics | Yield-curve history heatmap (tenors × dates) |
| MOST / MOVE | macro dashboard (fed funds, CPI…) | Macro dashboard (GDPNow, CPI, fed funds, DXY, VIX, real yields) |
| HRA / GC | multi-asset overlay, gold drivers | Gold-vs-real-yields overlay |
| SPREAD / RATIO <A,B> | relative-value spread/ratio charts | Pair analytics: spread/ratio + z-score bands + cointegration |
| IA / QST | ask-the-terminal natural language | Receipts-backed copilot (NL → read-only CLI, every number cited) |
| W / EXPORT | workspace output, export | CSV export everywhere (byte-identical), EOD report md |
| HELPx / palette | command hinting | Command palette with fuzzy registry + command prefixes |
| ALERT <sym> | alert routing with deep context | Telegram alerts deep-link to /markets/<symbol> drill-down |
| CIX / EOD | end-of-day composite | Branded EOD report (histogram + market state) from journal alone |
