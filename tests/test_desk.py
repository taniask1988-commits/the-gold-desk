"""Multi-analyst desk (GAUNTLET piece 4) — offline tests.

Pins the ai-hedge-fund discipline ported to our multi-asset desk:
  - personas are checklists + a shared signal contract, with tool
    entitlements that are a subset of DESK_TOOLS
  - run_desk with a scripted complete_json: 5 personas parsed, PM
    synthesized; one persona's LLM failing ABSTAINS while the other
    four still run; the PM failing falls back to a mechanical vote
  - context-gather errors PROPAGATE (fail loud — a raise, or an
    ok:False from the fail-soft markets plane → DeskContextError)
  - personas run in PARALLEL (a 5-party barrier would deadlock any
    sequential implementation)
  - journal: AgentRunStarted / AgentStep / AgentRunFinished / DeskReport
    (agent kinds never carry reason codes)
  - CLI --json shape; L12 blindfold: no account/balance keys in prompts
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.agent.desk import engine as eng  # noqa: E402
from gold_desk.agent.desk.engine import DeskContextError, run_desk  # noqa: E402
from gold_desk.agent.desk.personas import (  # noqa: E402
    DESK_TOOLS,
    PERSONAS,
    SIGNAL_CONTRACT,
)
from gold_desk.events import Journal  # noqa: E402
from gold_desk.llm.zen_client import LLMUnavailable  # noqa: E402

# ------------------------------------------------------------ fixtures

_MARKERS = {
    "You are The Technician": "technician",
    "You are The Macro Strategist": "macro",
    "You are The News Analyst": "news",
    "You are The Sentiment Reader": "sentiment",
    "You are The Risk Manager": "risk",
    "You are The Fundamentalist": "fundamentalist",
    "You are The Portfolio Manager": "pm",
}

PERSONA_REPLIES = {
    "technician": {"signal": "bullish", "confidence": 72,
                   "thesis": "Higher lows into range highs with orderly "
                             "pullbacks and expanding range.",
                   "key_evidence": ["close 106.0 vs 5d high 106.3",
                                    "6 of the last 8 bars closed up",
                                    "price sits at 88% of the 5d range"]},
    "macro": {"signal": "neutral", "confidence": 55,
              "thesis": "A softer dollar is offset by firm yields.",
              "key_evidence": ["DX-Y.NYB -0.40% on the day",
                               "^TNX +1.10% (yields rising)",
                               "VIX row flat at 14.2"]},
    "news": {"signal": "bullish", "confidence": 61,
             "thesis": "ETF-inflow coverage dominates a fresh tape.",
             "key_evidence": ['"Bitcoin surges on ETF inflows" (newest)',
                              "no negative catalyst in the 8 headlines"]},
    "sentiment": {"signal": "bearish", "confidence": 48,
                  "thesis": "Crowding looks one-way after a 6% day.",
                  "key_evidence": ["TOP +40.0% tops the market gainers",
                                   "symbol itself +6.0% (1d)"]},
    "risk": {"signal": "neutral", "confidence": 50,
             "thesis": "Stops are survivable but the tape is 3h stale.",
             "key_evidence": ["swing high 106.3 is 0.7 ATR away",
                              "last bar 180 minutes old"]},
    "fundamentalist": {"signal": "bullish", "confidence": 64,
                       "thesis": "Revenue and EPS rose across the 8 "
                                 "filed quarters; accession-cited growth.",
                       "key_evidence": ["latest revenue 109.4B (acc "
                                        "0000320193-26-000020)",
                                        "EPS diluted path rising 1.40 to 2.02",
                                        "13F top position AMEX 50.4B"]},
}

PM_REPLY = {"consensus": "bullish", "conviction": 64,
            "summary": "The desk leans bullish: structure and tape agree, "
                       "macro is neutral. Positioning is the swing factor.",
            "disagreements": "The Sentiment Reader sees crowded longs "
                             "while the Technician sees orderly markup.",
            "risk_flags": ["stop only 0.7 ATR from entry",
                           "news tape is 3h stale"]}


def _detail() -> dict:
    bars = []
    base = 100.0
    t0 = 1_756_000_000_000
    for i in range(60):
        o = base + i * 0.1
        bars.append({"ts": t0 + i * 1_800_000, "o": round(o, 2),
                     "h": round(o + 0.2, 2), "l": round(o - 0.2, 2),
                     "c": round(o + 0.05, 2)})
    return {
        "ok": True, "symbol": "BTC-USD", "name": "Bitcoin USD",
        "sector": "crypto", "currency": "USD", "price": 106.0,
        "prev_close": 100.0, "change": 6.0, "change_pct": 6.0,
        "range_5d_change_pct": 8.1, "bars": bars,
        "news": {"ok": True, "items": [
            {"title": "Bitcoin surges on ETF inflows",
             "published": "Mon, 24 Aug 2026 10:00:00 +0000"},
            {"title": "Analysts raise bitcoin targets",
             "published": "Mon, 24 Aug 2026 08:00:00 +0000"},
        ]},
    }


def _board() -> dict:
    return {
        "ok": True, "as_of": "2026-08-24T10:00:00Z",
        "sectors": [
            {"key": "rates", "label": "Rates & Dollar", "rows": [
                {"symbol": "DX-Y.NYB", "name": "Dollar Index",
                 "price": 98.0, "change_pct": -0.4},
                {"symbol": "^TNX", "name": "US 10Y Yield",
                 "price": 4.1, "change_pct": 1.1}]},
            {"key": "volatility", "label": "Volatility", "rows": [
                {"symbol": "^VIX", "name": "VIX", "price": 14.2,
                 "change_pct": 0.2}]},
            {"key": "crypto", "label": "Crypto", "rows": [
                {"symbol": "BTC-USD", "name": "Bitcoin USD",
                 "price": 106.0, "change_pct": 6.0}]},
        ],
        "watchlist_movers": {
            "gainers": [{"symbol": "BTC-USD", "name": "Bitcoin USD",
                         "sector": "crypto", "change_pct": 6.0,
                         "price": 106.0}],
            "losers": [{"symbol": "ETH-USD", "name": "Ethereum",
                        "sector": "crypto", "change_pct": -2.1,
                        "price": 3000.0}]},
        "errors": [],
    }


def _movers() -> dict:
    return {
        "ok": True, "as_of": "2026-08-24T10:00:00Z",
        "gainers": [{"symbol": "TOP", "name": "Top Financial",
                     "price": 16.2, "change_pct": 40.0}],
        "losers": [{"symbol": "BBB", "name": "Big Bad Bank",
                    "price": 9.1, "change_pct": -21.0}],
    }


def _patch_context(monkeypatch):
    monkeypatch.setattr(eng, "fetch_detail", lambda s, d: _detail())
    monkeypatch.setattr(eng, "fetch_board", lambda d: _board())
    monkeypatch.setattr(eng, "fetch_market_movers", lambda d: _movers())
    # R2-1: institutional context gather is fail-soft per slice; the
    # test patches it to empty slices so the fundamentalist persona
    # sees no XBRL and abstains (mirrors the dead-feed contract).
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})


def _fake_complete_json(calls=None, barrier=None, fail=()):
    """Scripted complete_json: persona/PM replies keyed by system prefix."""
    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        key = next((k for marker, k in _MARKERS.items()
                    if system.startswith(marker)), None)
        if calls is not None:
            calls.append((key, model))
        if key in fail:
            raise LLMUnavailable(f"zen down for {key}")
        if barrier is not None and key in _MARKERS and key != "pm":
            barrier.wait(timeout=10)     # only passes if all 5 overlap
        if key == "pm":
            return dict(PM_REPLY)
        return dict(PERSONA_REPLIES[key])
    return fake


# -------------------------------------------------------- persona shape

def test_personas_are_checklists_with_signal_contract():
    """Each persona: role, numbered checklist, signal rules, contract.
    R2-1: 5→6 personas (fundamentalist added with the institutional
    data plane)."""
    assert len(PERSONAS) == 6
    names = [p.name for p in PERSONAS]
    assert names == ["technician", "macro", "news", "sentiment",
                     "risk", "fundamentalist"]
    roles = [p.role for p in PERSONAS]
    assert roles == ["The Technician", "The Macro Strategist",
                     "The News Analyst", "The Sentiment Reader",
                     "The Risk Manager", "The Fundamentalist"]
    for p in PERSONAS:
        assert "Work through your checklist:" in p.system
        # numbered checklist items (1..4/5)
        assert "\n1." in p.system and "\n2." in p.system
        assert "Signal rules:" in p.system
        assert "Confidence scale (0-100)" in p.system
        assert p.system.rstrip().endswith(SIGNAL_CONTRACT), (
            f"{p.name}: prompt must END with the signal contract")


def test_persona_tools_are_desk_tool_subsets():
    """Every persona has tools, ⊆ DESK_TOOLS, with the briefed mapping.
    R2-1: the risk persona still sees the 5 market-data tools (its tools
    list was not extended — the new institutional slices feed the PM
    base_block instead; the fundamentalist is the only persona reading
    XBRL/13F directly).

    R2-2: the technician now also reads quant_indicators + verified_
    snapshot (the deterministic ground-truth block the technician must
    treat as the source of truth for any exact numeric claim, mirroring
    TradingAgents' market_analyst.py:51 + market_data_validator.py)."""
    for p in PERSONAS:
        assert p.tools, f"{p.name} has no tools"
        assert set(p.tools) <= set(DESK_TOOLS), (
            f"{p.name} lists unknown tools: "
            f"{set(p.tools) - set(DESK_TOOLS)}")
    by_name = {p.name: p for p in PERSONAS}
    assert by_name["technician"].tools == ["market_ohlc",
                                           "market_indicators",
                                           "quant_indicators",
                                           "verified_snapshot"]
    assert by_name["macro"].tools == ["board_sectors"]
    assert by_name["news"].tools == ["symbol_news"]
    assert by_name["sentiment"].tools == ["market_movers",
                                          "board_sectors"]
    assert set(by_name["risk"].tools) == {"market_ohlc",
                                          "market_indicators",
                                          "board_sectors",
                                          "symbol_news",
                                          "market_movers"}   # devil's advocate
    assert by_name["fundamentalist"].tools == ["fundamentals",
                                                "earnings",
                                                "institutional_top"]


def test_persona_prompts_no_account_or_balance_keys():
    """L12 blindfold: the personas reason over market data only — no
    account/balance/equity/PnL language anywhere in the prompts."""
    banned = ("account", "balance", "equity", "pnl", "bankroll",
              "capital", "withdraw", "deposit")
    for p in PERSONAS:
        low = p.system.lower()
        for word in banned:
            assert word not in low, (
                f"{p.name} prompt mentions {word!r} (L12 blindfold)")


def test_persona_prompts_reason_only_from_provided_data():
    """The ai-hedge-fund 'reason ONLY from the data provided' rule."""
    for p in PERSONAS:
        assert "ONLY from" in p.system, (
            f"{p.name} prompt lacks the reason-only hard rule")


# ------------------------------------------------------------- run_desk

def test_run_desk_scripted_six_personas_and_pm(monkeypatch, tmp_path):
    """6 scripted persona replies → parsed signals; PM synthesized.
    Renamed from test_run_desk_scripted_five_personas_and_pm when R2-1
    added the fundamentalist (the 5-party barrier test below proves
    parallelism for any N)."""
    _patch_context(monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(calls=calls))
    out = run_desk("btc", data_root=tmp_path)
    assert out["ok"] is True
    assert out["symbol"] == "BTC-USD"
    assert out["name"] == "Bitcoin USD"
    assert out["price"] == 106.0
    assert isinstance(out["as_of"], str) and out["as_of"].endswith("Z")
    assert isinstance(out["elapsed_ms"], int)
    assert len(out["personas"]) == 6
    for row in out["personas"]:
        reply = PERSONA_REPLIES[row["name"]]
        assert row["signal"] == reply["signal"]
        assert row["confidence"] == reply["confidence"]
        assert row["thesis"] == reply["thesis"]
        assert row["key_evidence"] == reply["key_evidence"]
        assert row["abstained"] is False
        assert row["model"]            # model recorded
        assert row["latency_ms"] >= 0
        assert row["role"]             # display role present
    # 7 LLM calls: 6 personas + 1 PM
    keys = [k for k, _ in calls]
    assert sorted(k for k in keys if k != "pm") == \
        ["fundamentalist", "macro", "news", "risk", "sentiment",
         "technician"]
    assert keys.count("pm") == 1
    # PM synthesis from the scripted PM reply
    assert out["pm"]["consensus"] == "bullish"
    assert out["pm"]["conviction"] == 64
    assert "ETF-inflow" not in out["pm"]["summary"]  # PM's own summary
    assert out["pm"]["mechanical"] is False
    assert out["abstained"] == 0


def test_run_desk_one_persona_abstains_others_still_run(monkeypatch,
                                                         tmp_path):
    """LLMUnavailable for one persona → abstention (neutral, 0, flagged);
    the other five run and the desk report is still ok. R2-1: 5→6
    personas."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(fail=("technician",)))
    out = run_desk("BTC-USD", data_root=tmp_path)
    assert out["ok"] is True
    by_name = {r["name"]: r for r in out["personas"]}
    tech = by_name["technician"]
    assert tech["abstained"] is True
    assert tech["signal"] == "neutral"
    assert tech["confidence"] == 0
    assert tech["thesis"].startswith("abstained:")
    assert "zen down for technician" in tech["thesis"]
    assert tech["key_evidence"] == []
    for other in ("macro", "news", "sentiment", "risk",
                  "fundamentalist"):
        assert by_name[other]["abstained"] is False
        assert by_name[other]["signal"] == \
            PERSONA_REPLIES[other]["signal"]
    assert out["abstained"] == 1
    # the PM still synthesized (non-mechanical) over 5 live + 1 abstain
    assert out["pm"]["mechanical"] is False
    assert out["pm"]["consensus"] == "bullish"


def test_run_desk_garbage_persona_json_abstains(monkeypatch, tmp_path):
    """A persona returning an invalid signal value abstains (ai-hedge-fund
    parse-failure contract) instead of crashing the desk."""
    _patch_context(monkeypatch)

    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        if system.startswith("You are The News Analyst"):
            return {"signal": "sideways", "confidence": 80,
                    "thesis": "huh"}
        return _fake_complete_json()(messages, model, **kwargs)

    monkeypatch.setattr(eng, "complete_json", fake)
    out = run_desk("BTC-USD", data_root=tmp_path)
    by_name = {r["name"]: r for r in out["personas"]}
    assert by_name["news"]["abstained"] is True
    assert "invalid signal" in by_name["news"]["thesis"]
    assert out["ok"] is True and out["abstained"] == 1


def test_run_desk_pm_failure_falls_back_to_mechanical_vote(monkeypatch,
                                                           tmp_path):
    """PM model down → mechanical majority vote, labeled, never silent.
    R2-1: 6 personas now — conviction is the mean across the 5 live
    (technician 72 + macro 55 + news 61 + sentiment 48 + risk 50 +
    fundamentalist 64 = 350 / 6 ≈ 58)."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(fail=("pm",)))
    out = run_desk("BTC-USD", data_root=tmp_path)
    pm = out["pm"]
    assert pm["mechanical"] is True
    assert pm["model"] == ""
    # live signals: bullish(72) bullish(61) neutral(55) neutral(50)
    #                bearish(48) bullish(64) → no strict majority → mixed
    assert pm["consensus"] == "mixed"
    assert pm["conviction"] == round((72 + 55 + 61 + 48 + 50 + 64) / 6)
    assert "mechanical" in pm["summary"].lower()
    assert any("pm synthesis unavailable" in f for f in pm["risk_flags"])
    assert out["ok"] is True


def test_context_error_propagates_fail_loud(monkeypatch, tmp_path):
    """A raising context gather PROPAGATES — never five silent neutrals.
    R2-1: institutional slices are fail-soft (NOT in the loud set); only
    the original 3 markets-plane calls (detail/board/movers) propagate."""
    def boom(symbol, data_root):
        raise RuntimeError("yahoo is on fire")
    monkeypatch.setattr(eng, "fetch_detail", boom)
    monkeypatch.setattr(eng, "fetch_board", lambda d: _board())
    monkeypatch.setattr(eng, "fetch_market_movers", lambda d: _movers())
    with pytest.raises(RuntimeError, match="yahoo is on fire"):
        run_desk("BTC-USD", data_root=tmp_path)


def test_context_ok_false_raises_desk_context_error(monkeypatch, tmp_path):
    """The markets plane is fail-soft ({ok: False}); the desk is not —
    a dead detail/board/movers becomes DeskContextError. R2-1: the
    institutional gather is fail-soft per slice and does NOT trigger
    DeskContextError (only the original 3 markets-plane calls do)."""
    monkeypatch.setattr(eng, "fetch_detail",
                        lambda s, d: {"ok": False, "error": "unknown "
                                                          "symbol: 'ZZZ'"})
    monkeypatch.setattr(eng, "fetch_board", lambda d: _board())
    monkeypatch.setattr(eng, "fetch_market_movers", lambda d: _movers())
    with pytest.raises(DeskContextError, match="detail"):
        run_desk("ZZZ", data_root=tmp_path)

    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": False, "error": "all fetches "
                                                        "failed"})
    with pytest.raises(DeskContextError, match="board"):
        run_desk("BTC-USD", data_root=tmp_path)

    monkeypatch.setattr(eng, "fetch_board", lambda d: _board())
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": False, "error": "screener "
                                                        "unreachable"})
    with pytest.raises(DeskContextError, match="movers"):
        run_desk("BTC-USD", data_root=tmp_path)


