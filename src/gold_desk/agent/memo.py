"""R2-5 — Institutional memo generator.

Produces a structured memo after the PM decision. The memo is the
audit-grade output that an institutional analyst would hand to a PM:
thesis, per-claim citations (every cited number links back to its
source), bull/base/bear scenarios with probabilities, risk factors,
vol-based sizing, kill criteria, conviction.

The memo is the SOURCE OF TRUTH for the downstream evidence-checker
(``evidence_checker.verify_memo``), which mechanically re-verifies
every cited number against the raw fetched artifacts. The brief's
zero-fabrication guarantee is the contract: if any cited number in
the memo doesn't match the raw artifact, the checker flags it and
the memo is marked as unverified.

Bars: institutional memo standard (Goldman/Morgan Stanley research
note structure: thesis + scenarios + sizing + kill criteria +
conviction) + deep-research verification discipline (every claim
cited, every citation machine-checkable).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Any

from ..features.verified_snapshot import extract_numeric_claims


# --------------------------------------------------------- memo schema

@dataclass
class Claim:
    """A single cited claim in the memo. The ``source`` field routes
    the evidence-checker to the right raw artifact (verified_snapshot,
    analyst_outputs, researcher_outputs, research_memo, debator_verdicts,
    trader_plan)."""
    claim: str                  # the claim text (e.g. "RSI 47.78")
    source: str                 # which artifact to verify against
    persona: str = ""           # the persona that produced the claim
    value: float | None = None  # the numeric value extracted from the claim
    kind: str = ""              # claim kind (rsi, macd_hist, atr, beta, etc.)
    verified: bool = False      # set by the evidence-checker


@dataclass
class Scenario:
    """One of bull/base/bear. Probabilities sum to 1.0 across the
    three scenarios (the memo generator enforces this)."""
    label: str                  # bull | base | bear
    probability: float          # 0.0-1.0
    target_price: float | None = None
    catalysts: list[str] = field(default_factory=list)


@dataclass
class Memo:
    """The institutional memo. Serialized to JSON + markdown."""
    ok: bool
    run_id: str
    symbol: str
    as_of: str
    thesis: str                 # LONG | SHORT | NEUTRAL
    action: str                 # BUY | SELL | HOLD | ABSTAIN
    entry_price: float | None
    stop_price: float | None
    target_price: float | None
    position_size_pct: float | None
    risk_reward_ratio: float | None
    conviction: str             # LOW | MED | HIGH
    per_claim_citations: list[dict]
    scenarios: dict            # {bull, base, bear} → Scenario dict
    risk_factors: list[str]
    vol_based_sizing_pct: float | None
    kill_criteria: list[str]
    summary: str

    def to_dict(self) -> dict:
        return asdict(self)

    def to_markdown(self) -> str:
        """Render the memo as an institutional-style research note."""
        lines = [
            f"# {self.symbol} — Memo ({self.as_of})",
            "",
            f"**Thesis**: {self.thesis}  ",
            f"**Action**: {self.action}  ",
            f"**Conviction**: {self.conviction}  ",
            f"**Entry**: {self.entry_price}  ",
            f"**Stop**: {self.stop_price}  ",
            f"**Target**: {self.target_price}  ",
            f"**Size**: {self.position_size_pct}  ",
            f"**R:R**: {self.risk_reward_ratio}  ",
            f"**Vol-based size**: {self.vol_based_sizing_pct}  ",
            "",
            "## Scenarios",
            "",
        ]
        for label in ("bull", "base", "bear"):
            sc = self.scenarios.get(label) or {}
            prob = sc.get("probability")
            tgt = sc.get("target_price")
            cats = sc.get("catalysts") or []
            lines.append(f"- **{label.upper()}** (p={prob}): target={tgt}; "
                         f"catalysts={', '.join(cats) if cats else 'n/a'}")
        lines += [
            "",
            "## Per-claim citations",
            "",
        ]
        for c in self.per_claim_citations:
            lines.append(f"- \"{c.get('claim')}\" — source={c.get('source')}, "
                         f"persona={c.get('persona')}, "
                         f"value={c.get('value')}, "
                         f"verified={c.get('verified')}")
        lines += [
            "",
            "## Risk factors",
            "",
        ]
        for r in self.risk_factors:
            lines.append(f"- {r}")
        lines += [
            "",
            "## Kill criteria",
            "",
        ]
        for k in self.kill_criteria:
            lines.append(f"- {k}")
        lines += [
            "",
            "## Summary",
            "",
            self.summary,
            "",
        ]
        return "\n".join(lines)


# --------------------------------------------------------- generator

def generate_memo(
    pm_decision: dict,
    run_id: str,
    symbol: str,
    as_of: str,
    verified_snapshot: dict | None,
    trader_plan: dict | None,
    research_memo: dict | None,
    personas_out: list[dict] | None,
    researchers_out: list[dict] | None,
    debators_out: list[dict] | None,
) -> Memo:
    """Generate the institutional memo from the PM decision + the
    raw artifacts. Pure-function: no LLM call, no I/O. The memo is
    a deterministic projection of the PM's output + the raw artifacts.

    The per_claim_citations list is built from the PM's evidence_cited
    list (each entry has {persona, claim, source}). The evidence-
    checker will re-verify each cited number against the matching
    source artifact.

    Scenarios (bull/base/bear) are mechanically derived from the
    PM's action + conviction + entry/stop/target:
      - bull scenario = target_price (probability weighted by conviction)
      - base scenario = entry_price ± 1 ATR (probability highest)
      - bear scenario = stop_price (probability weighted by conviction)
    Probabilities sum to 1.0 (the brief: "scenarios w/ probabilities").

    Vol-based sizing uses ATR% from the verified_snapshot:
      - low-vol regime (atr_pct < 1.5): position_size_pct * 1.2
      - mid-vol regime (1.5 <= atr_pct < 3.0): position_size_pct * 1.0
      - high-vol regime (atr_pct >= 3.0): position_size_pct * 0.7
    This is the brief's "vol-based sizing" — the PM's nominal size is
    adjusted by the volatility regime so high-vol names take smaller
    positions for the same dollar risk.
    """
    snap = verified_snapshot or {}
    action = str(pm_decision.get("action", "")).upper()
    conviction = str(pm_decision.get("conviction_label", "LOW")).upper()
    entry = pm_decision.get("entry_price")
    stop = pm_decision.get("stop_price")
    target = pm_decision.get("target_price")
    size = pm_decision.get("position_size_pct")
    rr = pm_decision.get("risk_reward_ratio")
    kill = list(pm_decision.get("kill_criteria") or [])

    # thesis mapping — action drives thesis; conviction labels confidence
    if action == "BUY":
        thesis = "LONG"
    elif action == "SELL":
        thesis = "SHORT"
    elif action == "HOLD":
        thesis = "NEUTRAL"
    else:
        thesis = "NEUTRAL"

    # per-claim citations — from PM's evidence_cited (already structured)
    raw_citations = list(pm_decision.get("evidence_cited") or [])
    per_claim_citations: list[dict] = []
    for c in raw_citations:
        if not isinstance(c, dict):
            continue
        claim_text = str(c.get("claim", ""))
        source = str(c.get("source", ""))
        persona = str(c.get("persona", ""))
        # extract numeric claims from the claim text (so the
        # evidence-checker can match them against the source)
        numeric_claims = extract_numeric_claims(claim_text)
        for nc in numeric_claims:
            per_claim_citations.append({
                "claim": claim_text,
                "source": source,
                "persona": persona,
                "value": nc.get("value"),
                "kind": nc.get("kind", ""),
                "verified": False,  # set by the evidence-checker
            })
        # also include the citation even if no numeric claim was extracted
        # (the citation is still in the memo; the checker just has nothing
        # to verify numerically)
        if not numeric_claims:
            per_claim_citations.append({
                "claim": claim_text,
                "source": source,
                "persona": persona,
                "value": None,
                "kind": "",
                "verified": False,
            })

    # scenarios — mechanically derived from action + conviction + prices
    # conviction weight: HIGH=0.30/0.50/0.20, MED=0.25/0.50/0.25,
    # LOW=0.20/0.50/0.30 (lower conviction → more bear weight)
    conv_weights = {
        "HIGH": (0.30, 0.50, 0.20),
        "MED":  (0.25, 0.50, 0.25),
        "LOW":  (0.20, 0.50, 0.30),
    }
    bull_w, base_w, bear_w = conv_weights.get(conviction, (0.25, 0.50, 0.25))
    # if action is ABSTAIN, the probabilities skew to base (no edge)
    if action == "ABSTAIN":
        bull_w, base_w, bear_w = 0.20, 0.60, 0.20
    # if action is SELL, flip bull/bear (bear scenario is the "win" side)
    if action == "SELL":
        bull_w, bear_w = bear_w, bull_w

    atr_pct = snap.get("atr_pct") if snap.get("ok") else None
    # base target = entry ± 1 ATR (the "mean reversion" path)
    base_target = None
    if entry is not None and atr_pct is not None:
        atr_abs = entry * (atr_pct / 100.0)
        if action == "SELL":
            base_target = entry - atr_abs
        else:
            base_target = entry + atr_abs

    scenarios = {
        "bull": {
            "label": "bull",
            "probability": round(bull_w, 4),
            "target_price": target if action != "SELL" else stop,
            "catalysts": _extract_catalysts(research_memo, "bull"),
        },
        "base": {
            "label": "base",
            "probability": round(base_w, 4),
            "target_price": base_target,
            "catalysts": _extract_catalysts(research_memo, "base"),
        },
        "bear": {
            "label": "bear",
            "probability": round(bear_w, 4),
            "target_price": stop if action != "SELL" else target,
            "catalysts": _extract_catalysts(research_memo, "bear"),
        },
    }

    # vol-based sizing — adjust the PM's nominal size by ATR% regime
    vol_based_size = None
    if size is not None and atr_pct is not None:
        if atr_pct < 1.5:
            vol_based_size = round(size * 1.2, 6)
        elif atr_pct < 3.0:
            vol_based_size = round(size * 1.0, 6)
        else:
            vol_based_size = round(size * 0.7, 6)

    # risk factors — pulled from the risk persona (analyst layer) +
    # debator REJECT/HOLD verdicts + the verified_snapshot regime
    risk_factors: list[str] = []
    for p in (personas_out or []):
        if p.get("name") == "risk":
            rf = p.get("risk_flags") or p.get("key_evidence") or []
            for r in rf:
                risk_factors.append(str(r))
    for d in (debators_out or []):
        v = str(d.get("verdict", "")).upper()
        if v in ("REJECT", "DOWNSIZE"):
            risk_factors.append(
                f"{d.get('name')} verdict={v}: {d.get('reasoning', '')}")
    regime = (snap.get("regime_labels") or {}) if snap.get("ok") else {}
    vol_regime = regime.get("vol_regime")
    if vol_regime:
        risk_factors.append(f"vol regime={vol_regime}")

    # summary — plain-English 2-3 sentences synthesizing the decision
    summary = _build_summary(action, conviction, thesis, entry, stop,
                             target, rr, kill, vol_based_size, atr_pct)

    return Memo(
        ok=True,
        run_id=str(run_id),
        symbol=str(symbol),
        as_of=str(as_of),
        thesis=thesis,
        action=action,
        entry_price=entry,
        stop_price=stop,
        target_price=target,
        position_size_pct=size,
        risk_reward_ratio=rr,
        conviction=conviction,
        per_claim_citations=per_claim_citations,
        scenarios=scenarios,
        risk_factors=risk_factors,
        vol_based_sizing_pct=vol_based_size,
        kill_criteria=kill,
        summary=summary,
    )


# --------------------------------------------------------- helpers

def _extract_catalysts(research_memo: dict | None, side: str) -> list[str]:
    """Pull catalysts from the research_memo's supporting_evidence
    (bull side) or counter_evidence (bear side). Base case uses
    the kill_criteria (the "what would invalidate the base case" list)."""
    if not research_memo:
        return []
    if side == "bull":
        return list(research_memo.get("supporting_evidence") or [])[:3]
    if side == "bear":
        return list(research_memo.get("counter_evidence") or [])[:3]
    # base — the kill criteria are the "what breaks the base case"
    return list(research_memo.get("kill_criteria") or [])[:3]


def _build_summary(action: str, conviction: str, thesis: str,
                   entry, stop, target, rr, kill, vol_size,
                   atr_pct) -> str:
    """Plain-English summary. The brief: institutional memo standard
    requires a thesis paragraph at the top of the note. 2-3 sentences
    synthesizing the decision."""
    if action == "ABSTAIN":
        return (f"{thesis} — desk ABSTAINS. The 6-phase debate did not "
                f"produce an actionable edge (bull+bear disagreed, or "
                f"r:r below threshold, or a debator rejected). Conviction "
                f"{conviction}; no entry/stop/target. Review the "
                f"transcript for the specific abstention reason.")
    parts = [f"{thesis} — desk {action} with {conviction} conviction."]
    if entry is not None and stop is not None and target is not None:
        parts.append(f"Entry {entry}, stop {stop}, target {target} "
                     f"(R:R {rr}).")
    if vol_size is not None and atr_pct is not None:
        parts.append(f"Vol-based size {vol_size*100:.1f}% (ATR% "
                     f"{atr_pct:.2f}).")
    if kill:
        parts.append(f"Kill criteria: {'; '.join(kill[:3])}.")
    return " ".join(parts)
