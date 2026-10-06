"""GAUNTLET 7 Piece 7 — workflow glue: byte-identical CSV export.

One canonical CSV builder (RFC 4180, stdlib csv module) feeds every
export surface: the CLI `export-csv` subcommand and the web deck's
/api/desk/export route. Byte-identical to the rendered table follows
from construction — the CSV and the JSON table rows come from the SAME
underlying command output, and the builder is pinned by round-trip
tests (quoted fields, embedded commas/newlines, unicode, None → '').

Honest labels: every CSV carries a comment header row (# ...) naming
the source command and generation timestamp, so an exported file is
self-describing — same discipline as the on-screen source hovers.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone


def rows_to_csv(columns: list[str], rows: list[dict],
                source: str = "") -> str:
    """Canonical CSV: comment preamble + header + data rows.

    - columns: the exact columns the table renders, in order.
    - rows: list of dicts keyed by column name (missing → '').
    - source: provenance stamped in the # comment preamble.
    - RFC 4180 minimal quoting (csv module default), CRLF line endings.
    """
    buf = io.StringIO()
    if source:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        buf.write(f"# gold-desk export — {source} — {ts}\r\n")
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(columns)
    for r in rows:
        w.writerow([("" if r.get(c) is None else r.get(c)) for c in columns])
    return buf.getvalue()


# ----------------------------------------------------------------- kinds
# Every kind maps to the SAME function the on-screen table uses, so the
# export is byte-identical to what's rendered by construction.

def _flatten_event(e: dict) -> dict:
    return {
        "ts": e.get("ts", ""),
        "kind": e.get("kind", ""),
        "reason_code": e.get("reason_code", ""),
        "symbol": (e.get("payload") or {}).get("symbol", "")
        if isinstance(e.get("payload"), dict) else "",
    }


def export_journal(data_root, limit: int = 500) -> dict:
    from .events import Journal
    events = Journal.read_events(data_root)[-abs(limit):]
    cols = ["ts", "kind", "reason_code", "symbol"]
    return {"ok": True, "columns": cols,
            "rows": [_flatten_event(e) for e in events],
            "source": f"events --limit {limit}"}


def export_screener(data_root, where: str, universe: str = "registry",
                    technical_only: bool = False, limit: int = 50) -> dict:
    from .markets.screener import screen
    out = screen(where, universe, data_root,
                 with_fundamentals=not technical_only, limit=limit)
    if not out.get("ok"):
        return {"ok": False, "errors": out.get("errors") or ["screen failed"]}
    cols = ["symbol", "name", "price", "rsi", "change_30d",
            "above_sma200"]
    rows = []
    for r in out.get("rows") or []:
        f = r.get("fields") or {}
        rows.append({
            "symbol": r.get("symbol", ""),
            "name": r.get("name", ""),
            "price": f.get("price"),
            "rsi": f.get("rsi"),
            "change_30d": f.get("change_30d"),
            "above_sma200": f.get("above_sma200"),
        })
    return {"ok": True, "columns": cols, "rows": rows,
            "source": f"screen --where '{where}' --universe {universe}"}


def export_pair(data_root, a: str, b: str, window: int = 60,
                mode: str = "ratio") -> dict:
    from .markets.pairs import pair_analytics
    out = pair_analytics(a, b, window=window, data_root=data_root,
                         mode=mode)
    if not out.get("ok"):
        return {"ok": False, "errors": out.get("errors") or ["pair failed"]}
    cols = ["date", "spread", "z"]
    zmap = {i: (z or {}).get("z") for i, z in enumerate(out.get("zscore")
                                                    or [])}
    rows = [{"date": d, "spread": s, "z": zmap.get(i)}
            for i, (d, s) in enumerate(zip(out.get("dates") or [],
                                           out.get("spread") or []))]
    return {"ok": True, "columns": cols, "rows": rows,
            "source": f"pair {out.get('a')} {out.get('b')} "
                      f"--window {window} --mode {mode}"}


def export_macro(data_root) -> dict:
    from .macro.fred import macro_dashboard
    out = macro_dashboard(data_root)
    if not out.get("ok"):
        return {"ok": False, "errors": out.get("errors") or
                ["macro dashboard unavailable (all series failed)"]}
    labels = out.get("labels") or {}
    cols = ["series", "label", "date", "value"]
    rows = []
    for sid, snap in (out.get("snapshot") or {}).items():
        if not isinstance(snap, dict):
            continue  # failed series: absent, not fabricated
        rows.append({"series": sid,
                     "label": labels.get(sid, sid) if isinstance(
                         labels, dict) else sid,
                     "date": snap.get("date", ""),
                     "value": snap.get("value")})
    return {"ok": True, "columns": cols, "rows": rows,
            "source": "macro-dashboard (sources: "
                      + ", ".join(out.get("sources") or []) + ")"}


def export_options(data_root, symbol: str = "GLD") -> dict:
    from .markets.options import analytics
    out = analytics(symbol, data_root=data_root)
    if not out.get("ok"):
        return {"ok": False, "errors": out.get("errors") or
                [f"options analytics unavailable for {symbol}"]}
    cols = ["strike", "call_oi", "put_oi", "total_oi"]
    rows = [{"strike": w.get("strike"),
             "call_oi": w.get("call_oi"),
             "put_oi": w.get("put_oi"),
             "total_oi": w.get("total_oi")}
            for w in (out.get("oi_wall") or [])
            if isinstance(w, dict)]
    return {"ok": True, "columns": cols, "rows": rows,
            "source": f"options-analytics {symbol} "
                      f"({out.get('source', 'n/a')}, "
                      f"delay={out.get('data_delay', 'n/a')})"}


def export_csv(kind: str, data_root, **kwargs) -> dict:
    """Dispatch: journal | screener | pair | macro | options."""
    k = (kind or "").strip().lower()
    if k == "journal":
        return export_journal(data_root, **kwargs)
    if k == "screener":
        return export_screener(data_root, **kwargs)
    if k == "pair":
        return export_pair(data_root, **kwargs)
    if k == "macro":
        return export_macro(data_root)
    if k == "options":
        return export_options(data_root, **kwargs)
    return {"ok": False,
            "errors": [f"unknown export kind: {kind} "
                       "(journal | screener | pair | macro | options)"]}
