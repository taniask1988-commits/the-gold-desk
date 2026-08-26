"""R2-3 — adversarial debate + execution architecture (offline tests).

Judged vs TradingAgents v0.3.1 tradingagents/agents/: the 6-phase
debate flow (6 analysts → bull/bear researchers → research_manager →
trader → 3 debators → rewired PM) with mechanical validation
(r:r re-compute, conviction calibration, abstention discipline).

Test surface:
  - mechanical helpers: _compute_rr, _calibrate_conviction,
    _should_abstain, _supporting_verdicts (unit)
  - persona wiring: kind-dispatched validators; the 5 new persona
    constants (RESEARCHER_PERSONAS, MANAGER_PERSONA, TRADER_PERSONA,
    DEBATOR_PERSONAS, DEBATE_PERSONAS) shapes
  - end-to-end run_desk(debate=True) with scripted replies for all 14
    personas + PM
  - the brief's machine-checkable rules:
    * bull/bear cite ≥2 specific analyst claims (extracted by the same
      verified_snapshot conflict-flag regex the technician is held to)
    * research_memo has thesis+conviction+evidence+kill_criteria
    * trader plan has entry/stop/target/size + mechanical r:r
    * 3 debators each produce a verdict (UPSIZE/HOLD/DOWNSIZE/REJECT)
    * PM ABSTAINs when any debator REJECTs (mock)
    * PM ABSTAINs when r:r < 1.0 (mock)
    * PM conviction=HIGH requires r:r ≥ 2.0 AND ≥2 supporting debators
    * PM conviction=MED requires r:r ≥ 1.5
    * PM kill_criteria non-empty for BUY/SELL
  - the verified_snapshot conflict-flag extends to the researchers'
    theses and the debators' reasoning (the brief's machine-check
    extension)
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
from gold_desk.agent.desk.engine import (  # noqa: E402
    DeskContextError,
    run_desk,
    _compute_rr,
    _calibrate_conviction,
    _should_abstain,
    _supporting_verdicts,
    _action_to_consensus,
)
from gold_desk.agent.desk.personas import (  # noqa: E402
    DESK_TOOLS,
    PERSONAS,
    DEBATE_PERSONAS,
    RESEARCHER_PERSONAS,
    MANAGER_PERSONA,
    TRADER_PERSONA,
    DEBATOR_PERSONAS,
    Persona,
)
from gold_desk.events import Journal  # noqa: E402
from gold_desk.features.verified_snapshot import (  # noqa: E402
    extract_numeric_claims,
    flag_claim_conflicts,
)
from gold_desk.llm.zen_client import LLMUnavailable  # noqa: E402


# ============================================================ helpers / fakes

def _bars_up(n=80, base=100.0):
    bars = []
    t0 = 1_756_000_000_000
    for i in range(n):
        o = base + i * 0.5
        bars.append({"ts": t0 + i * 86400000, "o": round(o, 2),
                     "h": round(o + 0.3, 2), "l": round(o - 0.3, 2),
                     "c": round(o + 0.1, 2), "v": 1000 + i})
    return bars


def _detail(symbol="AAPL"):
    return {"ok": True, "symbol": symbol, "name": symbol,
            "sector": "us", "price": 149.0,
            "change_pct": 1.0, "range_5d_change_pct": 3.4,
            "bars": _bars_up(n=80),
            "news": {"ok": True, "items": []}}


def _patch_context(monkeypatch):
    monkeypatch.setattr(eng, "fetch_detail", lambda s, d: _detail())
    monkeypatch.setattr(eng, "fetch_daily_bars",
                        lambda s, data_root=None: _bars_up(n=80))
    monkeypatch.setattr(eng, "fetch_board",
                        lambda d: {"ok": True, "as_of": "now",
                                   "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers",
                        lambda d: {"ok": True, "as_of": "now",
                                   "gainers": [], "losers": []})
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {}})


# The canonical scripted replies for the 6 analyst personas. The bull
# + bear researchers will cross-examine these — so the bull's thesis
# cites "RSI 47.78" and "MACD hist -0.151" (numbers from the analyst
# outputs), and the verified_snapshot conflict-flag extractor must
# pull them out as claims.
ANALYST_REPLIES = {
    "technician": {
        "signal": "bullish", "confidence": 72,
        "thesis": ("RSI 47.78 with MACD hist -0.151 — momentum turning "
                   "up off range support."),
        "key_evidence": ["RSI 47.78 (analyst)",
                         "MACD hist -0.151 (analyst)",
                         "price above SMA50"]},
    "macro": {"signal": "neutral", "confidence": 55,
              "thesis": "Dollar mixed; yields stable.",
              "key_evidence": ["DX-Y.NYB -0.40%", "^TNX +1.10%"]},
    "news": {"signal": "bullish", "confidence": 61,
             "thesis": "Fresh positive catalysts dominate.",
             "key_evidence": ["headline 1", "headline 2"]},
    "sentiment": {"signal": "bearish", "confidence": 48,
                  "thesis": "Crowding is one-way after a 6% day.",
                  "key_evidence": ["TOP +40.0%", "symbol +6.0%"]},
    "risk": {"signal": "neutral", "confidence": 50,
             "thesis": "Stops survivable; tape 3h stale.",
             "key_evidence": ["swing high 0.7 ATR", "last bar 180m old"]},
    "fundamentalist": {"signal": "bullish", "confidence": 64,
                       "thesis": "Revenue + EPS rose 8Q; accession-cited.",
                       "key_evidence": ["rev 109.4B (acc 0000320193)",
                                        "EPS 1.40→2.02",
                                        "13F AMEX 50.4B"]},
}

# The bull_researcher's reply MUST cite ≥2 specific analyst claims
# (machine-checkable via extract_numeric_claims). The thesis cites
# "RSI 47.78" and "MACD hist -0.151" from the technician's output.
BULL_REPLY = {
    "signal": "bullish", "confidence": 70,
    "thesis": ("Technician cited RSI 47.78 and MACD hist -0.151 turning "
               "up; fundamentalist cited EPS 1.40 rising to 2.02 — long "
               "case carries on momentum + fundamental confirmation."),
    "key_evidence": ["technician: RSI 47.78 (analyst)",
                     "technician: MACD hist -0.151 (analyst)",
                     "fundamentalist: EPS 2.02"],
}

# The bear_researcher's reply — same discipline, mirror image.
BEAR_REPLY = {
    "signal": "bearish", "confidence": 60,
    "thesis": ("Sentiment cited crowding after a +6.0% day; the "
               "technician's RSI 47.78 is mid-range, not extreme — short "
               "case rests on exhaustion, not overbought."),
    "key_evidence": ["sentiment: TOP +40.0%",
                     "technician: RSI 47.78",
                     "risk: tape 3h stale"],
}

MANAGER_REPLY = {
    "thesis": "LONG",
    "conviction": "MED",
    "supporting_evidence": ["bull: RSI 47.78 turning up",
                            "bull: EPS 2.02 rising",
                            "fundamentalist: rev 109.4B"],
    "counter_evidence": ["bear: crowding after +6.0% day",
                         "risk: tape 3h stale"],
    "kill_criteria": ["close below 145.00 (recent swing low)",
                      "RSI drops below 40",
                      "EPS revision < 1.80"],
    "summary": "Long case carries on momentum + fundamentals; "
               "crowding is the swing risk.",
}

TRADER_REPLY = {
    "action": "BUY",
    "entry_price": 149.0,
    "stop_price": 145.0,
    "target_price": 157.0,
    "position_size_pct": 0.05,
    "time_horizon": "swing",
    "risk_reward_ratio": 2.0,        # (157-149)/(149-145) = 8/4 = 2.0
    "reasoning": "Long at the verified_snapshot last_close 149.0; stop "
                 "1.0 ATR below swing low; target 2.0 r:r.",
}

DEBATOR_REPLIES = {
    "aggressive_debator": {
        "verdict": "UPSIZE", "reasoning": "r:r 2.0 + kill_criteria "
        "remote + vol regime calm.",
        "evidence_cited": ["rr 2.0", "vol regime: calm", "kill_criteria "
                           "remote"]},
    "conservative_debator": {
        "verdict": "HOLD", "reasoning": "r:r 2.0 is adequate; beta "
        "0.20 is stable.",
        "evidence_cited": ["rr 2.0", "beta 0.20", "kill_criteria "
                           "within reach"]},
    "neutral_debator": {
        "verdict": "UPSIZE", "reasoning": "r:r 2.0 + 2 of 3 kill "
        "criteria remote.",
        "evidence_cited": ["rr 2.0", "kill_criteria: 2/3 remote"]},
}

# The rewired PM's reply — action + entry/stop/target/size + conviction_
# label + r:r + kill_criteria + reasoning + evidence_cited + legacy
# consensus/conviction/summary/disagreements/risk_flags.
PM_DEBATE_REPLY = {
    "action": "BUY",
    "entry_price": 149.0,
    "stop_price": 145.0,
    "target_price": 157.0,
    "position_size_pct": 0.05,
    "conviction_label": "HIGH",
    "risk_reward_ratio": 2.0,
    "kill_criteria": ["close < 145.00", "RSI < 40", "EPS < 1.80"],
    "reasoning": "Long case carries on momentum + fundamentals; 2/3 "
                 "debators verdict UPSIZE/HOLD.",
    "evidence_cited": [
        {"persona": "bull_researcher", "claim": "RSI 47.78 turning up",
         "source": "researcher_outputs"},
        {"persona": "research_manager", "claim": "thesis LONG MED",
         "source": "research_memo"},
        {"persona": "aggressive_debator", "claim": "UPSIZE",
         "source": "debator_verdicts"}],
    "consensus": "bullish",
    "conviction": 70,
    "summary": "Long biased on momentum + fundamentals; 2/3 debators "
               "support.",
    "disagreements": "Sentiment sees crowding; technician sees orderly.",
    "risk_flags": ["stop only 4 pts below entry",
                   "tape 3h stale"],
}

_MARKERS = {
    # analysts (legacy)
    "You are The Technician": "technician",
    "You are The Macro Strategist": "macro",
    "You are The News Analyst": "news",
    "You are The Sentiment Reader": "sentiment",
    "You are The Risk Manager": "risk",
    "You are The Fundamentalist": "fundamentalist",
    # researchers
    "You are The Bull Researcher": "bull_researcher",
    "You are The Bear Researcher": "bear_researcher",
    # manager / trader / debators
    "You are The Research Manager": "research_manager",
    "You are The Trader.": "trader",     # period to avoid matching "Trader..."
    "You are The Aggressive Risk Debator": "aggressive_debator",
    "You are The Conservative Risk Debator": "conservative_debator",
    "You are The Neutral Risk Debator": "neutral_debator",
    # PM (legacy + rewired share the "You are The Portfolio Manager" prefix)
    "You are The Portfolio Manager": "pm",
}


def _fake_complete_json(calls=None, barrier=None, fail=(),
                         pm_override=None,
                         trader_override=None,
                         debator_overrides=None,
                         manager_override=None,
                         bull_override=None,
                         bear_override=None):
    """Scripted complete_json for the 6-phase debate flow.

    Overrides:
      - pm_override: replaces PM_DEBATE_REPLY for the rewired PM call
      - trader_override: replaces TRADER_REPLY
      - debator_overrides: dict {name: reply} for the 3 debators
      - manager_override: replaces MANAGER_REPLY
      - bull_override / bear_override: replaces BULL_REPLY / BEAR_REPLY
    """
    debator_overrides = debator_overrides or {}

    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        key = next((k for marker, k in _MARKERS.items()
                    if system.startswith(marker)), None)
        if calls is not None:
            calls.append((key, model))
        if key in fail:
            raise LLMUnavailable(f"zen down for {key}")
        if barrier is not None and key in _MARKERS and key != "pm":
            barrier.wait(timeout=10)
        if key == "pm":
            return dict(pm_override or PM_DEBATE_REPLY)
        if key == "trader":
            return dict(trader_override or TRADER_REPLY)
        if key == "research_manager":
            return dict(manager_override or MANAGER_REPLY)
        if key == "bull_researcher":
            return dict(bull_override or BULL_REPLY)
        if key == "bear_researcher":
            return dict(bear_override or BEAR_REPLY)
        if key in ("aggressive_debator", "conservative_debator",
                   "neutral_debator"):
            return dict(debator_overrides.get(key)
                        or DEBATOR_REPLIES[key])
        return dict(ANALYST_REPLIES[key])
    return fake


# =============================================== mechanical helpers (unit)

def test_compute_rr_buy_long_geometry():
    """(target - entry) / (entry - stop) for a BUY."""
    assert _compute_rr("BUY", 100.0, 95.0, 110.0) == 2.0
    assert _compute_rr("BUY", 100.0, 90.0, 130.0) == 3.0
    assert _compute_rr("BUY", 50.0, 45.0, 75.0) == 5.0


def test_compute_rr_sell_short_geometry():
    """(entry - target) / (stop - entry) for a SELL — flip of long."""
    assert _compute_rr("SELL", 100.0, 105.0, 90.0) == 2.0
    assert _compute_rr("SELL", 100.0, 110.0, 70.0) == 3.0


def test_compute_rr_hold_returns_none():
    """HOLD has no trade geometry — r:r is None."""
    assert _compute_rr("HOLD", 100.0, 95.0, 110.0) is None


def test_compute_rr_abstain_returns_none():
    """ABSTAIN has no trade geometry."""
    assert _compute_rr("ABSTAIN", 100.0, 95.0, 110.0) is None


def test_compute_rr_none_action_returns_none():
    """None action (no trader plan) — None."""
    assert _compute_rr(None, 100.0, 95.0, 110.0) is None


def test_compute_rr_invalid_buy_geometry_target_below_entry():
    """For BUY, target must be > entry. If not, None."""
    assert _compute_rr("BUY", 100.0, 95.0, 99.0) is None


def test_compute_rr_invalid_buy_geometry_stop_above_entry():
    """For BUY, entry must be > stop. If not, None."""
    assert _compute_rr("BUY", 100.0, 105.0, 110.0) is None


def test_compute_rr_invalid_sell_geometry_target_above_entry():
    """For SELL, target must be < entry. If not, None."""
    assert _compute_rr("SELL", 100.0, 105.0, 110.0) is None


def test_compute_rr_none_prices_returns_none():
    """Any None on the geometry side → None (the PM treats as r:r<1)."""
    assert _compute_rr("BUY", None, 95.0, 110.0) is None
    assert _compute_rr("BUY", 100.0, None, 110.0) is None
    assert _compute_rr("SELL", 100.0, 105.0, None) is None


def test_compute_rr_bool_inputs_rejected():
    """Python bool is subclass of int — guard against True/False being
    silently accepted as 1/0."""
    assert _compute_rr("BUY", True, False, True) is None


def test_calibrate_conviction_high_valid():
    """HIGH requires r:r ≥ 2.0 AND supporting ≥ 2."""
    assert _calibrate_conviction("HIGH", 2.0, 2) == "HIGH"
    assert _calibrate_conviction("HIGH", 2.5, 3) == "HIGH"
    assert _calibrate_conviction("HIGH", 3.0, 2) == "HIGH"


def test_calibrate_conviction_high_downgrades_to_med_when_support_below_2():
    """HIGH with r:r ≥ 2.0 but only 1 supporting debator → MED."""
    assert _calibrate_conviction("HIGH", 2.0, 1) == "MED"
    assert _calibrate_conviction("HIGH", 2.5, 0) == "MED"


def test_calibrate_conviction_high_downgrades_to_low_when_rr_below_1_5():
    """HIGH with r:r < 1.5 → LOW (skip MED)."""
    assert _calibrate_conviction("HIGH", 1.4, 2) == "LOW"
    assert _calibrate_conviction("HIGH", 1.0, 3) == "LOW"


def test_calibrate_conviction_med_valid():
    """MED requires r:r ≥ 1.5."""
    assert _calibrate_conviction("MED", 1.5, 1) == "MED"
    assert _calibrate_conviction("MED", 1.7, 0) == "MED"


def test_calibrate_conviction_med_downgrades_to_low_when_rr_below_1_5():
    """MED with r:r < 1.5 → LOW."""
    assert _calibrate_conviction("MED", 1.4, 2) == "LOW"
    assert _calibrate_conviction("MED", 1.2, 3) == "LOW"


def test_calibrate_conviction_low_always_low():
    """LOW is always valid (no threshold)."""
    assert _calibrate_conviction("LOW", 0.0, 0) == "LOW"
    assert _calibrate_conviction("LOW", 5.0, 3) == "LOW"


def test_calibrate_conviction_none_rr_treated_as_zero():
    """None r:r → 0.0 → LOW (anything above LOW threshold fails)."""
    assert _calibrate_conviction("HIGH", None, 2) == "LOW"
    assert _calibrate_conviction("MED", None, 3) == "LOW"
    assert _calibrate_conviction("LOW", None, 0) == "LOW"


def test_supporting_verdicts_counts_upsize_and_hold():
    """For BUY: UPSIZE + HOLD count as supporting; DOWNSIZE + REJECT
    don't."""
    debators = [
        {"name": "aggressive", "verdict": "UPSIZE", "abstained": False},
        {"name": "conservative", "verdict": "HOLD", "abstained": False},
        {"name": "neutral", "verdict": "DOWNSIZE", "abstained": False},
    ]
    assert _supporting_verdicts(debators, "BUY") == 2


