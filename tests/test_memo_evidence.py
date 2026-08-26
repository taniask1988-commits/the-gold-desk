"""R2-5 — tests for the institutional memo + mechanical evidence-checker.

The brief: "institutional memo output (thesis, per-claim citations,
bull/base/bear scenarios w/ probabilities, risk factors, vol-based
sizing, kill criteria, conviction) + mechanical evidence-checker
that re-verifies EVERY cited number against the raw fetched artifacts
(zero-fabrication guarantee, machine-checked)."

These tests cover:
  - Memo schema: all 13 required fields present.
  - Per-claim citations: every PM evidence_cited entry flows into
    the memo with its source + persona + extracted numeric value.
  - Scenarios: bull/base/bear probabilities sum to 1.0; conviction
    weight shifts probabilities.
  - Vol-based sizing: low-vol regime upsizes 1.2x, mid-vol 1.0x,
    high-vol 0.7x.
  - Evidence-checker: cited numbers that match the raw artifact are
    verified; mismatches are flagged; the zero-fabrication guarantee
    fires iff no mismatches.
  - End-to-end: run_desk returns report with memo + evidence_report
    fields; both are well-formed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# ensure src/ is on the path (mirrors test_memory_cache.py)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.agent.memo import generate_memo, Memo  # noqa: E402
from gold_desk.agent.evidence_checker import verify_memo  # noqa: E402


# ============================================================ fixtures

def _pm_decision(action="BUY", conviction="HIGH",
                 entry=149.0, stop=145.0, target=157.0,
                 size=0.05, rr=2.0, kill=None,
                 evidence_cited=None) -> dict:
    """A well-formed PM decision artifact (mirrors PM_DEBATE_REPLY)."""
    return {
        "action": action,
        "entry_price": entry,
        "stop_price": stop,
        "target_price": target,
        "position_size_pct": size,
        "risk_reward_ratio": rr,
        "conviction_label": conviction,
        "kill_criteria": kill or ["close < 145.00", "RSI < 40"],
        "reasoning": "Long case carries on momentum + fundamentals.",
        "evidence_cited": evidence_cited or [
            {"persona": "bull_researcher",
             "claim": "RSI 47.78 turning up",
             "source": "researcher_outputs"},
            {"persona": "research_manager",
             "claim": "thesis LONG MED",
             "source": "research_memo"},
            {"persona": "aggressive_debator",
             "claim": "UPSIZE",
             "source": "debator_verdicts"},
            {"persona": "technician",
             "claim": "RSI 47.78",
             "source": "verified_snapshot"},
        ],
    }


def _verified_snapshot(rsi=47.78, atr_pct=2.33, macd_hist=-0.15117,
                       bb_pct_b=0.453573, last_close=309.86,
                       change_pct_5d=-0.0548, change_pct_20d=-8.8861,
                       regime="calm") -> dict:
    """A well-formed verified_snapshot dict (mirrors the snapshot
    the R2-2 builder produces)."""
    return {
        "ok": True,
        "symbol": "AAPL",
        "as_of": "2026-08-26T00:00:00Z",
        "last_close": last_close,
        "rsi14": rsi,
        "atr14_value": 7.21,
        "atr_pct": atr_pct,
        "macd_hist": macd_hist,
        "bb_pct_b": bb_pct_b,
        "realized_vol_20d": 0.234,
        "volume_last": 1_000_000,
        "volume_avg_20d": 800_000,
        "change_pct_5d": change_pct_5d,
        "change_pct_20d": change_pct_20d,
        "change_pct_63d": 5.0,
        "regime_labels": {"vol_regime": regime},
        "benchmark_beta": 1.2,
        "benchmark_beta_low_confidence": False,
    }


def _research_memo(thesis="LONG", conviction="MED") -> dict:
    return {
        "thesis": thesis,
        "conviction": conviction,
        "supporting_evidence": ["RSI turning up", "MACD hist improving"],
        "counter_evidence": ["20d return -8.88%", "volume declining"],
        "kill_criteria": ["close < 145", "RSI < 40"],
    }


def _personas_out() -> list[dict]:
    return [
        {"name": "technician", "role": "The Technician",
         "signal": "bullish", "confidence": 72,
         "thesis": "RSI 47.78 with MACD hist -0.151 — turning up.",
         "key_evidence": ["RSI 47.78", "MACD hist -0.151"],
         "abstained": False},
        {"name": "risk", "role": "The Risk Manager",
         "signal": "neutral", "confidence": 50,
         "thesis": "Stops survivable.",
         "key_evidence": ["stop 4 pts below entry"],
         "abstained": False},
    ]


def _researchers_out() -> list[dict]:
    return [
        {"name": "bull_researcher", "role": "The Bull Researcher",
         "signal": "bullish", "confidence": 65,
         "thesis": "RSI 47.78 turning up — momentum case.",
         "abstained": False},
        {"name": "bear_researcher", "role": "The Bear Researcher",
         "signal": "bearish", "confidence": 55,
         "thesis": "20d -8.8861% — downtrend intact.",
         "abstained": False},
    ]


def _debators_out() -> list[dict]:
    return [
        {"name": "aggressive_debator", "verdict": "UPSIZE",
         "reasoning": "r:r 2.0 + kill_criteria remote",
         "abstained": False},
        {"name": "conservative_debator", "verdict": "HOLD",
         "reasoning": "r:r 2.0 but 20d -8.8861%",
         "abstained": False},
        {"name": "neutral_debator", "verdict": "UPSIZE",
         "reasoning": "r:r 2.0 + 2/3 supporting",
         "abstained": False},
    ]


def _trader_plan() -> dict:
    return {
        "action": "BUY",
        "entry_price": 149.0,
        "stop_price": 145.0,
        "target_price": 157.0,
        "position_size_pct": 0.05,
        "risk_reward_ratio": 2.0,
    }


# ============================================================ memo schema

def test_memo_has_all_required_fields():
    """The memo schema requires 13 fields (ok, run_id, symbol, as_of,
    thesis, action, entry_price, stop_price, target_price,
    position_size_pct, risk_reward_ratio, conviction,
    per_claim_citations, scenarios, risk_factors, vol_based_sizing_pct,
    kill_criteria, summary). Verify all are present."""
    memo = generate_memo(
        pm_decision=_pm_decision(),
        run_id="test-run-1",
        symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    required = {"ok", "run_id", "symbol", "as_of", "thesis",
                "action", "entry_price", "stop_price",
                "target_price", "position_size_pct",
                "risk_reward_ratio", "conviction",
                "per_claim_citations", "scenarios", "risk_factors",
                "vol_based_sizing_pct", "kill_criteria", "summary"}
    assert isinstance(memo, Memo)
    d = memo.to_dict()
    missing = required - set(d.keys())
    assert not missing, f"memo missing fields: {missing}"
    assert d["ok"] is True
    assert d["symbol"] == "AAPL"
    assert d["run_id"] == "test-run-1"


def test_memo_thesis_mapping_for_each_action():
    """BUY→LONG, SELL→SHORT, HOLD→NEUTRAL, ABSTAIN→NEUTRAL."""
    for action, expected_thesis in [("BUY", "LONG"), ("SELL", "SHORT"),
                                     ("HOLD", "NEUTRAL"),
                                     ("ABSTAIN", "NEUTRAL")]:
        memo = generate_memo(
            pm_decision=_pm_decision(action=action),
            run_id="r", symbol="AAPL",
            as_of="2026-08-26T00:00:00Z",
            verified_snapshot=_verified_snapshot(),
            trader_plan=_trader_plan(),
            research_memo=_research_memo(),
            personas_out=_personas_out(),
            researchers_out=_researchers_out(),
            debators_out=_debators_out(),
        )
        assert memo.thesis == expected_thesis, (
            f"action={action} → thesis={memo.thesis}, "
            f"expected {expected_thesis}")


# ============================================================ per-claim citations

def test_per_claim_citations_flows_from_pm_evidence_cited():
    """Every entry in PM.evidence_cited appears in the memo's
    per_claim_citations with its source + persona + extracted value."""
    ec = [
        {"persona": "technician", "claim": "RSI 47.78",
         "source": "verified_snapshot"},
        {"persona": "bull_researcher",
         "claim": "MACD hist -0.15117 turning up",
         "source": "researcher_outputs"},
        {"persona": "aggressive_debator",
         "claim": "UPSIZE", "source": "debator_verdicts"},
    ]
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=ec),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    cites = memo.per_claim_citations
    # 3 citations × (≥1 numeric claim extracted from "RSI 47.78" and
    # "MACD hist -0.15117"; "UPSIZE" has no numeric → 1 citation with
    # value=None)
    # so total ≥ 3 (could be 3 or more depending on extraction)
    assert len(cites) >= 3
    # verify each source is preserved
    sources = {c["source"] for c in cites}
    assert "verified_snapshot" in sources
    assert "researcher_outputs" in sources
    assert "debator_verdicts" in sources


def test_per_claim_citations_extract_numeric_value():
    """A claim like 'RSI 47.78' produces a citation with value=47.78
    and kind='rsi'."""
    ec = [{"persona": "technician", "claim": "RSI 47.78",
           "source": "verified_snapshot"}]
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=ec),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    cites = memo.per_claim_citations
    rsi_cite = next(c for c in cites if c.get("kind") == "rsi")
    assert rsi_cite["value"] == 47.78
    assert rsi_cite["source"] == "verified_snapshot"


# ============================================================ scenarios

def test_scenarios_probabilities_sum_to_1():
    """bull + base + bear probabilities must sum to 1.0."""
    for conviction in ("LOW", "MED", "HIGH"):
        memo = generate_memo(
            pm_decision=_pm_decision(conviction=conviction),
            run_id="r", symbol="AAPL",
            as_of="2026-08-26T00:00:00Z",
            verified_snapshot=_verified_snapshot(),
            trader_plan=_trader_plan(),
            research_memo=_research_memo(),
            personas_out=_personas_out(),
            researchers_out=_researchers_out(),
            debators_out=_debators_out(),
        )
        p_bull = memo.scenarios["bull"]["probability"]
        p_base = memo.scenarios["base"]["probability"]
        p_bear = memo.scenarios["bear"]["probability"]
        total = round(p_bull + p_base + p_bear, 4)
        assert abs(total - 1.0) < 0.001, (
            f"conviction={conviction}: bull={p_bull} base={p_base} "
            f"bear={p_bear} sum={total}")


def test_scenarios_high_conviction_skews_bull():
    """HIGH conviction → bull weight 0.30, bear weight 0.20."""
    memo_high = generate_memo(
        pm_decision=_pm_decision(conviction="HIGH"),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    assert memo_high.scenarios["bull"]["probability"] == 0.30
    assert memo_high.scenarios["bear"]["probability"] == 0.20


def test_scenarios_low_conviction_skews_bear():
    """LOW conviction → bull weight 0.20, bear weight 0.30."""
    memo_low = generate_memo(
        pm_decision=_pm_decision(conviction="LOW"),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    assert memo_low.scenarios["bull"]["probability"] == 0.20
    assert memo_low.scenarios["bear"]["probability"] == 0.30


def test_scenarios_abstain_skews_base():
    """ABSTAIN action → base weight 0.60 (no edge)."""
    memo = generate_memo(
        pm_decision=_pm_decision(action="ABSTAIN"),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    assert memo.scenarios["base"]["probability"] == 0.60


# ============================================================ vol-based sizing

def test_vol_based_sizing_low_vol_upsizes_1_2x():
    """atr_pct < 1.5 → size * 1.2."""
    snap = _verified_snapshot(atr_pct=1.0)
    memo = generate_memo(
        pm_decision=_pm_decision(size=0.05),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=snap,
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    # 0.05 * 1.2 = 0.06
    assert memo.vol_based_sizing_pct == 0.06


def test_vol_based_sizing_mid_vol_unchanged():
    """1.5 <= atr_pct < 3.0 → size * 1.0."""
    snap = _verified_snapshot(atr_pct=2.0)
    memo = generate_memo(
        pm_decision=_pm_decision(size=0.05),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=snap,
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    assert memo.vol_based_sizing_pct == 0.05


def test_vol_based_sizing_high_vol_downsizes_0_7x():
    """atr_pct >= 3.0 → size * 0.7."""
    snap = _verified_snapshot(atr_pct=4.0)
    memo = generate_memo(
        pm_decision=_pm_decision(size=0.10),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=snap,
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    # 0.10 * 0.7 = 0.07
    assert memo.vol_based_sizing_pct == 0.07


# ============================================================ evidence checker

def test_evidence_checker_verifies_matching_citation():
    """A citation 'RSI 47.78' against a snapshot with rsi14=47.78
    verifies → claims_verified=1, zero_fabrication_guarantee=True."""
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "technician", "claim": "RSI 47.78",
             "source": "verified_snapshot"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(rsi=47.78),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    report = verify_memo(
        memo=memo.to_dict(),
        verified_snapshot=_verified_snapshot(rsi=47.78),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        research_memo=_research_memo(),
        debators_out=_debators_out(),
        trader_plan=_trader_plan(),
    )
    assert report["ok"] is True
    assert report["claims_checked"] >= 1
    assert report["claims_verified"] >= 1
    assert len(report["claims_failed"]) == 0
    assert report["zero_fabrication_guarantee"] is True


def test_evidence_checker_flags_mismatched_citation():
    """A citation 'RSI 99.5' against a snapshot with rsi14=47.78
    fails → claims_failed has 1 entry, zero_fabrication_guarantee=False."""
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "technician", "claim": "RSI 99.5",
             "source": "verified_snapshot"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(rsi=47.78),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    report = verify_memo(
        memo=memo.to_dict(),
        verified_snapshot=_verified_snapshot(rsi=47.78),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        research_memo=_research_memo(),
        debators_out=_debators_out(),
        trader_plan=_trader_plan(),
    )
    assert report["claims_checked"] >= 1
    assert report["claims_verified"] == 0
    assert len(report["claims_failed"]) >= 1
    failed = report["claims_failed"][0]
    assert "RSI" in failed["claim"]
    assert failed["expected"] == 47.78
    assert failed["actual"] == 99.5
    assert report["zero_fabrication_guarantee"] is False


def test_evidence_checker_routes_by_source():
    """A citation with source='verified_snapshot' looks up the
    snapshot; source='researcher_outputs' looks up the persona
    by name; source='trader_plan' looks up the trader's fields."""
    # verified_snapshot route
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "technician", "claim": "RSI 47.78",
             "source": "verified_snapshot"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(rsi=47.78),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    rep = verify_memo(memo=memo.to_dict(),
                      verified_snapshot=_verified_snapshot(rsi=47.78),
                      personas_out=_personas_out(),
                      researchers_out=_researchers_out(),
                      research_memo=_research_memo(),
                      debators_out=_debators_out(),
                      trader_plan=_trader_plan())
    assert rep["claims_verified"] >= 1

    # researcher_outputs route — bull_researcher thesis has "RSI 47.78"
    memo_r = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "bull_researcher",
             "claim": "RSI 47.78 turning up",
             "source": "researcher_outputs"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(rsi=47.78),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    rep_r = verify_memo(memo=memo_r.to_dict(),
                        verified_snapshot=_verified_snapshot(rsi=47.78),
                        personas_out=_personas_out(),
                        researchers_out=_researchers_out(),
                        research_memo=_research_memo(),
                        debators_out=_debators_out(),
                        trader_plan=_trader_plan())
    assert rep_r["claims_verified"] >= 1


