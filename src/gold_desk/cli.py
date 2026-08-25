"""CLI entrypoints.

    python -m gold_desk.cli validate                 # constitution report
    python -m gold_desk.cli constitution [--json]     # constitution summary
    python -m gold_desk.cli demo [--days 30] [--seed 7] [--data-root DIR]
    python -m gold_desk.cli replay   --date YYYY-MM-DD [--data-root DIR]
    python -m gold_desk.cli eod      --date YYYY-MM-DD [--data-root DIR]
    python -m gold_desk.cli events   [--kind K] [--limit N] [--json]
    python -m gold_desk.cli zen      [--refresh]     # free-model catalog status
    python -m gold_desk.cli veto-bench [--model ID] [--scenario clean|news|stale]
                                                    # OFFLINE veto research bench
    python -m gold_desk.cli price    [--json]         # live gold spot (free feeds)
    python -m gold_desk.cli news     [--json]         # gold news headlines (free RSS)
    python -m gold_desk.cli chat     [--json] [--message "..." | --stdin]
                                                    # chat with The Desk expert
    python -m gold_desk.cli drivers  [--json]        # real driver values (free feeds)
    python -m gold_desk.cli markets  [SECTOR] [--symbol BTC] [--json]
                                                    # multi-market board
                                                    # crypto/forex/commodities/
                                                    # indices/us/india/etfs/
                                                    # rates/volatility
                                                    # + movers (free feeds)
    python -m gold_desk.cli ask     "question" [--model ID] [--max-steps N]
                                                    # agent loop with desk+web tools
    python -m gold_desk.cli research ASSET [--depth 2] [--refresh]
                                                    # cited deep-research report
    python -m gold_desk.cli watch    [--once] [--force]  # L2 watchlist pass
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .constitution import load_constitution, validation_report
from .version import __version__

REPO_ROOT = Path(__file__).resolve().parents[2]


def cmd_validate(args) -> int:
    constitution = load_constitution(REPO_ROOT / "trading_constitution.yaml")
    print(validation_report(constitution))
    return 0 if not constitution.problems else 1


def cmd_constitution(args) -> int:
    """Emit a machine-readable constitution summary.

    The web deck's /api/desk/overview route shells out here (with --json) to
    surface the real BLOCKED count, phase, and trade_capable flag to the
    ConstitutionPanel — instead of the hardcoded "30 BLOCKED, phase=1" that
    was there before.
    """
    constitution = load_constitution(REPO_ROOT / "trading_constitution.yaml")
    if args.json:
        blocked = constitution.blocked_fields()
        out = {
            "ok": True,
            "blocked_count": len(blocked),
            "blocked_fields": blocked,
            "phase": constitution.phase,
            "trade_capable": constitution.trade_capable,
            "demo": constitution.demo,
            "instrument": constitution.instrument,
            "content_hash": constitution.content_hash,
            "file_hash": constitution.file_hash,
            "summary_line": constitution.summary_line(),
            "problems": list(constitution.problems),
        }
        print(json.dumps(out, sort_keys=True))
        return 0
    print(validation_report(constitution))
    return 0 if not constitution.problems else 1


def cmd_demo(args) -> int:
    from .demo import run_demo
    run_demo(days=args.days, seed=args.seed, data_root=Path(args.data_root),
             quiet=False)
    return 0


def cmd_replay(args) -> int:
    from .replay import replay_day, render_replay
    report = replay_day(args.data_root, args.date)
    if not report["event_count"]:
        print(f"no journal events for {args.date} under {args.data_root}")
        return 1
    print(render_replay(report))
    return 0


def cmd_eod(args) -> int:
    from .eod import eod_summary
    print(eod_summary(args.data_root, args.date))
    return 0


def cmd_events(args) -> int:
    """Journal events feed (used by the web deck + agent panel)."""
    from .events import Journal
    events = Journal.read_events(args.data_root)
    if args.kind:
        events = [e for e in events if e.get("kind") == args.kind]
    if args.reason_code:
        events = [e for e in events if e.get("reason_code") == args.reason_code]
    events = events[-args.limit:]
    if args.json:
        print(json.dumps({"ok": True, "count": len(events),
                          "events": events}, ensure_ascii=False))
        return 0
    print(f"JOURNAL EVENTS — last {len(events)}")
    print("=" * 60)
    for e in reversed(events[-args.limit:]):
        rc = e.get("reason_code") or ""
        print(f"  {e['ts'][:19]} {e['kind']:20s} {rc:18s} "
              f"{str(e.get('payload', ''))[:60]}")
    return 0


def cmd_zen(args) -> int:
    from .llm.zen_sync import sync_catalog
    catalog = sync_catalog(args.data_root, force=args.refresh)
    print("OPENCODE ZEN — FREE MODEL CATALOG")
    print("=" * 56)
    print(f"source        : {catalog.get('source')}")
    print(f"default veto  : {catalog.get('default')}")
    print(f"free models   : {len(catalog.get('models', {}))}")
    if catalog.get("zen_served"):
        print(f"zen serves    : {catalog['zen_served']} total (free+paid)")
    print(f"base url      : https://opencode.ai/zen/v1 (keyless)")
    print("")
    for mid, meta in sorted(catalog.get("models", {}).items()):
        flag = " [deprecated]" if meta.get("deprecated") else ""
        star = " *" if mid == catalog.get("default") else ""
        ctx = meta.get("context_window") or "?"
        print(f"  {mid:38s} ctx={ctx:>9}{flag}{star}")
    print("")
    print("catalog file   :", Path(args.data_root) / "zen-catalog.json")
    return 0


def cmd_veto_bench(args) -> int:
    from .llm.veto_bench import run_bench
    ok = run_bench(model=args.model, scenario=args.scenario,
                   data_root=Path(args.data_root), as_json=args.json,
                   timeout=args.timeout)
    return 0 if ok else 1


def cmd_price(args) -> int:
    from .data.feeds import fetch_spot
    spot = fetch_spot(args.data_root)
    if args.json:
        print(json.dumps(spot, sort_keys=True))
        return 0 if spot.get("ok") else 1
    if not spot.get("ok"):
        print("price feeds unreachable:", spot.get("error"))
        return 1
    print("GOLD SPOT (live, free feed)")
    print("=" * 40)
    print(f"price      : {spot['price']:.2f} {spot.get('currency', 'USD')}")
    if spot.get("prev_close"):
        print(f"prev close : {spot['prev_close']}")
    print(f"source     : {spot['source']}")
    import datetime
    ts = spot.get("market_time")
    if ts:
        when = datetime.datetime.fromtimestamp(ts, tz=datetime.timezone.utc)
        print(f"as of      : {when.strftime('%Y-%m-%d %H:%M UTC')}")
    print("note       : display telemetry only — not the decision-loop feed")
    return 0


def cmd_news(args) -> int:
    from .data.feeds import fetch_news
    news = fetch_news(args.data_root, limit=args.limit)
    if args.json:
        print(json.dumps(news, sort_keys=True))
        return 0 if news.get("ok") else 1
    if not news.get("ok"):
        print("news feed unreachable:", news.get("error"))
        return 1
    print("GOLD NEWS (Yahoo Finance RSS)")
    print("=" * 60)
    for i, item in enumerate(news.get("items", []), 1):
        print(f"{i:2d}. {item['title']}")
        print(f"    {item['published']}  {item['link'][:80]}")
    return 0


def cmd_chat(args) -> int:
    import sys as _sys
    from .llm.expert_chat import chat as run_chat, chat_stream as run_chat_stream
    from .llm.zen_client import LLMUnavailable

    if args.json:
        # machine mode: single message from --message or stdin transcript
        if args.stdin:
            payload = json.loads(_sys.stdin.read() or "{}")
            messages = payload.get("messages", [])
        else:
            messages = [{"role": "user", "content": args.message or ""}]

        if args.stream and getattr(args, "agent", False):
            # AGENT MODE: the pi-loop with desk+web tools, streamed as the
            # same NDJSON protocol plus tool / tool_result events.
            # events: start | tool | tool_result | reasoning | content | done | error
            from .agent.chat_stream import agent_chat_stream
            out = _sys.stdout
            for evt in agent_chat_stream(messages,
                                         data_root=args.data_root,
                                         model=args.model,
                                         max_steps=getattr(args, "max_steps", 10)):
                out.write(json.dumps(evt, sort_keys=True) + "\n")
                out.flush()
            return 0

        if args.stream:
            # NDJSON streaming mode: one JSON event per line, flushed immediately.
            # events: start | reasoning | content | done | error
            #     start    {"type":"start","model":..,"grounded":bool}
            #     reasoning {"type":"reasoning","delta":str}     (optional, many)
            #     content  {"type":"content","delta":str}        (many)
            #     done     {"type":"done","model":..,"latency_ms":int,"grounded":bool}
            #     error    {"type":"error","error":str}          (terminal)
            # terminal is exactly one of done | error
            out = _sys.stdout
            for evt in run_chat_stream(messages, data_root=args.data_root,
                                       model=args.model):
                out.write(json.dumps(evt, sort_keys=True) + "\n")
                out.flush()
            return 0

        try:
            out = run_chat(messages, data_root=args.data_root, model=args.model)
        except LLMUnavailable as e:
            print(json.dumps({"ok": False, "error": f"LLM_UNAVAILABLE: {e}"}))
            return 1
        print(json.dumps(out, sort_keys=True))
        return 0

    # interactive REPL (Hermes-style terminal chat)
    print()
    print("  THE DESK — 20-year gold veteran · free OpenCode Zen models")
    print("  grounded with live spot + news + your journal · education, not advice")
    print("  type 'exit' to quit, 'price' for the live spot")
    print()
    history: list[dict] = []
    while True:
        try:
            question = input("\033[38;5;214myou \u279c\033[0m ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not question:
            continue
        if question.lower() in ("exit", "quit", "q"):
            break
        if question.lower() == "price":
            from .data.feeds import fetch_spot
            spot = fetch_spot(args.data_root)
            if spot.get("ok"):
                print(f"  \033[38;5;214mdesk \u279c\033[0m spot {spot['price']:.2f} USD/oz ({spot['source']})")
            else:
                print("  \033[38;5;214mdesk \u279c\033[0m feeds unreachable")
            print()
            continue
        history.append({"role": "user", "content": question})
        try:
            out = run_chat(history, data_root=args.data_root, model=args.model)
        except LLMUnavailable as e:
            print(f"  \033[38;5;203mdesk \u279c the model is unreachable ({e}) — try again shortly\033[0m")
            print()
            continue
        print(f"  \033[38;5;214mdesk \u279c\033[0m {out['reply']}")
        print(f"  \033[2m[{out['model']} · {out['latency_ms']}ms]\033[0m")
        print()
        history.append({"role": "assistant", "content": out["reply"]})
        history = history[-20:]
    return 0


def cmd_drivers(args) -> int:
    from .data.driver_feeds import fetch_driver_values
    out = fetch_driver_values(args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True))
        return 0 if out.get("ok") else 1
    print("MARKET DRIVERS — REAL VALUES (free feeds)")
    print("=" * 56)
    live = out.get("live", {})
    for did in sorted(live.keys()):
        v = live[did]
        extra = f" (= {v['display_k']}k)" if v.get("display_k") else ""
        print(f"  {did:4s} {v['value']:>12} {v['unit']:<11} {v['source']}{extra}")
    un = out.get("unavailable", [])
    if un:
        print()
        print(f"  unavailable right now: {', '.join(un)} (simulated in the UI)")
    print()
    print("  D6/D7/D8/D12 have no free feeds — simulated, badged SIM in the UI")
    return 0


def _fmt_price(v, symbol: str | None = None, derived: bool = False) -> str:
    """Board price formatting. FX pairs ("=X") keep pip resolution
    (5dp below 10 / 3dp at-or-above 10, JPY-style); DERIVED reciprocal
    pairs print one digit finer (6dp below 1 / 5dp at-or-above —
    1/95.717 = 0.010447); everything else thousands-separated 2dp
    (>=1) / 4dp (<1)."""
    if not isinstance(v, (int, float)):
        return "n/a"
    if derived:
        return f"{v:,.6f}" if abs(v) < 1 else f"{v:,.5f}"
    if symbol and str(symbol).upper().endswith("=X"):
        return f"{v:,.5f}" if abs(v) < 10 else f"{v:,.3f}"
    return f"{v:,.4f}" if abs(v) < 1 else f"{v:,.2f}"


def cmd_markets(args) -> int:
    from .markets.board import fetch_board, fetch_detail

    if args.symbol:
        out = fetch_detail(args.symbol, args.data_root)
        if args.json:
            print(json.dumps(out, sort_keys=True))
            return 0 if out.get("ok") else 1
        if not out.get("ok"):
            print("markets detail failed:", out.get("error"))
            return 1
        derived = bool(out.get("derived"))
        print(f"{out['symbol']} — {out.get('name', '')}"
              f"  [{out.get('sector', '')}]")
        print("=" * 60)
        if derived:
            print(f"derived from: {out.get('derived_from', '?')} "
                  "(inverted reciprocal — price = 1/price)")
        print(f"price      : "
              f"{_fmt_price(out.get('price'), out['symbol'], derived)} "
              f"{out.get('currency', '')}")
        print(f"prev close : "
              f"{_fmt_price(out.get('prev_close'), out['symbol'], derived)}"
              " (1d)")
        chg, pct = out.get("change"), out.get("change_pct")
        if isinstance(chg, (int, float)) and isinstance(pct, (int, float)):
            change_s = (f"{_fmt_price(chg, out['symbol'], derived)} "
                        f"({pct:+.2f}%)")
        elif isinstance(pct, (int, float)):
            change_s = f"{pct:+.2f}%"
        else:
            change_s = "n/a"
        print(f"change (1d): {change_s}")
        r5 = out.get("range_5d_change_pct")
        print(f"change (5d): "
              f"{f'{r5:+.2f}%' if isinstance(r5, (int, float)) else 'n/a'}"
              "  [bar-derived: first-bar close → last close]")
        bars = out.get("bars") or []
        print(f"bars       : {len(bars)} x 30m (5d)")
        if bars:
            import datetime as _dt
            last = bars[-1]
            when = _dt.datetime.fromtimestamp(last["ts"] / 1000,
                                               tz=_dt.timezone.utc)
            print(f"last bar   : {when:%Y-%m-%d %H:%M} UTC  "
                  f"o={last['o']} h={last['h']} l={last['l']} c={last['c']}")
        return 0

    sector_keys = None
    if getattr(args, "sector", None):
        from .markets.registry import SECTORS
        k = str(args.sector).strip().lower()
        if k not in SECTORS:
            print(f"unknown sector: {args.sector}")
            print("available   :", ", ".join(SECTORS.keys()))
            return 1
        sector_keys = [k]

    board = fetch_board(args.data_root, sectors=sector_keys)
    if args.json:
        print(json.dumps(board, sort_keys=True))
        return 0 if board.get("ok") else 1
    if not board.get("ok"):
        print("markets board unreachable:", board.get("error"))
        return 1

    print("MARKETS BOARD — multi-market, free feeds")
    print("=" * 64)
    print(f"as of : {board.get('as_of', '?')}")
    if board.get("cache_hit"):
        print("cache : served from file cache (TTL 120s)")
    print()
    for sec in board.get("sectors", []):
        print(sec["label"].upper())
        print("-" * 64)
        for row in sec["rows"]:
            chg = row.get("change_pct")
            chg_s = (f"{chg:+.2f}%" if isinstance(chg, (int, float))
                     else "n/a")
            print(f"  {row['symbol']:13s} {str(row.get('name', ''))[:16]:16s} "
                  f"{_fmt_price(row.get('price'), row['symbol']):>12s} "
                  f"{chg_s:>8s}")
        print()
    # whole-market movers (round-3): Yahoo predefined screeners
    market = board.get("market_movers") or {}
    if market.get("gainers") or market.get("losers"):
        print("MARKET MOVERS — whole market (Yahoo screener), "
              "top 12 by daily change")
        print("-" * 64)
        if market.get("gainers"):
            print("  gainers : " + "  ".join(
                f"{m['symbol']} {m['change_pct']:+.2f}%"
                for m in market["gainers"]))
        if market.get("losers"):
            print("  losers  : " + "  ".join(
                f"{m['symbol']} {m['change_pct']:+.2f}%"
                for m in market["losers"]))
        print()
    # watchlist movers (round-2 "movers", renamed round-3)
    movers = board.get("watchlist_movers") or board.get("movers") or {}
    gainers = movers.get("gainers") or []
    losers = movers.get("losers") or []
    if gainers or losers:
        print("WATCHLIST MOVERS — registry symbols, top 5 by daily change")
        print("-" * 64)
        if gainers:
            print("  gainers : " + "  ".join(
                f"{m['symbol']} {m['change_pct']:+.2f}%" for m in gainers))
        if losers:
            print("  losers  : " + "  ".join(
                f"{m['symbol']} {m['change_pct']:+.2f}%" for m in losers))
        print()
    errors = board.get("errors") or []
    if errors:
        print("failed    :", ", ".join(errors))
    if not (market.get("gainers") or market.get("losers")):
        print("note      : whole-market movers need the Yahoo screener "
              "(unavailable); showing watchlist movers")
    return 0


def _agent_registry():
    """Full research registry: desk tools + crypto tools + web tools."""
    from .agent.desk_tools import desk_registry
    from .agent.assets import asset_tools
    from .agent.browse import browse_tools
    reg = desk_registry()
    for t in asset_tools():
        reg.register(t)
    for t in browse_tools():
        reg.register(t)
    return reg


def cmd_ask(args) -> int:
    from .agent.loop import run_agent
    result = run_agent(
        args.question,
        _agent_registry(),
        data_root=args.data_root,
        model=args.model,
        max_steps=args.max_steps,
        max_minutes=args.max_minutes,
    )
    if args.json:
        print(json.dumps({
            "ok": result.ok, "answer": result.answer, "model": result.model,
            "steps": result.steps, "tool_calls": result.tool_calls,
            "elapsed_ms": result.elapsed_ms, "status": result.status,
            "detail": result.detail, "run_id": result.run_id,
            "transcript": result.transcript_path,
        }, ensure_ascii=False, indent=1))
        return 0 if result.ok else 1
    print()
    print(result.answer)
    print()
    print(f"  [{result.model} · {result.steps} steps · "
          f"{result.tool_calls} tool calls · {result.elapsed_ms}ms · "
          f"status={result.status}]")
    if result.detail:
        print(f"  detail: {result.detail}")
    print()
    return 0 if result.ok else 1


def cmd_research(args) -> int:
    from .agent.research import research
    out = research(args.asset, data_root=args.data_root,
                   depth=args.depth, model=args.model,
                   refresh=args.refresh)
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1,
                         default=str))
        return 0 if out.get("ok") else 1
    if out.get("ok"):
        print("RESEARCH COMPLETE")
        print("=" * 60)
        print(f"asset    : {out['asset']}")
        print(f"report   : {out['report_path']}")
        print(f"sources  : {len(out.get('sources') or [])}")
        print(f"model    : {out.get('model')}")
        print(f"elapsed  : {out.get('elapsed_ms')} ms")
        ver = (out.get("verification") or {}).get("claims") or []
        if ver:
            print("verified claims:")
            for c in ver:
                mark = "OK " if c.get("verdict") == "verified" else "?? "
                print(f"  {mark}{c['claim'][:70]}")
    else:
        print(f"research failed: {out.get('status')} — {out.get('detail')}")
    return 0 if out.get("ok") else 1


def cmd_watch(args) -> int:
    from .agent.watch import watch_once, autonomy_level
    if args.force or args.once:
        out = watch_once(data_root=args.data_root, force=args.force)
        if args.json:
            print(json.dumps(out, ensure_ascii=False, default=str))
            return 0 if out.get("ok") else 1
        level = autonomy_level()
        print(f"WATCH PASS (autonomy L{level})")
        print("=" * 60)
        for r in out.get("assets") or []:
            mark = "OK " if r.get("ok") else "ERR"
            print(f"  {mark} {r['asset']:8s} {r.get('path') or r.get('status')}")
        if out.get("status"):
            print(f"  status: {out['status']}")
        return 0 if out.get("ok") else 1
    print("watch runs one pass with --once (or schedule it via crontab:")
    print("  15 8,16 * * 1-5  cd ~/gold-desk && "
          "python -m gold_desk.cli watch --once")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gold-desk",
                                     description="Gold Decision Harness v1")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate").set_defaults(func=cmd_validate)

    p_const = sub.add_parser("constitution",
                             help="constitution summary (BLOCKED count, phase, trade_capable)")
    p_const.add_argument("--json", action="store_true",
                        help="machine-readable result (used by the web deck)")
    p_const.set_defaults(func=cmd_constitution)

    p_demo = sub.add_parser("demo", help="run the synthetic end-to-end demo")
    p_demo.add_argument("--days", type=int, default=30)
    p_demo.add_argument("--seed", type=int, default=7)
    p_demo.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_demo.set_defaults(func=cmd_demo)

    p_rep = sub.add_parser("replay", help="replay a journaled day")
    p_rep.add_argument("--date", required=True)
    p_rep.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_rep.set_defaults(func=cmd_replay)

    p_eod = sub.add_parser("eod", help="end-of-day summary")
    p_eod.add_argument("--date", required=True)
    p_eod.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_eod.set_defaults(func=cmd_eod)

    p_eve = sub.add_parser("events", help="journal events feed (web deck)")
    p_eve.add_argument("--json", action="store_true")
    p_eve.add_argument("--kind", default=None,
                       help="filter by event kind (e.g. AgentRunFinished)")
    p_eve.add_argument("--reason-code", default=None, dest="reason_code")
    p_eve.add_argument("--limit", type=int, default=100)
    p_eve.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_eve.set_defaults(func=cmd_events)

    p_zen = sub.add_parser("zen", help="OpenCode Zen free-model catalog")
    p_zen.add_argument("--refresh", action="store_true",
                       help="force full rebuild from Zen + models.dev")
    p_zen.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_zen.set_defaults(func=cmd_zen)

    p_vb = sub.add_parser("veto-bench",
                          help="OFFLINE veto research bench (never the live loop)")
    p_vb.add_argument("--model", default=None,
                      help="model id (default: catalog default)")
    p_vb.add_argument("--scenario", default="clean",
                      choices=["clean", "news", "stale"])
    p_vb.add_argument("--json", action="store_true",
                      help="machine-readable result (used by the web deck)")
    p_vb.add_argument("--timeout", type=float, default=120.0,
                      help="veto completion timeout in seconds")
    p_vb.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_vb.set_defaults(func=cmd_veto_bench)

    p_price = sub.add_parser("price", help="live gold spot (free feeds)")
    p_price.add_argument("--json", action="store_true")
    p_price.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_price.set_defaults(func=cmd_price)

    p_news = sub.add_parser("news", help="gold news headlines (free RSS)")
    p_news.add_argument("--json", action="store_true")
    p_news.add_argument("--limit", type=int, default=12)
    p_news.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_news.set_defaults(func=cmd_news)

    p_chat = sub.add_parser("chat", help="chat with The Desk expert")
    p_chat.add_argument("--json", action="store_true",
                        help="machine mode (used by the web deck)")
    p_chat.add_argument("--message", default=None,
                        help="single message (with --json)")
    p_chat.add_argument("--stdin", action="store_true",
                        help="read {messages:[...]} transcript from stdin")
    p_chat.add_argument("--stream", action="store_true",
                        help="with --json: stream NDJSON events "
                             "(start|reasoning|content|done|error)")
    p_chat.add_argument("--agent", action="store_true",
                        help="with --json --stream: run the research agent "
                             "(desk + web tools, cited answers) instead of "
                             "plain expert chat")
    p_chat.add_argument("--max-steps", type=int, default=10, dest="max_steps",
                        help="agent mode: step cap (default 10)")
    p_chat.add_argument("--model", default=None)
    p_chat.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_chat.set_defaults(func=cmd_chat)

    p_drv = sub.add_parser("drivers", help="real driver values (free feeds)")
    p_drv.add_argument("--json", action="store_true")
    p_drv.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_drv.set_defaults(func=cmd_drivers)

    p_mk = sub.add_parser("markets",
                          help="multi-market board "
                               "(crypto/forex/commodities/indices/india/us)")
    p_mk.add_argument("sector", nargs="?", default=None,
                      help="filter to one sector "
                           "(crypto|forex|commodities|indices|india|us)")
    p_mk.add_argument("--symbol", default=None,
                      help="single-symbol detail view (any alias: btc, gold, "
                           "nifty, reliance, aapl, eur/usd ...) — shows 5d "
                           "OHLC bars instead of the board")
    p_mk.add_argument("--json", action="store_true")
    p_mk.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_mk.set_defaults(func=cmd_markets)

    p_ask = sub.add_parser("ask", help="agent loop: desk + web tools, $0")
    p_ask.add_argument("question")
    p_ask.add_argument("--model", default=None)
    p_ask.add_argument("--max-steps", type=int, default=12)
    p_ask.add_argument("--max-minutes", type=float, default=10.0)
    p_ask.add_argument("--json", action="store_true")
    p_ask.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_ask.set_defaults(func=cmd_ask)

    p_res = sub.add_parser("research", help="cited deep-research report")
    p_res.add_argument("asset")
    p_res.add_argument("--depth", type=int, default=2)
    p_res.add_argument("--model", default=None)
    p_res.add_argument("--refresh", action="store_true",
                        help="bypass the http cache where possible")
    p_res.add_argument("--json", action="store_true")
    p_res.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_res.set_defaults(func=cmd_research)

    p_watch = sub.add_parser("watch", help="L2 watchlist pass (opt-in)")
    p_watch.add_argument("--once", action="store_true")
    p_watch.add_argument("--force", action="store_true",
                         help="run even below L2 / inside quiet hours")
    p_watch.add_argument("--json", action="store_true")
    p_watch.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_watch.set_defaults(func=cmd_watch)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