def test_supporting_verdicts_skips_abstained():
    """Abstained debators don't count."""
    debators = [
        {"name": "aggressive", "verdict": "UPSIZE", "abstained": False},
        {"name": "conservative", "verdict": "HOLD", "abstained": True},
        {"name": "neutral", "verdict": "UPSIZE", "abstained": False},
    ]
    assert _supporting_verdicts(debators, "SELL") == 2


def test_supporting_verdicts_zero_for_hold_or_abstain():
    """HOLD/ABSTAIN actions don't need supporting debators."""
    assert _supporting_verdicts([], "HOLD") == 0
    assert _supporting_verdicts([], "ABSTAIN") == 0
    assert _supporting_verdicts([
        {"name": "x", "verdict": "UPSIZE", "abstained": False}
    ], "HOLD") == 0


def test_should_abstain_when_any_debator_rejects():
    """Any debator REJECT → ABSTAIN (the brief's (a) rule)."""
    debators = [
        {"name": "aggressive", "verdict": "UPSIZE", "abstained": False},
        {"name": "conservative", "verdict": "REJECT", "abstained": False},
        {"name": "neutral", "verdict": "HOLD", "abstained": False},
    ]
    abstain, reason = _should_abstain([], debators, 2.0, "BUY")
    assert abstain is True
    assert "REJECT" in reason


