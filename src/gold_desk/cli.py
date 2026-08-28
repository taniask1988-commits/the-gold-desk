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
    python -m gold_desk.cli desk SYMBOL [--model ID] [--json]
                                                    # multi-analyst desk: 5 personas
                                                    # (technician/macro/news/
                                                    # sentiment/risk) judge any
                                                    # symbol in parallel + a PM
                                                    # consensus (6 LLM calls)
    python -m gold_desk.cli markets-eco [--json]   # economic calendar (ECO —
                                                    # ForexFactory mirror,
                                                    # static fallback)
    python -m gold_desk.cli markets-news QUERY [--json]
                                                    # NSE-style news search:
                                                    # query → merged Yahoo RSS
    python -m gold_desk.cli news-sentiment "HEADLINE" [--json]
                                                    # R3-2: NLP sentiment score
                                                    # (polarity/magnitude/
                                                    # subjectivity + assets +
                                                    # relevance + novelty +
                                                    # LLM fallback)
    python -m gold_desk.cli news-sentiment --tape [--limit 20] [--json]
                                                    # score the live news tape
                                                    # (8 instrument feeds;
                                                    # local-only — the LLM
                                                    # second opinion is a
                                                    # single-headline feature)
    python -m gold_desk.cli risk [--returns JSON] [--positions JSON]
                                 [--benchmark-returns JSON] [--json]
                                                    # R3-2: VaR (parametric/
                                                    # historical/Monte-Carlo)
                                                    # + ES + beta + stress
                                                    # (GFC/COVID/2022)
    python -m gold_desk.cli backtest [--bars 1y] [--setup guess] [--seed 7]
                                     [--journal PATH] [--json]
                                                    # R3-2: GUESS setup vs 1y
                                                    # GC=F 1h bars — Sharpe/
                                                    # Sortino/MaxDD/Calmar/
                                                    # hit-rate/profit-factor
                                                    # + equity journal +
                                                    # buy-and-hold compare
    python -m gold_desk.cli portfolio [--method mv|rp|hrp]
                                      [--lookback 90d] [--symbols A,B,C]
                                      [--max-weight 0.4] [--lambda 2.0]
                                      [--returns JSON] [--json]
                                                    # R3-3: portfolio
                                                    # construction — mean-
                                                    # variance / risk parity
                                                    # (ERC) / HRP weights +
                                                    # risk contributions +
                                                    # diversification ratio
    python -m gold_desk.cli pnl [--source journal|ledger]
                                [--ledger PATH] [--json]
                                                    # R3-3: P&L attribution —
                                                    # by asset / by setup /
                                                    # by hour-of-day with
                                                    # session labels
                                                    # (journal reconstruction
                                                    # or a ledger file;
                                                    # a deterministic
                                                    # synthetic ledger when
                                                    # no file is given)
    python -m gold_desk.cli watch-loop [--dry-run] [--daemon]
                                       [--interval 300] [--status] [--json]
                                                    # R4-1: autonomous alert
                                                    # sweep — one pass or a
                                                    # daemon; fires price/%/
                                                    # ATR/volume/corr rules,
                                                    # journals ALERT_FIRED,
                                                    # pushes via Telegram
    python -m gold_desk.cli alerts [--ack EVENT_ID] [--json]
    python -m gold_desk.cli alerts-add --symbol GC=F --kind pct_move
                                       [--threshold 1.5] [--window 24]
                                       [--level L] [--k 2.5] [--other SYM]
                                       [--cooldown 60] [--json]
    python -m gold_desk.cli alerts-rm --id RULE_ID [--json]
                                                    # R4-1: alert rule CRUD +
                                                    # fired log + ack
