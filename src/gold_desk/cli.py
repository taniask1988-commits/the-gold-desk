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
    """markets-multi — R3-1 Build 1: 8-instrument live multi-asset monitor
    (gold, S&P e-mini, US 10y yield, DXY, BTC, VIX, WTI, EUR/USD) with
    per-asset session VWAP + session-relative % move. Fail-soft per asset.
    """
    from .markets.multi_asset import MultiAssetMonitor, INSTRUMENT_ORDER
    mon = MultiAssetMonitor(data_root=args.data_root)
    out = mon.snapshot()
    if args.json:
        print(json.dumps(out, sort_keys=True, default=str))
        return 0 if out.get("ok") else 1
    if not out.get("ok"):
        print("multi-asset monitor failed:", out.get("error"))
        return 1
    print("MULTI-ASSET MONITOR — 8 instruments (keyless Yahoo)")
    print("=" * 72)
    print(f"as of : {out.get('as_of', '?')}"
          + ("   (cached)" if out.get("cache_hit") else ""))
    print()
    assets = out.get("assets") or {}
    print(f"{'symbol':<10s}{'name':<18s}{'price':>12s}"
          f"{'1d %':>9s}{'sess':>10s}{'vwap':>12s}{'rel %':>9s}")
    print("-" * 72)
    for sym in INSTRUMENT_ORDER:
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
    print("NEWS SENTIMENT — local lexicon + LLM fallback (R3-2)")
    print("=" * 64)
    print(f"headline    : {out.get('headline')}")
    print(f"polarity    : {_polarity_gauge(out.get('polarity', 0.0))}"
          f"  ({out.get('label')})")
    print(f"magnitude   : {out.get('magnitude', 0):.3f}    "
          f"subjectivity: {out.get('subjectivity', 0):.3f}")
    print(f"novelty     : {out.get('novelty', 0):.3f}    "
          f"relevance   : {out.get('relevance', 0):.3f}")
    assets = out.get("assets") or []
    if assets:
        parts = [f"{a['symbol']} ({a['name']}, conf {a['confidence']:.1f}, "
                 f"rel {a['relevance']:.2f})" for a in assets]
        print(f"assets      : " + " | ".join(parts))
    else:
        print("assets      : (none of the 8 desk instruments detected)")
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


def _risk_default_portfolio(data_root: str):
    """Live default portfolio: 1y daily closes per symbol (keyless Yahoo,
    cached, fail-soft per symbol), DATE-ALIGNED across calendars (the
    R3-1 D2 lesson) and blended by weight. Returns (positions, benchmark)
    where benchmark is the SPY return series; None on any fetch failure
    (fail-closed — a missing leg must never silently re-weight).
    """
    from datetime import datetime, timezone

    from .markets.board import fetch_daily_bars
    from .risk.metrics import date_aligned_returns, portfolio_returns

    def _day_key(bar: dict) -> str | None:
        """fetch_daily_bars stamps bars with EPOCH-MS integers (board.py's
        shape) — convert to a UTC YYYY-MM-DD key; tolerate ISO strings too.
        Returns None on an unusable timestamp (bar dropped, not crashed)."""
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


def cmd_risk(args) -> int:
    """risk — R3-2 Build 4: VaR (parametric Gaussian / historical /
    Monte Carlo) at 95%+99%, Expected Shortfall, beta-adjusted exposure
    and the GFC/COVID/2022 stress scenarios for a portfolio.
    """
    from .risk.metrics import risk_report
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
                                 "EUR/USD) — live session VWAP + relative %")
    p_mam.add_argument("--json", action="store_true")
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
                                  "(GFC/COVID/2022) for a portfolio")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