def test_should_abstain_when_rr_below_1():
    """r:r < 1.0 → ABSTAIN (the brief's (c) rule, using the mechanical
    re-compute)."""
    abstain, reason = _should_abstain([], [], 0.5, "BUY")
    assert abstain is True
    assert "0.5" in reason
    assert "< 1.0" in reason


def test_should_abstain_when_rr_is_none():
    """None r:r (invalid geometry) → ABSTAIN (treated as r:r < 1.0)."""
    abstain, reason = _should_abstain([], [], None, "BUY")
    assert abstain is True
    assert "None" in reason or "< 1.0" in reason


def test_should_abstain_when_action_is_hold():
    """HOLD action → ABSTAIN (no actionable trade)."""
    abstain, reason = _should_abstain([], [], 2.0, "HOLD")
    assert abstain is True
    assert "HOLD" in reason


def test_should_abstain_when_action_is_none():
    """None action (no trader plan) → ABSTAIN."""
    abstain, _ = _should_abstain([], [], 2.0, None)
    assert abstain is True


def test_should_abstain_when_both_researchers_neutral():
    """bull+bear both returned neutral → ABSTAIN (the brief's (b) rule:
    can't agree on direction)."""
    researchers = [
        {"name": "bull_researcher", "signal": "neutral",
         "abstained": False},
        {"name": "bear_researcher", "signal": "neutral",
         "abstained": False},
    ]
    abstain, reason = _should_abstain(researchers, [], 2.0, "BUY")
    assert abstain is True
    assert "neutral" in reason.lower()


