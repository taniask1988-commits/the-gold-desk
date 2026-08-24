"""CLI entrypoints.

    python -m gold_desk.cli validate                 # constitution report
    python -m gold_desk.cli constitution [--json]     # constitution summary
    python -m gold_desk.cli demo [--days 30] [--seed 7] [--data-root DIR]
    python -m gold_desk.cli replay   --date YYYY-MM-DD [--data-root DIR]
    python -m gold_desk.cli eod      --date YYYY-MM-DD [--data-root DIR]
    python -m gold_desk.cli zen      [--refresh]     # free-model catalog status
    python -m gold_desk.cli veto-bench [--model ID] [--scenario clean|news|stale]
                                                    # OFFLINE veto research bench
    python -m gold_desk.cli price    [--json]         # live gold spot (free feeds)
    python -m gold_desk.cli news     [--json]         # gold news headlines (free RSS)
    python -m gold_desk.cli chat     [--json] [--message "..." | --stdin]
                                                    # chat with The Desk expert
    python -m gold_desk.cli drivers  [--json]        # real driver values (free feeds)
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
    p_chat.add_argument("--model", default=None)
    p_chat.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_chat.set_defaults(func=cmd_chat)

    p_drv = sub.add_parser("drivers", help="real driver values (free feeds)")
    p_drv.add_argument("--json", action="store_true")
    p_drv.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_drv.set_defaults(func=cmd_drivers)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
