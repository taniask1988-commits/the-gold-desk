"""R4-1 — alert persistence: rule CRUD + fired-event log (JSON).

One file, `<data_root>/watch/alerts.json`:

    {
      "version": 1,
      "rules":       [AlertRule dicts],
      "last_fired":  {rule_id: ISO stamp}   ← AlertEngine cooldown map,
      "state":       {last_sweep, next_sweep, ticks, last_error,
                      interval_seconds},     ← watch-loop status surface,
      "fired":       [AlertEvent dicts (+ack, event_id), append-only,
                      capped to the last FIRED_LOG_CAP entries]
    }

The fired log is append-only with a hard cap of 500 entries (the oldest
are evicted — a bounded, on-disk audit trail). `ack_alert` marks a
fired event acknowledged (the UI's ack button) without rewriting
history beyond that flag.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from .alerts import AlertEvent, AlertRule

FIRED_LOG_CAP = 500
EMPTY_DOC: dict = {"version": 1, "rules": [], "last_fired": {},
                   "state": {}, "fired": []}


def _new_event_id() -> str:
    """Stable-enough id: epoch-ms + counter (stdlib, no ULID dep)."""
    return f"ae-{int(time.time() * 1000):013d}-{_EVENT_SEQ[0]}"
_EVENT_SEQ = [0]


class AlertStore:
    """JSON-file-backed rule + fired-log store (single writer expected —
    the watch loop / CLI; concurrent web POSTs are serialized by the
    read-modify-write being atomic at process level via the CLI)."""

    def __init__(self, data_root: str | Path = "data"):
        self.root = Path(data_root)
        self.path = self.root / "watch" / "alerts.json"

    # ------------------------------------------------------------- io
    def _read(self) -> dict:
        if not self.path.exists():
            return json.loads(json.dumps(EMPTY_DOC))
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return json.loads(json.dumps(EMPTY_DOC))
        if not isinstance(doc, dict):
            return json.loads(json.dumps(EMPTY_DOC))
        return doc

    def _write(self, doc: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, sort_keys=True),
                       encoding="utf-8")
        tmp.replace(self.path)

    # ------------------------------------------------------------- rules
    def load_rules(self) -> list[AlertRule]:
        return [AlertRule.from_dict(r)
                for r in self._read().get("rules") or []]

    def save_rules(self, rules: list[AlertRule]) -> None:
        doc = self._read()
        doc["rules"] = [r.to_dict() for r in rules]
        self._write(doc)

    def add_rule(self, rule: AlertRule) -> AlertRule:
        """Insert a rule. When `rule.id` is blank a stable id is minted
        (`<symbol>:<kind>:<n>`, n = count of same prefix + 1). A rule
        with an existing id replaces the stored one (idempotent upsert
        for the web form's resubmits)."""
        doc = self._read()
        rules = [AlertRule.from_dict(r) for r in doc.get("rules") or []]
        if not rule.id:
            prefix = f"{rule.symbol}:{rule.kind}"
            n = sum(1 for r in rules if r.id.startswith(prefix + ":"))
            rule.id = f"{prefix}:{n + 1}"
        rules = [r for r in rules if r.id != rule.id] + [rule]
        doc["rules"] = [r.to_dict() for r in rules]
        self._write(doc)
        return rule

    def remove_rule(self, rule_id: str) -> bool:
        doc = self._read()
        rules = [AlertRule.from_dict(r) for r in doc.get("rules") or []]
        kept = [r for r in rules if r.id != rule_id]
        if len(kept) == len(rules):
            return False
        doc["rules"] = [r.to_dict() for r in kept]
        self._write(doc)
        return True

    # ------------------------------------------------------- fired log
    def append_fired(self, event: AlertEvent, fired_at: str = "",
                     channel: str = "") -> dict:
        """Append a fired alert (cap: last FIRED_LOG_CAP entries)."""
        _EVENT_SEQ[0] += 1
        doc = self._read()
        row = event.to_dict()
        row["event_id"] = _new_event_id()
        row["wall_fired_at"] = fired_at or event.fired_at
        row["channel"] = channel
        row["ack"] = False
        fired = doc.get("fired") or []
        fired.append(row)
        doc["fired"] = fired[-FIRED_LOG_CAP:]
        self._write(doc)
        return row

    def list_fired(self, limit: int | None = None,
                   include_acked: bool = True) -> list[dict]:
        fired = list(self._read().get("fired") or [])
        if not include_acked:
            fired = [f for f in fired if not f.get("ack")]
        if limit is not None:
            fired = fired[-limit:]
        return fired

    def ack_alert(self, event_id: str) -> bool:
        doc = self._read()
        hit = False
        for row in doc.get("fired") or []:
            if row.get("event_id") == event_id and not row.get("ack"):
                row["ack"] = True
                hit = True
        if hit:
            self._write(doc)
        return hit

    # ------------------------------------------------- engine + loop state
    def load_last_fired(self) -> dict[str, str]:
        lf = self._read().get("last_fired") or {}
        return {str(k): str(v) for k, v in lf.items()}

    def save_last_fired(self, last_fired: dict[str, str]) -> None:
        doc = self._read()
        doc["last_fired"] = {str(k): str(v) for k, v in last_fired.items()}
        self._write(doc)

    def load_state(self) -> dict:
        return dict(self._read().get("state") or {})

    def save_state(self, state: dict) -> None:
        doc = self._read()
        doc["state"] = dict(state)
        self._write(doc)
