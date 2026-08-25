"""§9.4 — replay. Reconstruct any day purely from the journal. If you cannot
answer "why this ticket", it should not have existed.

replay --date YYYY-MM-DD prints, per bar: decision_ts, bar summary, terminal
reason code, plus candidate/gate/ticket/human chains when they exist, then a
reason-code histogram and (if any) the full ticket story.
"""
from __future__ import annotations

import json
from pathlib import Path


def replay_day(data_root: str | Path, date: str) -> dict:
    events = _read_day(Path(data_root), date)
    bars: list[dict] = []
    by_ticket: dict[str, list[dict]] = {}
    for ev in events:
        kind = ev.get("kind")
        if kind == "BarReceived":
            bars.append({
                "decision_ts": ev.get("decision_ts"),
                "bar": ev.get("payload", {}).get("bar"),
                "reason_code": None,
                "story": [],
            })
        tid = ev.get("payload", {}).get("ticket_id")
        if tid:
            by_ticket.setdefault(tid, []).append(ev)
    # attach terminal reason codes to bars: events are append-ordered, so
    # the LAST reason-coded event on a decision_ts is the bar's terminal code
    # (matches the orchestrator's close_bar_reason bookkeeping)
    for ev in events:
        code = ev.get("reason_code")
        dts = ev.get("decision_ts")
        if code and dts:
            for b in bars:
                if b["decision_ts"] == dts:
                    b["reason_code"] = code
                    b["story"].append({
                        "kind": ev.get("kind"),
                        "code": code,
                        "detail": ev.get("payload", {}).get("detail")
                        or ev.get("payload", {}).get("code")
                        or ev.get("payload", {}).get("action"),
                    })
                    break
    histogram: dict[str, int] = {}
    for b in bars:
        if b["reason_code"]:
            histogram[b["reason_code"]] = histogram.get(b["reason_code"], 0) + 1
    tickets = []
    for tid, evs in by_ticket.items():
        first = evs[0].get("payload", {})
        # authoritative status: the LAST ticket snapshot that carries one
        status = None
        for e in evs:
            st = (e.get("payload") or {}).get("status")
            if st:
                status = st
        tickets.append({
            "ticket_id": tid,
            "side": first.get("side"),
            "entry": first.get("entry"),
            "stop": first.get("stop"),
            "target": first.get("target"),
            "lots": first.get("lots"),
            "status": status or first.get("status"),
            "events": [
                {"ts": e.get("ts"), "kind": e.get("kind"),
                 "reason": e.get("reason_code")}
                for e in evs
            ],
        })
    return {"date": date, "bars": bars, "histogram": histogram, "tickets": tickets,
            "event_count": len(events)}


def render_replay(report: dict) -> str:
    lines = [f"REPLAY {report['date']} — {report['event_count']} events",
             "=" * 72]
    for b in report["bars"]:
        bar = b.get("bar") or {}
        lines.append(
            f"{b['decision_ts']}  O={bar.get('o')} H={bar.get('h')} "
            f"L={bar.get('l')} C={bar.get('c')}  -> {b['reason_code']}"
        )
        for s in b["story"]:
            if s["kind"] not in ("BarReceived", "NoSetup"):
                lines.append(f"    {s['kind']}: {s['code'] or ''} {s['detail'] or ''}".rstrip())
    lines.append("")
    lines.append("REASON HISTOGRAM:")
    for code, n in sorted(report["histogram"].items(), key=lambda kv: -kv[1]):
        lines.append(f"  {code:24s} {n}")
    if report["tickets"]:
        lines.append("")
        lines.append("TICKETS:")
        for t in report["tickets"]:
            lines.append(
                f"  {t['ticket_id']} {t['side']} entry={t['entry']} "
                f"stop={t['stop']} target={t['target']} lots={t['lots']} "
                f"status={t['status']}"
            )
            for e in t["events"]:
                lines.append(f"      {e['ts']} {e['kind']} {e['reason'] or ''}".rstrip())
    return "\n".join(lines)


def _read_day(root: Path, date: str) -> list[dict]:
    path = root / "events" / f"{date}.jsonl"
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out