def test_personas_run_in_parallel(monkeypatch, tmp_path):
    """An N-party threading barrier inside complete_json only releases
    when all N persona calls overlap in time — a sequential desk would
    deadlock into abstentions. R2-1: parameterized from Barrier(5) to
    Barrier(len(PERSONAS)) so the test proves parallelism for any N
    (currently 6). The barrier PROVES parallelism regardless of count."""
    _patch_context(monkeypatch)
    barrier = threading.Barrier(len(PERSONAS))
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(barrier=barrier))
    out = run_desk("BTC-USD", data_root=tmp_path)
    assert out["ok"] is True
    assert out["abstained"] == 0, (
        "personas did not run concurrently (barrier timed out): "
        f"{[(r['name'], r['abstained']) for r in out['personas']]}")


def test_desk_report_events_journaled(monkeypatch, tmp_path):
    """AgentRunStarted → 6 persona AgentSteps + PM AgentStep →
    DeskReport → AgentRunFinished; agent kinds carry no reason codes.
    R2-1: 5→6 persona steps + PM = 7 steps total."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json", _fake_complete_json())
    jr = Journal(tmp_path, "desk-test-hash")
    run_desk("BTC-USD", data_root=tmp_path, journal=jr)
    events = Journal.read_events(tmp_path)
    kinds = [e["kind"] for e in events]
    assert kinds.count("AgentRunStarted") == 1
    assert kinds.count("DeskReport") == 1
    assert kinds.count("AgentRunFinished") == 1
    # 6 persona steps + 1 pm step
    steps = [e for e in events if e["kind"] == "AgentStep"]
    assert len(steps) == 7
    persona_steps = [e for e in steps if e["payload"].get("persona")]
    assert sorted(e["payload"]["persona"] for e in persona_steps) == \
        ["fundamentalist", "macro", "news", "risk", "sentiment",
         "technician"]
    assert any(e["payload"].get("step") == "pm" for e in steps)
    # DeskReport payload shape
    rep = next(e for e in events if e["kind"] == "DeskReport")
    assert rep["payload"]["symbol"] == "BTC-USD"
    assert len(rep["payload"]["personas"]) == 6
    assert rep["payload"]["pm"]["consensus"] == "bullish"
    # the run-start event carries the union of persona tools + pm
    # R2-1: the union is now 8 keys (technician 2 + macro 1 + news 1 +
    # sentiment 2 + risk 5 + fundamentalist 3 = 8 unique), NOT all 12
    # DESK_TOOLS — the 4 PM-only institutional slices (macro_curve,
    # crypto_sentiment, onchain, social) feed the PM base_block, not
    # any persona's tools list. The test pins the actual union +
    # pm_synthesis.
    started = next(e for e in events if e["kind"] == "AgentRunStarted")
    expected_tools = set()
    for p in PERSONAS:
        expected_tools |= set(p.tools)
    expected_tools.add("pm_synthesis")
    assert set(started["payload"]["tools"]) == expected_tools
    # agent kinds never carry reason codes (L13-adjacent invariant)
    for e in events:
        if e["kind"] in ("AgentRunStarted", "AgentStep", "AgentRunFinished",
                         "DeskReport"):
            assert e.get("reason_code") is None


def test_on_event_progress_stream(monkeypatch, tmp_path):
    """on_event mirrors the run_agent callback surface (best-effort)."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json", _fake_complete_json())
    seen: list[str] = []
    out = run_desk("BTC-USD", data_root=tmp_path,
                   on_event=lambda ev: seen.append(ev["kind"]))
    assert "context" in seen
    assert seen.count("persona") == 6
    assert "pm" in seen
    assert out["ok"] is True


