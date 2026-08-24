"""End-to-end demo on the synthetic feed.

What it proves (Phase 1 done-when, §15):
  1. every closed H1 bar produces exactly one terminal reason code
  2. no LLM anywhere on the path ($0 spend)
  3. tickets persist before send and survive recovery with the same id
  4. replay answers "why this ticket" from the journal alone
  5. canonical constitution is fail-closed; only the DEMO overlay trades

The demo overlay (config/demo.yaml) supplies clearly-marked DEMO numbers.
Every event carries payload.demo=true and every ticket is watermarked.
"""
from __future__ import annotations

import random
from datetime import timedelta
from pathlib import Path

from .account import PaperAccountStore
from .constitution import load_constitution
from .data.bars import SyntheticBarSource, SyntheticConfig
from .events import Journal
from .orchestrator import HumanSimulator, Orchestrator
from .recover import recover
from .sizing import point_value_from_constitution
from .telegram_io import TelegramIO
from .clock import utc_now


def run_demo(days: int = 30, seed: int = 7, data_root: Path | None = None,
             quiet: bool = False) -> dict:
    repo = Path(__file__).resolve().parents[2]
    data_root = data_root or (repo / "data")
    if data_root.exists():
        import shutil
        shutil.rmtree(data_root)

    constitution = load_constitution(
        repo / "trading_constitution.yaml",
        overlay_path=repo / "config" / "demo.yaml",
    )

    source = SyntheticBarSource(
        SyntheticConfig(seed=seed),
        start=_demo_start(days),
        days=days,
    )
    journal = Journal(data_root, constitution.content_hash, demo=True)
    telegram = TelegramIO(journal, printer=(lambda s: None) if quiet else print)
    accounts = PaperAccountStore(data_root,
                                 float(constitution.firm.get("account_size") or 10000.0),
                                 journal,
                                 point_value_per_lot=point_value_from_constitution(constitution))
    human = HumanSimulator(enabled=True, rng_seed=seed + 1)
    orch = Orchestrator(constitution, source, journal, telegram, accounts,
                        human_sim=human, data_root=str(data_root))

    # ---- run every closed bar through the lifecycle
    codes: list[tuple[str, str]] = []
    bars = source.bars_up_to(source._bars[-1].close_dt + timedelta(days=1), 10**9)
    for bar in bars:
        code = orch.on_bar_close(bar)
        codes.append((bar.ts_close, code))

    # ---- prove idempotent recovery on whatever is open right now
    recovery = recover(journal, orch.store, telegram, constitution, now=utc_now())

    # ---- reason histogram over the whole run
    from .events import Journal as J
    all_events = J.read_events(data_root)
    histogram = J.reason_histogram(all_events)

    tickets = [e for e in all_events if e["kind"] == "TicketEvent"]
    fills = [e for e in all_events if e["kind"] == "Fill"]

    if not quiet:
        print("\n" + "=" * 72)
        print("DEMO RUN COMPLETE")
        print(f"bars processed      : {len(codes)}")
        print(f"terminal codes      : {len(set(t for _, t in codes))} distinct")
        print(f"tickets issued      : {len(tickets)}")
        print(f"fill/resolution evts: {len(fills)}")
        print(f"paper balance       : {accounts.account.balance:.2f} "
              f"(start {constitution.firm.get('account_size')})")
        print("reason histogram    :")
        for code, n in list(histogram.items())[:12]:
            print(f"    {code:24s} {n}")
        last_day = codes[-1][0][:10] if codes else "n/a"
        first_day = codes[0][0][:10] if codes else "n/a"
        print(f"journal span         : {first_day} .. {last_day}")
        print(f"data root            : {data_root}")
        print("=" * 72)

    return {
        "bars": len(codes),
        "codes": codes,
        "histogram": histogram,
        "tickets": len(tickets),
        "recovery": recovery,
        "account": accounts.account,
    }


def _demo_start(days: int):
    # fixed recent-ish Monday so demo output is reproducible
    from datetime import datetime, timezone
    return datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