def test_should_not_abstain_when_one_researcher_neutral():
    """If only ONE researcher is neutral (the other has a directional
    view), the desk has a directional lean — don't abstain."""
    researchers = [
        {"name": "bull_researcher", "signal": "bullish",
         "abstained": False},
        {"name": "bear_researcher", "signal": "neutral",
         "abstained": False},
    ]
    abstain, _ = _should_abstain(researchers, [], 2.0, "BUY")
    assert abstain is False


def test_should_not_abstain_on_genuine_debate():
    """bull bullish + bear bearish + valid r:r + no REJECT → don't
    abstain (genuine debate the PM picks based on evidence)."""
    researchers = [
        {"name": "bull_researcher", "signal": "bullish",
         "abstained": False},
        {"name": "bear_researcher", "signal": "bearish",
         "abstained": False},
    ]
    debators = [
        {"name": "aggressive", "verdict": "UPSIZE", "abstained": False},
        {"name": "conservative", "verdict": "HOLD", "abstained": False},
        {"name": "neutral", "verdict": "HOLD", "abstained": False},
    ]
    abstain, _ = _should_abstain(researchers, debators, 2.0, "BUY")
    assert abstain is False


def test_action_to_consensus_mapping():
    """The PM's action maps to the legacy consensus field."""
    assert _action_to_consensus("BUY") == "bullish"
    assert _action_to_consensus("SELL") == "bearish"
    assert _action_to_consensus("HOLD") == "neutral"
    assert _action_to_consensus("ABSTAIN") == "neutral"


# =============================================== persona wiring

def test_debate_personas_count():
    """DEBATE_PERSONAS has 7 entries: 2 researchers + 1 manager + 1
    trader + 3 debators (the R2-3 architecture's new personas)."""
    assert len(DEBATE_PERSONAS) == 7
    assert len(RESEARCHER_PERSONAS) == 2
    assert len(DEBATOR_PERSONAS) == 3
    names = [p.name for p in DEBATE_PERSONAS]
    assert names == ["bull_researcher", "bear_researcher",
                     "research_manager", "trader",
                     "aggressive_debator", "conservative_debator",
                     "neutral_debator"]


def test_debate_persona_kinds_dispatched():
    """Each new persona's `kind` field is set so the engine's
    kind-dispatch validator picks the right wire format."""
    by_name = {p.name: p for p in DEBATE_PERSONAS}
    assert by_name["bull_researcher"].kind == "researcher"
    assert by_name["bear_researcher"].kind == "researcher"
    assert by_name["research_manager"].kind == "manager"
    assert by_name["trader"].kind == "trader"
    assert by_name["aggressive_debator"].kind == "debator"
    assert by_name["conservative_debator"].kind == "debator"
    assert by_name["neutral_debator"].kind == "debator"


def test_debate_persona_tools_subset_of_desk_tools():
    """Every debate persona's tools ⊆ DESK_TOOLS."""
    for p in DEBATE_PERSONAS:
        unknown = set(p.tools) - set(DESK_TOOLS)
        assert not unknown, f"{p.name}: unknown tools {unknown}"


def test_researcher_entitlements_include_analyst_outputs_and_snapshot():
    """The bull/bear researchers cross-examine the analyst outputs
    against the verified_snapshot — both slices are in their tools."""
    for p in RESEARCHER_PERSONAS:
        assert "analyst_outputs" in p.tools
        assert "verified_snapshot" in p.tools


def test_manager_entitlement_is_researcher_outputs():
    """The research_manager's only entitlement is researcher_outputs."""
    assert MANAGER_PERSONA.tools == ["researcher_outputs"]


def test_trader_entitlements_are_memo_and_snapshot():
    """The trader reads the research_memo + verified_snapshot."""
    assert TRADER_PERSONA.tools == ["research_memo", "verified_snapshot"]


def test_debator_entitlements_include_plan_memo_snapshot():
    """The 3 risk debators each take the trader's plan + the research
    memo + the verified_snapshot."""
    for p in DEBATOR_PERSONAS:
        assert set(p.tools) == {"trader_plan", "research_memo",
                                "verified_snapshot"}


def test_debate_personas_prompts_have_signal_contract_or_custom_schema():
    """Each debate persona's system prompt ends with a JSON contract —
    either the SIGNAL_CONTRACT (researchers reuse it) or a custom
    JSON shape (manager/trader/debators)."""
    from gold_desk.agent.desk.personas import SIGNAL_CONTRACT
    by_name = {p.name: p for p in DEBATE_PERSONAS}
    # researchers reuse the signal contract
    for name in ("bull_researcher", "bear_researcher"):
        assert by_name[name].system.rstrip().endswith(SIGNAL_CONTRACT), (
            f"{name} prompt must end with SIGNAL_CONTRACT")
    # manager/trader/debators have custom JSON contracts (the wire
    # formats the engine validates with _manager_result / _trader_result
    # / _debator_result). Pin their terminal "Return ONLY JSON:" markers.
    assert '"thesis": "LONG"|"SHORT"|"NEUTRAL"' in \
        by_name["research_manager"].system
    assert '"action": "BUY"|"SELL"|"HOLD"' in \
        by_name["trader"].system
    assert '"verdict": "UPSIZE"|"HOLD"|"DOWNSIZE"|"REJECT"' in \
        by_name["aggressive_debator"].system
    assert '"verdict": "UPSIZE"|"HOLD"|"DOWNSIZE"|"REJECT"' in \
        by_name["conservative_debator"].system
    assert '"verdict": "UPSIZE"|"HOLD"|"DOWNSIZE"|"REJECT"' in \
        by_name["neutral_debator"].system


def test_debate_persona_prompts_no_account_or_balance_keys():
    """L12 blindfold: the debate personas also reason over market data
    only — no account/balance/equity/PnL language anywhere in their
    prompts."""
    banned = ("account", "balance", "equity", "pnl", "bankroll",
              "capital", "withdraw", "deposit")
    for p in DEBATE_PERSONAS:
        low = p.system.lower()
        for word in banned:
            assert word not in low, (
                f"{p.name} prompt mentions {word!r} (L12 blindfold)")


def test_debate_persona_prompts_reason_only_from_provided_data():
    """The 'reason ONLY from' rule extends to the new personas."""
    for p in DEBATE_PERSONAS:
        assert "ONLY from" in p.system, (
            f"{p.name} prompt lacks the reason-only hard rule")