# ------------------------------------------------------------------ CLI

def _run_cli(argv, capsys):
    from gold_desk.cli import main
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


def test_cli_desk_json_shape(monkeypatch, tmp_path, capsys):
    """cli desk --json emits the full report shape (web route contract)."""
    import gold_desk.agent.desk as desk_pkg
    monkeypatch.setattr(desk_pkg, "run_desk",
                        lambda s, **k: run_desk(s, **{
                            **k, "data_root": tmp_path}))
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json", _fake_complete_json())
    rc, out = _run_cli(["desk", "BTC-USD", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["symbol"] == "BTC-USD"
    assert payload["name"] == "Bitcoin USD"
    assert isinstance(payload["as_of"], str)
    assert payload["price"] == 106.0
    assert isinstance(payload["elapsed_ms"], int)
    assert len(payload["personas"]) == 6
    for row in payload["personas"]:
        for key in ("name", "role", "signal", "confidence", "thesis",
                    "key_evidence", "abstained", "model", "latency_ms"):
            assert key in row
    for key in ("consensus", "conviction", "summary", "disagreements",
                "risk_flags"):
        assert key in payload["pm"]


def test_cli_desk_context_error_fails_loud(monkeypatch, tmp_path, capsys):
    """CLI turns a context error into exit 1 + {ok: False} JSON."""
    import gold_desk.agent.desk as desk_pkg

    def boom(symbol, **kwargs):
        raise DeskContextError("detail: RuntimeError: yahoo is on fire")

    monkeypatch.setattr(desk_pkg, "run_desk", boom)
    rc, out = _run_cli(["desk", "ZZZ", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 1
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is False
    assert "yahoo is on fire" in payload["error"]
    assert payload["symbol"] == "ZZZ"


def test_cli_desk_human_output(monkeypatch, tmp_path, capsys):
    """Human output: symbol header, one line per persona, PM block."""
    import gold_desk.agent.desk as desk_pkg
    monkeypatch.setattr(desk_pkg, "run_desk",
                        lambda s, **k: run_desk(s, **{
                            **k, "data_root": tmp_path}))
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json", _fake_complete_json())
    rc, out = _run_cli(["desk", "BTC-USD",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    assert "ANALYST DESK" in out
    assert "BTC-USD" in out and "Bitcoin USD" in out
    for role in ("THE TECHNICIAN", "THE MACRO STRATEGIST",
                 "THE NEWS ANALYST", "THE SENTIMENT READER",
                 "THE RISK MANAGER", "THE FUNDAMENTALIST"):
        assert role in out
    # persona line format: ROLE signal NN% thesis
    assert "bullish   72%" in out or "bullish  72%" in out
    assert "consensus" in out and "bullish" in out
    assert "conviction" in out


# ---------------- P12 defect 1: parse-failure rescue re-prompt ----------------

def test_invalid_json_gets_one_rescue_reprompt(monkeypatch):
    """A model returning unparseable prose gets ONE JSON-only re-prompt on
    the same model before falling through (the 40%-abstention fix)."""
    from gold_desk.agent.desk import engine as desk_engine
    from gold_desk.llm.zen_client import LLMInvalidJSON

    calls = {"n": 0}

    def fake_complete_json(messages, model, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise LLMInvalidJSON("no JSON object found: 'I think therefore "
                                 "it is bullish but here is prose'")
        return {"signal": "bullish", "confidence": 80,
                "thesis": "rescued", "key_evidence": ["a"]}

    monkeypatch.setattr(desk_engine, "complete_json", fake_complete_json)
    parsed, model = desk_engine._complete_json_with_fallback(
        [{"role": "user", "content": "judge this"}], ["m-free"],
        timeout=30.0)
    assert parsed["signal"] == "bullish"
    assert parsed["thesis"] == "rescued"
    assert calls["n"] == 2, "rescue re-prompt must fire exactly once"
    assert model == "m-free"


def test_rescue_failure_falls_to_next_model(monkeypatch):
    """When the rescue re-prompt ALSO fails, fall through to the next
    model in the chain."""
    from gold_desk.agent.desk import engine as desk_engine
    from gold_desk.llm.zen_client import LLMInvalidJSON

    calls = {"n": 0}

    def fake_complete_json(messages, model, **kw):
        calls["n"] += 1
        if model == "m-a":
            raise LLMInvalidJSON("prose again")
        return {"signal": "neutral", "confidence": 50,
                "thesis": "from b", "key_evidence": []}

    monkeypatch.setattr(desk_engine, "complete_json", fake_complete_json)
    parsed, model = desk_engine._complete_json_with_fallback(
        [{"role": "user", "content": "judge"}], ["m-a", "m-b"],
        timeout=30.0)
    assert model == "m-b"
    assert parsed["thesis"] == "from b"
    # m-a failed twice (original + rescue), m-b succeeded once
    assert calls["n"] == 3


def test_transport_failure_skips_rescue(monkeypatch):
    """LLMUnavailable goes straight to the next model — no rescue burn."""
    from gold_desk.agent.desk import engine as desk_engine
    from gold_desk.llm.zen_client import LLMUnavailable

    calls = {"n": 0}

    def fake_complete_json(messages, model, **kw):
        calls["n"] += 1
        if model == "m-a":
            raise LLMUnavailable("zen http 503")
        return {"signal": "bearish", "confidence": 60,
                "thesis": "from b", "key_evidence": []}

    monkeypatch.setattr(desk_engine, "complete_json", fake_complete_json)
    parsed, model = desk_engine._complete_json_with_fallback(
        [{"role": "user", "content": "judge"}], ["m-a", "m-b"],
        timeout=30.0)
    assert model == "m-b"
    assert calls["n"] == 2, "no rescue call for transport failures"


# ---------------- R2-1 fix — defect 1: prompt-size + max_tokens ----------------

def test_slice_institutional_trims_to_top_10_by_value():
    """The fundamentalist's 13F entitlement slice trims the FULL 89-
    position Berkshire array to TOP 10 BY VALUE — the persona prompt
    drops from ~16,260 chars to ~6,000 chars so the JSON lands within
    the free-tier model's response budget on DEFAULT engine settings
    (no max_tokens CLI knob required for normal runs). The full
    picture is preserved via total_value + n_positions + top10_pct so
    the persona still reasons about concentration honestly."""
    # synthetic 89-position 13F (descending values)
    positions = [{"issuer": f" issuer-{i:02d}",
                  "cusip": f"c{i:08d}",
                  "value": (89 - i) * 1_000_000_000,
                  "shares": (89 - i) * 1_000_000,
                  "type": "SH"} for i in range(89)]
    inst = {"ok": True, "fund": "BRK", "cik": "0001067983",
            "filed": "2026-08-14",
            "accession": "0001193125-26-352200",
            "total_value": sum(p["value"] for p in positions),
            "n_positions": 89, "top10_pct": 66.8,
            "positions": positions}
    sl = eng._slice_institutional(inst)
    assert sl["ok"] is True
    assert sl["n_positions_shown"] == 10
    assert sl["n_positions"] == 89  # full picture preserved
    assert sl["total_value"] == inst["total_value"]
    assert sl["top10_pct"] == 66.8
    assert sl["accession"] == "0001193125-26-352200"
    # top-10 by value, descending
    shown = [p["value"] for p in sl["positions"]]
    assert len(shown) == 10
    assert shown == sorted(shown, reverse=True)
    assert shown[0] == 89 * 1_000_000_000
    assert shown[-1] == 80 * 1_000_000_000
    # each shown position keeps the core fields (issuer/cusip/value/shares/type)
    for p in sl["positions"]:
        assert "issuer" in p and "value" in p and "type" in p
    # note explicitly tells the LLM the slice was trimmed
    assert "top 10" in sl["note"]


def test_slice_institutional_preserves_ok_false_shape():
    """A dead slice (ok:False) is passed through so the persona can
    abstain cleanly when the 13F feed is down — the trim logic only
    applies to live slices."""
    dead = {"ok": False, "error": "13F feed 429"}
    sl = eng._slice_institutional(dead)
    assert sl["ok"] is False
    assert sl.get("error") == "13F feed 429"
    assert "positions" not in sl  # no fabricated positions


def test_slice_institutional_handles_none_input():
    """None inst slice → {ok:False} shape (the fundamentalist's
    abstention path when the institutional gather returned no slice)."""
    sl = eng._slice_institutional(None)
    assert sl["ok"] is False
    assert "no institutional slice" in sl["error"]


def test_slice_institutional_handles_empty_positions():
    """A live slice with zero positions (rare edge case: a filer that
    filed a 13F-HR with no holdings) yields ok:True with empty
    positions array — the persona sees an honest empty slice."""
    inst = {"ok": True, "fund": "EMPTY", "cik": "0000000000",
            "filed": "2026-08-14", "accession": "x",
            "total_value": 0.0, "n_positions": 0,
            "top10_pct": 0.0, "positions": []}
    sl = eng._slice_institutional(inst)
    assert sl["ok"] is True
    assert sl["positions"] == []
    assert sl["n_positions_shown"] == 0
    assert sl["n_positions"] == 0


def test_fundamentalist_persona_max_tokens_override_is_4800():
    """R2-1 fix — defect 1: the fundamentalist's per-persona
    max_tokens is bumped to 4800 (default stays 2400 for every other
    persona). Combined with the 13F top-10 trim, this lets the
    fundamentalist's call land within the model's response budget on
    DEFAULT engine settings (no max_tokens CLI knob required)."""
    assert eng.PERSONA_DEFAULT_MAX_TOKENS == 2400
    assert eng.PERSONA_MAX_TOKENS["fundamentalist"] == 4800
    # no other persona is overridden — they all use the 2400 default
    for name in ("technician", "macro", "news", "sentiment", "risk"):
        assert name not in eng.PERSONA_MAX_TOKENS


def test_run_desk_passes_per_persona_max_tokens_to_complete_json(
        monkeypatch, tmp_path):
    """The fundamentalist's complete_json call carries max_tokens=4800;
    every other persona's call carries max_tokens=2400. Pinned via a
    scripted complete_json that records the kwarg per call."""
    _patch_context(monkeypatch)
    seen: dict[str, int] = {}

    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        key = next((k for marker, k in _MARKERS.items()
                    if system.startswith(marker)), None)
        if key and key != "pm":
            seen[key] = kwargs.get("max_tokens")
        if key == "pm":
            return dict(PM_REPLY)
        return dict(PERSONA_REPLIES[key])
    monkeypatch.setattr(eng, "complete_json", fake)
    out = run_desk("btc", data_root=tmp_path)
    assert out["ok"] is True
    # the fundamentalist gets 4800; every other persona gets 2400
    assert seen["fundamentalist"] == 4800
    for name in ("technician", "macro", "news", "sentiment", "risk"):
        assert seen[name] == 2400, \
            f"{name} should use the default 2400, got {seen[name]}"


def test_run_desk_fundamentalist_sees_trimmed_13f_in_prompt(
        monkeypatch, tmp_path):
    """The fundamentalist's user message carries the trimmed 13F
    (top-10) + total_value + n_positions + top10_pct + note. It does
    NOT carry all 89 positions — that would blow the prompt-size
    budget. Verified by intercepting the user content of the
    fundamentalist's complete_json call."""
    _patch_context(monkeypatch)
    # inject a live 89-position institutional_top slice (overrides the
    # _patch_context default which returns ok:False empty slices)
    positions = [{"issuer": f"issuer-{i:02d}",
                  "value": (89 - i) * 1_000_000_000,
                  "shares": (89 - i) * 1_000_000, "type": "SH"}
                 for i in range(89)]
    monkeypatch.setattr(eng, "gather_institutional_context",
        lambda s, d: {"ok": True, "slices": {
            "institutional_top": {"ok": True, "fund": "BRK",
                "cik": "0001067983", "filed": "2026-08-14",
                "accession": "0001193125-26-352200",
                "total_value": sum(p["value"] for p in positions),
                "n_positions": 89, "top10_pct": 66.8,
                "positions": positions}}})
    captured: dict = {}

    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        if system.startswith("You are The Fundamentalist"):
            captured["user"] = messages[-1]["content"]
            return dict(PERSONA_REPLIES["fundamentalist"])
        if system.startswith("You are The Portfolio Manager"):
            return dict(PM_REPLY)
        key = next((k for marker, k in _MARKERS.items()
                    if system.startswith(marker)), None)
        return dict(PERSONA_REPLIES[key])
    monkeypatch.setattr(eng, "complete_json", fake)
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    user = captured["user"]
    # the trim happened: only 10 positions appear in the user message
    # (count "issuer-" prefix occurrences on the values — each of the
    # 10 shown positions has issuer "issuer-NN"; the other 79 don't
    # appear at all)
    assert user.count("issuer-") == 10
    # the full picture fields are present so the LLM can reason about
    # concentration honestly without seeing all 89 rows
    assert '"n_positions": 89' in user
    assert '"total_value"' in user
    assert '"top10_pct": 66.8' in user
    assert '"n_positions_shown": 10' in user
    assert "top 10 by value" in user
    # the user message size is bounded (well under the original 16,260
    # chars the critic reproduced — the trim is the actual fix)
    assert len(user) < 7000, \
        f"fundamentalist user msg too long ({len(user)} chars)"
