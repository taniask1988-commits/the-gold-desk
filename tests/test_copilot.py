"""GAUNTLET 7 Piece 6 — receipts-backed AI copilot tests.

Pinned laws:
  - the copilot command allowlist exposes NO mutator (mirror of
    test_no_mutating_tool_reachable_from_research_registry);
  - the copilot module source imports/mentions no mutating module;
  - every number the composer emits traces to a receipt (mechanical
    evidence gate) — and a fabricated number is flagged, never published;
  - the BAR probe question plans the right read-only commands;
  - no live network in tests (subprocess is monkeypatched).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

from gold_desk.agent import copilot as cp


# --------------------------------------------------------- allowlist laws

def test_allowlist_has_no_mutating_commands():
    """Mirror of the research-registry law, on the copilot allowlist."""
    banned = {"demo", "watch", "watch-loop", "alerts-add", "alerts-rm",
              "tune-rule", "evolve-run", "constitution", "chat", "zen",
              "ask", "research", "veto-bench", "replay", "account-alpaca",
              "propose_ticket", "make_ticket", "persist", "send",
              "open_position", "force_close", "set_kill_switch"}
    names = set(cp.COPILOT_COMMANDS)
    assert names, "allowlist must not be empty"
    assert not (names & banned), f"mutators in allowlist: {names & banned}"
    for name in names:
        assert not name.startswith("_")
        # every spec carries read-only params only
        spec = cp.COPILOT_COMMANDS[name]
        assert isinstance(spec.get("params", {}), dict)
        assert "mutating" not in str(spec).lower() or \
            spec.get("mutating") is False


def test_build_argv_refuses_unknown_commands():
    for bad in ("demo", "tune-rule", "evolve-run", "alerts-add",
                "propose_ticket", ""):
        with pytest.raises(ValueError, match="allowlist"):
            cp.build_argv(bad, {})


def test_copilot_module_source_has_no_mutator_references():
    """AST/text law: copilot.py never references mutating CLI verbs or
    mutator modules — not in imports, not in strings that build argv."""
    src = (REPO / "src" / "gold_desk" / "agent" / "copilot.py").read_text()
    # argv-building code must never name these subcommands
    for verb in ("\"demo\"", "\"watch\"", "\"alerts-add\"", "\"alerts-rm\"",
                 "\"tune-rule\"", "\"evolve-run\"", "\"constitution\"",
                 "\"propose_ticket\"", "\"make_ticket\"",
                 "\"account-alpaca\"", "\"veto-bench\"", "\"replay\"",
                 "\"zen\"", "\"research\"", "\"chat\""):
        assert verb not in src, f"copilot source references mutator {verb}"
    # and never imports a module that could mutate desk state
    for mod in ("gold_desk.ticket", "gold_desk.demo", "gold_desk.watch",
                "gold_desk.evolve", "gold_desk.constitution",
                "gold_desk.orchestrator", "gold_desk.risk_gate"):
        assert mod not in src, f"copilot imports mutator module {mod}"


def test_build_argv_shapes():
    argv = cp.build_argv("markets-quant", {"symbol": "GC=F"})
    assert argv == ["markets-quant", "GC=F"]
    argv = cp.build_argv("eod", {"date": "2026-07-10"})
    assert argv == ["eod", "--date", "2026-07-10"]
    argv = cp.build_argv("screen", {"where": "rsi < 70",
                                    "technical_only": True})
    assert argv == ["screen", "--where", "rsi < 70", "--technical-only"]
    argv = cp.build_argv("pair", {"a": "GC=F", "b": "SLV"})
    assert argv == ["pair", "GC=F", "SLV"]


# ---------------------------------------------------------------- planning

def test_plan_probe_question_runs_real_commands():
    """THE BAR probe: gold's RSI + CFTC positioning + desk view must plan
    the indicator battery, the drivers (CFTC lives there) and risk."""
    p = cp.plan("What's gold's RSI, CFTC positioning, and the desk view?")
    names = [n for n, _ in p]
    assert "markets-quant" in names
    assert "drivers" in names
    assert "risk" in names
    # params: gold resolved to the futures symbol
    quant = dict(p)["markets-quant"]
    assert quant.get("symbol") == "GC=F"


def test_plan_symbol_aliases():
    p = cp.plan("bitcoin indicators")
    assert p and p[0][0] == "markets-quant"
    assert p[0][1]["symbol"] == "BTC-USD"


def test_plan_grounded_fallback():
    p = cp.plan("hello there")
    names = [n for n, _ in p]
    assert names == ["markets-snapshot", "drivers"]


def test_plan_is_deterministic():
    a = cp.plan("silver vs gold spread and max pain")
    b = cp.plan("silver vs gold spread and max pain")
    assert a == b


# ------------------------------------------------------------ receipts run

class _FakeProc:
    def __init__(self, stdout, returncode=0, stderr=""):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = stderr


def test_run_receipt_parses_json_and_stamps_receipt(monkeypatch):
    captured = {}

    def fake_run(cmd, **kw):
        captured["cmd"] = cmd
        return _FakeProc(json.dumps({"ok": True, "rsi14": 43.21}))

    monkeypatch.setattr(cp.subprocess, "run", fake_run)
    r = cp.run_receipt("markets-quant", {"symbol": "GC=F"},
                       data_root="/tmp/x")
    assert r["ok"] is True
    assert r["data"]["rsi14"] == 43.21
    assert "markets-quant" in r["argv"] and "GC=F" in r["argv"]
    assert "--json" in r["argv"] and "--data-root" in r["argv"]
    assert r["command"] == "markets-quant"
    assert r["ts"] and r["duration_ms"] >= 0


def test_run_receipt_fails_soft_on_garbage(monkeypatch):
    monkeypatch.setattr(cp.subprocess, "run",
                        lambda cmd, **kw: _FakeProc("not json",
                                                    returncode=0))
    r = cp.run_receipt("drivers", {}, data_root="/tmp/x")
    assert r["ok"] is False
    assert "raw" in r["data"]


def test_run_receipt_fails_soft_on_crash(monkeypatch):
    monkeypatch.setattr(cp.subprocess, "run",
                        lambda cmd, **kw: _FakeProc("", returncode=1,
                                                    stderr="boom"))
    r = cp.run_receipt("risk", {}, data_root="/tmp/x")
    assert r["ok"] is False and "boom" in (r["error"] or "")


def test_run_receipt_never_raises_on_timeout(monkeypatch):
    def boom(cmd, **kw):
        raise cp.subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(cp.subprocess, "run", boom)
    r = cp.run_receipt("markets-multi", {}, data_root="/tmp/x")
    assert r["ok"] is False and "timeout" in r["error"]


# ------------------------------------------------------------ evidence gate

def _receipt(rid, data, argv_extra="", command="markets-quant"):
    return {"receipt_id": rid, "command": command,
            "argv": [command, "GC=F"] + (
                [argv_extra] if argv_extra else []),
            "ts": "2026-07-10T00:00:00Z", "ok": True, "data": data,
            "error": None, "duration_ms": 5}


def test_evidence_gate_passes_cited_numbers():
    receipts = [_receipt("1", {"rsi14": 43.2178})]
    text = "RSI14 43.22 [R1] — neutral band"
    g = cp.evidence_gate(text, receipts)
    assert g["ok"] is True and g["checked"] >= 1


def test_evidence_gate_flags_fabricated_number():
    receipts = [_receipt("1", {"rsi14": 43.2178})]
    text = "RSI14 43.22 [R1] and confidence 97.3 about this"
    g = cp.evidence_gate(text, receipts)
    assert g["ok"] is False
    vals = [u["value"] for u in g["untraceable"]]
    assert "97.3" in vals
    assert "43.22" not in vals


def test_evidence_gate_skips_citations_dates_and_symbol_tokens():
    receipts = [_receipt("1", {"n": 5})]
    text = ("as of 2026-07-10, events E-9 and RSI14/ATR14 tokens, "
            "see [R1] [R2] refs 5")
    g = cp.evidence_gate(text, receipts)
    # only the bare '5' is checked and it traces to the payload
    assert g["ok"] is True
    assert g["checked"] == 1


def test_evidence_gate_integer_tolerance():
    receipts = [_receipt("1", {"bars": 12})]
    assert cp.evidence_gate("12 bars processed", receipts)["ok"] is True
    assert cp.evidence_gate("13 bars processed", receipts)["ok"] is False


# --------------------------------------------------------------- composer

def test_compose_cites_every_number_and_publishes():
    receipts = [_receipt("1", {"symbol": "GC=F", "rsi14": 38.03,
                               "atr14": 92.1}),
                _receipt("2", {"live": {"D2": {"value": 101.81,
                                               "unit": "idx",
                                               "source": "Yahoo Finance"}},
                               "unavailable": ["D5"]})]
    out = cp.compose("gold rsi and dxy", receipts)
    assert "[R1]" in out["answer_md"] and "[R2]" in out["answer_md"]
    assert out["published"] is True
    assert out["evidence"]["ok"] is True


def test_compose_handles_failed_receipts():
    receipts = [{"receipt_id": "1", "command": "risk", "argv": ["risk"],
                 "ts": "2026-07-10T00:00:00Z", "ok": False, "data": {},
                 "error": "boom", "duration_ms": 1}]
    out = cp.compose("desk view", receipts)
    assert "failed" in out["answer_md"]
    assert out["published"] is True  # honest failure text, no numbers


def test_compose_renders_drivers_with_names():
    receipts = [_receipt("2", {"live": {"D1": {"value": 2.95, "unit": "%",
                                               "source": "US Treasury"}},
                               "unavailable": []}, command="drivers")]
    out = cp.compose("drivers", receipts)
    assert "10y real yield" in out["answer_md"]
    assert "2.95" in out["answer_md"]


def test_compose_renders_macd_dict():
    receipts = [_receipt("1", {"symbol": "GC=F",
                               "macd": {"hist": -17.93, "line": -66.77},
                               "rsi14": 38.03})]
    out = cp.compose("gold rsi", receipts)
    assert "MACD hist" in out["answer_md"]
    assert "'hist'" not in out["answer_md"]  # never a raw dict dump


# --------------------------------------------------------------- end-to-end

def test_ask_end_to_end_with_fake_runner(monkeypatch, tmp_path):
    """Full pipeline without network: plan → fake receipts → gate."""
    def fake_run(name, params, data_root=None, timeout=60.0):
        data = {"markets-quant": {"ok": True, "symbol": "GC=F",
                                  "rsi14": 38.03, "atr14": 92.1},
                "drivers": {"ok": True, "live": {}, "unavailable": []},
                "risk": {"ok": True, "var95": 0.42},
                "markets-snapshot": {"ok": True, "price": 4011.2}}[name]
        return {"receipt_id": "", "command": name,
                "argv": cp.build_argv(name, params),
                "ts": "2026-07-10T00:00:00Z",
                "ok": bool(data.get("ok")), "data": data, "error": None,
                "duration_ms": 3}

    monkeypatch.setattr(cp, "run_receipt", fake_run)
    out = cp.ask("gold's RSI + CFTC positioning + desk view",
                 data_root=str(tmp_path))
    assert out["plan"][0] == "markets-quant"
    assert len(out["receipts"]) >= 3
    assert out["published"] is True
    assert out["evidence"]["checked"] >= 3
    # every number in the answer came from a receipt — mechanical check
    assert out["evidence"]["untraceable"] == []
    assert "38.03" in out["answer_md"] and "[R1]" in out["answer_md"]


def test_cli_copilot_json_contract(monkeypatch, capsys):
    """The CLI --json contract the web deck shells into."""
    from gold_desk.cli import main

    def fake_ask(question, data_root=None, max_commands=4, timeout=60.0):
        return {"ok": True, "question": question, "plan": ["drivers"],
                "answer_md": "DXY 101.81 [R1]", "receipts": [],
                "evidence": {"checked": 1, "untraceable": [], "ok": True},
                "published": True, "mode": "deterministic",
                "elapsed_ms": 1}

    monkeypatch.setattr("gold_desk.agent.copilot.ask", fake_ask)
    rc = main(["copilot", "dxy now", "--json"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["published"] is True and out["plan"] == ["drivers"]
    assert out["evidence"]["ok"] is True


def test_copilot_l11_fence_still_enforced():
    """L11 regression guard: the browse fence is untouched by Piece 6 —
    the copilot adds NO web-text ingestion path at all."""
    src = (REPO / "src" / "gold_desk" / "agent" / "copilot.py").read_text()
    assert "fetch_news" not in src and "urlopen" not in src
    assert "UNTRUSTED" not in src  # no web text consumed, so no fence needed


def test_plan_covers_every_mentioned_symbol():
    """D7 fix (final critic): 'silver RSI vs platinum ATR and gold change'
    plans an indicator leg PER mentioned symbol — no silent leg drops."""
    p = cp.plan("compare silver's RSI14 vs platinum's ATR and gold's change")
    quants = [dict(params)["symbol"] for n, params in p
              if n == "markets-quant"]
    assert "SLV" in quants and "PL=F" in quants and "GC=F" in quants


def test_gate_ignores_question_echo_numbers():
    """D7 fix: numbers the USER typed (e.g. '30d') are input, not claims
    — the gate judges the answer body, never blocks on the echo."""
    receipts = [_receipt("1", {"rsi14": 38.15})]
    out = cp.compose("gold rsi and its 30 d trend", receipts)
    assert out["published"] is True
    assert out["evidence"]["untraceable"] == []