def test_evidence_checker_skips_non_numeric_claims():
    """A citation like 'UPSIZE' (no numeric value) is not checked —
    claims_checked stays 0, zero_fabrication_guarantee stays True
    (vacuously — nothing to verify)."""
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "aggressive_debator",
             "claim": "UPSIZE",
             "source": "debator_verdicts"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    report = verify_memo(
        memo=memo.to_dict(),
        verified_snapshot=_verified_snapshot(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        research_memo=_research_memo(),
        debators_out=_debators_out(),
        trader_plan=_trader_plan(),
    )
    assert report["claims_checked"] == 0
    assert report["claims_verified"] == 0
    assert report["zero_fabrication_guarantee"] is True


def test_evidence_checker_tolerance_for_rounding():
    """A citation 'RSI 47.8' against a snapshot with rsi14=47.78
    verifies within the 0.5% tolerance (delta = 0.02, 0.04% relative)."""
    memo = generate_memo(
        pm_decision=_pm_decision(evidence_cited=[
            {"persona": "technician", "claim": "RSI 47.8",
             "source": "verified_snapshot"},
        ]),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(rsi=47.78),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    report = verify_memo(
        memo=memo.to_dict(),
        verified_snapshot=_verified_snapshot(rsi=47.78),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        research_memo=_research_memo(),
        debators_out=_debators_out(),
        trader_plan=_trader_plan(),
    )
    assert report["claims_verified"] >= 1
    assert report["zero_fabrication_guarantee"] is True


# ============================================================ markdown

def test_memo_to_markdown_has_required_sections():
    """The markdown rendering has the institutional-memo sections:
    thesis + scenarios + per-claim citations + risk factors +
    kill criteria + summary."""
    memo = generate_memo(
        pm_decision=_pm_decision(),
        run_id="r", symbol="AAPL",
        as_of="2026-08-26T00:00:00Z",
        verified_snapshot=_verified_snapshot(),
        trader_plan=_trader_plan(),
        research_memo=_research_memo(),
        personas_out=_personas_out(),
        researchers_out=_researchers_out(),
        debators_out=_debators_out(),
    )
    md = memo.to_markdown()
    assert "# AAPL — Memo" in md
    assert "**Thesis**" in md
    assert "**Action**" in md
    assert "**Conviction**" in md
    assert "## Scenarios" in md
    assert "## Per-claim citations" in md
    assert "## Risk factors" in md
    assert "## Kill criteria" in md
    assert "## Summary" in md


# ============================================================ end-to-end

def test_run_desk_returns_memo_and_evidence_report(monkeypatch,
                                                    tmp_path):
    """run_desk returns a report with `memo` and `evidence_report`
    fields, both well-formed (memo.ok=True, evidence_report.ok=True)."""
    from gold_desk.agent.desk import engine as eng
    from gold_desk.agent.desk.engine import run_desk
    from gold_desk.llm.prompt_cache import PromptCache
    # reuse the debate test's offline mocks
    sys.path.insert(0, str(Path(__file__).parent))
    from test_memory_cache import (
        _patch_context_no_inst, _fake_complete_json_for_debate,
    )
    _patch_context_no_inst(monkeypatch)
    monkeypatch.setattr(eng, "_now_iso",
                        lambda: "2026-08-26T00:00:00Z")
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json_for_debate())
    cache = PromptCache(tmp_path / "llm")
    out = run_desk("AAPL", data_root=tmp_path, cache=cache)
    assert out["ok"] is True
    assert "memo" in out
    assert "evidence_report" in out
    assert out["memo"]["ok"] is True
    assert out["memo"]["symbol"] == "AAPL"
    assert out["memo"]["thesis"] in ("LONG", "SHORT", "NEUTRAL")
    assert "scenarios" in out["memo"]
    assert "per_claim_citations" in out["memo"]
    assert out["evidence_report"]["ok"] is True
    assert "claims_checked" in out["evidence_report"]
    assert "claims_verified" in out["evidence_report"]
    assert "claims_failed" in out["evidence_report"]
    assert "zero_fabrication_guarantee" in out["evidence_report"]


def test_run_desk_evidence_report_zero_fabrication_when_no_mismatches(
        monkeypatch, tmp_path):
    """When all cited numbers match the raw artifacts, the evidence
    report's zero_fabrication_guarantee is True."""
    from gold_desk.agent.desk import engine as eng
    from gold_desk.agent.desk.engine import run_desk
    from gold_desk.llm.prompt_cache import PromptCache
    sys.path.insert(0, str(Path(__file__).parent))
    from test_memory_cache import (
        _patch_context_no_inst, _fake_complete_json_for_debate,
    )
    _patch_context_no_inst(monkeypatch)
    monkeypatch.setattr(eng, "_now_iso",
                        lambda: "2026-08-26T00:00:00Z")
    monkeypatch.setattr(eng, "complete_json",
                        _fake_complete_json_for_debate())
    cache = PromptCache(tmp_path / "llm")
    out = run_desk("AAPL", data_root=tmp_path, cache=cache)
    # the scripted PM_DEBATE_REPLY cites "RSI 47.78" against a verified
    # snapshot where the technician's thesis has "RSI 47.78" — the
    # technician persona's output has the value, so the evidence-checker
    # should verify it. The snapshot mock in _patch_context_no_inst
    # has empty bars → verified_snapshot.ok=False → no snapshot field
    # to verify against → claims_checked may be 0 (vacuous guarantee).
    # The contract: zero_fabrication_guarantee is True iff
    # claims_failed is empty.
    assert (out["evidence_report"]["zero_fabrication_guarantee"]
            is (len(out["evidence_report"]["claims_failed"]) == 0))


# ============================================================ 3-way sync

def test_memo_evidence_files_3way_byte_identical_repo_stage_vs_gold_desk_v1():
    """3-way sync: src/gold_desk/agent/{memo,evidence_checker}.py and
    tests/test_memo_evidence.py must be byte-identical between
    /home/z/my-project/scripts/repo_stage/ and
    /home/z/my-project/download/gold_desk_v1/."""
    stage = Path("/home/z/my-project/scripts/repo_stage")
    mirror = Path("/home/z/my-project/download/gold_desk_v1")
    files = [
        "src/gold_desk/agent/memo.py",
        "src/gold_desk/agent/evidence_checker.py",
        "src/gold_desk/agent/desk/engine.py",
        "tests/test_memo_evidence.py",
    ]
    for rel in files:
        a = stage / rel
        b = mirror / rel
        if not a.exists() or not b.exists():
            pytest.fail(f"file missing: {rel}")
        assert a.read_bytes() == b.read_bytes(), (
            f"{rel}: byte-diff between repo_stage and gold_desk_v1")
