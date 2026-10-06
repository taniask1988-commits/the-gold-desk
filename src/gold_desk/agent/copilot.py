"""GAUNTLET 7 Piece 6 — the receipts-backed AI copilot.

Natural language in → the desk's own read-only CLI subcommands out → a
synthesized answer where EVERY number carries a [R#] citation to the
receipt that produced it, mechanically checked by an evidence gate
(the R2-5 zero-fabrication discipline, applied to copilot output).

BAR-COPILOT: this must EXCEED the existing /chat AGENT MODE. It does,
structurally:
  - AGENT MODE: one LLM conversation, tools ad-hoc, prose unverified.
  - copilot:   a deterministic planner executes MULTIPLE read-only
               desk commands, every number is cited to a receipt, and
               an evidence gate re-verifies every emitted number
               against the receipt payload. Untraceable numbers are
               flagged, never quietly published.

LAWS (pinned by tests/test_copilot.py):
  - The command allowlist contains ONLY read-only subcommands — no
    demo/ticket/alert/evolve/constitution mutators, by construction.
    (Mirrors test_no_mutating_tool_reachable_from_research_registry.)
  - L11: command output is DATA. The composer treats it as such; the
    evidence gate is a pure function with no LLM.
  - L10 untouched: nothing here runs in the live bar loop. This module
    is observation-layer, a read-only consumer of feeds + journal.
  - L14: the copilot proposes nothing; it answers questions.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# THE ALLOWLIST — read-only CLI subcommands the copilot may execute.
# Anything that writes (demo, watch, alerts-add/rm, tune-rule, evolve-run,
# constitution, chat/zen/ask/research sidecars with budgets, replay) is
# absent BY CONSTRUCTION — the AST test asserts these banned names never
# appear in this module's source at all.
# ---------------------------------------------------------------------------

COPILOT_COMMANDS: dict[str, dict] = {
    "markets-quant": {
        "desc": "numpy-free indicator battery (RSI14, MACD, BBands, ATR, "
                "realized vol, SMAs, ADX, Stoch, CCI, OBV)",
        "params": {"symbol": "positional"},
        "default_params": {"symbol": "GC=F"},
    },
    "markets-snapshot": {
        "desc": "verified snapshot for a symbol (price, change, day range, "
                "volume, quant fields)",
        "params": {"symbol": "positional"},
        "default_params": {"symbol": "GC=F"},
    },
    "markets-multi": {
        "desc": "8-instrument multi-asset monitor (session VWAP, relative "
                "change)",
        "params": {},
        "default_params": {},
    },
    "drivers": {
        "desc": "macro driver readings the desk tracks (DXY, yields, VIX, "
                "CFTC positioning, NFP)",
        "params": {},
        "default_params": {},
    },
    "risk": {
        "desc": "risk engine: VaR/ES, stress vectors, exposure read-out "
                "from the journal",
        "params": {},
        "default_params": {},
    },
    "portfolio": {
        "desc": "portfolio construction read-out (weights, risk "
                "contributions)",
        "params": {},
        "default_params": {},
    },
    "pnl": {
        "desc": "P&L attribution from the journal (by asset/setup/hour)",
        "params": {},
        "default_params": {},
    },
    "eod": {
        "desc": "end-of-day summary from the journal (bars, reason "
                "histogram, tickets)",
        "params": {"date": {"flag": "--date"}},
        "default_params": {},
    },
    "events": {
        "desc": "recent journal events (kind/reason filtered)",
        "params": {"limit": {"flag": "--limit"}},
        "default_params": {"limit": 10},
    },
    "pair": {
        "desc": "pair analytics: spread/ratio, rolling z-score, EG "
                "cointegration",
        "params": {"a": "positional", "b": "positional"},
        "default_params": {"a": "GC=F", "b": "SLV"},
    },
    "macro-dashboard": {
        "desc": "macro plane: rates complex, CPI, dollar, VIX, curve "
                "history",
        "params": {},
        "default_params": {},
    },
    "curve-history": {
        "desc": "Treasury yield-curve history (keyless Treasury.gov XML)",
        "params": {},
        "default_params": {},
    },
    "options-analytics": {
        "desc": "options analytics: Greeks, IV skew, OI wall, max pain, "
                "put/call, IV rank",
        "params": {"symbol": "positional"},
        "default_params": {"symbol": "GLD"},
    },
    "news": {
        "desc": "recent gold/macro headlines (keyless RSS)",
        "params": {"limit": {"flag": "--limit"}},
        "default_params": {"limit": 6},
    },
    "screen": {
        "desc": "screener: technical + fundamental filters over the "
                "registry",
        "params": {"where": {"flag": "--where"},
                   "universe": {"flag": "--universe"},
                   "technical_only": "store_true"},
        "default_params": {"where": "rsi < 70", "technical_only": True},
    },
}

# The desk's mutators (demo, watch, alerts-add/rm, tune-rule, evolve-run,
# constitution, replay, the budgeted sidecars) are absent BY CONSTRUCTION
# — tests/test_copilot.py owns the banned list and pins this allowlist.

_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("markets-quant", ("rsi", "macd", "atr", "indicator", "overbought",
                       "oversold", "technical", "stoch", "adx", "bollinger",
                       "momentum", "quant")),
    ("drivers", ("cftc", "positioning", "dxy", "dollar", "vix", "yield",
                 "yields", "rates", "nfp", "driver", "drivers",
                 "real yield")),
    ("risk", ("desk view", "desk state", "exposure", "var", "stress",
              "risk", "drawdown")),
    ("options-analytics", ("option", "options", "greeks", "max pain",
                           "max-pain", "open interest", "oi wall",
                           "iv rank", "iv skew", "put/call", "pcr")),
    ("pair", ("pair", "spread", "relative value", "ratio", "cointegrat",
              "z-score", "zscore", "vs silver", "silver ratio")),
    ("macro-dashboard", ("macro", "fed", "fomc", "cpi", "gdp", "inflation",
                         "curve", "fred", "treasury", "rate path")),
    ("screen", ("screen", "screener", "scan", "universe", "filter symbols",
                "find symbols")),
    ("portfolio", ("portfolio", "allocation", "weights", "hrp",
                   "risk parity", "optimization")),
    ("pnl", ("pnl", "attribution", "performance", "pl by", "profit")),
    ("eod", ("eod", "end of day", "end-of-day", "session summary",
             "today summary")),
    ("events", ("journal", "events", "what happened", "reason codes",
                "log")),
    ("markets-snapshot", ("snapshot", "overview", "quote", "price of",
                          "level", "day range")),
    ("news", ("news", "headline", "wire", "latest stories")),
    ("markets-multi", ("board", "multi-asset", "instruments", "sweep",
                       "monitor")),
    ("markets-snapshot", ("gold", "silver", "btc", "bitcoin")),
]

# symbol resolution: natural names → registry-normalized Yahoo symbols
_SYMBOL_ALIASES = {
    "gold": "GC=F", "xauusd": "GC=F", "xau": "GC=F", "gld": "GLD",
    "silver": "SLV", "xagusd": "SI=F", "slv": "SLV",
    "bitcoin": "BTC-USD", "btc": "BTC-USD", "btcusd": "BTC-USD",
    "ethereum": "ETH-USD", "eth": "ETH-USD",
    "oil": "CL=F", "wti": "CL=F", "vix": "^VIX", "dxy": "DX-Y.NYB",
    "sp500": "^GSPC", "spx": "^GSPC", "nasdaq": "^IXIC",
    "copper": "HG=F", "platinum": "PL=F", "es": "ES=F",
}


def resolve_symbol(token: str) -> str:
    """'gold' → GC=F; unknown tokens pass through upper-cased."""
    t = (token or "").strip().lower().rstrip(".,?!")
    return _SYMBOL_ALIASES.get(t, (token or "").strip().upper())


def plan(question: str, max_commands: int = 5) -> list[tuple[str, dict]]:
    """Deterministic intent router: question → ordered command plan.

    Pure keyword scoring, no LLM, no network. The SAME question always
    plans the SAME commands (reproducibility is the point). Every
    distinct symbol mentioned gets its own indicator leg (up to 3) —
    "silver's RSI vs platinum's ATR" plans BOTH, never just gold.
    """
    q = " " + re.sub(r"\s+", " ", (question or "").lower().strip()) + " "
    chosen: list[tuple[str, dict]] = []
    seen: set[tuple] = set()

    def _key(name, params):
        return (name, tuple(sorted((k, str(v)) for k, v in
                                   (params or {}).items())))

    def _add(name: str, params: dict) -> None:
        if name in COPILOT_COMMANDS and _key(name, params) not in seen \
                and len(chosen) < max_commands:
            spec = COPILOT_COMMANDS[name]
            merged = dict(spec.get("default_params") or {})
            merged.update(params or {})
            chosen.append((name, merged))
            seen.add(_key(name, merged))

    # every alias mention, in order of first appearance
    mentioned: list[str] = []
    for alias, yahoo in _SYMBOL_ALIASES.items():
        if yahoo in mentioned:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", q):
            mentioned.append(yahoo)
    symbol = mentioned[0] if mentioned else "GC=F"

    import datetime as _dt
    quant_requested = False
    for name, kws in _INTENT_KEYWORDS:
        for kw in kws:
            if kw in q:
                params: dict = {}
                if name in ("markets-quant", "markets-snapshot",
                            "options-analytics"):
                    params["symbol"] = symbol
                if name == "pair":
                    params = {"a": symbol, "b": "SLV" if symbol == "GC=F"
                              else "GC=F"}
                if name == "eod":
                    params["date"] = _dt.datetime.now(
                        _dt.timezone.utc).strftime("%Y-%m-%d")
                if name == "markets-quant":
                    quant_requested = True
                _add(name, params)
                break

    # one indicator leg per additional mentioned symbol (≤3 total)
    if quant_requested:
        for extra in mentioned[1:3]:
            _add("markets-quant", {"symbol": extra})

    # grounded fallback: never answer from nothing
    if not chosen:
        _add("markets-snapshot", {"symbol": symbol})
        _add("drivers", {})
    return chosen


# ---------------------------------------------------------------------------
# receipted execution — shell out to the desk's OWN CLI, read-only verbs
# ---------------------------------------------------------------------------

def build_argv(name: str, params: dict) -> list[str]:
    """Command spec + params → CLI argv (validated against the allowlist)."""
    if name not in COPILOT_COMMANDS:
        raise ValueError(f"command not in read-only allowlist: {name}")
    spec = COPILOT_COMMANDS[name]
    argv = [name]
    for pname, how in (spec.get("params") or {}).items():
        if pname not in params or params[pname] is None:
            continue
        v = params[pname]
        if how == "positional":
            argv.append(str(v))
        elif how == "store_true":
            if v:
                argv.append("--" + pname.replace("_", "-"))
        else:
            argv += [how["flag"], str(v)]
    return argv


def _receipt_env() -> dict:
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin",
           "PYTHONPATH": str(REPO_ROOT / "src")}
    for k in ("FRED_API_KEY", "GOLD_DESK_TG_TOKEN"):
        v = __import__("os").environ.get(k, "")
        if v:
            env[k] = v
    return env


def run_receipt(name: str, params: dict, data_root=None,
                timeout: float = 60.0) -> dict:
    """Execute ONE allowlisted read-only CLI command; return a receipt.

    The receipt is the citation target: command, argv, ts, ok, data.
    Fail-soft: a crashed or unparseable run is a receipt with ok=False
    and the raw output preserved — never an exception out of here.
    """
    t0 = time.time()
    argv = build_argv(name, params)
    root = str(data_root or (REPO_ROOT / "data"))
    full_argv = [*argv, "--json", "--data-root", root]
    cmd = [sys.executable, "-m", "gold_desk.cli", *full_argv]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout, env=_receipt_env(),
                              cwd=str(REPO_ROOT))
        raw = proc.stdout or ""
        err = (proc.stderr or "")[-400:]
        ok = proc.returncode == 0
    except subprocess.TimeoutExpired:
        return {"receipt_id": "", "command": name, "argv": full_argv,
                "ts": _iso_now(), "ok": False, "data": {},
                "error": f"timeout after {timeout:.0f}s",
                "duration_ms": int((time.time() - t0) * 1000)}
    except OSError as e:
        return {"receipt_id": "", "command": name, "argv": full_argv,
                "ts": _iso_now(), "ok": False, "data": {},
                "error": f"spawn failed: {e}",
                "duration_ms": int((time.time() - t0) * 1000)}
    data: dict = {}
    if raw.strip():
        try:
            parsed = json.loads(raw)
            data = parsed if isinstance(parsed, dict) else {"value": parsed}
        except json.JSONDecodeError:
            ok = False
            data = {"raw": raw[:600]}
            err = err or "stdout was not valid JSON"
    return {"receipt_id": "", "command": name, "argv": full_argv,
            "ts": _iso_now(), "ok": ok, "data": data,
            "error": None if ok else (err or f"exit {proc.returncode}"),
            "duration_ms": int((time.time() - t0) * 1000)}


def _iso_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# deterministic composer — every number carries a [R#] citation
# ---------------------------------------------------------------------------

def _fmt(v, dec: int = 2) -> str:
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int,)):
        return str(v)
    if isinstance(v, float):
        return f"{v:.{dec}f}"
    return str(v)


def _fmt_num(v) -> str | None:
    """Numbers formatted 2dp when float; ints verbatim."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        try:
            if v != v or v in (float("inf"), float("-inf")):
                return None
        except Exception:
            return None
        return f"{v:.2f}"
    return None