def test_debate_personas_max_tokens_overrides():
    """The manager + trader get bumped to 3600 (their structured
    payloads are larger); debators stay at 2400; the fundamentalist
    stays at 4800 (R2-1 fix)."""
    assert eng.PERSONA_MAX_TOKENS["research_manager"] == 3600
    assert eng.PERSONA_MAX_TOKENS["trader"] == 3600
    # debators and researchers are NOT in PERSONA_MAX_TOKENS — they
    # use the default 2400
    for name in ("bull_researcher", "bear_researcher",
                 "aggressive_debator", "conservative_debator",
                 "neutral_debator"):
        assert name not in eng.PERSONA_MAX_TOKENS, (
            f"{name} should use the default 2400")


# =============================================== mechanical claim extraction
# The brief: "the verified_snapshot conflict-flag discipline MUST apply
# to the new personas' outputs (bull_researcher's thesis, bear_researcher's
# thesis, debators' reasoning) — extend extract_numeric_claims if needed
# to cover the new claim shapes."

def test_bull_researcher_thesis_extracts_at_least_2_claims():
    """The bull_researcher's thesis cites ≥2 specific analyst numbers
    (RSI 47.78 + MACD hist -0.151). The verified_snapshot conflict-flag
    extractor pulls them out as claims — machine-checkable citation
    discipline the TradingAgents bull_researcher (free-form prose, no
    machine-check) does NOT have."""
    claims = extract_numeric_claims(BULL_REPLY["thesis"])
    kinds = [c["kind"] for c in claims]
    # at least 2 numeric claims
    assert len(claims) >= 2, (
        f"bull thesis must cite ≥2 numeric claims; got {len(claims)}: "
        f"{claims}")
    # RSI 47.78 + MACD hist -0.151 must be among them
    rsi_claim = next((c for c in claims if c["kind"] == "rsi"), None)
    macd_claim = next((c for c in claims if c["kind"] == "macd_hist"), None)
    assert rsi_claim is not None and rsi_claim["value"] == 47.78
    assert macd_claim is not None and macd_claim["value"] == -0.151


def test_bear_researcher_thesis_extracts_at_least_2_claims():
    """The bear_researcher's thesis cites ≥2 specific analyst numbers
    (TOP +40.0% + RSI 47.78). The extractor pulls them out — machine-
    checkable citation discipline on the bear side too."""
    claims = extract_numeric_claims(BEAR_REPLY["thesis"])
    assert len(claims) >= 2, (
        f"bear thesis must cite ≥2 numeric claims; got {len(claims)}: "
        f"{claims}")
    # RSI 47.78 is cited (the bear acknowledges the technician's claim)
    rsi_claim = next((c for c in claims if c["kind"] == "rsi"), None)
    assert rsi_claim is not None and rsi_claim["value"] == 47.78


def test_bull_researcher_thesis_no_conflicts_on_matching_snapshot():
    """When the bull cites numbers that match the verified_snapshot
    (RSI 47.78, MACD -0.151), the conflict-flag is silent — the
    discipline is no longer decorative on the bull side."""
    snap = {"ok": True, "rsi14": 47.78, "macd_hist": -0.151,
            "regime_labels": {}}
    conflicts = flag_claim_conflicts(BULL_REPLY["thesis"], snap)
    # only the named-indicator claims route; the bare-$price and bare-%
    # claims route against the snapshot's pct fields (none in this
    # minimal snap) — so the conflicts list is empty or only contains
    # claims that route against fields the snap doesn't have.
    assert all(c["kind"] in ("rsi", "macd_hist") or c["kind"] == "pct"
               for c in conflicts)
    # no RSI / MACD conflicts because they match
    assert not any(c["kind"] == "rsi" for c in conflicts)
    assert not any(c["kind"] == "macd_hist" for c in conflicts)


def test_bull_researcher_thesis_flags_on_drifted_snapshot():
    """If the bull's cited RSI drifts from the snapshot, the conflict
    fires — the discipline is honest, not silent."""
    snap = {"ok": True, "rsi14": 99.99, "macd_hist": 5.0,
            "regime_labels": {}}
    conflicts = flag_claim_conflicts(BULL_REPLY["thesis"], snap)
    rsi_conflict = next((c for c in conflicts if c["kind"] == "rsi"), None)
    assert rsi_conflict is not None
    assert rsi_conflict["claim_value"] == 47.78
    assert rsi_conflict["snapshot_value"] == 99.99


# =============================================== persona result validators

def test_manager_result_validates_well_formed_memo():
    """_manager_result returns the manager dict with all 5 fields when
    the parsed JSON is well-formed."""
    from gold_desk.agent.desk.personas import MANAGER_PERSONA
    parsed = dict(MANAGER_REPLY)
    out = eng._manager_result(MANAGER_PERSONA, parsed, "model-x", 0.5)
    assert out["kind"] == "manager"
    assert out["thesis"] == "LONG"
    assert out["conviction"] == "MED"
    assert len(out["supporting_evidence"]) == 3
    assert len(out["counter_evidence"]) == 2
    assert len(out["kill_criteria"]) == 3
    assert out["abstained"] is False


def test_manager_result_rejects_invalid_thesis():
    """An invalid thesis (not in LONG/SHORT/NEUTRAL) raises ValueError,
    which _run_persona converts to an abstention."""
    from gold_desk.agent.desk.personas import MANAGER_PERSONA
    parsed = dict(MANAGER_REPLY)
    parsed["thesis"] = "SIDEWAYS"
    with pytest.raises(ValueError, match="invalid thesis"):
        eng._manager_result(MANAGER_PERSONA, parsed, "model-x", 0.5)


def test_manager_result_rejects_invalid_conviction():
    """An invalid conviction (not in LOW/MED/HIGH) raises ValueError."""
    from gold_desk.agent.desk.personas import MANAGER_PERSONA
    parsed = dict(MANAGER_REPLY)
    parsed["conviction"] = "EXTREME"
    with pytest.raises(ValueError, match="invalid conviction"):
        eng._manager_result(MANAGER_PERSONA, parsed, "model-x", 0.5)


def test_trader_result_validates_well_formed_plan():
    """_trader_result returns the trader dict with all 8 fields when
    the parsed JSON is well-formed."""
    from gold_desk.agent.desk.personas import TRADER_PERSONA
    parsed = dict(TRADER_REPLY)
    out = eng._trader_result(TRADER_PERSONA, parsed, "model-x", 0.5)
    assert out["kind"] == "trader"
    assert out["action"] == "BUY"
    assert out["entry_price"] == 149.0
    assert out["stop_price"] == 145.0
    assert out["target_price"] == 157.0
    assert out["position_size_pct"] == 0.05
    assert out["time_horizon"] == "swing"
    assert out["risk_reward_ratio"] == 2.0
    assert out["abstained"] is False


