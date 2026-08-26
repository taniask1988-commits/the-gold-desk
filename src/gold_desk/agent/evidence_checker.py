"""R2-5 — Mechanical evidence-checker.

Re-verifies EVERY cited number in the memo against the raw fetched
artifacts. Zero-fabrication guarantee: if any cited number in the
memo doesn't match its source artifact, the checker flags it and
the memo is marked as unverified.

This is the brief's "mechanical evidence-checker that re-verifies
EVERY cited number against the raw fetched artifacts (zero-
fabrication guarantee, machine-checked)" — the institutional-grade
audit step that an analyst would do by hand, but machine-checked.

Bars: deep-research verification discipline (every claim cited, every
citation machine-checkable, zero-fabrication guarantee).

The checker is pure-function: no LLM call, no I/O. It takes the
memo + the raw artifacts (verified_snapshot, personas_out,
researchers_out, research_memo, debators_out, trader_plan) and
returns a verification_report dict.
"""

from __future__ import annotations

from typing import Any

from ..features.verified_snapshot import extract_numeric_claims


# tolerance for numeric comparison (the verified_snapshot rounds to
# 4 decimal places; PM citations are typically 2-4 decimal places;
# 0.5% tolerance handles rounding without false-flagging honest claims)
_TOL_PCT = 0.5
_TOL_ABS = 0.05  # for small absolute values (e.g. MACD hist -0.15117)


def verify_memo(
    memo: dict,
    verified_snapshot: dict | None = None,
    personas_out: list[dict] | None = None,
    researchers_out: list[dict] | None = None,
    research_memo: dict | None = None,
    debators_out: list[dict] | None = None,
    trader_plan: dict | None = None,
) -> dict:
    """Re-verify every cited number in the memo against the raw
    artifacts. Returns a verification_report dict.

    The memo's ``per_claim_citations`` list has entries like:
        {claim, source, persona, value, kind, verified}
    For each entry with a non-None ``value``:
      1. Route by ``source`` to the matching raw artifact.
      2. Find the matching field in that artifact by ``kind`` (the
         extract_numeric_claims kind: rsi, macd_hist, atr, beta, etc.)
         OR by persona name (for researcher/debator outputs).
      3. Compare the cited value against the artifact's value within
         tolerance (0.5% relative OR 0.05 absolute).
      4. Mark the citation's ``verified`` field True/False.

    Returns:
        {
            "ok": True,
            "claims_checked": int,        # citations with a numeric value
            "claims_verified": int,       # citations that matched
            "claims_failed": list[dict],  # the mismatches (claim, expected, actual, source)
            "zero_fabrication_guarantee": bool,  # True iff claims_failed is empty
            "verified_citations": list[dict],     # the verified memo citations (with verified flag set)
        }
    """
    snap = verified_snapshot or {}
    personas = personas_out or []
    researchers = researchers_out or []
    memo_raw = research_memo or {}
    debators = debators_out or []
    trader = trader_plan or {}

    citations = list(memo.get("per_claim_citations") or [])
    verified_citations: list[dict] = []
    failed: list[dict] = []
    checked = 0
    verified = 0

    for c in citations:
        c_out = dict(c)
        value = c.get("value")
        kind = c.get("kind", "")
        source = c.get("source", "")
        persona = c.get("persona", "")
        if value is None:
            # no numeric claim — can't verify; mark as not-checked
            c_out["verified"] = False
            c_out["verify_reason"] = "no numeric value in claim"
            verified_citations.append(c_out)
            continue
        checked += 1
        # find the matching value in the source artifact
        expected = _lookup_value(source, kind, persona, snap, personas,
                                  researchers, memo_raw, debators, trader)
        if expected is None:
            # couldn't find the source field — can't verify
            c_out["verified"] = False
            c_out["verify_reason"] = (f"source {source!r} kind {kind!r} "
                                      "not found in raw artifacts")
            failed.append({
                "claim": c.get("claim"),
                "cited_value": value,
                "expected": None,
                "actual": None,
                "source": source,
                "persona": persona,
                "reason": "source field not found",
            })
            verified_citations.append(c_out)
            continue
        # compare within tolerance
        if _matches(value, expected):
            c_out["verified"] = True
            c_out["verify_reason"] = "verified"
            verified += 1
        else:
            c_out["verified"] = False
            c_out["verify_reason"] = (f"mismatch: cited {value} vs "
                                      f"actual {expected}")
            failed.append({
                "claim": c.get("claim"),
                "cited_value": value,
                "expected": expected,
                "actual": value,
                "source": source,
                "persona": persona,
                "reason": f"value mismatch ({value} vs {expected})",
            })
        verified_citations.append(c_out)

    return {
        "ok": True,
        "claims_checked": checked,
        "claims_verified": verified,
        "claims_failed": failed,
        "zero_fabrication_guarantee": len(failed) == 0,
        "verified_citations": verified_citations,
    }