def _find(data, *keys):
    """Depth-first search for the first key match in nested dicts/lists."""
    for k in keys:
        if k in data:
            return data[k]
    if isinstance(data, dict):
        for v in data.values():
            r = _find(v, *keys) if isinstance(v, (dict, list)) else None
            if r is not None:
                return r
    elif isinstance(data, list):
        for v in data:
            r = _find(v, *keys) if isinstance(v, (dict, list)) else None
            if r is not None:
                return r
    return None


def _facts(name: str, r: dict) -> list[str]:
    """Per-command headline facts, each ending with its [R#] citation."""
    rid = r["receipt_id"]
    d = r.get("data") or {}
    lines: list[str] = []
    if not r.get("ok"):
        lines.append(f"- command `{name}` failed: "
                     f"{(r.get('error') or 'unknown')[:200]} [R{rid}]")
        return lines
    if name == "markets-quant":
        rsi = _find(d, "rsi14", "rsi")
        macd = _find(d, "macd", "macd_hist")
        atr = _find(d, "atr14", "atr", "atr_pct")
        trend = _find(d, "trend", "regime", "vol_regime")
        sym = _find(d, "symbol") or ""
        if rsi is not None:
            lines.append(f"- RSI14 **{_fmt(rsi)}** for {sym} [R{rid}] — "
                         + ("oversold (<30)" if isinstance(rsi, (int, float))
                            and rsi < 30 else
                            "overbought (>70)" if isinstance(rsi, (int, float))
                            and rsi > 70 else "neutral band"))
        if isinstance(macd, dict):
            hist = macd.get("hist")
            line = macd.get("line")
            if hist is not None:
                lines.append(f"- MACD hist **{_fmt(hist)}** [R{rid}]")
            if line is not None:
                lines.append(f"- MACD line **{_fmt(line)}** [R{rid}]")
        elif macd is not None:
            lines.append(f"- MACD **{_fmt(macd)}** [R{rid}]")
        if atr is not None:
            lines.append(f"- ATR **{_fmt(atr)}** [R{rid}]")
        if trend:
            lines.append(f"- regime/trend: {trend} [R{rid}]")
    elif name == "markets-snapshot":
        px = _find(d, "last_close", "price", "last", "c")
        chg = _find(d, "last_change_pct", "change", "change_pct")
        hi20 = _find(d, "change_pct_20d")
        rsi = _find(d, "rsi14")
        if px is not None:
            lines.append(f"- last close **{_fmt(px)}** [R{rid}]")
        if chg is not None:
            lines.append(f"- last change **{_fmt(chg)}**% [R{rid}]")
        if hi20 is not None:
            lines.append(f"- 20d change **{_fmt(hi20)}**% [R{rid}]")
        if rsi is not None:
            lines.append(f"- RSI14 **{_fmt(rsi)}** [R{rid}]")
    elif name == "drivers":
        live = d.get("live") if isinstance(d.get("live"), dict) else \
            _find(d, "live") or {}
        _names = {"D1": "10y real yield", "D2": "DXY", "D3": "policy rate path",
                  "D4": "10y breakeven", "D5": "COT managed money",
                  "D9": "event clock (NFP)", "D10": "VIX",
                  "D11": "session liquidity"}
        if isinstance(live, dict):
            for did, row in sorted(live.items())[:8]:
                if isinstance(row, dict) and row.get("value") is not None:
                    unit = row.get("unit") or ""
                    label = _names.get(str(did), str(did))
                    lines.append(f"- {label}: **{_fmt(row.get('value'))}** "
                                 f"{unit} (source: {row.get('source', 'n/a')}"
                                 f") [R{rid}]")
        for u in (d.get("unavailable") or [])[:4]:
            lines.append(f"- {u}: unavailable (surfaced honestly) [R{rid}]")
    elif name == "risk":
        var = _find(d, "var") or {}
        var95 = var.get("historical", {}).get("95") if isinstance(
            var, dict) else None
        es = _find(d, "expected_shortfall") or {}
        es95 = es.get("historical_95") if isinstance(es, dict) else None
        beta_raw = _find(d, "beta")
        beta = beta_raw.get("beta") if isinstance(beta_raw, dict) else None
        stress = (_find(d, "stress") or {}).get("portfolio_shocks") or {}
        for label, v in (("VaR95 (historical)", var95),
                         ("ES95 (historical)", es95)):
            if v is not None and not isinstance(v, (dict, list)):
                lines.append(f"- {label} **{_fmt(v)}** [R{rid}]")
        if isinstance(beta, (int, float)):
            lines.append(f"- gold/BTC beta **{_fmt(beta)}** [R{rid}]")
        if isinstance(stress, dict):
            for k, v in list(stress.items())[:3]:
                lines.append(f"- stress {k}: **{_fmt(v)}** [R{rid}]")
        if not lines:
            lines.append(f"- risk engine returned no scalar exposure "
                         f"fields — full payload [R{rid}]")
    elif name == "portfolio":
        w = _find(d, "weights", "weight")
        if isinstance(w, dict):
            for k, v in list(w.items())[:8]:
                lines.append(f"- {k}: **{_fmt(v)}** weight [R{rid}]")
        else:
            lines.append(f"- portfolio payload [R{rid}]")
    elif name == "pnl":
        total = _find(d, "total_pnl", "pnl_total", "total")
        by_setup = _find(d, "by_setup", "by_asset")
        if total is not None and not isinstance(total, (dict, list)):
            lines.append(f"- total P&L **{_fmt(total)}** [R{rid}] "
                         "(journal-derived; scrubbed in agent contexts)")
        if isinstance(by_setup, dict):
            for k, v in list(by_setup.items())[:6]:
                lines.append(f"- {k}: **{_fmt(v)}** [R{rid}]")
        if not lines:
            lines.append(f"- P&L attribution payload [R{rid}]")
    elif name == "eod":
        hist = d.get("histogram") or {}
        if isinstance(hist, dict):
            top = sorted(hist.items(), key=lambda kv: -kv[1])[:5]
            for code, n in top:
                lines.append(f"- {code}: **{n}** bars [R{rid}]")
        tk = d.get("tickets")
        if tk is not None:
            lines.append(f"- tickets: **{len(tk) if isinstance(tk, list)
                       else tk}** [R{rid}]")
    elif name == "events":
        evs = d.get("events") or []
        lines.append(f"- **{d.get('count', len(evs))}** journal events "
                     f"[R{rid}]")
        for e in evs[:4]:
            if isinstance(e, dict):
                lines.append(f"- {e.get('ts', '')} {e.get('kind', '')} "
                             f"{e.get('reason_code') or ''} [R{rid}]")
    elif name == "pair":
        z = _find(d, "zscore_now")
        beta = _find(d, "beta") if isinstance(d.get("hedge"), dict) else None
        eg = d.get("cointegration") or {}
        label = d.get("spread_label") or "pair"
        if z is not None:
            lines.append(f"- {label} z-score now **{_fmt(z)}** (±2σ band) "
                         f"[R{rid}]")
        if beta is not None:
            lines.append(f"- hedge beta **{_fmt(beta)}** [R{rid}]")
        if isinstance(eg, dict) and eg.get("verdict"):
            tstat = eg.get("adf_t_stat")
            tpart = f"ADF t-stat {_fmt(tstat)}, " if tstat is not None else ""
            lines.append(f"- cointegration: {eg['verdict']} ({tpart}"
                         f"exploratory) [R{rid}]")
    elif name == "macro-dashboard" or name == "curve-history":
        rows = _find(d, "series", "rows", "curve") or {}
        if isinstance(rows, dict):
            for k, v in list(rows.items())[:8]:
                if isinstance(v, (int, float)):
                    lines.append(f"- {k}: **{_fmt(v)}** [R{rid}]")
                elif isinstance(v, dict) and v.get("value") is not None:
                    lines.append(f"- {k}: **{_fmt(v.get('value'))}** "
                                 f"({v.get('source', 'n/a')}) [R{rid}]")
        elif isinstance(rows, list):
            for row in rows[:8]:
                if isinstance(row, dict) and row.get("value") is not None:
                    lines.append(f"- {row.get('tenor') or row.get('date')}"
                                 f": **{_fmt(row.get('value'))}** [R{rid}]")
        if not lines:
            lines.append(f"- macro payload [R{rid}]")
    elif name == "options-analytics":
        pcr = _find(d, "put_call_ratio", "pcr")
        mp = _find(d, "max_pain", "max_pain_strike")
        ivr = _find(d, "iv_rank", "ivrank")
        sym = _find(d, "symbol") or ""
        if pcr is not None:
            lines.append(f"- {sym} put/call ratio **{_fmt(pcr)}** [R{rid}]")
        if mp is not None:
            lines.append(f"- max pain **{_fmt(mp)}** [R{rid}]")
        if ivr is not None:
            lines.append(f"- IV rank **{_fmt(ivr)}** [R{rid}]")
    elif name == "news":
        items = d.get("items") or []
        lines.append(f"- **{d.get('count', len(items))}** headlines "
                     f"[R{rid}]")
        for it in items[:3]:
            if isinstance(it, dict):
                lines.append(f"- {str(it.get('title', ''))[:110]} "
                             f"[R{rid}]")
    elif name == "markets-multi":
        rows = d.get("rows") or d.get("instruments") or []
        if isinstance(rows, list):
            for row in rows[:8]:
                if isinstance(row, dict):
                    chg = row.get("change_pct", row.get("change"))
                    if chg is not None:
                        lines.append(f"- {row.get('symbol', '?')}: "
                                     f"**{_fmt(chg)}** [R{rid}]")
    elif name == "screen":
        rows = d.get("rows") or d.get("results") or []
        lines.append(f"- **{len(rows) if isinstance(rows, list) else '?'}** "
                     f"symbols passed the filters [R{rid}]")
        for row in (rows if isinstance(rows, list) else [])[:5]:
            if isinstance(row, dict):
                lines.append(f"- {row.get('symbol', '?')} "
                             f"(rsi {row.get('rsi', 'n/a')}) [R{rid}]")
    if not lines:
        lines.append(f"- payload [R{rid}] (no headline scalar surfaced — "
                     "honesty over invention)")
    return lines