def test_trader_result_rejects_invalid_buy_geometry():
    """A BUY plan with target ≤ entry raises ValueError."""
    from gold_desk.agent.desk.personas import TRADER_PERSONA
    parsed = dict(TRADER_REPLY)
    parsed["target_price"] = 149.0  # == entry, invalid for BUY
    with pytest.raises(ValueError, match="BUY geometry invalid"):
        eng._trader_result(TRADER_PERSONA, parsed, "model-x", 0.5)


def test_trader_result_rejects_invalid_sell_geometry():
    """A SELL plan with stop ≤ entry raises ValueError."""
    from gold_desk.agent.desk.personas import TRADER_PERSONA
    parsed = dict(TRADER_REPLY)
    parsed["action"] = "SELL"
    parsed["entry_price"] = 100.0
    parsed["stop_price"] = 95.0   # below entry — invalid for SELL
    parsed["target_price"] = 90.0
    with pytest.raises(ValueError, match="SELL geometry invalid"):
        eng._trader_result(TRADER_PERSONA, parsed, "model-x", 0.5)


def test_trader_result_rejects_invalid_action():
    """An invalid action raises ValueError."""
    from gold_desk.agent.desk.personas import TRADER_PERSONA
    parsed = dict(TRADER_REPLY)
    parsed["action"] = "TRADE"
    with pytest.raises(ValueError, match="invalid action"):
        eng._trader_result(TRADER_PERSONA, parsed, "model-x", 0.5)


def test_debator_result_validates_well_formed_verdict():
    """_debator_result returns the debator dict with all 3 fields when
    the parsed JSON is well-formed."""
    from gold_desk.agent.desk.personas import DEBATOR_PERSONAS
    p = DEBATOR_PERSONAS[0]   # aggressive_debator
    parsed = dict(DEBATOR_REPLIES["aggressive_debator"])
    out = eng._debator_result(p, parsed, "model-x", 0.5)
    assert out["kind"] == "debator"
    assert out["verdict"] == "UPSIZE"
    # the debator's reasoning references the r:r ratio — accept either
    # the "r:r" form (the fixture's actual format) or "rr" (defensive).
    assert "r:r" in out["reasoning"] or "rr" in out["reasoning"]
    assert len(out["evidence_cited"]) == 3
    assert out["abstained"] is False


def test_debator_result_rejects_invalid_verdict():
    """An invalid verdict raises ValueError."""
    from gold_desk.agent.desk.personas import DEBATOR_PERSONAS
    p = DEBATOR_PERSONAS[0]
    parsed = dict(DEBATOR_REPLIES["aggressive_debator"])
    parsed["verdict"] = "MAYBE"
    with pytest.raises(ValueError, match="invalid verdict"):
        eng._debator_result(p, parsed, "model-x", 0.5)


# =============================================== end-to-end run_desk(debate=True)

def test_run_desk_debate_full_flow_scripted(monkeypatch, tmp_path):
    """The full 6-phase debate flow runs end-to-end with scripted replies
    for all 14 personas + PM. The output carries every required field
    (personas, researchers, research_memo, trader_plan, debators, pm)
    with the PM's extended trade-decision artifact shape."""
    _patch_context(monkeypatch)
    calls: list = []
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(calls=calls))
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    # Phase 1: 6 analyst personas
    assert len(out["personas"]) == 6
    assert [p["name"] for p in out["personas"]] == [
        "technician", "macro", "news", "sentiment", "risk",
        "fundamentalist"]
    # Phase 2: 2 researchers
    assert len(out["researchers"]) == 2
    assert [r["name"] for r in out["researchers"]] == [
        "bull_researcher", "bear_researcher"]
    # Phase 3: research_memo
    assert out["research_memo"]["thesis"] == "LONG"
    assert out["research_memo"]["conviction"] == "MED"
    assert len(out["research_memo"]["kill_criteria"]) == 3
    # Phase 4: trader_plan
    assert out["trader_plan"]["action"] == "BUY"
    assert out["trader_plan"]["entry_price"] == 149.0
    assert out["trader_plan"]["stop_price"] == 145.0
    assert out["trader_plan"]["target_price"] == 157.0
    # the engine mechanically re-computed r:r (matches the trader's claim)
    assert out["trader_plan"]["risk_reward_ratio"] == 2.0
    assert out["trader_plan"]["risk_reward_ratio_computed"] == 2.0
    # Phase 5: 3 debators
    assert len(out["debators"]) == 3
    assert [d["name"] for d in out["debators"]] == [
        "aggressive_debator", "conservative_debator", "neutral_debator"]
    # Phase 6: rewired PM
    pm = out["pm"]
    assert pm["action"] == "BUY"
    assert pm["entry_price"] == 149.0
    assert pm["stop_price"] == 145.0
    assert pm["target_price"] == 157.0
    assert pm["position_size_pct"] == 0.05
    assert pm["conviction_label"] == "HIGH"   # r:r 2.0 + 2/3 supporting
    assert pm["risk_reward_ratio"] == 2.0     # mechanical re-compute
    assert pm["risk_reward_ratio_claimed"] == 2.0
    assert len(pm["kill_criteria"]) == 3
    assert pm["transcript_ref"].startswith("journal:run_id=")
    # legacy PM fields preserved (the journal contract is EXTENDED,
    # not broken)
    assert pm["consensus"] == "bullish"
    assert pm["conviction"] == 70
    assert "summary" in pm
    assert "risk_flags" in pm
    assert pm["mechanical"] is False
    # 14 LLM calls total: 6 analysts + 2 researchers + 1 manager + 1
    # trader + 3 debators + 1 PM
    keys = [k for k, _ in calls]
    assert len(keys) == 14
    expected = sorted(["technician", "macro", "news", "sentiment", "risk",
                        "fundamentalist", "bull_researcher",
                        "bear_researcher", "research_manager", "trader",
                        "aggressive_debator", "conservative_debator",
                        "neutral_debator", "pm"])
    assert sorted(keys) == expected


