# Document 1.5 — Simulator Contract (the exam)

Status: **SKELETON — kill numbers BLOCKED pending owner's data history and
committed constitution numbers.** Its existence and order are locked (plan §13).

The simulator is a **separate offline program**. The live orchestrator cannot
call it. The LLM cannot call it. It imports the *same* decision functions as
the live path — asof filter, setup engine, hard filters, lot formula, cost
model, session/news blackout — otherwise its verdicts are lies (plan §12.2).

## 1. Frozen shape of the battery

1. **Costs on** — London-open-pessimistic spread (max of live / typical /
   min assumption), commission, slippage; unfillable when spread > cap.
2. **No lookahead** — the shared `filter_asof` is the only data gate; the
   calendar/news/indicator streams pass through it identically to live.
3. **Purged / embargoed walk-forward** — in-sample windows never touch the
   embargo gap adjacent to each test window.
4. **Random-start challenge paths** — N seeded starts (count derived from
   trade frequency, not fiat), each path running the full rule machine:
   internal daily stop, internal DD, max trades/day, stand-down, blackout,
   weekend policy.
5. **Holdout** — one date range, chosen once, never re-tuned. Seeing it and
   tweaking = new spec version + new holdout or a declared burn.
6. **Trade-count sanity** — below the minimum, a "pass" is void.
7. **Daily-loss breach probability** — distribution across paths.
8. **DD distribution** — across paths, vs internal caps.
9. **Regime slices** — trend / range / high-vol / post-news, where blackout
   allows.
10. **Sensitivity grid** — +spread, +slippage, stop ± jitter; small changes
    must not flip the verdict, or the edge is curve.

## 2. Verdicts

- `KILLED` — battery failed: write a new Doc 2 hypothesis; do not patch, do
  not add a veto, do not add agents.
- `INCOMPLETE` — constitution/data still BLOCKED: the runner refuses to
  produce numbers by design (fail closed, like everything else here).
- `FROZEN_LIVE_CANDIDATE` — battery passed on holdout AND
  constitution_hash + spec_hash recorded AND no parameter changed after
  seeing holdout. This is the ONLY promotion path (plan §12.5). The LLM
  cannot promote. Reflections cannot promote.

## 3. Priority metrics (plan §12.4)

1. Challenge pass-rate *distribution* (not one number)
2. Daily-loss breach probability
3. Max-DD distribution vs caps
4. Trade frequency stability
5. Expectancy after costs
6. Profit factor / Calmar (secondary). Never total return, never win rate
   alone.

## 4. BLOCKED until owner provides

- Historical H1 data range (broker-aligned) → walk-forward windows, holdout
- Random-start protocol count + seed policy
- Kill criteria numbers derived from the committed internal limits
- Minimum trade count
- Sensitivity grid steps