def compose(question: str, receipts: list[dict]) -> dict:
    """Deterministic answer composition — every number cited to [R#]."""
    parts: list[str] = [f"**Q:** {question}", "",
                        "Executed the desk's own read-only commands below; "
                        "every number is mechanically cited to a receipt "
                        "and evidence-gated.", ""]
    for r in receipts:
        okmark = "ok" if r.get("ok") else "FAILED"
        show: list[str] = []
        skip_next = False
        for a in r["argv"]:
            if skip_next:
                skip_next = False
                continue
            if a == "--json":
                continue
            if a == "--data-root":
                skip_next = True
                continue
            show.append(a)
        parts.append(f"### {r['command']} [R{r['receipt_id']}]")
        parts.append(f"argv: `{' '.join(show)}` · {okmark}")
        parts.extend(_facts(r["command"], r))
        parts.append("")
    gate = evidence_gate("\n".join(parts[1:]), receipts)
    return {"ok": True, "question": question, "answer_md": "\n".join(parts),
            "receipts": receipts, "evidence": gate,
            "published": gate["ok"], "mode": "deterministic"}


# ---------------------------------------------------------------------------
# evidence gate — the mechanical zero-fabrication check (R2-5 discipline)
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"\[R\d+\]")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_NUM_RE = re.compile(
    r"((?<![\w./])-\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w./])-\d+(?:\.\d+)?"
    r"|(?<![\w./-])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![\w./-])\d+(?:\.\d+)?)")