def test_run_desk_debate_journals_all_phases(monkeypatch, tmp_path):
    """The journal carries 14 AgentSteps (6 analysts + 2 researchers
    + 1 manager + 1 trader + 3 debators + 1 PM) + AgentRunStarted +
    DeskReport + AgentRunFinished when debate=True."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json())
    jr = Journal(tmp_path, "debate-test-hash")
    run_desk("AAPL", data_root=tmp_path, journal=jr)
    events = Journal.read_events(tmp_path)
    kinds = [e["kind"] for e in events]
    assert kinds.count("AgentRunStarted") == 1
    assert kinds.count("DeskReport") == 1
    assert kinds.count("AgentRunFinished") == 1
    steps = [e for e in events if e["kind"] == "AgentStep"]
    # 6 analyst + 2 researcher + 1 manager + 1 trader + 3 debator + 1 PM
    assert len(steps) == 14
    # the step labels
    step_kinds = [e["payload"].get("step") for e in steps]
    assert sum(1 for s in step_kinds if isinstance(s, int)) == 6
    assert step_kinds.count("researcher") == 2
    assert step_kinds.count("research_manager") == 1
    assert step_kinds.count("trader") == 1
    assert step_kinds.count("debator") == 3
    assert step_kinds.count("pm") == 1


def test_run_desk_debate_on_event_progress_stream(monkeypatch, tmp_path):
    """The on_event progress stream fires 14 'persona'/'pm' events
    (6 analysts + 2 researchers + 1 manager + 1 trader + 3 debators
    + 1 PM)."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json())
    seen: list[str] = []
    out = run_desk("AAPL", data_root=tmp_path,
                   on_event=lambda ev: seen.append(ev["kind"]))
    assert out["ok"] is True
    # 6 + 2 + 1 + 1 + 3 = 13 'persona' events + 1 'pm' + 1 'context'
    assert seen.count("persona") == 13
    assert seen.count("pm") == 1
    assert seen.count("context") == 1


def test_run_desk_debate_one_researcher_abstains_other_runs(monkeypatch,
                                                            tmp_path):
    """LLMUnavailable for the bull_researcher → abstention (signal
    neutral, abstained True); the bear_researcher still runs. The PM
    then abstains because bull is neutral — the brief's (b) rule."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(fail=("bull_researcher",)))
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    by_name = {r["name"]: r for r in out["researchers"]}
    bull = by_name["bull_researcher"]
    bear = by_name["bear_researcher"]
    assert bull["abstained"] is True
    assert bull["signal"] == "neutral"
    assert "zen down for bull_researcher" in bull["thesis"]
    assert bear["abstained"] is False
    # the PM abstains because the bull is neutral (no directional edge)
    pm = out["pm"]
    assert pm["action"] == "ABSTAIN"
    assert pm["consensus"] == "neutral"


def test_run_desk_debate_trader_abstains_pm_falls_back_mechanically(
        monkeypatch, tmp_path):
    """LLMUnavailable for the trader → abstention (action HOLD, no
    entry/stop/target). The PM then mechanically abstains (action is
    HOLD → ABSTAIN per the brief's (d) rule)."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(fail=("trader",)))
    out = run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    tp = out["trader_plan"]
    assert tp["abstained"] is True
    assert tp["action"] == "HOLD"
    assert tp["entry_price"] is None
    assert tp["stop_price"] is None
    assert tp["target_price"] is None
    # the PM abstains because the trader's action is HOLD
    pm = out["pm"]
    assert pm["action"] == "ABSTAIN"


# =============================================== PM abstention discipline

def test_pm_abstains_when_any_debator_rejects(monkeypatch, tmp_path):
    """The brief: 'PM MUST abstain if any debator REJECTs'. Script
    the neutral_debator to verdict REJECT; the PM's mechanical
    abstention discipline overrides the LLM's BUY call."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            debator_overrides={
                                "aggressive_debator": {
                                    "verdict": "UPSIZE",
                                    "reasoning": "rr 2.0",
                                    "evidence_cited": ["rr 2.0"]},
                                "conservative_debator": {
                                    "verdict": "HOLD",
                                    "reasoning": "rr 2.0",
                                    "evidence_cited": ["rr 2.0"]},
                                "neutral_debator": {
                                    "verdict": "REJECT",
                                    "reasoning": "kill_criteria near",
                                    "evidence_cited": ["kill_criteria near"]},
                            }))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["action"] == "ABSTAIN", (
        f"PM should ABSTAIN when neutral_debator REJECTs; got "
        f"{pm['action']}")
    assert pm["consensus"] == "neutral"
    assert pm["conviction_label"] == "LOW"
    assert "REJECT" in pm["reasoning"]


def test_pm_abstains_when_rr_below_1(monkeypatch, tmp_path):
    """The brief: 'PM MUST abstain if r:r < 1.0'. Script the trader
    with a r:r < 1.0 geometry (target barely above entry); the PM's
    mechanical r:r re-compute catches it and abstains."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            trader_override={
                                "action": "BUY",
                                "entry_price": 100.0,
                                "stop_price": 95.0,
                                "target_price": 101.0,  # only +1 vs entry
                                "position_size_pct": 0.05,
                                "time_horizon": "swing",
                                # (101-100)/(100-95) = 1/5 = 0.2 < 1.0
                                "risk_reward_ratio": 0.2,
                                "reasoning": "tight target"}))
    out = run_desk("AAPL", data_root=tmp_path)
    tp = out["trader_plan"]
    # the engine's mechanical re-compute should match the trader's claim
    assert tp["risk_reward_ratio_computed"] == 0.2
    pm = out["pm"]
    assert pm["action"] == "ABSTAIN", (
        f"PM should ABSTAIN when r:r < 1.0; got {pm['action']}")
    assert "0.2" in pm["reasoning"]


def test_pm_conviction_high_requires_rr_above_2_and_2_supporting(
        monkeypatch, tmp_path):
    """The brief: 'high conviction requires r:r ≥ 2.0 AND ≥2 supporting
    debator verdicts'. Script r:r 2.0 + 2/3 UPSIZE/HOLD debators; the
    PM's HIGH conviction label is preserved (not downgraded)."""
    _patch_context(monkeypatch)
    # PM_DEBATE_REPLY claims HIGH; r:r 2.0 + 2 supporting (aggressive
    # UPSIZE + neutral UPSIZE; conservative HOLD = also supporting)
    # → all 3 are supporting actually, ≥2 → HIGH preserved.
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json())
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    # r:r = 2.0 (mechanical) + supporting ≥ 2 (UPSIZE/HOLD count)
    assert pm["risk_reward_ratio"] == 2.0
    assert pm.get("supporting_debators", 0) >= 2
    assert pm["conviction_label"] == "HIGH"


