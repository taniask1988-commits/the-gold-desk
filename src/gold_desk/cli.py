"""CLI entrypoints.

    python -m gold_desk.cli validate                 # constitution report
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
    from .llm.expert_chat import chat as run_chat
    from .llm.zen_client import LLMUnavailable

    if args.json:
        # machine mode: single message from --message or stdin transcript
        if args.stdin:
            payload = json.loads(_sys.stdin.read() or "{}")
            messages = payload.get("messages", [])
        else:
            messages = [{"role": "user", "content": args.message or ""}]
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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="gold-desk",
                                     description="Gold Decision Harness v1")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate").set_defaults(func=cmd_validate)

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
    p_chat.add_argument("--model", default=None)
    p_chat.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_chat.set_defaults(func=cmd_chat)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