def evidence_gate(text: str, receipts: list[dict]) -> dict:
    """Extract EVERY number in the answer text; trace each to a receipt.

    A number is traceable iff some number in the receipt payload (argv
    included) matches within half-a-last-decimal of the text's rounding
    (e.g. text '43.22' matches receipt 43.2178; text '12' matches 12.x).
    Mechanical, pure, no LLM. Any untraceable number blocks publication.
    """
    body = _DATE_RE.sub(" ", _CITE_RE.sub("", text or ""))
    haystack: list[float] = []
    for r in receipts:
        blob = json.dumps(r.get("data") or {}, default=str) + " " + \
            " ".join(str(a) for a in r.get("argv") or []) + " " + \
            str(r.get("receipt_id") or "")
        for m in re.finditer(r"-?\d+(?:\.\d+)?(?:e[+-]?\d+)?", blob,
                             re.IGNORECASE):
            try:
                haystack.append(float(m.group(0)))
            except ValueError:
                continue
    untraceable: list[dict] = []
    checked = 0
    for m in _NUM_RE.finditer(body):
        tok = m.group(1).replace(",", "")
        try:
            n = float(tok)
        except ValueError:
            continue
        checked += 1
        dec = len(tok.split(".")[1]) if "." in tok else 0
        tol = 0.5 * (10 ** -dec) + 1e-9
        if not any(abs(n - h) <= tol for h in haystack):
            ctx = body[max(0, m.start() - 40):m.end() + 20]
            untraceable.append({"value": tok, "context": "... " +
                                " ".join(ctx.split()) + " ..."})
    return {"checked": checked, "untraceable": untraceable,
            "ok": not untraceable,
            "note": "every number must trace to a receipt payload "
                    "(half-last-decimal tolerance) — R2-5 discipline"}


# ---------------------------------------------------------------------------
# orchestration entry
# ---------------------------------------------------------------------------

def ask(question: str, data_root=None, max_commands: int = 4,
        timeout: float = 60.0) -> dict:
    """NL question → plan → receipted runs → composed, gated answer."""
    t0 = time.time()
    p = plan(question, max_commands=max_commands)
    receipts: list[dict] = []
    for i, (name, params) in enumerate(p, 1):
        r = run_receipt(name, params, data_root=data_root, timeout=timeout)
        r["receipt_id"] = str(i)
        receipts.append(r)
    out = compose(question, receipts)
    out["elapsed_ms"] = int((time.time() - t0) * 1000)
    out["plan"] = [name for name, _ in p]
    return out