"""
from __future__ import annotations

import argparse
import json
import sys
import time
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
        # per-symbol news (round-4): keyless Yahoo RSS headlines,
        # fail-soft — an empty feed (no NSE-listed coverage) is skipped
        news = out.get("news") or {}
        items = news.get("items") or []
        if items:
            print()
            print(f"NEWS — {out['symbol']} "
                  f"(Yahoo RSS, {len(items)} shown)")
            print("-" * 60)
            for it in items:
                when = it.get("published", "")
                if when:
                    when = when.split(",", 1)[-1].strip()
                    when = when.rsplit(" ", 1)[0]  # drop timezone word
                title = str(it.get("title", ""))[:68]
                print(f"  · [{when}] {title}")
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


def cmd_markets_eco(args) -> int:
    """ECO — this week's economic calendar (GAUNTLET-P13)."""
    from .markets.calendar import fetch_calendar
    out = fetch_calendar(args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("economic calendar unreachable:", out.get("error"))
        return 1
    src = out.get("source", "?")
    print("ECONOMIC CALENDAR — this week"
          f"  [{src} feed]"
          f"  week of {out.get('week_start', '?')}")
    if out.get("note"):
        print(f"  note: {out['note']}")
    print("=" * 64)
    import datetime as _dt
    day = None
    mark = {"high": "###", "medium": "##", "low": "#"}
    for ev in out.get("events", []):
        t = _dt.datetime.fromtimestamp(ev["ts"] / 1000,
                                       tz=_dt.timezone.utc)
        if t.date() != day:
            day = t.date()
            print()
            print(day.strftime("%A %Y-%m-%d").upper())
            print("-" * 64)
        dot = mark.get(ev.get("impact", "low"), "#")
        extra = ""
        if ev.get("forecast"):
            extra = f"  fcst {ev['forecast']}"
        if ev.get("previous"):
            extra += f"  prev {ev['previous']}"
        print(f"  {dot} {t:%H:%M} {ev.get('country', '??'):4s} "
              f"{str(ev.get('title', ''))[:44]}{extra}")
    if not out.get("events"):
        print("  (no events served this week)")
    return 0


def cmd_markets_news(args) -> int:
    """NSE-style news search (GAUNTLET-P13; topic pass GAUNTLET-P15)."""
    from .markets.news_search import search_news
    out = search_news(" ".join(args.query), args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("news search failed:", out.get("error", "feeds unreachable"))
        return 1
    q = out.get("query", "")
    matched = out.get("matched") or []
    if out.get("topic"):
        tag = f"topic: headline text for \"{q}\""
    elif matched:
        tag = "matched: " + ", ".join(matched)
    else:
        tag = "none — general feed"
    print(f"NEWS SEARCH — \"{q}\"  ({tag})")
    print("=" * 64)
    items = out.get("items", [])
    if not items:
        print("  (no headlines served)")
    for it in items:
        when = str(it.get("published", ""))
        if when:
            when = when.split(",", 1)[-1].strip()
            when = when.rsplit(" ", 1)[0]  # drop the timezone word
        print(f"  [{when}] {str(it.get('title', ''))[:64]}")
        print(f"           — {it.get('source', '')}")
    return 0


# ---------------- R2-1 institutional data plane (keyless superset) ----------------

def cmd_markets_fundamentals(args) -> int:
    """markets-fundamentals SYMBOL — 8 quarters of PIT GAAP fundamentals
    (SEC XBRL primary, Yahoo timeseries fallback), accession-cited."""
    from .markets.institutional import fetch_fundamentals
    out = fetch_fundamentals(args.symbol, data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("fundamentals unreachable for", args.symbol, ":", out.get("error"))
        return 1
    print(f"FUNDAMENTALS — {out['symbol']}  "
          f"[source: {out.get('source')}, CIK {out.get('cik') or 'n/a'}]")
    print("=" * 64)
    print(f"latest quarter: {out.get('latest_quarter')}  "
          f"(n_quarters: {out.get('n_quarters')})")
    print()
    print(f"{'fp':4s} {'fy':6s} {'filed':12s} {'revenue':>16s} "
          f"{'net_income':>16s} {'eps_dil':>8s}  accession")
    print("-" * 80)
    for p in out.get("periods") or []:
        def _b(v):
            return f"{v/1e9:,.2f}B" if isinstance(v, (int, float)) \
                and abs(v) >= 1e9 else (f"{v:,.0f}" if isinstance(v,
                                                                   (int, float)) else "-")
        eps = p.get("eps_diluted")
        eps_s = f"{eps:.2f}" if isinstance(eps, (int, float)) else "-"
        print(f"{p.get('fp','?'):4s} {str(p.get('fy','')):6s} "
              f"{p.get('filed','?'):12s} {_b(p.get('revenue')):>16s} "
              f"{_b(p.get('net_income')):>16s} {eps_s:>8s}  "
              f"{p.get('accn','?')}")
    return 0


def cmd_markets_13f(args) -> int:
    """markets-13f [CIK] — latest 13F-HR holdings (default Berkshire
    0001067983). Shows top-10 positions, total disclosed value, top10 %."""
    from .markets.institutional import fetch_institutional
    out = fetch_institutional(cik=args.cik, data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("13F unreachable:", out.get("error"))
        return 1
    print(f"13F INSTITUTIONAL HOLDINGS — {out.get('fund')}")
    print("=" * 64)
    print(f"filed: {out.get('filed')}  accession: {out.get('accession')}")
    print(f"n_positions: {out.get('n_positions')}  "
          f"total_value: ${out.get('total_value'):,.0f}  "
          f"top10_pct: {out.get('top10_pct')}%")
    print()
    print(f"{'issuer':32s} {'value':>16s} {'shares':>14s} {'type':4s}  cusip")
    print("-" * 80)
    positions = sorted(out.get("positions") or [],
                       key=lambda p: p.get("value", 0), reverse=True)[:10]
    for p in positions:
        val = p.get("value", 0)
        shrs = p.get("shares", 0)
        val_s = f"${val/1e9:,.2f}B" if val >= 1e9 else f"${val:,.0f}"
        shrs_s = f"{shrs/1e6:,.2f}M" if shrs >= 1e6 else f"{shrs:,.0f}"
        print(f"{(p.get('issuer') or '')[:32]:32s} {val_s:>16s} "
              f"{shrs_s:>14s} {(p.get('type') or 'SH'):4s}  "
              f"{p.get('cusip','')}")
    return 0


def cmd_markets_curve(args) -> int:
    """markets-curve — Treasury daily yield curve (1M-30Y)."""
    from .markets.institutional import fetch_yield_curve
    out = fetch_yield_curve(data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("yield curve unreachable:", out.get("error"))
        return 1
    print(f"US TREASURY YIELD CURVE — {out.get('latest_date')}")
    print("=" * 64)
    curve = out.get("curve") or {}
    for tenor in ("1M", "3M", "6M", "1Y", "2Y", "5Y", "10Y", "20Y", "30Y"):
        v = curve.get(tenor)
        if isinstance(v, (int, float)):
            print(f"  {tenor:4s} {v:.3f}%")
    print()
    print(f"last 5 days:")
    for d in out.get("history_last_5") or []:
        ten = d.get("date", "?")
        y10 = d.get("10Y")
        y2 = d.get("2Y")
        print(f"  {ten}  2Y={y2}  10Y={y10}")
    return 0


def cmd_markets_sentiment(args) -> int:
    """markets-sentiment — alternative.me Fear & Greed index (30d history)."""
    from .markets.institutional import fetch_crypto_sentiment
    out = fetch_crypto_sentiment(data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("F&G unreachable:", out.get("error"))
        return 1
    latest = out.get("latest") or {}
    print(f"CRYPTO FEAR & GREED — {latest.get('value')} "
          f"({latest.get('classification')})")
    print("=" * 64)
    print(f"history ({out.get('n_days')} days):")
    for h in (out.get("history") or [])[:10]:
        from datetime import datetime as _dt
        ts = h.get("ts")
        if ts:
            when = _dt.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        else:
            when = "?"
        print(f"  {when} {h.get('value'):>4} {h.get('classification')}")
    return 0


def cmd_markets_onchain(args) -> int:
    """markets-onchain — blockchain.info BTC 24h stats."""
    from .markets.institutional import fetch_onchain
    out = fetch_onchain(data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("onchain unreachable:", out.get("error"))
        return 1
    print(f"BTC ON-CHAIN — {out.get('as_of')}")
    print("=" * 64)
    print(f"  market price : ${out.get('market_price_usd'):,.2f}")
    print(f"  hash rate    : {out.get('hash_rate'):,.0f} H/s")
    print(f"  n_tx         : {out.get('n_tx'):,}")
    print(f"  n_btc_mined : {out.get('n_btc_mined'):,.0f}")
    print(f"  min/blocks  : {out.get('minutes_between_blocks'):.2f}")
    print(f"  total_fees   : {out.get('total_fees_btc'):,.0f} BTC")
    return 0


def cmd_markets_social(args) -> int:
    """markets-social [SUB] — Reddit RSS feed (asset-class routed)."""
    from .markets.institutional import fetch_social
    out = fetch_social(symbol=args.symbol, data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("social unreachable:", out.get("error"))
        return 1
    print(f"SOCIAL — r/{out.get('sub')}  (n={out.get('n')})")
    print("=" * 64)
    for it in (out.get("items") or [])[:10]:
        pub = it.get("published", "")
        if pub:
            pub = pub.split("T")[0]
        print(f"  [{pub}] {str(it.get('title',''))[:70]}")
    return 0


def cmd_markets_institutional(args) -> int:
    """markets-institutional SYMBOL — the 7-slice aggregator (fail-soft
    per slice). The fundamentals/curve/sentiment slices are required;
    institutional_top is fund-positioning (Berkshire default)."""
    from .markets.institutional import gather_institutional_context
    out = gather_institutional_context(args.symbol, data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    print(f"INSTITUTIONAL CONTEXT — {args.symbol}")
    print("=" * 64)
    slices = out.get("slices") or {}
    for key in ("fundamentals", "institutional_top", "macro_curve",
                "crypto_sentiment", "onchain", "global_crypto", "social"):
        s = slices.get(key) or {}
        ok = s.get("ok")
        mark = "OK " if ok else "ERR"
        line = f"  {mark} {key:18s}"
        if ok:
            if key == "fundamentals":
                line += f"  n={s.get('n_quarters')} source={s.get('source')}"
            elif key == "institutional_top":
                line += f"  n={s.get('n_positions')} total=${s.get('total_value'):,.0f}"
            elif key == "macro_curve":
                c = s.get("curve") or {}
                line += f"  10Y={c.get('10Y')}  latest={s.get('latest_date')}"
            elif key == "crypto_sentiment":
                l = s.get("latest") or {}
                line += f"  {l.get('value')} {l.get('classification')}"
            elif key == "onchain":
                line += f"  BTC=${s.get('market_price_usd'):,.2f}"
            elif key == "global_crypto":
                line += f"  BTC dom={s.get('btc_dominance')}%"
            elif key == "social":
                line += f"  r/{s.get('sub')} n={s.get('n')}"
        else:
            line += f"  err: {s.get('error', 'unknown')}"
        print(line)
    return 0


# -------------------------------------- R2-2 quant + verified snapshot ---


def cmd_markets_quant(args) -> int:
    """markets-quant SYMBOL — the numpy-free indicator battery
    (RSI14, MACD, BBands, ATR14/ATR%, realized_vol_20d, vol_regime,
    SMA{20,50,200}, EMA{12,26}, ADX14, Stoch, CCI20, OBV). Computed
    deterministically from DAILY bars; no LLM involved.

    Uses fetch_daily_bars (range=1y&interval=1d) so the indicator
    windows (RSI14, MACD 26+9, BBands 20, ATR14, realized_vol_20d) use
    the proper daily resolution — a 5d/30m bar series would render
    these indicators meaningless."""
    from .markets.board import fetch_daily_bars
    from .features.quant import compute_indicators
    bars = fetch_daily_bars(args.symbol, data_root=args.data_root)
    if not bars:
        if args.json:
            print(json.dumps({"ok": False, "symbol": args.symbol,
                              "error": "daily bars fetch failed"}))
            return 1
        print(f"markets-quant: daily bars fetch failed for {args.symbol}")
        return 1
    out = compute_indicators(bars)
    out["symbol"] = args.symbol.upper()
    # name + sector from a fetch_detail lookup (lightweight; cached)
    try:
        from .markets.board import fetch_detail
        d = fetch_detail(args.symbol, data_root=args.data_root)
        out["name"] = d.get("name")
        out["sector"] = d.get("sector")
    except Exception:
        pass
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    print(f"QUANT INDICATORS — {out['symbol']}  ({out.get('bar_count')}"
          f" bars)")
    print("=" * 64)
    if not out.get("ok"):
        print(f"  err: {out.get('error')}")
        return 1
    print(f"  last close   : {out.get('last_close')}")
    print(f"  RSI(14)      : {out.get('rsi14')}")
    m = out.get("macd") or {}
    print(f"  MACD         : line={m.get('line')}  signal="
          f"{m.get('signal')}  hist={m.get('hist')}")
    b = out.get("bbands") or {}
    print(f"  Bollinger(20): upper={b.get('upper')}  mid={b.get('middle')}"
          f"  lower={b.get('lower')}  width={b.get('width')}"
          f"  pct_b={b.get('pct_b')}")
    print(f"  ATR(14)      : {out.get('atr14')}  ({out.get('atr_pct')}%"
          f" of price)")
    print(f"  realized vol : {out.get('realized_vol_20d')}  "
          f"→ regime {out.get('vol_regime')}")
    s = out.get("sma") or {}
    print(f"  SMA          : 20={s.get('20')}  50={s.get('50')}  "
          f"200={s.get('200')}")
    e = out.get("ema") or {}
    print(f"  EMA          : 12={e.get('12')}  26={e.get('26')}")
    print(f"  ADX(14)      : {out.get('adx14')}")
    st = out.get("stoch") or {}
    print(f"  Stoch(14,3)  : %K={st.get('k')}  %D={st.get('d')}")
    print(f"  CCI(20)      : {out.get('cci20')}")
    print(f"  OBV          : {out.get('obv')}")
    return 0


def cmd_markets_snapshot(args) -> int:
    """markets-snapshot SYMBOL — deterministic verified snapshot (no
    LLM, no network beyond the bar fetch). The source-of-truth block
    the technician persona treats as ground truth for any exact
    numeric claim; closes the TradingAgents v0.3.1
    market_data_validator.py:1-25 discipline keyless.

    Uses fetch_daily_bars (range=1y&interval=1d) so the 5d/20d/63d
    change pct fields and the indicator windows use the proper daily
    resolution."""
    from .markets.board import fetch_daily_bars
    from .features.verified_snapshot import build_verified_snapshot
    bars = fetch_daily_bars(args.symbol, data_root=args.data_root)
    if not bars:
        if args.json:
            print(json.dumps({"ok": False, "symbol": args.symbol,
                              "error": "daily bars fetch failed"}))
            return 1
        print(f"markets-snapshot: daily bars fetch failed for "
              f"{args.symbol}")
        return 1
    # SPY benchmark bars for beta (fail-soft — uses daily bars so the
    # 63-day beta window sees DAILY log-returns, not the 30m bars that
    # fetch_detail ships over 5 days)
    bench_bars: list[dict] = []
    try:
        bench_bars = fetch_daily_bars("SPY", data_root=args.data_root)
    except Exception:
        pass
    snap = build_verified_snapshot(
        args.symbol.upper(), bars,
        indicators=None, benchmark_bars=bench_bars)
    if args.json:
        print(json.dumps(snap, sort_keys=True, default=str))
        return 0 if snap.get("ok") else 1
    print(f"VERIFIED SNAPSHOT — {snap.get('symbol')}")
    print("=" * 64)
    if not snap.get("ok"):
        print(f"  err: {snap.get('error', 'unknown')}")
        return 1
    print(f"  as_of           : {snap.get('as_of')}")
    print(f"  last_close      : {snap.get('last_close')}")
    print(f"  last_change_pct : {snap.get('last_change_pct')}")
    print(f"  5d / 20d / 63d  : {snap.get('change_pct_5d')} / "
          f"{snap.get('change_pct_20d')} / {snap.get('change_pct_63d')}")
    print(f"  atr14 / atr_pct : {snap.get('atr14_value')} / "
          f"{snap.get('atr_pct')}")
    print(f"  realized_vol_20d: {snap.get('realized_vol_20d')}")
    print(f"  RSI14           : {snap.get('rsi14')}")
    print(f"  MACD hist       : {snap.get('macd_hist')}")
    print(f"  BB %b           : {snap.get('bb_pct_b')}")
    print(f"  volume last/avg : {snap.get('volume_last')} / "
          f"{snap.get('volume_avg_20d')}")
    r = snap.get("regime_labels") or {}
    print(f"  regime          : trend={r.get('trend')}  vol="
          f"{r.get('vol')}  breakout={r.get('breakout')}")
    print(f"  beta vs SPY     : {snap.get('benchmark_beta')}")
    return 0


def cmd_markets_beta(args) -> int:
    """markets-beta SYMBOL [BENCH] — OLS regression symbol ~ benchmark
    over daily log-returns (default 63-day window, default bench SPY).
    Returns beta, alpha, r_squared, correlation, n.

    Uses fetch_daily_bars (range=1y&interval=1d) for both symbol and
    benchmark — daily log-returns need daily closes history, which
    fetch_detail's 5d/30m bars don't carry."""
    from .markets.board import fetch_daily_bars
    from .features.quant import compute_beta
    bench = args.bench or "SPY"
    sym_bars = fetch_daily_bars(args.symbol, data_root=args.data_root)
    bench_bars = fetch_daily_bars(bench, data_root=args.data_root)
    if not sym_bars or not bench_bars:
        if args.json:
            print(json.dumps({"ok": False, "symbol": args.symbol,
                              "benchmark": bench,
                              "error": "daily bars fetch failed"}))
            return 1
        print(f"markets-beta: daily bars fetch failed for "
              f"{args.symbol if not sym_bars else bench}")
        return 1
    out = compute_beta(sym_bars, bench_bars, window=args.window)
    out["ok"] = True
    out["symbol"] = args.symbol.upper()
    out["benchmark"] = bench.upper()
    out["window"] = args.window
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print(f"BETA — {out['symbol']} vs {out['benchmark']}  "
          f"(window={out['window']}, n={out['n']})")
    print("=" * 64)
    print(f"  beta        : {out.get('beta')}")
    print(f"  alpha       : {out.get('alpha')}")
    print(f"  r_squared   : {out.get('r_squared')}")
    print(f"  correlation : {out.get('correlation')}")
    return 0


def cmd_markets_corr(args) -> int:
    """markets-corr SYM1,SYM2,SYM3 — symmetric correlation matrix
    across the comma-listed symbols, daily log-returns, default
    63-day window. Uses fetch_daily_bars for daily close history."""
    from .markets.board import fetch_daily_bars
    from .features.quant import compute_correlation_matrix
    syms = [s.strip().upper() for s in args.symbols.split(",")
            if s.strip()]
    if len(syms) < 2:
        if args.json:
            print(json.dumps({"ok": False, "error":
                              "need ≥2 symbols (comma-separated)"}))
        else:
            print("markets-corr: need ≥2 symbols (comma-separated)")
        return 1
    # fetch daily bars per symbol (fail-soft per symbol)
    bars_map: dict[str, list[dict]] = {}
    for s in syms:
        try:
            bars_map[s] = fetch_daily_bars(s, data_root=args.data_root)
        except Exception:
            bars_map[s] = []
    out = {"ok": True, "symbols": syms, "window": args.window,
           "matrix": {}}
    # build the matrix directly here (the public helper uses the
    # board pattern's 5d/30m bars; we want daily bars instead)
    import math as _math
    closes_map: dict[str, list[float]] = {}
    for s in syms:
        closes_map[s] = [b["c"] for b in bars_map[s] if b.get("c")]
    matrix: dict[str, dict[str, float | None]] = {s: {} for s in syms}
    for i, si in enumerate(syms):
        for sj in syms[i:]:
            if si == sj:
                matrix[si][sj] = 1.0
                matrix[sj][si] = 1.0
                continue
            s_rets = []
            for k in range(1, len(closes_map[si])):
                a, b = closes_map[si][k - 1], closes_map[si][k]
                if a > 0 and b > 0:
                    s_rets.append(_math.log(b / a))
            b_rets = []
            for k in range(1, len(closes_map[sj])):
                a, b = closes_map[sj][k - 1], closes_map[sj][k]
                if a > 0 and b > 0:
                    b_rets.append(_math.log(b / a))
            sr = s_rets[-args.window:]
            br = b_rets[-args.window:]
            n = min(len(sr), len(br))
            sr, br = sr[-n:], br[-n:]
            if n < 2:
                matrix[si][sj] = None
                matrix[sj][si] = None
                continue
            ms = sum(sr) / n
            mb = sum(br) / n
            cov = sum((sr[i] - ms) * (br[i] - mb)
                      for i in range(n)) / n
            vs = sum((sr[i] - ms) ** 2 for i in range(n)) / n
            vb = sum((br[i] - mb) ** 2 for i in range(n)) / n
            sds = _math.sqrt(vs) if vs > 0 else 0.0
            sdb = _math.sqrt(vb) if vb > 0 else 0.0
            if sds == 0 or sdb == 0:
                matrix[si][sj] = None
                matrix[sj][si] = None
                continue
            r = max(-1.0, min(1.0, cov / (sds * sdb)))
            matrix[si][sj] = round(r, 6)
            matrix[sj][si] = round(r, 6)
    out["matrix"] = matrix
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print(f"CORRELATION MATRIX — {','.join(out.get('symbols', []))}  "
          f"(window={out.get('window')})")
    print("=" * 64)
    syms = out.get("symbols") or []
    matrix = out.get("matrix") or {}
    # header
    print("        " + "  ".join(f"{s:>10s}" for s in syms))
    for s in syms:
        row = matrix.get(s) or {}
        cells = "  ".join(
            f"{(row.get(s2) if row.get(s2) is not None else 0):>10.4f}"
            for s2 in syms)
        print(f"{s:>8s} {cells}")
    return 0


def cmd_markets_multi(args) -> int:
    """markets-multi — R3-1 Build 1 multi-asset live monitor (R4-2: now
    24 instruments via --all / --symbols). Default = the 8-instrument
    watchlist (backward compat); per-asset session VWAP + session-relative
    % move; fail-soft per asset.
    """
    from .markets.multi_asset import MultiAssetMonitor, INSTRUMENT_ORDER
    mon = MultiAssetMonitor(data_root=args.data_root,
                            symbols=getattr(args, "symbols", None),
                            all=getattr(args, "all", False))
    out = mon.snapshot()
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("multi-asset monitor failed:", out.get("error"))
        return 1
    universe_n = len(assets) if assets else 0
    label = (f"{universe_n} instruments" if universe_n != 8
             else "8 instruments (keyless Yahoo)")
    print(f"MULTI-ASSET MONITOR — {label}")
    print("=" * 72)
    print(f"as of : {out.get('as_of', '?')}"
          + ("   (cached)" if out.get("cache_hit") else ""))
    print()
    assets = out.get("assets") or {}
    print(f"{'symbol':<10s}{'name':<18s}{'price':>12s}"
          f"{'1d %':>9s}{'sess':>10s}{'vwap':>12s}{'rel %':>9s}")
    print("-" * 72)
    # iterate the snapshot's own key order (watchlist order or universe order)
    order = [s for s in (out.get("symbols") or INSTRUMENT_ORDER)
             if s in assets] or list(assets)
    for sym in order:
        a = assets.get(sym) or {}
        if not a.get("live"):
            err = a.get("error", "fetch failed")
            print(f"{sym:<10s}{a.get('name',''):<18s}"
                  f"{'-':>12s}{'-':>9s}{'ERROR':>10s}{'-':>12s}"
                  f"{'-':>9s}   ({err})")
            continue
        price = a.get("price")
        price_s = f"{price:.5f}" if isinstance(price, (int, float)) else "n/a"
        chg = a.get("change_pct")
        chg_s = f"{chg:+.2f}%" if isinstance(chg, (int, float)) else "n/a"
        sess = a.get("session", "?")
        vwap = a.get("session_vwap")
        vwap_s = f"{vwap:.5f}" if isinstance(vwap, (int, float)) else "n/a"
        rel = a.get("session_relative_pct")
        rel_s = f"{rel:+.3f}%" if isinstance(rel, (int, float)) else "n/a"
        print(f"{sym:<10s}{a.get('name',''):<18s}"
              f"{price_s:>12s}{chg_s:>9s}{sess:>10s}"
              f"{vwap_s:>12s}{rel_s:>9s}")
    errs = out.get("errors") or []
    if errs:
        print()
        print(f"errors (fail-soft, not fatal): {', '.join(errs)}")
    return 0


def cmd_markets_multi_corr(args) -> int:
    """markets-multi-corr — R3-1 Build 1: cross-asset correlation matrix
    (Pearson or Spearman) over rolling 30/60/90-day daily log-returns."""
    from .markets.multi_asset import MultiAssetMonitor, INSTRUMENT_ORDER
    mon = MultiAssetMonitor(data_root=args.data_root)
    out = mon.compute_correlation(window=args.window,
                                  method=args.method)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("correlation matrix failed:", out.get("error"))
        return 1
    print(f"CORRELATION MATRIX — {out.get('method','?').upper()} "
          f"window={out.get('window','?')}d")
    print("=" * 72)
    syms = out.get("symbols") or []
    matrix = out.get("matrix") or {}
    print("        " + "  ".join(f"{s:>9s}" for s in syms))
    for s in syms:
        row = matrix.get(s) or {}
        cells = "  ".join(
            (f"{row[s2]:>9.4f}"
             if isinstance(row.get(s2), (int, float))
             else f"{'n/a':>9s}")
            for s2 in syms)
        print(f"{s:>8s} {cells}")
    # D3: surface dropped symbols / insufficient-overlap pairs — null
    # cells are rendered "n/a" above, never as a fake 0.0000.
    errs = out.get("errors") or []
    if errs:
        parts = []
        for e in errs:
            if not isinstance(e, dict) or not e.get("symbol"):
                continue
            if e.get("reason") == "daily_closes_fetch_failed":
                parts.append(e["symbol"])
            else:
                parts.append(f"{e['symbol']}<->{e.get('pair', '?')}"
                             f" ({e.get('reason', '?')})")
        if parts:
            print()
            print("WARNING: degraded correlation matrix — "
                  + ", ".join(parts))
    return 0


def cmd_account_alpaca(args) -> int:
    """account-alpaca — R3-1 Build 2: Alpaca paper account summary
    (balance, buying power, positions, open orders, today P&L).
    Fail-closed when ALPACA_PAPER_KEY/SECRET are missing — the user
    must paste paper creds (free) into the constitution or env.
    """
    from .account_alpaca import AlpacaPaperAccount
    if not AlpacaPaperAccount.available():
        if args.json:
            print(json.dumps({"ok": False,
                              "reason_code": "ALPACA_CREDS_MISSING",
                              "blocked": "CONSTITUTION_BLOCKED",
                              "message": "Set ALPACA_PAPER_KEY + "
                                         "ALPACA_PAPER_SECRET env vars "
                                         "(paper keys are free at "
                                         "alpaca.markets)"}))
        else:
            print("ALPACA PAPER ACCOUNT — CONSTITUTION_BLOCKED")
            print("missing ALPACA_PAPER_KEY / ALPACA_PAPER_SECRET env vars.")
            print("paper keys are free at alpaca.markets — set both env "
                  "vars or paste them into the constitution.")
        return 1
    acct = AlpacaPaperAccount()
    out = acct.summary(timeout=args.timeout)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("alpaca account unreachable:", out.get("error"))
        return 1
    print("ALPACA PAPER ACCOUNT")
    print("=" * 60)
    acc = out.get("account") or {}
    print(f"status        : {acc.get('status','?')}")
    print(f"equity        : ${acc.get('equity','?')}")
    print(f"cash          : ${acc.get('cash','?')}")
    print(f"buying power  : ${acc.get('buying_power','?')}")
    print(f"last equity   : ${acc.get('last_equity','?')}")
    pnl = acc.get("unrealized_pl_today")
    if isinstance(pnl, (int, float)):
        print(f"today P&L     : ${pnl:.2f} ({acc.get('unrealized_plpc_today',0)*100:+.2f}%)")
    pos = out.get("positions") or []
    print()
    print(f"OPEN POSITIONS ({len(pos)})")
    print("-" * 60)
    for p in pos:
        print(f"  {p.get('symbol',''):<8s} {p.get('qty',''):>6s} @ "
              f"${p.get('avg_entry_price','?')}  "
              f"current=${p.get('current_price','?')}  "
              f"P&L=${p.get('unrealized_pl','?')}")
    orders = out.get("orders") or []
    print()
    print(f"OPEN ORDERS ({len(orders)})")
    print("-" * 60)
    for o in orders[:10]:
        print(f"  {o.get('id','')[:10]} {o.get('side',''):<5s} "
              f"{o.get('symbol',''):<8s} {o.get('qty','')} "
              f"{o.get('type','')} @ {o.get('limit_price') or o.get('stop_price') or 'mkt'}  "
              f"[{o.get('status','')}]")
    return 0


def _polarity_gauge(polarity: float, width: int = 21) -> str:
    """ASCII gauge from −1 (all ◀) to +1 (all ▶), center-marked."""
    p = max(-1.0, min(1.0, polarity))
    center = width // 2
    filled = int(round((p + 1.0) / 2.0 * (width - 1)))
    cells = ["·"] * width
    for i in range(width):
        if i < filled and i > center - 1 and p >= 0:
            cells[i] = "▓"
        elif i >= filled and i < center + 1 and p < 0:
            cells[i] = "▓"
    cells[center] = "│"
    return "[" + "".join(cells) + f"] {p:+.3f}"


def cmd_news_sentiment(args) -> int:
    """news-sentiment — R3-2 Build 3: NLP sentiment for one headline or
    the live news tape. Local lexicon (polarity/magnitude/subjectivity) +
    8-instrument asset detection + relevance + novelty, with a
    fail-closed Zen free-tier LLM second opinion for ambiguous stories.
    """
    from .markets.news_sentiment import NewsSentimentAnalyzer, score_tape
    if args.tape:
        # bulk tape scoring is local-only (see score_tape docstring): a
        # 20-story tape must never fan out 20 sequential LLM second opinions
        out = score_tape(data_root=args.data_root, limit=args.limit,
                         llm_enabled=False)
        if args.json:
            print(json.dumps(out, sort_keys=True, default=str))
            return 0 if out.get("ok") else 1
        if not out.get("ok"):
            print("news tape unreachable:", out.get("error", "all feeds failed"))
            return 1
        print(f"NEWS SENTIMENT TAPE — {out.get('n_feeds', 0)}/"
              f"{out.get('n_feeds_requested', 8)} feeds · "
              f"{out.get('n_stories', 0)} stories scored")
        print("=" * 72)
        for s in out.get("stories") or []:
            pol = s.get("polarity", 0.0)
            assets = ", ".join(a.get("symbol", "?") for a in s.get("assets") or [])
            print(f"{pol:+.3f} nov={s.get('novelty', 0):.2f} "
                  f"[{s.get('feed_symbol', '?'):<9s}] "
                  f"{(s.get('headline') or '')[:60]}  {{{assets}}}")
        return 0

    headline = (args.headline or "").strip()
    if not headline:
        if args.json:
            print(json.dumps({"ok": False, "error": "no headline "
                              "(pass a quoted headline or --tape)"}))
        else:
            print("news-sentiment: pass a quoted headline or --tape")
        return 1
    analyzer = NewsSentimentAnalyzer(data_root=args.data_root,
                                     llm_enabled=not args.no_llm)
    out = analyzer.score(headline)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("news-sentiment failed:", out.get("error"))
        return 1
    print("NEWS SENTIMENT — local lexicon + LLM fallback (R3-2 · R4-3)")
    print("=" * 64)
    print(f"headline    : {out.get('headline')}")
    print(f"polarity    : {_polarity_gauge(out.get('polarity', 0.0))}"
          f"  ({out.get('label')})")
    print(f"magnitude   : {out.get('magnitude', 0):.3f}    "
          f"subjectivity: {out.get('subjectivity', 0):.3f}")
    print(f"novelty     : {out.get('novelty', 0):.3f}    "
          f"semantic   : {out.get('semantic_novelty', out.get('novelty', 0)):.3f}    "
          f"relevance   : {out.get('relevance', 0):.3f}")
    ev_conf = out.get('event_confidence', 0.0)
    ev_matched = out.get('event_matched') or []
    print(f"event       : {out.get('event', 'other')} "
          f"(confidence {ev_conf:.2f})"
          + (f"  [{', '.join(ev_matched[:4])}]" if ev_matched else ""))
    assets = out.get("assets") or []
    if assets:
        parts = [f"{a['symbol']} ({a['name']}, conf {a['confidence']:.1f}, "
                 f"rel {a['relevance']:.2f})" for a in assets]
        print(f"assets      : " + " | ".join(parts))
    else:
        print("assets      : (none of the 8 desk instruments detected)")
    per_asset = out.get("per_asset") or []
    if len(per_asset) > 1:
        print("per-asset   :")
        for pa in per_asset:
            print(f"  {pa['symbol']:<10} {pa['polarity']:+.4f}  "
                  f"{pa.get('evidence', '')[:66]}")
    elif per_asset:
        pa = per_asset[0]
        print(f"per-asset   : {pa['symbol']} {pa['polarity']:+.4f} "
              f"({pa.get('evidence', '')})")
    terms = out.get("terms_fired") or []
    if terms:
        parts = [f"{t['term']} {t['contribution']:+.2f}"
                 + (f" ×{t['multiplier']:g}" if t["multiplier"] != 1.0 else "")
                 + (" [negated]" if t["negated"] else "")
                 for t in terms]
        print(f"terms fired : " + " | ".join(parts))
    else:
        print("terms fired : (no lexicon terms matched)")
    if out.get("llm_fallback_used"):
        print(f"llm 2nd op. : blended 50/50 — llm polarity "
              f"{out.get('llm_polarity', 0):+.3f}"
              + (f" ({out.get('llm_note', '')})" if out.get("llm_note") else ""))
    elif out.get("llm_fallback_failed"):
        print("llm 2nd op. : FAILED — local score kept (fail-closed)")
    else:
        print("llm 2nd op. : not needed (|polarity| ≥ 0.15 or low signal)")
    return 0


# default live portfolio for `risk` — keyless Yahoo daily bars, 1y
DEFAULT_RISK_PORTFOLIO = [
    {"symbol": "SPY", "weight": 0.40},
    {"symbol": "GC=F", "weight": 0.30},
    {"symbol": "BTC-USD", "weight": 0.15},
    {"symbol": "CASH", "weight": 0.15},
]


def _bar_day_key(bar: dict) -> str | None:
    """fetch_daily_bars stamps bars with EPOCH-MS integers (board.py's
    shape) — convert to a UTC YYYY-MM-DD key; tolerate ISO strings too.
    Returns None on an unusable timestamp (bar dropped, not crashed).
    Shared by the `risk` and `portfolio` live default paths."""
    from datetime import datetime, timezone
    ts = bar.get("ts")
    try:
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts) / 1000.0,
                                          tz=timezone.utc
                                          ).strftime("%Y-%m-%d")
        if isinstance(ts, str):
            return ts[:10]
    except (OverflowError, OSError, ValueError):
        return None
    return None


def _risk_default_portfolio(data_root: str):
    """Live default portfolio: 1y daily closes per symbol (keyless Yahoo,
    cached, fail-soft per symbol), DATE-ALIGNED across calendars (the
    R3-1 D2 lesson) and blended by weight. Returns (positions, benchmark)
    where benchmark is the SPY return series; None on any fetch failure
    (fail-closed — a missing leg must never silently re-weight).
    """
    from .markets.board import fetch_daily_bars
    from .risk.metrics import date_aligned_returns, portfolio_returns

    _day_key = _bar_day_key

    closes: dict[str, dict[str, float]] = {}
    for pos in DEFAULT_RISK_PORTFOLIO:
        sym = pos["symbol"]
        if sym == "CASH":
            continue
        bars = fetch_daily_bars(sym, "1y", data_root=data_root)
        by_date = {}
        for b in bars:
            c = b.get("c")
            day = _day_key(b)
            if c is not None and day:
                by_date[day] = float(c)
        if len(by_date) < 30:
            return None
        closes[sym] = by_date
    aligned = date_aligned_returns(closes)
    positions = [{**pos, "returns": aligned.get(pos["symbol"], [])}
                 for pos in DEFAULT_RISK_PORTFOLIO if pos["symbol"] != "CASH"]
    bench = aligned.get("SPY") or []
    return positions, bench


def _parse_json_list(raw: str, flag: str):
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"--{flag}: invalid JSON ({e})"
    if not isinstance(data, list):
        return None, f"--{flag}: expected a JSON list"
    return data, None


def _print_stress_replay(replay: dict) -> None:
    """Pretty-print the R4-3 historical stress-replay block."""
    print()
    print("STRESS REPLAY — historical daily-return paths (R4-3)")
    print("-" * 64)
    for s in (replay or {}).get("scenarios") or []:
        win = s.get("window") or {}
        mode = s.get("mode", "historical")
        tag = {"historical": f"historical, {s.get('n_days', 0)} days",
               "static": "static vector (--fast)",
               "fallback": "STATIC FALLBACK (fetch failed)"}.get(mode, mode)
        print(f"  {s.get('label', s.get('scenario'))} "
              f"({win.get('start')} → {win.get('end')})  [{tag}]")
        if mode == "historical":
            wd = s.get("worst_day", 0.0)
            wdd = (f" ({s.get('worst_day_date')})"
                   if s.get("worst_day_date") else "")
            print(f"    cumulative {s.get('cumulative', 0):+.2%}   "
                  f"worst day {wd:+.2%}{wdd}   "
                  f"MaxDD {s.get('max_drawdown', 0):.2%}")
        else:
            st = s.get("static") or s
            print(f"    portfolio shock {st.get('portfolio_shock', 0):+.2%}")
        st = s.get("static")
        if mode == "historical" and st:
            print(f"    (static vector for contrast: "
                  f"{st.get('portfolio_shock', 0):+.2%})")
        unshocked = s.get("unshocked") or []
        if unshocked:
            print(f"    unshocked: {', '.join(unshocked)}")
        if mode == "fallback" and s.get("error"):
            print(f"    error: {s['error'][:100]}")


def cmd_risk(args) -> int:
    """risk — R3-2 Build 4: VaR (parametric Gaussian / historical /
    Monte Carlo) at 95%+99%, Expected Shortfall, beta-adjusted exposure
    and the GFC/COVID/2022 stress scenarios for a portfolio.
    R4-3: --stress-replay applies the REAL historical daily-return paths
    (2008-H2 / 2020-Mar / 2022) to the current book (--fast = static).
    """
    from .risk.metrics import risk_report, stress_replay_all
    positions = None
    returns = None
    benchmark = None
    portfolio_label = "explicit series"

    if args.returns:
        returns, err = _parse_json_list(args.returns, "returns")
        if err:
            print(json.dumps({"ok": False, "error": err}) if args.json else err)
            return 1
        returns = [float(r) for r in returns]
        if args.benchmark_returns:
            benchmark, err = _parse_json_list(args.benchmark_returns,
                                              "benchmark-returns")
            if err:
                print(json.dumps({"ok": False, "error": err}) if args.json else err)
                return 1
            benchmark = [float(r) for r in benchmark]
        if args.positions:
            positions, err = _parse_json_list(args.positions, "positions")
            if err:
                print(json.dumps({"ok": False, "error": err}) if args.json else err)
                return 1
    else:
        built = _risk_default_portfolio(args.data_root)
        if built is None:
            if getattr(args, "stress_replay", False):
                # replay-only report on the static default book — the
                # replay fetches its own window bars, so it still works
                replay = stress_replay_all(
                    DEFAULT_RISK_PORTFOLIO, fast=getattr(args, "fast", False),
                    data_root=args.data_root)
                out = {"ok": bool(replay.get("ok")),
                       "portfolio": ("default 40% SPY / 30% GC=F / 15% "
                                     "BTC-USD / 15% cash (replay-only: "
                                     "1y bar fetch failed)"),
                       "error": "default portfolio 1y fetch failed — "
                                "VaR block unavailable",
                       "stress_replay": replay}
                if args.json:
                    print(json.dumps(out, sort_keys=True, default=str))
                else:
                    print("RISK REPORT — VaR · ES · beta · stress (R3-2 · R4-3)")
                    print("=" * 64)
                    print(f"portfolio   : {out['portfolio']}")
                    print(out["error"])
                    _print_stress_replay(replay)
                return 0 if out["ok"] else 1
            msg = ("default portfolio fetch failed (Yahoo daily bars "
                   "unreachable — pass --returns '[...]' for an offline "
                   "series)")
            print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
            return 1
        positions, benchmark = built
        from .risk.metrics import portfolio_returns
        returns = portfolio_returns(positions)
        portfolio_label = "default 40% SPY / 30% GC=F / 15% BTC-USD / 15% cash"
        if args.positions:
            positions, err = _parse_json_list(args.positions, "positions")
            if err:
                print(json.dumps({"ok": False, "error": err}) if args.json else err)
                return 1

    out = risk_report(returns, benchmark=benchmark, positions=positions)
    out["portfolio"] = portfolio_label
    if getattr(args, "stress_replay", False):
        rp_positions = positions if positions else list(DEFAULT_RISK_PORTFOLIO)
        out["stress_replay"] = stress_replay_all(
            rp_positions, fast=getattr(args, "fast", False),
            data_root=args.data_root)
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("risk report needs ≥2 returns (got "
              f"{out.get('n_observations', 0)})")
        return 1
    print("RISK REPORT — VaR · ES · beta · stress (R3-2)")
    print("=" * 64)
    print(f"portfolio   : {out.get('portfolio')}")
    print(f"observations: {out.get('n_observations')}   "
          f"mean {out.get('mean'):+.5f}   σ {out.get('stdev'):.5f}")
    print()
    print(f"{'method':<14s}{'VaR 95%':>12s}{'VaR 99%':>12s}")
    print("-" * 40)
    for method, label in (("parametric", "Gaussian"),
                          ("historical", "historical"),
                          ("monte_carlo", "Monte Carlo")):
        row = (out.get("var") or {}).get(method) or {}
        v95, v99 = row.get("95"), row.get("99")
        print(f"{label:<14s}"
              f"{(f'{v95:+.4%}' if v95 is not None else 'n/a'):>12s}"
              f"{(f'{v99:+.4%}' if v99 is not None else 'n/a'):>12s}")
    es = out.get("expected_shortfall") or {}
    print()
    print(f"expected shortfall (tail mean): "
          f"95% {es.get('historical_95', 0):+.4%}   "
          f"99% {es.get('historical_99', 0):+.4%}")
    beta = out.get("beta")
    if beta and beta.get("beta") is not None:
        print(f"beta vs benchmark: {beta['beta']:.4f}   "
              f"α/period {beta['alpha']:+.6f}   "
              f"R² {beta['r_squared']:.3f}   n={beta['n']}")
    stress = out.get("stress")
    if stress:
        print()
        print("STRESS SCENARIOS")
        print("-" * 64)
        for s in stress.get("scenarios") or []:
            unshocked = s.get("unshocked") or []
            note = (f"  (unshocked: {', '.join(unshocked)})" if unshocked
                    else "")
            print(f"  {s.get('label', s.get('name')):<34s}"
                  f"{s.get('portfolio_shock', 0):+.2%}{note}")
    if getattr(args, "stress_replay", False) and out.get("stress_replay"):
        _print_stress_replay(out["stress_replay"])
    return 0


def cmd_backtest(args) -> int:
    """backtest — R3-2 Build 4: the GUESS London-range-breakout setup run
    against keyless GC=F 1h bars (default 1y) with mechanical exits,
    equity journal and the full stat grid + buy-and-hold comparison.
    Deterministic: seed-pinned, no wall-clock in the output.
    """
    from .risk.backtest import BacktestEngine, fetch_hourly_bars
    try:
        bars = fetch_hourly_bars(args.symbol, args.bars,
                                 data_root=args.data_root)
    except Exception as e:  # noqa: BLE001 — surface fetch failure honestly
        msg = f"bar fetch failed for {args.symbol} ({args.bars}): {e}"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    journal = args.journal or str(Path(args.data_root) / "backtest_equity.jsonl")
    engine = BacktestEngine(bars, seed=args.seed,
                            slippage_atr_mult=args.slippage)
    out = engine.run(journal_path=journal)
    out["symbol"] = args.symbol
    out["range"] = args.bars
    out["journal_path"] = journal
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    print(f"BACKTEST — {args.symbol} {args.bars} · setup "
          f"{out.get('setup_id')} v{out.get('setup_version')} (R3-2)")
    print("=" * 64)
    print(f"bars        : {out.get('n_bars')}  "
          f"({out.get('first_bar')} → {out.get('last_bar')})")
    print(f"equity      : {out.get('equity_start'):,.0f} → "
          f"{out.get('equity_end'):,.0f}  "
          f"({out.get('total_return', 0):+.2%})")
    print(f"buy & hold  : {out.get('buy_hold_return', 0):+.2%}"
          + ("   ← benchmark" if out.get("buy_hold_return") is not None else ""))
    print(f"sharpe      : {_fmt(out.get('sharpe'))}   "
          f"sortino: {_fmt(out.get('sortino'))}   "
          f"calmar: {_fmt(out.get('calmar'))}")
    print(f"max drawdown: {out.get('max_drawdown', 0):.2%}")
    print(f"trades      : {out.get('n_trades')}  "
          f"({out.get('n_wins')}W/{out.get('n_losses')}L  "
          f"hit {out.get('hit_rate', 0):.1%})  "
          f"profit factor: {_fmt(out.get('profit_factor'))}")
    print(f"avg win/loss: {_fmt(out.get('avg_win'))} / "
          f"{_fmt(out.get('avg_loss'))}")
    print(f"determinism : seed {out.get('seed')} · journal sha256 "
          f"{(out.get('equity_curve_sha256') or '')[:16]}…")
    print(f"journal     : {journal} (JSONL, bar-by-bar equity)")
    trades = out.get("trades") or []
    if trades:
        print()
        print(f"{'side':<6s}{'entry':>10s}{'exit':>10s}"
              f"{'pnl':>10s}  {'reason':<10s}{'bars':>5s}")
        print("-" * 56)
        for t in trades[-6:]:
            print(f"{t['side']:<6s}{t['entry']:>10.2f}{t['exit']:>10.2f}"
                  f"{t['pnl']:>10.2f}  {t['reason']:<10s}{t['bars_held']:>5d}")
    return 0


PORTFOLIO_SYMBOLS = ["SPY", "GC=F", "BTC-USD"]


# ------------------------------------------------------------------- R5 ---
def cmd_evolve_run(args) -> int:
    """evolve-run — R5: self-evolving strategy parameters. Runs the
    deterministic evolution engine (population + walk-forward
    evaluation + champion/challenger promotion gate) over keyless
    hourly bars. Writes the JSONL archive (full lineage) and prints the
    head-to-head verdict vs the shipped GUESS incumbent. The engine
    NEVER writes the live spec — promotion is an operator decision.
    """
    from .evolve.engine import EvolutionEngine
    from .risk.backtest import fetch_hourly_bars
    try:
        bars = fetch_hourly_bars(args.symbol, args.bars,
                                 data_root=args.data_root)
    except Exception as e:  # noqa: BLE001 — surface fetch failure honestly
        msg = f"bar fetch failed for {args.symbol} ({args.bars}): {e}"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    archive = args.archive or str(Path(args.data_root) / "evolve_archive.jsonl")
    eng = EvolutionEngine(bars, seed=args.seed, population=args.population,
                          generations=args.generations,
                          min_trades=args.min_trades,
                          promotion_margin=args.margin,
                          max_overfit_gap=args.max_gap)
    out = eng.run(archive_path=archive)
    out["symbol"] = args.symbol
    out["range"] = args.bars
    out["archive_path"] = archive
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print(f"EVOLVE — {out.get('error')}: {out.get('note', '')}")
        return 1
    ch, inc = out.get("champion") or {}, out["incumbent"]
    print(f"EVOLVE — {args.symbol} {args.bars} · seed {out['seed']} · "
          f"population {out['config']['population']} × "
          f"{out['config']['generations']} generations (R5)")
    print("=" * 64)
    print(f"bars        : {out['n_bars']} "
          f"(train {out['train_bars']} / test {out['test_bars']}, "
          f"day-aligned, test never seen during selection)")
    print(f"archive     : {out['archive_size']} individuals · "
          f"full lineage at {archive}")
    print(f"incumbent   : IS {_fmt(inc.get('is_fitness'))}  "
          f"OOS {_fmt(inc.get('oos_fitness'))}  "
          f"({inc.get('is_trades', 0)}+{inc.get('oos_trades', 0)} trades)")
    if ch:
        print(f"champion    : IS {_fmt(ch.get('is_fitness'))}  "
              f"OOS {_fmt(ch.get('oos_fitness'))}  "
              f"({ch.get('is_trades', 0)}+{ch.get('oos_trades', 0)} trades)  "
              f"born by {ch.get('birth_op')}")
        gap = out.get("overfit_gap")
        print(f"overfit gap : {_fmt(gap)} "
              f"(IS − OOS; larger = more overfit)")
    print(f"VERDICT     : {out['verdict']} — {out['verdict_reason']}")
    if out.get("verdict") == "PROMOTE":
        print("             (the engine NEVER writes the live spec — "
              "review the champion genome and promote by hand)")
    return 0


def cmd_evolve_status(args) -> int:
    """evolve-status — R5: read the evolution archive (lineage + last
    verdict). The archive is the population DB: every individual ever
    born, with parents, birth operator and both fitnesses."""
    from .evolve.engine import load_archive
    archive = args.archive or str(Path(args.data_root) / "evolve_archive.jsonl")
    inds, result = load_archive(archive)
    if not inds and result is None:
        msg = f"no archive at {archive} (run evolve-run first)"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    by_status: dict[str, int] = {}
    ops: dict[str, int] = {}
    gens: set[int] = set()
    for ind in inds:
        by_status[ind.status] = by_status.get(ind.status, 0) + 1
        ops[ind.birth_op] = ops.get(ind.birth_op, 0) + 1
        gens.add(ind.generation)
    out = {
        "ok": True, "archive_path": archive,
        "n_individuals": len(inds),
        "n_generations": len(gens),
        "statuses": by_status, "birth_ops": ops,
        "last_verdict": (result or {}).get("verdict"),
        "last_verdict_reason": (result or {}).get("verdict_reason"),
        "last_champion": (result or {}).get("champion"),
        "last_incumbent": (result or {}).get("incumbent"),
        "lineage_tail": [
            {"ident": i.ident, "generation": i.generation,
             "birth_op": i.birth_op, "parent": i.parent,
             "is_fitness": i.is_fitness, "oos_fitness": i.oos_fitness,
             "is_trades": i.is_trades, "status": i.status}
            for i in inds[-12:]
        ],
    }
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print(f"EVOLVE STATUS — {archive}")
    print("=" * 64)
    print(f"individuals : {len(inds)} across {len(gens)} generations")
    print(f"statuses    : {by_status}")
    print(f"birth ops   : {ops}")
    if result:
        print(f"last verdict: {result.get('verdict')} — "
              f"{result.get('verdict_reason')}")
        ch = result.get("champion") or {}
        if ch:
            print(f"champion    : {ch.get('ident')} "
                  f"IS {_fmt(ch.get('is_fitness'))} "
                  f"OOS {_fmt(ch.get('oos_fitness'))}")
    print("lineage tail:")
    for ind in inds[-8:]:
        print(f"  {ind.ident}  gen{ind.generation:<3d} "
              f"{ind.birth_op:<15s} "
              f"IS {_fmt(ind.is_fitness)} OOS {_fmt(ind.oos_fitness)} "
              f"[{ind.status}]")
    return 0


def cmd_lessons(args) -> int:
    """lessons — R5: the temporal lesson store (Zep/Graphiti-style
    validity windows + evidence counters + contradiction retirement).
    Subcommands: list / add / evidence / retire."""
    from .evolve.lessons import TemporalLessonStore
    path = args.store or str(Path(args.data_root) / "lessons.jsonl")
    if args.lessons_cmd == "add" and not args.text:
        msg = "lessons add requires --text"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    if args.lessons_cmd == "evidence" and (not args.lesson_id
                                            or not args.outcome):
        msg = "lessons evidence requires --lesson-id and --outcome"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    if args.lessons_cmd == "retire" and not args.lesson_id:
        msg = "lessons retire requires --lesson-id"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    store = TemporalLessonStore.load(path)
    now = time.time()
    out: dict = {"ok": True, "store_path": path}
    if args.lessons_cmd == "add":
        rec = store.add_lesson(args.text, args.symbol, now,
                               regime=args.regime,
                               halflife_days=args.halflife,
                               max_age_days=args.max_age)
        out.update(action="add", lesson=rec.to_dict())
        store.save(path)
    elif args.lessons_cmd == "evidence":
        t = store.add_evidence(args.lesson_id, args.outcome, now)
        out.update(action="evidence", transition=t)
        store.save(path)
        if not t.get("ok"):
            out["ok"] = False
    elif args.lessons_cmd == "retire":
        t = store.retire(args.lesson_id, now)
        out.update(action="retire", transition=t)
        store.save(path)
        if not t.get("ok"):
            out["ok"] = False
    else:  # list
        sym_filter = None if args.symbol in (None, "all") else args.symbol
        reg_filter = None if args.regime in (None, "all") else args.regime
        rows = store.active_lessons(now, symbol=sym_filter,
                                    regime=reg_filter)
        out.update(action="list", active=rows,
                   n_total=len(store.all_lessons()))
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    print(f"LESSONS — {path}")
    print("=" * 64)
    if out.get("action") == "list":
        rows = out.get("active") or []
        print(f"active: {len(rows)} of {out.get('n_total', 0)} total")
        for r in rows[:20]:
            print(f"  [{r['confidence']:+.3f}] {r['symbol']:<6s} "
                  f"{r['regime']:<12s} {r['text'][:44]} "
                  f"(s{r['support']}/c{r['contradict']}, "
                  f"{r['age_days']:.0f}d)")
    else:
        print(json.dumps(out, sort_keys=True, default=str, indent=2))
    return 0 if out.get("ok") else 1


def cmd_tune_rule(args) -> int:
    """tune-rule — R5: champion/challenger threshold tuning for a
    watch rule (pct_move or atr_spike). The score is information
    content: does firing at θ predict elevated follow-through movement?
    The incumbent always wins ties; a dormant rule is never force-tuned.
    """
    from .evolve.rule_tuner import (TuneConfig, atr_spike_score_fn,
                                    pct_move_score_fn, tune_threshold)
    from .risk.backtest import fetch_hourly_bars
    try:
        bars = fetch_hourly_bars(args.symbol, args.bars,
                                 data_root=args.data_root)
    except Exception as e:  # noqa: BLE001 — fail-soft with a real message
        msg = f"bar fetch failed for {args.symbol} ({args.bars}): {e}"
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    closes = [b.close for b in bars]
    if len(closes) < args.window + 5:
        msg = (f"not enough bars for {args.symbol} ({len(closes)} closes; "
               f"need > {args.window + 5})")
        print(json.dumps({"ok": False, "error": msg}) if args.json else msg)
        return 1
    if args.rule == "pct_move":
        score_fn = pct_move_score_fn(closes, args.window)
    else:
        # ATR proxy from closes: |close-to-close| change series
        atrs = [abs(closes[i] - closes[i - 1]) for i in range(1, len(closes))]
        score_fn = atr_spike_score_fn(atrs, 20)
    cfg = TuneConfig(lo=args.lo, hi=args.hi, min_fires=args.min_fires,
                     margin=args.margin, grain=args.grain)
    res = tune_threshold(score_fn, args.incumbent, cfg, seed=args.seed)
    res.update({"ok": True, "rule": args.rule, "symbol": args.symbol,
                "n_closes": len(closes)})
    if args.json:
        print(json.dumps(res, sort_keys=True, default=str))
        return 0 if res.get("verdict") in ("PROMOTE", "KEEP_INCUMBENT") else 1
    print(f"TUNE RULE — {args.rule} on {args.symbol} · {len(closes)} closes (R5)")
    print("=" * 64)
    print(f"champion    : {res['champion_value']} "
          f"(score {_fmt(res['champion_score'])}, "
          f"{res['champion_fires']} fires)")
    print(f"best probe  : {res['best_value']} "
          f"(score {_fmt(res['best_score'])}) over {res['n_probes']} probes")
    print(f"VERDICT     : {res['verdict']} — {res['reason']}")
    return 0 if res.get("verdict") in ("PROMOTE", "KEEP_INCUMBENT") else 1



def _parse_lookback(raw: str) -> int:
    """'90d' / '90' → 90 return observations (tail of the aligned window)."""
    text = str(raw).strip().lower().rstrip("d")
    try:
        n = int(text)
    except ValueError:
        raise ValueError(f"invalid --lookback {raw!r} (use e.g. 90d)")
    if n < 2:
        raise ValueError(f"--lookback {raw!r} must be ≥ 2 observations")
    return n


def _portfolio_returns_map(symbols: list[str], lookback: int,
                           data_root: str) -> tuple[dict, int]:
    """Live returns map for the portfolio optimizers: 1y keyless Yahoo
    daily bars per symbol, DATE-ALIGNED across calendars (R3-1 D2
    lesson), tail-truncated to `lookback` return observations. Returns
    (returns_by_symbol, n_aligned). Raises ValueError on any fetch
    failure (fail-closed — a missing leg must never silently re-weight).
    """
    from .markets.board import fetch_daily_bars
    from .risk.metrics import date_aligned_returns

    closes: dict[str, dict[str, float]] = {}
    for sym in symbols:
        bars = fetch_daily_bars(sym, "1y", data_root=data_root)
        by_date: dict[str, float] = {}
        for b in bars:
            c = b.get("c")
            day = _bar_day_key(b)
            if c is not None and day:
                by_date[day] = float(c)
        if len(by_date) < 30:
            raise ValueError(f"not enough daily bars for {sym} "
                             f"({len(by_date)} dates)")
        closes[sym] = by_date
    aligned = date_aligned_returns(closes)
    n_aligned = min((len(v) for v in aligned.values() if v), default=0)
    if n_aligned < 2:
        raise ValueError("date alignment left < 2 common observations")
    return {s: v[-lookback:] for s, v in aligned.items() if v}, n_aligned


def cmd_portfolio(args) -> int:
    """portfolio — R3-3 Build 5a: mean-variance / risk-parity / HRP
    weights over the default 3-asset book (or --returns offline map) with
    portfolio vol, per-asset risk contributions and the diversification
    ratio. Deterministic (MV search seed-pinned)."""
    from .risk.portfolio import optimize

    if args.returns:
        try:
            parsed = json.loads(args.returns)
        except json.JSONDecodeError as e:
            print(json.dumps({"ok": False,
                              "error": f"--returns: invalid JSON ({e})"})
                  if args.json else f"--returns: invalid JSON ({e})")
            return 1
        if not isinstance(parsed, dict):
            print(json.dumps({"ok": False,
                              "error": "--returns: expected a JSON object "
                                       "{symbol: [returns]}"}) if args.json
                  else "--returns: expected a JSON object "
                        "{symbol: [returns]}")
            return 1
        returns_map, n_aligned = parsed, min(
            (len(v) for v in parsed.values()
             if isinstance(v, list) and v), default=0)
        label = "explicit series"
    else:
        symbols = [s.strip().upper() for s in args.symbols.split(",")
                   if s.strip()]
        try:
            returns_map, n_aligned = _portfolio_returns_map(
                symbols, _parse_lookback(args.lookback), args.data_root)
        except ValueError as e:
            msg = (f"live bar fetch failed ({e} — pass "
                   f"--returns '{{\"SPY\": [...]}}' for an offline map)")
            print(json.dumps({"ok": False, "error": msg}) if args.json
                  else msg)
            return 1
        label = (f"live {', '.join(symbols)} · last "
                 f"{min(n_aligned, _parse_lookback(args.lookback))} obs")

    kwargs = {}
    if args.method == "mv":
        kwargs = {"lambda_risk": args.lambda_risk,
                  "max_weight": args.max_weight, "seed": args.seed}
    out = optimize(returns_map, method=args.method, **kwargs)
    out["source"] = label
    out["lookback"] = args.lookback
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print(f"portfolio optimization failed: {out.get('error')}")
        return 1
    print(f"PORTFOLIO — {out['method']} · {len(out['symbols'])} assets · "
          f"{out['n_observations']} obs (R3-3)")
    print("=" * 64)
    print(f"source      : {label}")
    if out["method"] == "mv":
        print(f"objective   : μᵀw − {out['lambda_risk']}·wᵀΣw "
              f"(max_weight {out['max_weight']}, "
              f"{out['n_candidates']} candidates, seed {out['seed']})")
    if out["method"] == "rp":
        print(f"ERC         : converged in {out['iterations']} sweeps "
              f"(tol {out['tol']})")
    if out["method"] == "hrp":
        print(f"quasi-diag  : {' → '.join(out['quasi_diagonal_order'])}")
    print(f"σ portfolio : {out['portfolio_vol']:.4%}   "
          f"DR {out['diversification_ratio']:.3f}   "
          f"E[r] {out['expected_return']:+.4%}")
    print()
    print(f"{'symbol':<10s}{'weight':>9s}{'vol':>9s}{'E[r]':>9s}"
          f"{'risk contrib':>14s}")
    print("-" * 56)
    for sym in out["symbols"]:
        print(f"{sym:<10s}{out['weights'][sym]:>9.2%}"
              f"{out['volatilities'][sym]:>9.2%}"
              f"{out['expected_returns'][sym]:>9.4%}"
              f"{out['risk_contributions'][sym]:>13.2%}")
    return 0


def cmd_pnl(args) -> int:
    """pnl — R3-3 Build 5b: P&L attribution over a trade ledger: by asset,
    by setup (with win rates) and by hour-of-day (24 UTC buckets, Asia /
    London / NY session labels). Sources: journal reconstruction, a
    ledger file, or the deterministic synthetic demo ledger."""
    from .risk.attribution import (attribute, load_journal_ledger,
                                   read_ledger_file, synthetic_ledger)

    reconstruction = None
    if args.source == "journal":
        rec = load_journal_ledger(args.data_root)
        ledger = rec["ledger"]
        reconstruction = {k: v for k, v in rec.items() if k != "ledger"}
        source_label = (f"journal reconstruction "
                        f"({reconstruction['matched']} matched trades)")
    elif args.ledger:
        ledger = read_ledger_file(args.ledger)
        source_label = f"ledger file {args.ledger}"
    else:
        ledger = synthetic_ledger()
        source_label = "synthetic demo ledger (deterministic, seed 11)"

    out = attribute(ledger)
    out["source"] = source_label
    if reconstruction is not None:
        out["reconstruction"] = reconstruction
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    print(f"P&L ATTRIBUTION — {source_label} (R3-3)")
    print("=" * 64)
    print(f"trades      : {out['n_trades']}  "
          f"({out['n_wins']}W/{out['n_losses']}L  "
          f"win {out['win_rate']:.1%})")
    print(f"total P&L   : {out['total_pnl']:+,.2f}")
    print(f"gross P/L   : {out['gross_profit']:+,.2f} / "
          f"{-out['gross_loss']:+,.2f}   "
          f"profit factor: {_fmt(out['profit_factor'], '{:.2f}')}")
    if out["n_skipped_rows"]:
        print(f"skipped rows: {out['n_skipped_rows']} (invalid — surfaced "
              f"in JSON)")
    print()
    print(f"{'BY ASSET':<12s}{'P&L':>12s}{'% of total':>11s}"
          f"{'trades':>8s}{'win rate':>10s}")
    print("-" * 56)
    for row in out["by_asset"]:
        print(f"{row['symbol']:<12s}{row['pnl']:>12,.2f}"
              f"{row['pct_of_total']:>10.1%}{row['n_trades']:>8d}"
              f"{row['win_rate']:>10.1%}")
    print()
    print(f"{'BY SETUP':<28s}{'P&L':>12s}{'trades':>8s}{'win rate':>10s}")
    print("-" * 60)
    for row in out["by_setup"]:
        print(f"{row['setup'][:27]:<28s}{row['pnl']:>12,.2f}"
              f"{row['n_trades']:>8d}{row['win_rate']:>10.1%}")
    print()
    print(f"{'BY HOUR (UTC)':<22s}{'session':<9s}{'P&L':>12s}"
          f"{'trades':>8s}")
    print("-" * 52)
    for row in out["by_hour"]:
        if row["n_trades"] or row["hour"] % 3 == 0:
            print(f"{row['hour']:02d}:00{'':<16s}{row['session']:<9s}"
                  f"{row['pnl']:>12,.2f}{row['n_trades']:>8d}")
    if out["n_unparsed_timestamps"]:
        print(f"({out['n_unparsed_timestamps']} trades with unparseable "
              f"timestamps excluded from the hourly view)")
    return 0


def _fmt(v, spec: str = "{:+.3f}") -> str:
    return spec.format(v) if isinstance(v, (int, float)) else "n/a"


def _agent_registry():
    """Full research registry: desk tools + crypto tools + web tools +
    the multi-analyst desk bridge (piece 6)."""
    from .agent.desk_tools import desk_registry
    from .agent.assets import asset_tools
    from .agent.browse import browse_tools
    from .agent.desk_bridge import desk_bridge_tools
    reg = desk_registry()
    for t in asset_tools():
        reg.register(t)
    for t in browse_tools():
        reg.register(t)
    for t in desk_bridge_tools():
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


def cmd_desk(args) -> int:
    """Multi-analyst desk: 5 personas in parallel + PM synthesis."""
    from .agent.desk import run_desk
    try:
        out = run_desk(args.symbol, data_root=args.data_root,
                       model=args.model)
    except Exception as e:  # noqa: BLE001 — context errors fail loud here
        if args.json:
            print(json.dumps({"ok": False, "symbol": args.symbol,
                              "error": f"{type(e).__name__}: {e}"}))
        else:
            print(f"analyst desk failed: {type(e).__name__}: {e}")
        return 1
    if args.json:
        print(json.dumps(out, ensure_ascii=False, sort_keys=True,
                         default=str))
        return 0 if out.get("ok") else 1

    # human output: symbol header + one line per persona + PM block
    print("ANALYST DESK")
    print("=" * 64)
    print(f"{out['symbol']} — {out.get('name') or ''}"
          f"  [{out.get('sector') or '?'}]")
    chg = out.get("change_pct")
    print(f"as of : {out.get('as_of', '?')}   "
          f"price {_fmt_price(out.get('price'), out['symbol'])}"
          f"   1d {f'{chg:+.2f}%' if isinstance(chg, (int, float)) else 'n/a'}")
    print()
    for p in out.get("personas") or []:
        line = (f"  {p['role'].upper():16s} {p['signal']:8s} "
                f"{p['confidence']:3d}%  {p['thesis']}")
        print(line)
        for ev in p.get("key_evidence") or []:
            print(f"  { ' ':16s}           · {ev}")
        if p.get("abstained"):
            print(f"  { ' ':16s}           (model: {p.get('model') or 'n/a'})")
    print()
    pm = out.get("pm") or {}
    print("PM — THE PORTFOLIO MANAGER")
    print("-" * 64)
    print(f"consensus     : {pm.get('consensus', '?')} "
          f"(conviction {pm.get('conviction', 0)}/100)"
          + ("  [mechanical vote — PM model unavailable]"
             if pm.get("mechanical") else ""))
    print(f"summary       : {pm.get('summary', '')}")
    if pm.get("disagreements"):
        print(f"disagreements : {pm['disagreements']}")
    for flag in pm.get("risk_flags") or []:
        print(f"risk flag     : {flag}")
    print()
    abst = out.get("abstained", 0)
    print(f"  [{len(out.get('personas') or [])} personas · {abst} abstained"
          f" · {out.get('elapsed_ms', 0)}ms · model {out.get('model') or '?'}"
          f" · run {out.get('run_id', '?')[:10]}…]")
    return 0


def _watch_loop(args):
    """Shared sweep runner for the watch-loop subcommand. Telegram push
    is wired through the existing TelegramIO (env GOLD_DESK_TG_TOKEN /
    GOLD_DESK_TG_CHAT_ID); unconfigured env → delivery silently
    skipped, fired log + journal still record everything."""
    from pathlib import Path as _P
    from .watch.loop import WatchLoop, _watch_journal
    from .telegram_io import TelegramIO

    telegram = TelegramIO(_watch_journal(_P(args.data_root)))
    loop = WatchLoop(data_root=args.data_root, telegram=telegram,
                     correlation_provider=_corr_provider(args.data_root))
    if getattr(args, "interval", None):
        loop.interval_seconds = int(args.interval)
    return loop


def _corr_provider(data_root):
    """30d Pearson correlation for corr_flip rules (fail-soft)."""
    from .markets.multi_asset import MultiAssetMonitor
    mon = MultiAssetMonitor(data_root=data_root)

    def _provide() -> dict:
        return mon.compute_correlation(window=30, method="pearson")
    return _provide


def cmd_watch_loop(args) -> int:
    """watch-loop — R4-1: autonomous alert sweep over the 8 instruments.

    --dry-run (default when neither --daemon nor --status): one sweep,
    print fired alerts. --daemon: sweep every --interval seconds until
    Ctrl-C (clean exit). --status: loop state (last/next sweep, rules
    count, per-instrument session open/closed).
    """
    from .watch.loop import watch_status
    if args.status:
        out = watch_status(args.data_root)
        if args.json:
            print(json.dumps(out, sort_keys=True, default=str))
            return 0
        print("WATCH LOOP STATUS (R4-1)")
        print("=" * 64)
        print(f"running       : {'yes' if out.get('running') else 'no recorded sweep'}")
        print(f"last sweep    : {out.get('last_sweep') or '—'}")
        print(f"next sweep    : {out.get('next_sweep') or '—'}"
              + (f"  (every {out['interval_seconds']}s)"
                 if out.get("interval_seconds") else ""))
        print(f"ticks         : {out.get('ticks', 0)}")
        print(f"rules         : {out.get('rules_count', 0)}"
              f"   fired logged: {out.get('fired_logged', 0)}")
        if out.get("last_error"):
            print(f"last error    : {out['last_error']}")
        print()
        print(f"{'instrument':<12s}{'session':>10s}")
        print("-" * 24)
        for sym, open_ in (out.get("sessions") or {}).items():
            print(f"{sym:<12s}{'OPEN' if open_ else 'closed':>10s}")
        return 0

    loop = _watch_loop(args)
    if args.daemon:
        print(f"watch loop daemon — sweeping every "
              f"{args.interval}s (Ctrl-C to stop)")
        fired = loop.run_daemon(interval_seconds=args.interval)
        if args.json:
            print(json.dumps({"ok": True, "daemon": True,
                              "fired": [e.to_dict() for e in fired]},
                             sort_keys=True, default=str))
        return 0
    # one sweep (--dry-run or bare)
    fired = loop.run_once()
    out = {
        "ok": True,
        "dry_run": True,
        "as_of": loop.last_sweep_at,
        "rules": len(loop.rules()),
        "fired": [e.to_dict() for e in fired],
        "last_error": loop.last_error,
    }
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print("WATCH LOOP SWEEP — R4-1 (dry run)")
    print("=" * 64)
    print(f"as of   : {out['as_of'] or '?'}   rules: {out['rules']}")
    if out["last_error"]:
        print(f"error   : {out['last_error']} (fail-soft — sweep skipped)")
    if not fired:
        print("fired   : (nothing — no rule threshold met on live data)")
        return 0
    print(f"fired   : {len(fired)}")
    for e in fired:
        print(f"  [{e.kind}] {e.symbol} — {e.message}"
              f"  (rule {e.rule_id}, data {e.fired_at})")
    return 0


def cmd_alerts(args) -> int:
    """alerts — R4-1: list alert rules + recent fired alerts, or ack a
    fired alert (--ack EVENT_ID)."""
    from .watch.store import AlertStore
    from .watch.loop import default_rules
    store = AlertStore(args.data_root)
    if args.ack:
        ok = store.ack_alert(args.ack)
        out = {"ok": ok, "acked": args.ack if ok else None}
        if args.json:
            print(json.dumps(out, sort_keys=True))
        else:
            print(f"ack {'set' if ok else 'NOT FOUND (unknown event id)'}: "
                  f"{args.ack}")
        return 0 if ok else 1
    rules = store.load_rules() or default_rules()
    fired = store.list_fired(limit=args.limit)
    out = {
        "ok": True,
        "rules": [r.to_dict() for r in rules],
        "fired": fired,
        "fired_count": len(fired),
        "rules_count": len(rules),
    }
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print("ALERT RULES (R4-1)")
    print("=" * 78)
    print(f"{'id':<26s}{'symbol':<11s}{'kind':<14s}{'params':<22s}"
          f"{'cd/min':>7s}{'on':>4s}")
    print("-" * 78)
    for r in rules:
        p = json.dumps(r.params, sort_keys=True, default=str)
        print(f"{r.id[:25]:<26s}{r.symbol:<11s}{r.kind:<14s}"
              f"{p[:21]:<22s}{r.cooldown_minutes:>7d}"
              f"{'✓' if r.enabled else '·':>4s}")
    print()
    print(f"FIRED LOG (last {len(fired)})")
    print("-" * 78)
    if not fired:
        print("(no alerts fired yet)")
    for f in fired:
        ack = " [ack]" if f.get("ack") else ""
        print(f"  {f.get('wall_fired_at') or f.get('fired_at') or '?'}"
              f"  [{f.get('kind')}] {f.get('symbol')}"
              f"  {f.get('message')}{ack}")
    return 0


def cmd_alerts_add(args) -> int:
    """alerts-add — R4-1: add an alert rule (persisted to
    <data_root>/watch/alerts.json)."""
    from .watch.alerts import AlertRule
    from .watch.store import AlertStore
    params: dict = {}
    if args.level is not None:
        params["level"] = args.level
    if args.threshold is not None:
        params["threshold"] = args.threshold
    if args.window is not None:
        params["window_bars"] = args.window
    if args.k is not None:
        params["k"] = args.k
    if args.other:
        params["other"] = args.other
    if not args.symbol:
        print("alerts-add: --symbol is required", file=sys.stderr)
        return 1
    rule = AlertRule(
        id=args.id or "",
        symbol=args.symbol, kind=args.kind, params=params,
        cooldown_minutes=args.cooldown,
        note=args.note or "")
    store = AlertStore(args.data_root)
    added = store.add_rule(rule)
    out = {"ok": True, "rule": added.to_dict()}
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0
    print(f"added rule {added.id}: {added.symbol} {added.kind} "
          f"{added.params}")
    return 0


def cmd_alerts_rm(args) -> int:
    """alerts-rm — R4-1: remove an alert rule by id."""
    from .watch.store import AlertStore
    store = AlertStore(args.data_root)
    ok = store.remove_rule(args.id)
    out = {"ok": ok, "removed": args.id if ok else None}
    if args.json:
        print(json.dumps(out, sort_keys=True))
    else:
        print(f"{'removed' if ok else 'NOT FOUND'}: {args.id}")
    return 0 if ok else 1


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

    p_desk = sub.add_parser(
        "desk", help="multi-analyst desk: 6 personas + PM consensus "
                     "(technician/macro/news/sentiment/risk/fundamentalist)")
    p_desk.add_argument("symbol",
                        help="any Yahoo symbol or alias (btc, AAPL, GC=F, "
                             "^NSEI, TOP, eur/usd ...)")
    p_desk.add_argument("--model", default=None,
                        help="model id (default: catalog default chain)")
    p_desk.add_argument("--json", action="store_true",
                        help="machine-readable result (used by the web)")
    p_desk.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_desk.set_defaults(func=cmd_desk)

    p_eco = sub.add_parser("markets-eco",
                           help="economic calendar this week (ECO — "
                                "ForexFactory mirror, static fallback)")
    p_eco.add_argument("--json", action="store_true")
    p_eco.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_eco.set_defaults(func=cmd_markets_eco)

    p_nsec = sub.add_parser("markets-news",
                            help="NSE-style news search — query → "
                                 "merged Yahoo RSS headlines")
    p_nsec.add_argument("query", nargs="+",
                        help="search query (topic, symbol, alias: bitcoin, "
                             "gold, nifty, inr/usd, crypto, aapl nvda ...)")
    p_nsec.add_argument("--json", action="store_true")
    p_nsec.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_nsec.set_defaults(func=cmd_markets_news)

    # --- R2-1 institutional data plane subcommands ---
    p_fund = sub.add_parser("markets-fundamentals",
                             help="8Q PIT GAAP fundamentals for a symbol "
                                  "(SEC XBRL primary, Yahoo fallback, "
                                  "accession-cited)")
    p_fund.add_argument("symbol",
                        help="any US equity ticker (AAPL, MSFT, GOOGL ...)")
    p_fund.add_argument("--json", action="store_true")
    p_fund.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_fund.set_defaults(func=cmd_markets_fundamentals)

    p_13f = sub.add_parser("markets-13f",
                            help="latest 13F-HR institutional holdings "
                                 "(default: Berkshire Hathaway)")
    p_13f.add_argument("cik", nargs="?", default=None,
                        help="10-digit CIK (default: 0001067983 = Berkshire)")
    p_13f.add_argument("--json", action="store_true")
    p_13f.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_13f.set_defaults(func=cmd_markets_13f)

    p_curve = sub.add_parser("markets-curve",
                              help="US Treasury daily yield curve (1M-30Y)")
    p_curve.add_argument("--json", action="store_true")
    p_curve.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_curve.set_defaults(func=cmd_markets_curve)

    p_sent = sub.add_parser("markets-sentiment",
                             help="crypto Fear & Greed index (alternative.me, "
                                  "30-day history)")
    p_sent.add_argument("--json", action="store_true")
    p_sent.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_sent.set_defaults(func=cmd_markets_sentiment)

    p_onch = sub.add_parser("markets-onchain",
                             help="BTC 24h on-chain stats (blockchain.info)")
    p_onch.add_argument("--json", action="store_true")
    p_onch.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_onch.set_defaults(func=cmd_markets_onchain)

    p_soc = sub.add_parser("markets-social",
                            help="Reddit RSS social feed (asset-class routed: "
                                 "crypto→r/CryptoCurrency, equity→r/stocks, "
                                 "else→r/wallstreetbets)")
    p_soc.add_argument("symbol", nargs="?", default=None,
                        help="optional symbol to route sub by asset class")
    p_soc.add_argument("--json", action="store_true")
    p_soc.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_soc.set_defaults(func=cmd_markets_social)

    p_inst = sub.add_parser("markets-institutional",
                             help="7-slice institutional context aggregator "
                                  "(fundamentals + 13F + curve + F&G + "
                                  "onchain + global + social, fail-soft per slice)")
    p_inst.add_argument("symbol",
                        help="any symbol (XBRL for US equities; Yahoo fallback "
                             "elsewhere)")
    p_inst.add_argument("--json", action="store_true")
    p_inst.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_inst.set_defaults(func=cmd_markets_institutional)

    # --- R2-2 quant toolkit + deterministic verified snapshot ---
    p_quant = sub.add_parser("markets-quant",
                              help="numpy-free indicator battery for a symbol "
                                   "(RSI14, MACD, BBands, ATR/ATR%, realized "
                                   "vol 20d + vol regime, SMA{20,50,200}, "
                                   "EMA{12,26}, ADX14, Stoch, CCI20, OBV)")
    p_quant.add_argument("symbol",
                          help="any Yahoo symbol (AAPL, BTC-USD, GC=F, ...)")
    p_quant.add_argument("--json", action="store_true")
    p_quant.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_quant.set_defaults(func=cmd_markets_quant)

    p_snap = sub.add_parser("markets-snapshot",
                              help="deterministic verified snapshot for a "
                                   "symbol — the no-LLM ground-truth block "
                                   "the technician persona treats as the "
                                   "source of truth for exact claims")
    p_snap.add_argument("symbol",
                         help="any Yahoo symbol (AAPL, BTC-USD, GC=F, ...)")
    p_snap.add_argument("--json", action="store_true")
    p_snap.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_snap.set_defaults(func=cmd_markets_snapshot)

    p_beta = sub.add_parser("markets-beta",
                             help="OLS beta/alpha/r²/correlation vs a "
                                  "benchmark (default SPY, 63-day window)")
    p_beta.add_argument("symbol",
                        help="any Yahoo symbol (AAPL, BTC-USD, ...)")
    p_beta.add_argument("bench", nargs="?", default="SPY",
                        help="benchmark Yahoo symbol (default SPY)")
    p_beta.add_argument("--window", type=int, default=63,
                        help="log-return window in days (default 63)")
    p_beta.add_argument("--json", action="store_true")
    p_beta.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_beta.set_defaults(func=cmd_markets_beta)

    p_corr = sub.add_parser("markets-corr",
                             help="symmetric correlation matrix across "
                                  "comma-separated symbols (63-day window)")
    p_corr.add_argument("symbols",
                         help="comma-separated symbols (e.g. "
                              "AAPL,MSFT,NVDA)")
    p_corr.add_argument("--window", type=int, default=63,
                        help="log-return window in days (default 63)")
    p_corr.add_argument("--json", action="store_true")
    p_corr.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_corr.set_defaults(func=cmd_markets_corr)

    # --- R3-1 multi-asset monitor + Alpaca paper execution ---
    p_mam = sub.add_parser("markets-multi",
                            help="R3-1: 8-instrument multi-asset monitor "
                                 "(gold, ES, ^TNX, DXY, BTC, VIX, WTI, "
                                 "EUR/USD) — live session VWAP + relative % "
                                 "(R4-2: --all = 24-instrument universe, "
                                 "--symbols=A,B = any subset)")
    p_mam.add_argument("--json", action="store_true")
    p_mam.add_argument("--all", action="store_true",
                       help="monitor the full 24-instrument universe")
    p_mam.add_argument("--symbols", default=None,
                       help="comma-separated subset (e.g. SI=F,NQ=F,ETH-USD)")
    p_mam.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_mam.set_defaults(func=cmd_markets_multi)

    p_mcorr = sub.add_parser("markets-multi-corr",
                              help="R3-1: cross-asset correlation matrix "
                                   "across the 8 instruments (Pearson or "
                                   "Spearman, 30/60/90-day windows)")
    p_mcorr.add_argument("--window", type=int, default=30,
                          help="rolling window in days (default 30; "
                               "charter mandates 30/60/90)")
    p_mcorr.add_argument("--method", default="pearson",
                          choices=["pearson", "spearman"],
                          help="correlation method (default pearson)")
    p_mcorr.add_argument("--json", action="store_true")
    p_mcorr.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_mcorr.set_defaults(func=cmd_markets_multi_corr)

    p_alp = sub.add_parser("account-alpaca",
                            help="R3-1: Alpaca paper account summary "
                                 "(balance, positions, open orders, today P&L) "
                                 "— fail-closed when creds missing")
    p_alp.add_argument("--json", action="store_true")
    p_alp.add_argument("--timeout", type=float, default=8.0,
                        help="REST timeout seconds (default 8)")
    p_alp.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_alp.set_defaults(func=cmd_account_alpaca)

    p_nsent = sub.add_parser("news-sentiment",
                              help="R3-2: NLP sentiment for a headline or "
                                   "the live tape — polarity/magnitude/"
                                   "subjectivity + 8-asset detection + "
                                   "relevance + novelty + LLM fallback")
    p_nsent.add_argument("headline", nargs="?", default=None,
                          help="headline to score (quoted)")
    p_nsent.add_argument("--tape", action="store_true",
                          help="score the live news tape (8 instrument feeds)")
    p_nsent.add_argument("--limit", type=int, default=20,
                          help="tape: max stories to score (default 20)")
    p_nsent.add_argument("--no-llm", action="store_true", dest="no_llm",
                          help="disable the Zen LLM second opinion")
    p_nsent.add_argument("--json", action="store_true")
    p_nsent.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_nsent.set_defaults(func=cmd_news_sentiment)

    p_risk = sub.add_parser("risk",
                             help="R3-2: VaR (Gaussian/historical/Monte "
                                  "Carlo) + ES + beta + stress scenarios "
                                  "(GFC/COVID/2022) for a portfolio; R4-3 "
                                  "--stress-replay applies the REAL "
                                  "historical daily-return paths")
    p_risk.add_argument("--returns", default=None,
                         help="JSON list of returns (offline mode; default: "
                              "live 40%% SPY / 30%% GC=F / 15%% BTC / 15%% "
                              "cash from keyless Yahoo)")
    p_risk.add_argument("--benchmark-returns", default=None, dest="benchmark_returns",
                         help="JSON list of benchmark returns (enables the "
                              "beta block in offline mode)")
    p_risk.add_argument("--positions", default=None,
                         help="JSON list of {symbol, weight} positions for "
                              "the stress scenarios")
    p_risk.add_argument("--stress-replay", action="store_true",
                         dest="stress_replay",
                         help="R4-3: replay the REAL historical daily-return "
                              "paths (2008-H2 / 2020-Mar / 2022) on the "
                              "current book (cumulative / worst day / MaxDD "
                              "+ equity path)")
    p_risk.add_argument("--fast", action="store_true",
                         help="stress replay: skip the network, use the "
                              "static shock vectors")
    p_risk.add_argument("--json", action="store_true")
    p_risk.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_risk.set_defaults(func=cmd_risk)

    p_bt = sub.add_parser("backtest",
                           help="R3-2: GUESS London-range-breakout backtest "
                                "vs keyless GC=F 1h bars — Sharpe/Sortino/"
                                "MaxDD/Calmar/hit-rate/profit-factor + "
                                "equity journal + buy-and-hold")
    p_bt.add_argument("--bars", default="1y",
                       choices=["1mo", "3mo", "6mo", "1y", "2y"],
                       help="bar range (default 1y)")
    p_bt.add_argument("--setup", default="guess", choices=["guess"],
                       help="setup spec (only the GUESS rule exists)")
    p_bt.add_argument("--symbol", default="GC=F",
                       help="Yahoo symbol for the bars (default GC=F)")
    p_bt.add_argument("--seed", type=int, default=7,
                       help="determinism seed (slippage RNG)")
    p_bt.add_argument("--slippage", type=float, default=0.0, dest="slippage",
                       help="adverse slippage in ATR multiples (default 0 "
                            "= pure mechanical fills)")
    p_bt.add_argument("--journal", default=None,
                       help="equity journal JSONL path (default "
                            "<data-root>/backtest_equity.jsonl)")
    p_bt.add_argument("--json", action="store_true")
    p_bt.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_bt.set_defaults(func=cmd_backtest)

    # --- R3-3 portfolio construction + P&L attribution ---
    p_port = sub.add_parser("portfolio",
                             help="R3-3: portfolio construction — "
                                  "mean-variance / risk parity (ERC) / HRP "
                                  "weights + risk contributions + "
                                  "diversification ratio")
    p_port.add_argument("--method", default="mv", choices=["mv", "rp", "hrp"],
                         help="optimizer (default mv: seed-pinned random/"
                              "grid search on μᵀw − λ·wᵀΣw)")
    p_port.add_argument("--lookback", default="90d",
                         help="live mode: trailing return observations "
                              "(default 90d)")
    p_port.add_argument("--symbols", default=",".join(PORTFOLIO_SYMBOLS),
                         help="comma-separated Yahoo symbols for live mode "
                              f"(default {','.join(PORTFOLIO_SYMBOLS)})")
    p_port.add_argument("--max-weight", type=float, default=0.4,
                         dest="max_weight",
                         help="MV per-asset cap (default 0.4)")
    p_port.add_argument("--lambda", type=float, default=2.0,
                         dest="lambda_risk",
                         help="MV risk aversion λ (default 2.0)")
    p_port.add_argument("--seed", type=int, default=7,
                         help="MV search seed (determinism)")
    p_port.add_argument("--returns", default=None,
                         help="offline mode: JSON map {symbol: [returns]}")
    p_port.add_argument("--json", action="store_true")
    p_port.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_port.set_defaults(func=cmd_portfolio)

    p_pnl = sub.add_parser("pnl",
                            help="R3-3: P&L attribution — by asset / by "
                                 "setup / by hour-of-day with Asia/London/"
                                 "NY session labels")
    p_pnl.add_argument("--source", default="journal",
                        choices=["journal", "ledger"],
                        help="ledger source (default journal: reconstruct "
                             "closed trades from data/events/*.jsonl; "
                             "ledger: --ledger file, or the deterministic "
                             "synthetic demo ledger when no file is given)")
    p_pnl.add_argument("--ledger", default=None,
                        help="ledger file (JSONL or JSON array of trade "
                             "rows: symbol/side/qty/entry/exit/timestamp/"
                             "setup_tag)")
    p_pnl.add_argument("--json", action="store_true")
    p_pnl.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_pnl.set_defaults(func=cmd_pnl)

    # --- R4-1 autonomous watch loop + alert engine ---
    p_wl = sub.add_parser("watch-loop",
                          help="R4-1: autonomous alert sweep — price "
                               "levels, % moves, ATR/volume spikes, "
                               "correlation flips over the 8 keyless "
                               "instruments (session-gated polling, "
                               "cooldown dedup, journal + Telegram)")
    p_wl.add_argument("--dry-run", action="store_true", dest="dry_run",
                      help="one sweep, print fired alerts (default "
                           "when --daemon/--status are absent)")
    p_wl.add_argument("--daemon", action="store_true",
                      help="sweep forever every --interval seconds "
                           "(Ctrl-C exits cleanly)")
    p_wl.add_argument("--interval", type=int, default=300,
                      help="daemon sweep interval seconds (default 300)")
    p_wl.add_argument("--status", action="store_true",
                      help="print loop state (last/next sweep, rules "
                           "count, per-instrument session open/closed)")
    p_wl.add_argument("--json", action="store_true")
    p_wl.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_wl.set_defaults(func=cmd_watch_loop)

    p_al = sub.add_parser("alerts",
                          help="R4-1: list alert rules + recent fired "
                               "alerts, or ack a fired alert")
    p_al.add_argument("--ack", default=None, metavar="EVENT_ID",
                      help="mark a fired alert acknowledged")
    p_al.add_argument("--limit", type=int, default=25,
                      help="max fired alerts to list (default 25)")
    p_al.add_argument("--json", action="store_true")
    p_al.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_al.set_defaults(func=cmd_alerts)

    p_ala = sub.add_parser("alerts-add",
                           help="R4-1: add an alert rule "
                                "(price_above/price_below/pct_move/"
                                "atr_spike/volume_spike/corr_flip)")
    p_ala.add_argument("--symbol", default=None,
                       help="instrument symbol (e.g. GC=F, BTC-USD)")
    p_ala.add_argument("--kind", default="pct_move",
                       choices=["price_above", "price_below", "pct_move",
                                "atr_spike", "volume_spike", "corr_flip"],
                       help="rule kind (default pct_move)")
    p_ala.add_argument("--threshold", type=float, default=None,
                       help="pct_move: threshold %% (abs move)")
    p_ala.add_argument("--window", type=int, default=None, dest="window",
                       help="pct_move: window in 15m bars (1 = vs prior "
                            "close, the daily move)")
    p_ala.add_argument("--level", type=float, default=None,
                       help="price_above/price_below: price level")
    p_ala.add_argument("--k", type=float, default=None,
                       help="atr_spike/volume_spike: multiple of the "
                            "20-bar mean")
    p_ala.add_argument("--other", default=None,
                       help="corr_flip: the other symbol")
    p_ala.add_argument("--cooldown", type=int, default=60,
                       help="cooldown minutes before re-firing (default 60)")
    p_ala.add_argument("--note", default="", help="free-text note")
    p_ala.add_argument("--id", default="", help="explicit rule id "
                        "(default: auto <symbol>:<kind>:<n>)")
    p_ala.add_argument("--json", action="store_true")
    p_ala.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_ala.set_defaults(func=cmd_alerts_add)

    p_alr = sub.add_parser("alerts-rm",
                           help="R4-1: remove an alert rule by id")
    p_alr.add_argument("--id", required=True, help="rule id to remove")
    p_alr.add_argument("--json", action="store_true")
    p_alr.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_alr.set_defaults(func=cmd_alerts_rm)

    # --- R5 self-evolving desk ---
    p_evr = sub.add_parser("evolve-run",
                            help="R5: evolve GUESS strategy parameters — "
                                 "population + walk-forward evaluation + "
                                 "champion/challenger promotion gate vs "
                                 "the shipped incumbent (never writes the "
                                 "live spec)")
    p_evr.add_argument("--symbol", default="GC=F",
                        help="Yahoo symbol for the bars (default GC=F)")
    p_evr.add_argument("--bars", default="1y",
                        choices=["1mo", "3mo", "6mo", "1y", "2y"],
                        help="bar range (default 1y)")
    p_evr.add_argument("--seed", type=int, default=7,
                        help="determinism seed (pins the whole run)")
    p_evr.add_argument("--population", type=int, default=10,
                        help="individuals per generation (default 10)")
    p_evr.add_argument("--generations", type=int, default=6,
                        help="selection→variation rounds (default 6)")
    p_evr.add_argument("--min-trades", type=int, default=8, dest="min_trades",
                        help="min total train trades (anti-inactivity gate, "
                             "default 8)")
    p_evr.add_argument("--margin", type=float, default=0.05,
                        help="promotion margin on OOS fitness (default 0.05)")
    p_evr.add_argument("--max-gap", type=float, default=1.0, dest="max_gap",
                        help="max overfit gap IS−OOS (default 1.0)")
    p_evr.add_argument("--archive", default=None,
                        help="archive JSONL path (default "
                             "<data-root>/evolve_archive.jsonl)")
    p_evr.add_argument("--json", action="store_true")
    p_evr.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_evr.set_defaults(func=cmd_evolve_run)

    p_evs = sub.add_parser("evolve-status",
                            help="R5: evolution archive status — lineage, "
                                 "birth ops, last verdict")
    p_evs.add_argument("--archive", default=None,
                        help="archive JSONL path (default "
                             "<data-root>/evolve_archive.jsonl)")
    p_evs.add_argument("--json", action="store_true")
    p_evs.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_evs.set_defaults(func=cmd_evolve_status)

    p_les = sub.add_parser("lessons",
                            help="R5: temporal lesson store — validity "
                                 "windows, evidence counters, contradiction "
                                 "retirement (list/add/evidence/retire)")
    p_les.add_argument("lessons_cmd", nargs="?", default="list",
                        choices=["list", "add", "evidence", "retire"])
    p_les.add_argument("--text", default=None,
                        help="lesson text (for add)")
    p_les.add_argument("--symbol", default="GC=F",
                        help="lesson symbol (default GC=F; 'all' or --regime "
                             "'all' disable list filters)")
    p_les.add_argument("--regime", default="all",
                        help="regime tag (default 'all' = no filter for "
                             "list; used as tag for add)")
    p_les.add_argument("--lesson-id", default=None, dest="lesson_id",
                        help="lesson id (for evidence/retire)")
    p_les.add_argument("--outcome", default=None,
                        choices=["support", "contradict"],
                        help="evidence outcome (for evidence)")
    p_les.add_argument("--halflife", type=float, default=90.0,
                        help="confidence halflife in days (default 90)")
    p_les.add_argument("--max-age", type=float, default=365.0, dest="max_age",
                        help="max lesson age in days (default 365)")
    p_les.add_argument("--store", default=None,
                        help="store JSONL path (default "
                             "<data-root>/lessons.jsonl)")
    p_les.add_argument("--json", action="store_true")
    p_les.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_les.set_defaults(func=cmd_lessons)

    p_tr = sub.add_parser("tune-rule",
                           help="R5: champion/challenger threshold tuning "
                                "for pct_move/atr_spike watch rules — "
                                "information-content score, min-fire gate, "
                                "incumbent wins ties")
    p_tr.add_argument("--rule", default="pct_move",
                       choices=["pct_move", "atr_spike"],
                       help="rule kind to tune (default pct_move)")
    p_tr.add_argument("--symbol", default="GC=F",
                       help="Yahoo symbol for bars (default GC=F)")
    p_tr.add_argument("--bars", default="1y",
                       choices=["1mo", "3mo", "6mo", "1y", "2y"],
                       help="bar range (default 1y)")
    p_tr.add_argument("--window", type=int, default=1,
                       help="pct_move window bars (default 1)")
    p_tr.add_argument("--incumbent", type=float, default=0.005,
                       help="current threshold value (default 0.005)")
    p_tr.add_argument("--lo", type=float, default=0.001,
                       help="search bound low (default 0.001)")
    p_tr.add_argument("--hi", type=float, default=0.03,
                       help="search bound high (default 0.03)")
    p_tr.add_argument("--min-fires", type=int, default=5, dest="min_fires",
                       help="min fires per threshold (default 5)")
    p_tr.add_argument("--margin", type=float, default=0.005,
                       help="promotion margin (default 0.005)")
    p_tr.add_argument("--grain", type=float, default=0.001,
                       help="snap thresholds to this step (default 0.001)")
    p_tr.add_argument("--seed", type=int, default=7)
    p_tr.add_argument("--json", action="store_true")
    p_tr.add_argument("--data-root", default=str(REPO_ROOT / "data"))
    p_tr.set_defaults(func=cmd_tune_rule)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