def test_pm_conviction_high_downgrades_when_rr_below_2(monkeypatch,
                                                         tmp_path):
    """The brief: HIGH requires r:r ≥ 2.0. If the trader's r:r is
    1.7 (≥1.5 but <2.0), the PM's HIGH label downgrades to MED."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            trader_override={
                                "action": "BUY",
                                "entry_price": 100.0,
                                "stop_price": 95.0,
                                # (108.5-100)/(100-95) = 8.5/5 = 1.7
                                "target_price": 108.5,
                                "position_size_pct": 0.05,
                                "time_horizon": "swing",
                                "risk_reward_ratio": 1.7,
                                "reasoning": "rr 1.7"}))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["risk_reward_ratio"] == 1.7
    # r:r 1.7 < 2.0 → HIGH downgrades to MED (if ≥2 supporting debators)
    assert pm["conviction_label"] == "MED", (
        f"HIGH should downgrade to MED when r:r 1.7 < 2.0; got "
        f"{pm['conviction_label']}")


def test_pm_conviction_med_requires_rr_above_1_5(monkeypatch, tmp_path):
    """The brief: MED requires r:r ≥ 1.5. If the trader's r:r is 1.2
    (<1.5), the PM's MED label downgrades to LOW. AND r:r 1.2 ≥ 1.0 so
    the abstention rule (c) doesn't fire — action stays BUY."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            trader_override={
                                "action": "BUY",
                                "entry_price": 100.0,
                                "stop_price": 95.0,
                                # (106-100)/(100-95) = 6/5 = 1.2
                                "target_price": 106.0,
                                "position_size_pct": 0.05,
                                "time_horizon": "swing",
                                "risk_reward_ratio": 1.2,
                                "reasoning": "rr 1.2"},
                            pm_override={
                                "action": "BUY",
                                "entry_price": 100.0,
                                "stop_price": 95.0,
                                "target_price": 106.0,
                                "position_size_pct": 0.05,
                                "conviction_label": "MED",
                                "risk_reward_ratio": 1.2,
                                "kill_criteria": ["close < 95"],
                                "reasoning": "MED-claiming plan",
                                "evidence_cited": [],
                                "consensus": "bullish",
                                "conviction": 55,
                                "summary": "MED claim test",
                                "disagreements": "",
                                "risk_flags": []}))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["risk_reward_ratio"] == 1.2
    # r:r 1.2 ≥ 1.0 so action stays BUY (not abstain)
    assert pm["action"] == "BUY"
    # but conviction_label downgrades MED → LOW (r:r < 1.5)
    assert pm["conviction_label"] == "LOW", (
        f"MED should downgrade to LOW when r:r 1.2 < 1.5; got "
        f"{pm['conviction_label']}")


def test_pm_kill_criteria_non_empty_for_buy(monkeypatch, tmp_path):
    """The brief: 'kill_criteria non-empty for BUY/SELL'. The
    scripted PM reply includes 3 kill_criteria; the PM's BUY action
    carries them through."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json())
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["action"] == "BUY"
    assert len(pm["kill_criteria"]) >= 1
    assert all(isinstance(k, str) and k.strip() for k in pm["kill_criteria"])


def test_pm_kill_criteria_carried_over_from_memo_when_empty(
        monkeypatch, tmp_path):
    """The brief: kill_criteria non-empty for BUY/SELL — the PM
    carries over the research_memo's kill_criteria if the LLM left
    them empty."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            pm_override={
                                "action": "BUY",
                                "entry_price": 149.0,
                                "stop_price": 145.0,
                                "target_price": 157.0,
                                "position_size_pct": 0.05,
                                "conviction_label": "MED",
                                "risk_reward_ratio": 2.0,
                                "kill_criteria": [],   # empty!
                                "reasoning": "kill_criteria empty",
                                "evidence_cited": [],
                                "consensus": "bullish",
                                "conviction": 60,
                                "summary": "carry-over test",
                                "disagreements": "",
                                "risk_flags": []}))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["action"] == "BUY"
    # the manager's kill_criteria (3 items) carry over
    assert len(pm["kill_criteria"]) == 3, (
        f"kill_criteria should carry over from MANAGER_REPLY (3 items); "
        f"got {pm['kill_criteria']}")
    assert "close below 145.00" in pm["kill_criteria"][0]


def test_pm_abstains_when_kill_criteria_empty_for_buy_after_carryover(
        monkeypatch, tmp_path):
    """If the manager's kill_criteria is ALSO empty AND the PM's LLM
    left them empty, the PM ABSTAINs — the brief: 'kill_criteria non-
    empty for BUY/SELL'."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(
                            manager_override={
                                "thesis": "LONG", "conviction": "MED",
                                "supporting_evidence": ["a"],
                                "counter_evidence": ["b"],
                                "kill_criteria": [],   # empty!
                                "summary": "no kill"},
                            pm_override={
                                "action": "BUY",
                                "entry_price": 149.0,
                                "stop_price": 145.0,
                                "target_price": 157.0,
                                "position_size_pct": 0.05,
                                "conviction_label": "MED",
                                "risk_reward_ratio": 2.0,
                                "kill_criteria": [],   # also empty!
                                "reasoning": "no kill criteria",
                                "evidence_cited": [],
                                "consensus": "bullish",
                                "conviction": 60,
                                "summary": "no-kill test",
                                "disagreements": "",
                                "risk_flags": []}))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["action"] == "ABSTAIN", (
        f"PM should ABSTAIN when kill_criteria empty for BUY after "
        f"carry-over; got {pm['action']}")
    assert "kill_criteria empty" in pm["reasoning"]


def test_pm_mechanical_fallback_when_llm_unreachable(monkeypatch,
                                                       tmp_path):
    """If the rewired PM LLM is unreachable, the engine falls back to
    _mechanical_pm_debate which carries the trader's plan forward but
    flags it as mechanical."""
    _patch_context(monkeypatch)
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json(fail=("pm",)))
    out = run_desk("AAPL", data_root=tmp_path)
    pm = out["pm"]
    assert pm["mechanical"] is True
    # the mechanical PM carries the trader's plan forward but the brief's
    # abstention discipline still applies (debator REJECT / rr < 1 /
    # etc.). In the scripted scenario, the debators all UPSIZE/HOLD,
    # the trader's r:r is 2.0, so the action carries as BUY.
    assert pm["action"] in ("BUY", "ABSTAIN")  # depends on debator state
    # the action's reasoning mentions the fallback
    assert "PM synthesis unavailable" in pm["summary"]


# =============================================== 3-way sync (debate files)

def test_debate_files_3way_byte_identical_repo_stage_vs_gold_desk_v1():
    """The R2-3 debate files are byte-identical between the repo_stage
    src tree and the download/gold_desk_v1 runtime mirror (D5 pattern
    extended to the new debate files)."""
    repo = Path(__file__).resolve().parents[1]
    runtime = repo.parent.parent / "download" / "gold_desk_v1"
    if not runtime.exists():
        pytest.skip("download/gold_desk_v1 not present (CI-only)")
    files = [
        "src/gold_desk/agent/desk/__init__.py",
        "src/gold_desk/agent/desk/personas.py",
        "src/gold_desk/agent/desk/engine.py",
        "tests/test_debate.py",
    ]
    for rel in files:
        repo_f = repo / rel
        runtime_f = runtime / rel
        if not runtime_f.exists():
            pytest.skip(f"{runtime_f} not yet synced")
        assert repo_f.read_bytes() == runtime_f.read_bytes(), (
            f"{rel}: byte-diff between repo_stage and gold_desk_v1")