# --------------------------------------------------------- helpers

def _lookup_value(
    source: str,
    kind: str,
    persona: str,
    snap: dict,
    personas: list[dict],
    researchers: list[dict],
    memo_raw: dict,
    debators: list[dict],
    trader: dict,
) -> float | None:
    """Route by ``source`` to the matching raw artifact and find the
    value for ``kind`` (or persona name). Returns the expected value
    or None if not found.

    Source routes:
      - "verified_snapshot" / "snapshot" / "verified_snapshot_headline"
        → look up the field by kind in the snapshot dict
      - "researcher_outputs" → look up the persona (bull/bear_researcher)
        and extract the matching numeric claim from their thesis
      - "research_memo" → look up the field by kind in the memo
      - "debator_verdicts" → look up the debator by persona name and
        extract the matching numeric claim from their reasoning
      - "trader_plan" / "trader" → look up entry/stop/target/size/r:r
      - "analyst_outputs" → look up the persona and extract the
        matching numeric claim from their thesis
    """
    src = source.lower().strip()
    if src in ("verified_snapshot", "snapshot",
               "verified_snapshot_headline"):
        return _lookup_snapshot_field(snap, kind)
    if src == "researcher_outputs":
        return _lookup_persona_claim(researchers, persona, kind)
    if src == "research_memo":
        return _lookup_memo_field(memo_raw, kind)
    if src == "debator_verdicts":
        return _lookup_persona_claim(debators, persona, kind)
    if src in ("trader_plan", "trader"):
        return _lookup_trader_field(trader, kind)
    if src == "analyst_outputs":
        return _lookup_persona_claim(personas, persona, kind)
    return None


def _lookup_snapshot_field(snap: dict, kind: str) -> float | None:
    """Map the extract_numeric_claims ``kind`` to the verified_snapshot
    field name, then return the snapshot's value."""
    if not snap or not snap.get("ok"):
        return None
    kind_to_field = {
        "rsi": "rsi14",
        "rsi14": "rsi14",
        "macd_hist": "macd_hist",
        "macd": "macd_hist",
        "atr": "atr14_value",
        "atr14": "atr14_value",
        "atr_pct": "atr_pct",
        "bb_pct_b": "bb_pct_b",
        "pct_b": "bb_pct_b",
        "realized_vol": "realized_vol_20d",
        "realized_vol_20d": "realized_vol_20d",
        "price": "last_close",
        "last_close": "last_close",
        "beta": "benchmark_beta",
        "benchmark_beta": "benchmark_beta",
        "change_pct_5d": "change_pct_5d",
        "change_pct_20d": "change_pct_20d",
        "change_pct_63d": "change_pct_63d",
        "pct_5d": "change_pct_5d",
        "pct_20d": "change_pct_20d",
        "pct_63d": "change_pct_63d",
    }
    field = kind_to_field.get(kind.lower())
    if not field:
        return None
    val = snap.get(field)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def _lookup_persona_claim(personas: list[dict], persona_name: str,
                          kind: str) -> float | None:
    """Find the persona by name in the list, extract numeric claims
    from their thesis, return the value matching ``kind``."""
    for p in personas:
        if str(p.get("name", "")).lower() != persona_name.lower():
            continue
        thesis = str(p.get("thesis", ""))
        claims = extract_numeric_claims(thesis)
        for c in claims:
            if c.get("kind", "").lower() == kind.lower():
                v = c.get("value")
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    return float(v)
        return None
    return None


def _lookup_memo_field(memo: dict, kind: str) -> float | None:
    """The research_memo has fields like thesis, conviction,
    supporting_evidence, counter_evidence, kill_criteria. The
    numeric claims a PM might cite from the memo are limited —
    usually the conviction value or a numeric in the thesis."""
    # the memo doesn't have structured numeric fields by kind;
    # the PM rarely cites numeric values FROM the memo (the memo is
    # qualitative synthesis). Return None to flag "not found".
    return None


def _lookup_trader_field(trader: dict, kind: str) -> float | None:
    """Map kind to the trader_plan field."""
    kind_to_field = {
        "entry": "entry_price",
        "entry_price": "entry_price",
        "stop": "stop_price",
        "stop_price": "stop_price",
        "target": "target_price",
        "target_price": "target_price",
        "size": "position_size_pct",
        "position_size_pct": "position_size_pct",
        "rr": "risk_reward_ratio",
        "risk_reward_ratio": "risk_reward_ratio",
    }
    field = kind_to_field.get(kind.lower())
    if not field:
        return None
    val = trader.get(field)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def _matches(cited: float, expected: float) -> bool:
    """Compare within tolerance: 0.5% relative OR 0.05 absolute."""
    if expected == 0:
        return abs(cited) < _TOL_ABS
    rel = abs(cited - expected) / abs(expected) * 100.0
    return rel <= _TOL_PCT or abs(cited - expected) <= _TOL_ABS
