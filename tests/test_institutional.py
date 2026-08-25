"""R2-1 institutional data plane — offline tests.

Pins every keyless feed's parser against VCR-style fixtures captured
live (scripts/gauntlet2-research/capture_fixtures.py, 2026-08-25) so
the suite stays offline (no network, deterministic). Tests cover:

  * XBRL: 10-K + 10-Q mixed forms, dedupe same-period (10-K supersedes
    10-Q for FY), filter to last 8 quarters, sort by filed date desc;
    missing-concept graceful; non-US equity falls to Yahoo timeseries
    fallback; ok:False on total failure; CIK resolution via
    company_tickers.json (zero-pad to 10); accession preserved per period
  * 13F: parse 89-position Berkshire fixture (sum $299.3B), put/call
    type defaults SH, top10_pct math; ok:False on no-recent-filing;
    default cik = Berkshire; holdings-xml selection from index.json
    (skip primary_doc.xml)
  * Curve: parse XML → 1M-30Y dict + latest_date; fixture from
    probe_feeds3 output
  * F&G: 30-day history, classification text, value int
  * onchain: all fields parsed
  * global: dominance, total_market_cap, change_24h_pct
  * social: sub-routing by asset class; 10-item cap; fail-soft
  * gather_institutional_context: each slice fail-soft independently,
    total result still ok:True with the slices that lived
  * fundamentalist persona: checklist shape (Persona dataclass), tools
    ⊆ DESK_TOOLS, signal rules, confidence scale, L11 accession-
    citation rule, abstain-if-<2-quarters
  * engine: 6-persona context includes fundamentals/institutional
    blocks when the slice returns ok; PM base_block includes
    fundamentals headline; dead-XBRL → fail-soft slice, desk still
    runs (5-persona context preserved)
  * CLI: --json shape for each new subcommand; human-table output for
    fundamentals
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

FIXTURES = REPO / "tests" / "fixtures" / "institutional"


def _load(name: str) -> dict:
    with open(FIXTURES / f"{name}.json") as f:
        return json.load(f)


def _load_bytes(name: str) -> bytes:
    with open(FIXTURES / f"{name}.bin", "rb") as f:
        return f.read()


# =====================================================================
# XBRL fundamentals — _merge_xbrl_periods + _is_standalone_period
# =====================================================================

from gold_desk.markets.institutional import (  # noqa: E402
    _is_standalone_period, _is_ytd_period, _merge_xbrl_periods,
    _parse_13f_xml, _parse_treasury_xml, _parse_reddit_rss,
    _derive_cashflow_standalone, XBRL_CONCEPTS,
    XBRL_CONCEPT_FALLBACKS, CASH_FLOW_FIELDS, CURVE_FIELDS,
    DEFAULT_BRK_CIK, TTL_S, N_QUARTERS,
)
from gold_desk.markets import institutional  # noqa: E402


def test_is_standalone_period_filters_ytd_rows():
    """A 9-month YTD row (10-Q) is rejected; the 3-month quarter row
    is accepted."""
    q3_3m = {"form": "10-Q", "start": "2026-03-29",
             "end": "2026-06-27"}  # 90 days
    q3_9m = {"form": "10-Q", "start": "2025-09-28",
             "end": "2026-06-27"}  # 272 days — YTD
    fy_12m = {"form": "10-K", "start": "2025-09-28",
              "end": "2026-09-27"}  # 364 days
    fy_3m = {"form": "10-K", "start": "2025-12-28",
             "end": "2026-03-29"}  # 91 days — 10-K's 3-month slice
    assert _is_standalone_period(q3_3m) is True
    assert _is_standalone_period(q3_9m) is False
    assert _is_standalone_period(fy_12m) is True
    assert _is_standalone_period(fy_3m) is False


def test_is_standalone_period_rejects_malformed_rows():
    """Missing form/start/end → False."""
    assert _is_standalone_period({}) is False
    assert _is_standalone_period({"form": "10-Q"}) is False
    assert _is_standalone_period({"form": "10-Q", "start": "x"}) is False
    assert _is_standalone_period({"form": "8-K", "start": "2026-01-01",
                                  "end": "2026-04-01"}) is False


def test_merge_xbrl_periods_keeps_latest_8_quarters():
    """The AAPL fixture has 117 revenue rows covering 8+ years; the
    merge must keep only the last 8 quarters sorted by filed date desc."""
    bundle = _load("xbrl_aapl_concepts")
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) == N_QUARTERS == 8
    # sorted desc by filed date
    filed_dates = [p.get("filed") for p in periods]
    assert filed_dates == sorted(filed_dates, reverse=True)
    # latest period is the most recent AAPL filing
    latest = periods[0]
    assert latest["fy"] == 2026
    assert latest["fp"] == "Q3"
    assert latest["filed"] == "2026-07-31"
    # the standalone quarter (not the 9-month YTD) won — revenue 109.4B
    assert latest.get("revenue") == 109417000000.0


def test_merge_xbrl_periods_prefers_later_start_for_same_period():
    """When XBRL returns both the current-quarter row AND a prior-year
    comparative tagged with the FILING's (fy,fp), the merge picks the
    later-start row (current quarter)."""
    bundle = _load("xbrl_aapl_concepts")
    periods = _merge_xbrl_periods(bundle)
    # Q3 FY26 had two rows: start=2026-03-29 (current) and start=2025-03-30
    # (comparative). Merge must pick the current (revenue 109.4B, not 94B).
    q3 = [p for p in periods if p["fy"] == 2026 and p["fp"] == "Q3"][0]
    assert q3["start"] == "2026-03-29"
    assert q3["revenue"] == 109417000000.0


def test_merge_xbrl_periods_preserves_accession_per_period():
    """Each row carries the accession number for L11 audit-quality citation."""
    bundle = _load("xbrl_aapl_concepts")
    periods = _merge_xbrl_periods(bundle)
    for p in periods:
        assert p.get("accn"), f"period {p['fp']} {p['fy']} missing accession"
        assert "-" in p["accn"], f"accession {p['accn']} not dashed form"
    # the latest period's accession is the AAPL Q3 FY26 10-Q
    assert periods[0]["accn"] == "0000320193-26-000020"


def test_merge_xbrl_periods_handles_missing_concepts_gracefully():
    """A bundle missing some concepts still merges the rest."""
    bundle = _load("xbrl_aapl_concepts")
    # remove half the concepts
    keys = list(bundle.keys())
    for k in keys[:len(keys) // 2]:
        del bundle[k]
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) > 0
    # each period has at least one field set
    for p in periods:
        assert any(p.get(f) is not None
                   for f in XBRL_CONCEPTS.values())


def test_merge_xbrl_periods_empty_bundle_returns_empty():
    """An empty bundle yields no periods (the caller falls back to Yahoo)."""
    periods = _merge_xbrl_periods({})
    assert periods == []


def test_merge_xbrl_periods_filters_non_usd_units():
    """XBRL concepts can have multiple unit blocks (USD, USD/shares).
    Only USD rows make it through the merge."""
    bundle = {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {
                "USD": [{"form": "10-Q", "start": "2026-03-29",
                          "end": "2026-06-27", "fy": 2026, "fp": "Q3",
                          "filed": "2026-07-31", "accn": "acc-1",
                          "val": 100000}],
                "USD/shares": [{"form": "10-Q", "start": "2026-03-29",
                                  "end": "2026-06-27", "fy": 2026,
                                  "fp": "Q3", "filed": "2026-07-31",
                                  "accn": "acc-1", "val": 999}],
            }
        }
    }
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) == 1
    assert periods[0]["revenue"] == 100000.0


# =====================================================================
# R2-1 fix — defect 2: OCF concept-name fix + derived FCF (OCF − capex)
# =====================================================================

def test_xbrl_concepts_use_correct_ocf_concept_for_aapl():
    """The builder's original CashFlowFromOperatingActivities 404s on
    SEC for AAPL (verified live); the correct us-gaap concept AAPL
    actually files is NetCashProvidedByUsedInOperatingActivities. The
    XBRL_CONCEPTS map must map that concept to operating_cash_flow,
    NOT the broken one."""
    assert ("NetCashProvidedByUsedInOperatingActivities" in XBRL_CONCEPTS
            and XBRL_CONCEPTS["NetCashProvidedByUsedInOperatingActivities"]
            == "operating_cash_flow")
    # the broken concept must NOT be registered (would silently 404)
    assert "CashFlowFromOperatingActivities" not in XBRL_CONCEPTS


def test_xbrl_capex_concept_and_fallback_chain_registered():
    """capex is sourced from PaymentsToAcquirePropertyPlantAndEquipment
    (the standard us-gaap capex tag AAPL files — verified 200 OK live).
    XBRL_CONCEPT_FALLBACKS registers a fallback chain so a filer that
    files the older PaymentsForCapitalImprovements tag instead still
    gets capex populated via _fetch_xbrl_bundle."""
    assert XBRL_CONCEPTS.get("PaymentsToAcquirePropertyPlantAndEquipment") \
        == "capex"
    assert "capex" in XBRL_CONCEPT_FALLBACKS
    chain = XBRL_CONCEPT_FALLBACKS["capex"]
    assert chain[0] == "PaymentsToAcquirePropertyPlantAndEquipment"
    assert chain[1] == "PaymentsForCapitalImprovements"


def test_merge_xbrl_periods_derives_fcf_when_ocf_and_capex_present():
    """fcf_derived = operating_cash_flow − capex per period, computed
    in the merge's final pass when both are sourced. Labeled
    fcf_derived (not free_cash_flow) so the audit trail shows this is
    COMPUTED, not sourced from a single XBRL tag (most filers incl.
    AAPL don't file FreeCashFlow as a standalone concept)."""
    bundle = {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            {"form": "10-Q", "start": "2026-03-29",
             "end": "2026-06-27", "fy": 2026, "fp": "Q3",
             "filed": "2026-07-31", "accn": "a1", "val": 25000},
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            {"form": "10-Q", "start": "2026-03-29",
             "end": "2026-06-27", "fy": 2026, "fp": "Q3",
             "filed": "2026-07-31", "accn": "a1", "val": 5000},
        ]}},
    }
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) == 1
    assert periods[0]["operating_cash_flow"] == 25000.0
    assert periods[0]["capex"] == 5000.0
    assert periods[0]["fcf_derived"] == 20000.0  # 25000 − 5000


def test_merge_xbrl_periods_fcf_derived_none_when_capex_missing():
    """When capex is missing (concept 404'd and fallback chain all
    404'd), fcf_derived stays None — the LLM sees the gap honestly,
    never a fabricated FCF."""
    bundle = {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            {"form": "10-Q", "start": "2026-03-29",
             "end": "2026-06-27", "fy": 2026, "fp": "Q3",
             "filed": "2026-07-31", "accn": "a1", "val": 25000},
        ]}},
        # capex deliberately absent (no PaymentsToAcquire* key)
    }
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) == 1
    assert periods[0]["operating_cash_flow"] == 25000.0
    assert periods[0].get("capex") is None
    assert periods[0]["fcf_derived"] is None


def test_merge_xbrl_periods_fcf_derived_none_when_ocf_missing():
    """Symmetric: when OCF is missing, fcf_derived stays None too."""
    bundle = {
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            {"form": "10-Q", "start": "2026-03-29",
             "end": "2026-06-27", "fy": 2026, "fp": "Q3",
             "filed": "2026-07-31", "accn": "a1", "val": 5000},
        ]}},
    }
    periods = _merge_xbrl_periods(bundle)
    assert len(periods) == 1
    assert periods[0].get("operating_cash_flow") is None
    assert periods[0]["capex"] == 5000.0
    assert periods[0]["fcf_derived"] is None


def test_fetch_xbrl_bundle_capex_fallback_walks_chain(monkeypatch,
                                                       tmp_path):
    """When the primary capex concept 404s (no `units` block in the
    SEC companyconcept response — the missing-concept shape), the
    fallback chain is walked and the fallback payload is stored UNDER
    THE PRIMARY CONCEPT NAME so XBRL_CONCEPTS.get(concept) in
    _merge_xbrl_periods finds the field unchanged."""
    primary = "PaymentsToAcquirePropertyPlantAndEquipment"
    fallback = "PaymentsForCapitalImprovements"
    capex_payload = {"units": {"USD": [
        {"form": "10-Q", "start": "2026-03-29",
         "end": "2026-06-27", "fy": 2026, "fp": "Q3",
         "filed": "2026-07-31", "accn": "a1", "val": 5000},
    ]}}
    # the SEC companyconcept 404 shape — has no `units` block
    not_found_marker = {"ok": False, "err": "HTTP Error 404: Not Found",
                        "status": 404}

    def fake_fetch_one_concept(cik, concept):
        if concept == primary:
            return not_found_marker
        if concept == fallback:
            return capex_payload
        # other concepts — return empty-payload marker
        return {"ok": False, "err": "404", "status": 404}

    monkeypatch.setattr(institutional, "_fetch_one_concept",
                        fake_fetch_one_concept)
    # short-circuit the cache so the bundle's _fetch closure runs
    monkeypatch.setattr(institutional, "_cached_fetch",
        lambda dr, name, ttl, fetch: {**fetch(), "fetched_at": 0.0,
                                       "cache_hit": False})
    bundle = institutional._fetch_xbrl_bundle("0000320193", tmp_path)
    # the fallback payload is stored under the PRIMARY concept name
    assert primary in bundle
    assert bundle[primary] is capex_payload
    # the fallback concept name is NOT added as a separate bundle key
    # (the merge would otherwise see two entries for the same field)
    assert fallback not in bundle
    # downstream merge finds capex via XBRL_CONCEPTS.get(primary)
    assert XBRL_CONCEPTS.get(primary) == "capex"
    # and the merge produces capex=5000 on the period
    periods = _merge_xbrl_periods(bundle)
    assert periods[0]["capex"] == 5000.0


def test_fetch_xbrl_bundle_keeps_primary_when_capex_primary_ok(
        monkeypatch, tmp_path):
    """When the primary capex concept returns real data, the fallback
    chain is NOT walked (no extra HTTP call)."""
    primary = "PaymentsToAcquirePropertyPlantAndEquipment"
    fallback = "PaymentsForCapitalImprovements"
    calls = []

    def fake_fetch_one_concept(cik, concept):
        calls.append(concept)
        if concept == primary:
            return {"units": {"USD": [
                {"form": "10-Q", "start": "2026-03-29",
                 "end": "2026-06-27", "fy": 2026, "fp": "Q3",
                 "filed": "2026-07-31", "accn": "a1", "val": 5000},
            ]}}
        # the fallback should NEVER be called when the primary is OK
        raise AssertionError("fallback should not be called when "
                            f"primary returned units (called {concept})")

    monkeypatch.setattr(institutional, "_fetch_one_concept",
                        fake_fetch_one_concept)
    monkeypatch.setattr(institutional, "_cached_fetch",
        lambda dr, name, ttl, fetch: {**fetch(), "fetched_at": 0.0,
                                       "cache_hit": False})
    institutional._fetch_xbrl_bundle("0000320193", tmp_path)
    assert primary in calls
    assert fallback not in calls, \
        "fallback must not be fetched when primary returned units"


# =====================================================================
# R2-1 fix — defect 2 caveat: cash-flow YTD → standalone derivation
# (AAPL files only YTD for Q2/Q3 on the 10-Q cash flow statement)
# =====================================================================

def test_is_ytd_period_accepts_cashflow_ytd_spans():
    """_is_ytd_period accepts the AAPL cash-flow YTD spans: Q1 90-day,
    Q2 181-day, Q3 272-day on 10-Q; FY 300-400 day on 10-K. The
    standalone filter (_is_standalone_period) only accepts 60-100 day
    10-Q rows (Q1) and 300-400 day 10-K rows (FY) — it rejects Q2/Q3
    YTD rows; the YTD filter accepts them so _derive_cashflow_standalone
    can recover the standalone quarter."""
    # Q1 90-day — both filters accept
    assert _is_standalone_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2025-12-27"}) is True
    assert _is_ytd_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2025-12-27"}) is True
    # Q2 181-day — only YTD filter accepts
    assert _is_standalone_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2026-03-28"}) is False
    assert _is_ytd_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2026-03-28"}) is True
    # Q3 272-day — only YTD filter accepts
    assert _is_standalone_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2026-06-27"}) is False
    assert _is_ytd_period({"form": "10-Q",
        "start": "2025-09-28", "end": "2026-06-27"}) is True
    # FY 365-day — both filters accept
    assert _is_standalone_period({"form": "10-K",
        "start": "2024-09-29", "end": "2025-09-27"}) is True
    assert _is_ytd_period({"form": "10-K",
        "start": "2024-09-29", "end": "2025-09-27"}) is True


def test_derive_cashflow_standalone_q2_from_q2ytd_minus_q1ytd():
    """Q2 standalone OCF = Q2 YTD − Q1 YTD (same fiscal year). The Q2
    10-Q cash flow statement ships only the 6-month YTD (181-day), so
    the standalone filter rejects it; _derive_cashflow_standalone
    recovers the standalone quarter by subtracting Q1's 3-month YTD."""
    # Q1 10-Q standalone (90-day) — already accepted by Step 1
    periods = [
        {"fy": 2026, "fp": "Q1", "operating_cash_flow": 53925000000.0,
         "capex": 2373000000.0},
        # Q2 — Step 1 rejected everything (only YTD rows exist)
        {"fy": 2026, "fp": "Q2", "operating_cash_flow": None,
         "capex": None},
    ]
    # YTD rows (the AAPL bundle would have these for Q1 AND Q2)
    ytd_rows = {
        (2026, "Q1", "operating_cash_flow"): {"val": 53925000000},
        (2026, "Q2", "operating_cash_flow"): {"val": 82627000000},
        (2026, "Q1", "capex"): {"val": 2373000000},
        (2026, "Q2", "capex"): {"val": 4344000000},
    }
    _derive_cashflow_standalone(periods, ytd_rows)
    # Q1 untouched (already had standalone from Step 1)
    assert periods[0]["operating_cash_flow"] == 53925000000.0
    # Q2 derived: 82627 - 53925 = 28702
    assert periods[1]["operating_cash_flow"] == 28702000000.0
    # Q2 capex derived: 4344 - 2373 = 1971
    assert periods[1]["capex"] == 1971000000.0


def test_derive_cashflow_standalone_q3_from_q3ytd_minus_q2ytd():
    """Q3 standalone = Q3 YTD − Q2 YTD. Same logic as Q2 but for Q3."""
    periods = [
        {"fy": 2026, "fp": "Q2", "operating_cash_flow": 28702000000.0,
         "capex": 1971000000.0},
        {"fy": 2026, "fp": "Q3", "operating_cash_flow": None,
         "capex": None},
    ]
    ytd_rows = {
        (2026, "Q2", "operating_cash_flow"): {"val": 82627000000},
        (2026, "Q3", "operating_cash_flow"): {"val": 116996000000},
        (2026, "Q2", "capex"): {"val": 4344000000},
        (2026, "Q3", "capex"): {"val": 6799000000},
    }
    _derive_cashflow_standalone(periods, ytd_rows)
    # Q3 derived: 116996 - 82627 = 34369
    assert periods[1]["operating_cash_flow"] == 34369000000.0
    # Q3 capex derived: 6799 - 4344 = 2455
    assert periods[1]["capex"] == 2455000000.0


def test_derive_cashflow_standalone_q1_safety_net_uses_ytd_when_missing():
    """Q1's YTD IS standalone (90 days, 3-month). The standalone filter
    should already accept it, but if it missed for any reason, the
    derivation's safety-net branch uses the YTD value directly."""
    periods = [{"fy": 2026, "fp": "Q1",
                "operating_cash_flow": None, "capex": None}]
    ytd_rows = {
        (2026, "Q1", "operating_cash_flow"): {"val": 53925000000},
        (2026, "Q1", "capex"): {"val": 2373000000},
    }
    _derive_cashflow_standalone(periods, ytd_rows)
    assert periods[0]["operating_cash_flow"] == 53925000000.0
    assert periods[0]["capex"] == 2373000000.0


def test_derive_cashflow_standalone_skips_when_prior_ytd_missing():
    """When the prior quarter's YTD is missing (e.g., Q1 10-Q was filed
    late or the concept 404'd), Q2 standalone stays None — never
    fabricated."""
    periods = [{"fy": 2026, "fp": "Q2",
                "operating_cash_flow": None, "capex": None}]
    ytd_rows = {
        # Q1 YTD is MISSING — derivation can't subtract
        (2026, "Q2", "operating_cash_flow"): {"val": 82627000000},
    }
    _derive_cashflow_standalone(periods, ytd_rows)
    assert periods[0]["operating_cash_flow"] is None


def test_derive_cashflow_standalone_skips_when_field_already_set():
    """When Step 1 already accepted the standalone row, the derivation
    leaves it untouched (no overwrite)."""
    periods = [{"fy": 2026, "fp": "Q1",
                "operating_cash_flow": 53925000000.0}]
    ytd_rows = {
        (2026, "Q1", "operating_cash_flow"): {"val": 9999999},
    }
    _derive_cashflow_standalone(periods, ytd_rows)
    # Q1 standalone value preserved — not overwritten with YTD value
    assert periods[0]["operating_cash_flow"] == 53925000000.0


def test_merge_xbrl_periods_derives_cashflow_standalone_for_aapl_q3():
    """End-to-end: AAPL's Q3 FY26 standalone OCF and capex are derived
    from YTD rows because AAPL's 10-Q ships only the 9-month YTD on the
    cash flow statement (no standalone 3-month row). The merge produces
    Q3 standalone OCF = 116996 − 82627 = 34369, capex = 6799 − 4344 =
    2455, fcf_derived = 34369 − 2455 = 31914."""
    # Synthesize an AAPL-shaped bundle: revenue has BOTH standalone
    # and YTD rows (income statement requires both); OCF and capex
    # ship ONLY YTD rows for Q2 and Q3 (cash flow statement allows it).
    bundle = {
        # revenue: standalone 90-day Q1, Q2, Q3 rows
        "RevenueFromContractWithCustomerExcludingAssessedTax": {
            "units": {"USD": [
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2025-12-27", "fy": 2026, "fp": "Q1",
                 "filed": "2026-01-30", "accn": "a1",
                 "val": 143756000000},
                {"form": "10-Q", "start": "2025-12-28",
                 "end": "2026-03-28", "fy": 2026, "fp": "Q2",
                 "filed": "2026-05-01", "accn": "a2",
                 "val": 111184000000},
                {"form": "10-Q", "start": "2026-03-29",
                 "end": "2026-06-27", "fy": 2026, "fp": "Q3",
                 "filed": "2026-07-31", "accn": "a3",
                 "val": 109417000000},
            ]}},
        # OCF: Q1 90-day standalone, Q2 181-day YTD, Q3 272-day YTD
        "NetCashProvidedByUsedInOperatingActivities": {
            "units": {"USD": [
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2025-12-27", "fy": 2026, "fp": "Q1",
                 "filed": "2026-01-30", "accn": "a1",
                 "val": 53925000000},
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2026-03-28", "fy": 2026, "fp": "Q2",
                 "filed": "2026-05-01", "accn": "a2",
                 "val": 82627000000},
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2026-06-27", "fy": 2026, "fp": "Q3",
                 "filed": "2026-07-31", "accn": "a3",
                 "val": 116996000000},
            ]}},
        # capex: same shape as OCF
        "PaymentsToAcquirePropertyPlantAndEquipment": {
            "units": {"USD": [
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2025-12-27", "fy": 2026, "fp": "Q1",
                 "filed": "2026-01-30", "accn": "a1",
                 "val": 2373000000},
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2026-03-28", "fy": 2026, "fp": "Q2",
                 "filed": "2026-05-01", "accn": "a2",
                 "val": 4344000000},
                {"form": "10-Q", "start": "2025-09-28",
                 "end": "2026-06-27", "fy": 2026, "fp": "Q3",
                 "filed": "2026-07-31", "accn": "a3",
                 "val": 6799000000},
            ]}},
    }
    periods = _merge_xbrl_periods(bundle)
    # 3 periods, sorted by filed desc (Q3 first, then Q2, then Q1)
    assert len(periods) == 3
    q3 = [p for p in periods if p["fp"] == "Q3"][0]
    q2 = [p for p in periods if p["fp"] == "Q2"][0]
    q1 = [p for p in periods if p["fp"] == "Q1"][0]
    # Q1: standalone filter accepted the 90-day row
    assert q1["operating_cash_flow"] == 53925000000.0
    assert q1["capex"] == 2373000000.0
    assert q1["fcf_derived"] == 51552000000.0
    # Q2: derived from YTD difference
    assert q2["operating_cash_flow"] == 28702000000.0  # 82627 - 53925
    assert q2["capex"] == 1971000000.0  # 4344 - 2373
    assert q2["fcf_derived"] == 26731000000.0  # 28702 - 1971
    # Q3: derived from YTD difference
    assert q3["operating_cash_flow"] == 34369000000.0  # 116996 - 82627
    assert q3["capex"] == 2455000000.0  # 6799 - 4344
    assert q3["fcf_derived"] == 31914000000.0  # 34369 - 2455


def test_cik_resolution_zero_pads_to_10_digits(monkeypatch, tmp_path):
    """company_tickers.json CIK is int; resolution zero-pads to 10."""
    # pre-populate the cache by writing the fixture as the cache file
    master_fixture = _load("company_tickers")
    cache_path = tmp_path / "cache"
    cache_path.mkdir()
    (cache_path / "company_tickers_master.json").write_text(
        json.dumps({"ok": True, "data": master_fixture,
                    "fetched_at": time.time(), "cache_hit": True}))
    cik = institutional._resolve_cik("AAPL", data_root=tmp_path)
    assert cik == "0000320193"
    assert len(cik) == 10
    # check a few others
    assert institutional._resolve_cik("NVDA", data_root=tmp_path) == "0001045810"
    assert institutional._resolve_cik("GOOGL", data_root=tmp_path) == "0001652044"


def test_cik_resolution_unknown_ticker_returns_none(monkeypatch, tmp_path):
    """An unknown ticker yields None — the caller falls back to Yahoo."""
    master_fixture = _load("company_tickers")
    cache_path = tmp_path / "cache"
    cache_path.mkdir()
    (cache_path / "company_tickers_master.json").write_text(
        json.dumps({"ok": True, "data": master_fixture,
                    "fetched_at": time.time(), "cache_hit": True}))
    cik = institutional._resolve_cik("ZZZZZ9", data_root=tmp_path)
    assert cik is None
    # empty symbol
    assert institutional._resolve_cik("", data_root=tmp_path) is None


# =====================================================================
# fetch_fundamentals — top-level + Yahoo fallback
# =====================================================================

def test_fetch_fundamentals_xbrl_path(monkeypatch, tmp_path):
    """AAPL resolves via CIK → XBRL bundle → 8 quarters accession-cited."""
    bundle = _load("xbrl_aapl_concepts")
    master = _load("company_tickers")
    # wire cache so no network is hit
    def fake_cached_fetch(data_root, name, ttl, fetch):
        if name == "company_tickers_master":
            return {"ok": True, "data": master, "fetched_at": time.time(),
                    "cache_hit": True}
        if name.startswith("xbrl_bundle_"):
            return {"ok": True, "data": bundle, "cik": "0000320193",
                    "fetched_at": time.time(), "cache_hit": True}
        return {"ok": False, "error": "test stub"}
    monkeypatch.setattr(institutional, "_cached_fetch", fake_cached_fetch)
    out = institutional.fetch_fundamentals("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    assert out["symbol"] == "AAPL"
    assert out["cik"] == "0000320193"
    assert out["source"] == "sec_xbrl"
    assert out["n_quarters"] == 8
    assert out["latest_quarter"] == "Q3 FY2026"
    # latest period revenue matches the XBRL fixture
    assert out["periods"][0]["revenue"] == 109417000000.0


def test_fetch_fundamentals_yahoo_fallback_for_non_us(monkeypatch, tmp_path):
    """A symbol with no CIK (non-US equity, futures, FX, crypto) falls
    to Yahoo fundamentals-timeseries fallback."""
    master = _load("company_tickers")
    yahoo_fixture = _load("yahoo_fundamentals_aapl")
    def fake_cached_fetch(data_root, name, ttl, fetch):
        if name == "company_tickers_master":
            return {"ok": True, "data": master, "fetched_at": time.time(),
                    "cache_hit": True}
        if name.startswith("yahoo_ft_"):
            return {"ok": True, "data": yahoo_fixture,
                    "fetched_at": time.time(), "cache_hit": True}
        return {"ok": False, "error": "test stub"}
    monkeypatch.setattr(institutional, "_cached_fetch", fake_cached_fetch)
    # GC=F has no SEC CIK (it's a futures contract)
    out = institutional.fetch_fundamentals("GC=F", data_root=tmp_path)
    assert out["ok"] is True
    assert out["source"] == "yahoo_timeseries"
    assert out["n_quarters"] > 0
    assert out["periods"][0].get("revenue") is not None


def test_fetch_fundamentals_total_failure_ok_false(monkeypatch, tmp_path):
    """If XBRL returns nothing AND Yahoo returns nothing → ok:False."""
    master = {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple"}}
    def fake_cached_fetch(data_root, name, ttl, fetch):
        if name == "company_tickers_master":
            return {"ok": True, "data": master, "fetched_at": time.time(),
                    "cache_hit": True}
        return {"ok": False, "error": "test stub — no data"}
    monkeypatch.setattr(institutional, "_cached_fetch", fake_cached_fetch)
    out = institutional.fetch_fundamentals("AAPL", data_root=tmp_path)
    assert out["ok"] is False
    assert out["periods"] == []
    assert out["n_quarters"] == 0


def test_fetch_fundamentals_empty_symbol_ok_false(monkeypatch, tmp_path):
    """Empty symbol → ok:False immediately."""
    out = institutional.fetch_fundamentals("", data_root=tmp_path)
    assert out["ok"] is False
    assert out["error"] == "no symbol"


# =====================================================================
# 13F — _parse_13f_xml + fetch_institutional
# =====================================================================

def test_parse_13f_xml_berkshire_fixture():
    """The Berkshire 13F infotable XML has 89 positions; the parser
    extracts issuer, value, shares, type."""
    xml_bytes = _load_bytes("brk_13f_holdings_xml")
    positions = _parse_13f_xml(xml_bytes)
    assert len(positions) == 89
    # total disclosed value ~$299.3B
    total = sum(p["value"] for p in positions)
    assert 299e9 < total < 300e9, f"total {total} not ~$299.3B"
    # all positions have type SH (default) — no putCall in this XML
    types = {p["type"] for p in positions}
    assert types == {"SH"}, f"unexpected types {types}"
    # each position has issuer, cusip, value, shares
    for p in positions:
        assert p["issuer"]
        assert p["cusip"]
        assert isinstance(p["value"], (int, float))
        assert isinstance(p["shares"], (int, float))


def test_parse_13f_xml_malformed_returns_empty():
    """A non-XML body yields [] (never raises)."""
    assert _parse_13f_xml(b"not xml") == []
    assert _parse_13f_xml(b"") == []


def test_parse_13f_xml_top10_pct_math():
    """top10_pct = sum(top 10 values) / total."""
    xml_bytes = _load_bytes("brk_13f_holdings_xml")
    positions = _parse_13f_xml(xml_bytes)
    total = sum(p["value"] for p in positions)
    top10 = sum(p["value"] for p in sorted(
        positions, key=lambda p: p["value"], reverse=True)[:10])
    pct = round(top10 / total * 100, 2)
    # Berkshire Q2-26 is highly concentrated — top10 ~ 66.8%
    assert 60 < pct < 75, f"top10_pct {pct} not in expected range"


def test_fetch_institutional_default_cik_is_berkshire(monkeypatch, tmp_path):
    """fetch_institutional() with no cik defaults to Berkshire 0001067983."""
    sub = _load("brk_submissions")
    meta = _load("brk_latest_13f_meta")
    index = _load("brk_13f_index")
    holdings_xml = _load_bytes("brk_13f_holdings_xml")
    def fake_cached_fetch(data_root, name, ttl, fetch):
        if name.startswith("inst_13f_"):
            # call the actual fetch, but with mocked HTTP
            return fetch()
        return {"ok": False, "error": "test stub"}
    monkeypatch.setattr(institutional, "_cached_fetch", fake_cached_fetch)
    def fake_http_get(url, ua, timeout=8):
        if "submissions" in url:
            return json.dumps(sub)
        return json.dumps({"directory": {"item": [
            {"name": n} for n in index["files"]]}})
    monkeypatch.setattr(institutional, "_http_get", fake_http_get)
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=8: holdings_xml)
    out = institutional.fetch_institutional(data_root=tmp_path)
    assert out["ok"] is True
    assert out["cik"] == DEFAULT_BRK_CIK
    assert out["fund"] == "BERKSHIRE HATHAWAY INC"
    assert out["filed"] == meta["filed"]
    assert out["accession"] == meta["accession"]
    assert out["n_positions"] == 89
    assert 299e9 < out["total_value"] < 300e9
    assert 60 < out["top10_pct"] < 75


def test_fetch_institutional_holdings_xml_selection_skips_primary_doc(
        monkeypatch, tmp_path):
    """The holdings xml is the .xml that's NOT primary_doc.xml and
    contains 'info' in the filename (the 13F infotable)."""
    # construct an index with multiple .xml files
    files = ["primary_doc.xml", "form13fInfoTable.xml", "doc.xml"]
    sub = _load("brk_submissions")
    holdings_xml = _load_bytes("brk_13f_holdings_xml")
    def fake_cached_fetch(data_root, name, ttl, fetch):
        if name.startswith("inst_13f_"):
            return fetch()
        return {"ok": False, "error": "test stub"}
    monkeypatch.setattr(institutional, "_cached_fetch", fake_cached_fetch)
    monkeypatch.setattr(institutional, "_http_get",
                        lambda url, ua, timeout=8: json.dumps(sub)
                        if "submissions" in url else
                        json.dumps({"directory": {"item": [
                            {"name": n} for n in files]}}))
    captured = {"url": None}
    def cap(url, ua, timeout=8):
        captured["url"] = url
        return holdings_xml
    monkeypatch.setattr(institutional, "_http_get_bytes", cap)
    out = institutional.fetch_institutional(data_root=tmp_path)
    assert out["ok"] is True
    assert "form13fInfoTable.xml" in captured["url"]
    assert "primary_doc" not in captured["url"]


def test_fetch_institutional_no_13f_filing_returns_ok_false(
        monkeypatch, tmp_path):
    """A filer with no 13F-HR in recent submissions → ok:False.

    The production _cached_fetch catches the RuntimeError raised by the
    fetch closure and returns {ok: False, error: ...}; the test keeps
    _cached_fetch intact (only mocking _http_get) so the wrapper's
    exception handling is exercised end-to-end."""
    sub_no_13f = {"name": "NO 13F CORP", "filings": {"recent": {
        "form": ["10-K", "8-K"], "accessionNumber": ["a1", "a2"],
        "filingDate": ["2026-01-01", "2026-02-01"]}}}
    # leave _cached_fetch real; only _http_get is mocked. The wrapper's
    # try/except will turn the raised RuntimeError into ok:False.
    monkeypatch.setattr(institutional, "_http_get",
                        lambda url, ua, timeout=8: json.dumps(sub_no_13f))
    out = institutional.fetch_institutional(data_root=tmp_path)
    assert out["ok"] is False
    assert "no 13F-HR" in out["error"]


# =====================================================================
# Treasury yield curve — _parse_treasury_xml + fetch_yield_curve
# =====================================================================

def test_parse_treasury_xml_fixture_yields_curve_dict():
    """The 2026 fixture parses to {date, 1M, 3M, ..., 30Y} entries."""
    xml_bytes = _load_bytes("treasury_yield_2026_xml")
    days = _parse_treasury_xml(xml_bytes)
    assert len(days) >= 100  # 162 days YTD per fixture
    # all 9 curve fields present on the latest day
    latest = days[-1]
    for tenor in CURVE_FIELDS.values():
        assert tenor in latest, f"missing {tenor} on latest day"
    assert latest["date"]
    # 10Y in the 4-5% range for 2026
    assert 3 < latest["10Y"] < 6


def test_parse_treasury_xml_malformed_returns_empty():
    """Non-XML body yields [] (never raises)."""
    assert _parse_treasury_xml(b"not xml") == []
    assert _parse_treasury_xml(b"") == []


def test_fetch_yield_curve_uses_cache_and_returns_latest(monkeypatch, tmp_path):
    """fetch_yield_curve returns {ok, latest_date, curve, history_last_5}."""
    xml_bytes = _load_bytes("treasury_yield_2026_xml")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f() if n.startswith("treasury")
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=15: xml_bytes)
    out = institutional.fetch_yield_curve(data_root=tmp_path)
    assert out["ok"] is True
    assert out["source"] == "treasury.gov"
    assert out["latest_date"]
    assert "10Y" in out["curve"]
    assert len(out["history_last_5"]) == 5


# =====================================================================
# F&G — fetch_crypto_sentiment
# =====================================================================

def test_fetch_crypto_sentiment_fixture_parses(monkeypatch, tmp_path):
    """alternative.me F&G fixture → {ok, latest:{value, classification},
    history, n_days}."""
    fixture = _load("fng_30d")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f() if n == "crypto_fng"
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get",
                        lambda url, ua, timeout=8: json.dumps(fixture))
    out = institutional.fetch_crypto_sentiment(data_root=tmp_path)
    assert out["ok"] is True
    assert out["source"] == "alternative.me"
    assert isinstance(out["latest"]["value"], int)
    assert out["latest"]["classification"]
    assert out["n_days"] >= 20
    assert len(out["history"]) >= 20
    # each history row has value, classification, ts
    for h in out["history"][:5]:
        assert isinstance(h["value"], int)
        assert h["classification"]
        assert isinstance(h["ts"], int)


# =====================================================================
# Onchain — fetch_onchain
# =====================================================================

def test_fetch_onchain_fixture_parses(monkeypatch, tmp_path):
    """blockchain.info stats fixture → all fields parsed."""
    fixture = _load("blockchain_stats")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f() if n == "onchain_btc"
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get",
                        lambda url, ua, timeout=8: json.dumps(fixture))
    out = institutional.fetch_onchain(data_root=tmp_path)
    assert out["ok"] is True
    assert out["network"] == "btc"
    assert isinstance(out["market_price_usd"], (int, float))
    assert isinstance(out["hash_rate"], (int, float))
    assert isinstance(out["n_tx"], (int, float))
    assert isinstance(out["minutes_between_blocks"], (int, float))
    assert out["as_of"]  # ISO timestamp


# =====================================================================
# Global crypto — fetch_global_crypto
# =====================================================================

def test_fetch_global_crypto_fixture_parses(monkeypatch, tmp_path):
    """CoinGecko global fixture → BTC/ETH dominance, total mcap, 24h change."""
    fixture = _load("coingecko_global")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f() if n == "global_crypto"
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get",
                        lambda url, ua, timeout=8: json.dumps(fixture))
    out = institutional.fetch_global_crypto(data_root=tmp_path)
    assert out["ok"] is True
    assert out["source"] == "coingecko"
    assert isinstance(out["btc_dominance"], (int, float))
    assert isinstance(out["eth_dominance"], (int, float))
    assert isinstance(out["total_market_cap_usd"], (int, float))
    assert isinstance(out["total_volume_usd"], (int, float))
    assert isinstance(out["change_24h_pct"], (int, float))


# =====================================================================
# Social — fetch_social + sub routing
# =====================================================================

def test_fetch_social_routes_crypto_to_cryptocurrency(monkeypatch, tmp_path):
    """A crypto symbol (BTC-USD) routes to r/CryptoCurrency."""
    xml_bytes = _load_bytes("reddit_CryptoCurrency_rss")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f()
                        if n.startswith("social_reddit_CryptoCurrency")
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=8: xml_bytes)
    out = institutional.fetch_social("BTC-USD", data_root=tmp_path)
    assert out["ok"] is True
    assert out["sub"] == "CryptoCurrency"
    assert out["n"] > 0
    assert len(out["items"]) <= 10  # capped at 10


def test_fetch_social_routes_equity_to_stocks(monkeypatch, tmp_path):
    """A US equity ticker (AAPL) routes to r/stocks."""
    xml_bytes = _load_bytes("reddit_stocks_rss")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f()
                        if n.startswith("social_reddit_stocks")
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=8: xml_bytes)
    out = institutional.fetch_social("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    assert out["sub"] == "stocks"
    assert out["n"] > 0


def test_fetch_social_routes_default_to_wsb(monkeypatch, tmp_path):
    """No symbol (or FX/commodity/index) routes to r/wallstreetbets."""
    xml_bytes = _load_bytes("reddit_wallstreetbets_rss")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f()
                        if n.startswith("social_reddit_wallstreetbets")
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=8: xml_bytes)
    out = institutional.fetch_social(None, data_root=tmp_path)
    assert out["ok"] is True
    assert out["sub"] == "wallstreetbets"
    assert out["n"] > 0


def test_fetch_social_caps_at_10_items(monkeypatch, tmp_path):
    """The Reddit RSS feed serves 10 items; the parser caps at 10."""
    # build a fixture with 25 entries
    many = ('<?xml version="1.0"?>'
            '<feed xmlns="http://www.w3.org/2005/Atom">'
            + "".join(f"<entry><title>title{i}</title>"
                       f"<link href='https://reddit.com/r/x/{i}'/>"
                       f"<published>2026-08-2{i % 10}T10:00:00Z</published>"
                       f"</entry>" for i in range(25))
            + "</feed>")
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f: f()
                        if n.startswith("social_reddit_")
                        else {"ok": False, "error": "stub"})
    monkeypatch.setattr(institutional, "_http_get_bytes",
                        lambda url, ua, timeout=8: many.encode())
    out = institutional.fetch_social(None, data_root=tmp_path)
    assert out["ok"] is True
    assert len(out["items"]) <= 10


def test_fetch_social_fail_soft_on_429(monkeypatch, tmp_path):
    """A Reddit 429 → ok:False (no cache to fall back on).

    The production _cached_fetch catches the exception and returns
    {ok: False}; the test keeps _cached_fetch intact so the wrapper's
    fail-soft contract is exercised end-to-end."""
    def boom(url, ua, timeout=8):
        raise Exception("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(institutional, "_http_get_bytes", boom)
    out = institutional.fetch_social("BTC-USD", data_root=tmp_path)
    assert out["ok"] is False


# =====================================================================
# gather_institutional_context — fail-soft per slice
# =====================================================================

def test_gather_institutional_context_each_slice_fail_soft(monkeypatch,
                                                              tmp_path):
    """A dead slice doesn't kill the gather — the total result still
    carries the slices that lived, ok:True if any lived.

    Patches the high-level fetch_* functions directly (not _cached_fetch)
    so the test asserts the gather aggregator's fail-soft composition,
    not the per-feed cache wrapper."""
    monkeypatch.setattr(institutional, "fetch_fundamentals",
                        lambda s, d: {"ok": True, "symbol": s,
                                      "periods": [], "n_quarters": 0,
                                      "source": "test"})
    monkeypatch.setattr(institutional, "fetch_yield_curve",
                        lambda year=None, data_root=None: {"ok": True,
                            "latest_date": "2026-08-24",
                            "curve": {"10Y": 4.7}, "history_last_5": []})
    monkeypatch.setattr(institutional, "fetch_crypto_sentiment",
                        lambda data_root=None: {"ok": True,
                            "latest": {"value": 74,
                                       "classification": "Greed"},
                            "history": [], "n_days": 0})
    monkeypatch.setattr(institutional, "fetch_institutional",
                        lambda cik=None, data_root=None: {"ok": False,
                            "error": "test stub — dead slice"})
    monkeypatch.setattr(institutional, "fetch_onchain",
                        lambda data_root=None: {"ok": False,
                            "error": "test stub — dead slice"})
    monkeypatch.setattr(institutional, "fetch_global_crypto",
                        lambda data_root=None: {"ok": False,
                            "error": "test stub — dead slice"})
    monkeypatch.setattr(institutional, "fetch_social",
                        lambda symbol=None, data_root=None: {"ok": False,
                            "error": "test stub — dead slice"})
    out = institutional.gather_institutional_context("BTC-USD",
                                                       data_root=tmp_path)
    assert out["ok"] is True  # at least one slice lived
    slices = out["slices"]
    # the dead slices still have a key with ok:False
    for k in ("fundamentals", "institutional_top", "macro_curve",
              "crypto_sentiment", "onchain", "global_crypto", "social"):
        assert k in slices
    assert slices["fundamentals"]["ok"] is True
    assert slices["macro_curve"]["ok"] is True
    assert slices["crypto_sentiment"]["ok"] is True
    assert slices["institutional_top"]["ok"] is False
    assert slices["onchain"]["ok"] is False
    assert slices["global_crypto"]["ok"] is False


def test_gather_institutional_context_all_dead_ok_false(monkeypatch,
                                                          tmp_path):
    """If every slice is dead, gather returns ok:False but never raises."""
    monkeypatch.setattr(institutional, "_cached_fetch",
                        lambda dr, n, t, f:
                        {"ok": False, "error": "all dead"})
    out = institutional.gather_institutional_context("ZZZ",
                                                       data_root=tmp_path)
    assert out["ok"] is False
    assert all(not s.get("ok") for s in out["slices"].values())


# =====================================================================
# FUNDAMENTALIST persona shape + L11 + abstain-if-<2-quarters
# =====================================================================

from gold_desk.agent.desk.personas import (  # noqa: E402
    PERSONAS, DESK_TOOLS, SIGNAL_CONTRACT, persona_by_name,
)


def test_fundamentalist_persona_in_roster():
    """The fundamentalist is the 6th persona."""
    assert len(PERSONAS) == 6
    fund = persona_by_name("fundamentalist")
    assert fund is not None
    assert fund.role == "The Fundamentalist"


def test_fundamentalist_tools_subset_of_desk_tools():
    """The fundamentalist's tools ⊆ DESK_TOOLS, with the briefed mapping."""
    fund = persona_by_name("fundamentalist")
    assert fund.tools == ["fundamentals", "earnings", "institutional_top"]
    assert set(fund.tools) <= set(DESK_TOOLS)


def test_fundamentalist_checklist_shape():
    """The fundamentalist's prompt has the 10-point checklist structure."""
    fund = persona_by_name("fundamentalist")
    sys = fund.system
    assert "Work through your checklist:" in sys
    # 10 numbered items 1..10
    for i in range(1, 11):
        assert f"\n{i}." in sys, f"missing checklist item {i}"
    assert "Signal rules:" in sys
    assert "Confidence scale (0-100)" in sys
    # signal rules cover bullish/bearish/neutral
    assert "bullish:" in sys
    assert "bearish:" in sys
    assert "neutral:" in sys
    # prompt ends with the shared signal contract
    assert sys.rstrip().endswith(SIGNAL_CONTRACT)


def test_fundamentalist_l11_accession_citation_rule():
    """The fundamentalist's prompt contains the L11 audit-grade rule:
    cite accession numbers when quoting specific figures."""
    fund = persona_by_name("fundamentalist")
    sys = fund.system
    assert "accession" in sys.lower(), "prompt must mention accession numbers"
    assert "ONLY from" in sys, "prompt must enforce reason-only-from-data"
    assert "AS FILED" in sys, "prompt must enforce point-in-time reasoning"


def test_fundamentalist_abstain_if_less_than_2_quarters():
    """The fundamentalist's prompt declares the abstain rule: if fewer
    than 2 quarters available, ABSTAIN (neutral, 0)."""
    fund = persona_by_name("fundamentalist")
    sys = fund.system
    assert "fewer than 2 quarters" in sys.lower(), (
        "prompt must declare the abstain rule")
    assert "ABSTAIN" in sys, "prompt must name the abstain action"


def test_fundamentalist_no_l11_banned_words():
    """L11 blindfold: the fundamentalist's prompt has no account/
    balance/equity/pnl/bankroll/capital/withdraw/deposit words."""
    banned = ("account", "balance", "equity", "pnl", "bankroll",
              "capital", "withdraw", "deposit")
    fund = persona_by_name("fundamentalist")
    low = fund.system.lower()
    for w in banned:
        assert w not in low, f"fundamentalist prompt mentions {w!r}"


# =====================================================================
# engine.py — _build_context + _base_block with institutional slices
# =====================================================================

from gold_desk.agent.desk import engine as eng  # noqa: E402


def test_build_context_includes_institutional_slices_when_ok():
    """When the institutional slices are ok, _build_context carries them
    in the same dict as the market-data slices."""
    detail = {"symbol": "AAPL", "bars": [], "news": {"items": []}}
    board = {"sectors": []}
    movers = {"gainers": [], "losers": []}
    inst = {
        "fundamentals": {"ok": True, "symbol": "AAPL", "source": "test",
                          "periods": [{"fy": 2026, "fp": "Q3",
                                       "filed": "2026-07-31",
                                       "accn": "test-acc",
                                       "revenue": 109417000000.0,
                                       "eps_diluted": 2.02}],
                          "latest_quarter": "Q3 FY2026", "n_quarters": 1},
        "institutional_top": {"ok": True, "fund": "BRK",
                              "total_value": 299e9, "n_positions": 89,
                              "top10_pct": 66.8, "positions": []},
        "macro_curve": {"ok": True, "latest_date": "2026-08-24",
                         "curve": {"10Y": 4.7}, "history_last_5": []},
        "crypto_sentiment": {"ok": True, "latest": {"value": 74,
                                                       "classification": "Greed"}},
        "onchain": {"ok": True, "market_price_usd": 79000.0},
        "social": {"ok": True, "sub": "stocks", "items": [], "n": 0},
    }
    ctx = eng._build_context(detail, board, movers, inst)
    assert ctx["fundamentals"]["ok"] is True
    assert ctx["earnings"]["ok"] is True  # EPS slice derived from fundamentals
    assert ctx["institutional_top"]["ok"] is True
    assert ctx["macro_curve"]["ok"] is True
    assert ctx["crypto_sentiment"]["ok"] is True
    assert ctx["onchain"]["ok"] is True
    assert ctx["social"]["ok"] is True
    # the EPS slice has the right period fields
    assert ctx["earnings"]["periods"][0]["eps_diluted"] == 2.02
    assert ctx["earnings"]["periods"][0]["accn"] == "test-acc"


def test_build_context_dead_xbrl_yields_ok_false_slice():
    """When the fundamentals slice is ok:False (dead XBRL), the context
    still carries {ok:False} so the fundamentalist can abstain cleanly."""
    detail = {"symbol": "ZZZ", "bars": [], "news": {"items": []}}
    board = {"sectors": []}
    movers = {"gainers": [], "losers": []}
    inst = {"fundamentals": {"ok": False, "error": "no XBRL"},
            "macro_curve": {"ok": True, "latest_date": "x",
                             "curve": {}, "history_last_5": []}}
    ctx = eng._build_context(detail, board, movers, inst)
    assert ctx["fundamentals"]["ok"] is False
    assert ctx["earnings"]["ok"] is False  # cascades from dead fundamentals
    assert ctx["macro_curve"]["ok"] is True  # the other slice lived


def test_base_block_includes_fundamentals_headline():
    """The PM base_block carries fundamentals_headline + institutional
    + curve + sentiment + onchain headlines when the slices lived."""
    detail = {"symbol": "AAPL", "name": "Apple", "price": 220.0,
              "news": {"as_of": "2026-08-25T10:00:00Z"}}
    board = {"sectors": []}
    inst = {
        "fundamentals": {"ok": True, "symbol": "AAPL",
                          "source": "sec_xbrl", "periods": [
                              {"fy": 2026, "fp": "Q3", "filed": "2026-07-31",
                               "accn": "0000320193-26-000020",
                               "revenue": 109417000000.0,
                               "eps_diluted": 2.02},
                              {"fy": 2025, "fp": "Q3", "filed": "2025-07-31",
                               "accn": "0000320193-25-000020",
                               "revenue": 94036000000.0,
                               "eps_diluted": 1.40}],
                          "latest_quarter": "Q3 FY2026", "n_quarters": 2},
        "institutional_top": {"ok": True, "fund": "BRK",
                              "filed": "2026-08-14",
                              "accession": "0001193125-26-352200",
                              "total_value": 299253556246.0,
                              "n_positions": 89, "top10_pct": 66.8,
                              "positions": [
                                  {"issuer": "APPLE INC", "value": 23e9,
                                   "type": "SH"}]},
        "macro_curve": {"ok": True, "latest_date": "2026-08-24",
                         "curve": {"1M": 3.79, "2Y": 4.24, "10Y": 4.7,
                                   "30Y": 5.23}, "history_last_5": []},
        "crypto_sentiment": {"ok": True, "latest": {"value": 74,
                                                       "classification": "Greed"}},
        "onchain": {"ok": True, "market_price_usd": 79000.0},
    }
    bb = eng._base_block(detail, board, inst)
    # fundamentals headline
    fh = bb["fundamentals_headline"]
    assert fh["ok"] is True
    assert fh["n_quarters"] == 2
    assert fh["latest_quarter"] == "Q3 FY2026"
    assert fh["latest_revenue"] == 109417000000.0
    assert fh["latest_eps"] == 2.02
    assert fh["latest_accession"] == "0000320193-26-000020"
    # 8Q growth: 109.4B vs 94.0B → ~16.3% growth
    assert 15 < fh["revenue_growth_8q_pct"] < 17
    # institutional headline
    ih = bb["institutional_headline"]
    assert ih["fund"] == "BRK"
    assert ih["n_positions"] == 89
    assert ih["top10_pct"] == 66.8
    assert ih["top3"][0]["issuer"] == "APPLE INC"
    # curve headline
    ch = bb["macro_curve_headline"]
    assert ch["latest_date"] == "2026-08-24"
    assert ch["yields"]["10Y"] == 4.7
    # sentiment headline
    assert bb["crypto_sentiment_headline"]["value"] == 74
    # onchain headline
    assert bb["onchain_headline"]["price"] == 79000.0


def test_base_block_null_headlines_when_slice_dead():
    """A dead slice yields a null headline (not {ok:False}) in the
    base_block — the PM sees the field's absence, not an error block."""
    detail = {"symbol": "ZZZ", "news": {}}
    board = {"sectors": []}
    bb = eng._base_block(detail, board, {"fundamentals":
                                          {"ok": False, "error": "dead"}})
    assert bb["fundamentals_headline"] is None
    assert bb["institutional_headline"] is None
    assert bb["macro_curve_headline"] is None
    assert bb["crypto_sentiment_headline"] is None
    assert bb["onchain_headline"] is None


def test_engine_dead_xbrl_desk_still_runs_5_persona_context(monkeypatch,
                                                                tmp_path):
    """A dead XBRL doesn't kill the desk — the 5 market-data slices
    live, the fundamentalist abstains. The desk report still has 6
    personas."""
    # patch the markets-plane context (kept from test_desk.py pattern)
    monkeypatch.setattr(eng, "fetch_detail", lambda s, d: {
        "ok": True, "symbol": "AAPL", "name": "Apple Inc",
        "sector": "us", "price": 220.0, "change_pct": 1.2,
        "range_5d_change_pct": 3.4, "bars": [], "news": {"items": []}})
    monkeypatch.setattr(eng, "fetch_board", lambda d: {"ok": True,
                                                          "sectors": []})
    monkeypatch.setattr(eng, "fetch_market_movers", lambda d: {"ok": True,
        "gainers": [], "losers": []})
    # patch the institutional gather to return all-dead slices
    monkeypatch.setattr(eng, "gather_institutional_context",
                        lambda s, d: {"ok": False, "slices": {
                            "fundamentals": {"ok": False, "error": "dead"},
                            "macro_curve": {"ok": False, "error": "dead"}}})
    # scripted complete_json: fundamentalist's call RAISES (mirrors the
    # _fake_complete_json(fail=...) pattern in test_desk.py) — the engine
    # converts the raised LLMUnavailable into _abstain_result (abstained=True).
    from gold_desk.llm.zen_client import LLMUnavailable
    def fake(messages, model, **kwargs):
        system = messages[0]["content"]
        if system.startswith("You are The Fundamentalist"):
            raise LLMUnavailable("insufficient history (n_quarters < 2)")
        if system.startswith("You are The Portfolio Manager"):
            return {"consensus": "bullish", "conviction": 60,
                    "summary": "structure is bullish, fundamentals abstain.",
                    "disagreements": "fundamentalist had no data.",
                    "risk_flags": ["fundamentals slice dead"]}
        return {"signal": "bullish", "confidence": 70,
                "thesis": "chart bullish.", "key_evidence": ["price up"]}
    monkeypatch.setattr(eng, "complete_json", fake)
    out = eng.run_desk("AAPL", data_root=tmp_path)
    assert out["ok"] is True
    assert len(out["personas"]) == 6
    fund = [p for p in out["personas"] if p["name"] == "fundamentalist"][0]
    assert fund["abstained"] is True
    assert "n_quarters < 2" in fund["thesis"]
    # PM still synthesized (not mechanical) over 5 live + 1 abstain
    assert out["pm"]["mechanical"] is False
    assert out["abstained"] == 1


# =====================================================================
# CLI — new markets-* subcommands (--json shape)
# =====================================================================

def _run_cli(argv, capsys):
    from gold_desk.cli import main
    rc = main(argv)
    out = capsys.readouterr().out
    return rc, out


def test_cli_markets_fundamentals_json(monkeypatch, tmp_path, capsys):
    """markets-fundamentals AAPL --json emits {ok, symbol, periods, ...}."""
    monkeypatch.setattr(institutional, "fetch_fundamentals",
                        lambda s, data_root=None: {"ok": True, "symbol": s,
                                      "cik": "0000320193", "source": "sec_xbrl",
                                      "periods": [{"fy": 2026, "fp": "Q3",
                                                   "filed": "2026-07-31",
                                                   "accn": "test",
                                                   "revenue": 109.4e9,
                                                   "eps_diluted": 2.02}],
                                      "latest_quarter": "Q3 FY2026",
                                      "n_quarters": 1})
    rc, out = _run_cli(["markets-fundamentals", "AAPL", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["symbol"] == "AAPL"
    assert payload["cik"] == "0000320193"
    assert payload["source"] == "sec_xbrl"
    assert len(payload["periods"]) == 1


def test_cli_markets_13f_json(monkeypatch, tmp_path, capsys):
    """markets-13f --json emits {ok, fund, cik, filed, accession,
    total_value, n_positions, positions, top10_pct}."""
    monkeypatch.setattr(institutional, "fetch_institutional",
                        lambda cik=None, data_root=None: {"ok": True,
                            "fund": "BERKSHIRE HATHAWAY INC",
                            "cik": DEFAULT_BRK_CIK,
                            "filed": "2026-08-14",
                            "accession": "0001193125-26-352200",
                            "total_value": 299253556246,
                            "n_positions": 89, "top10_pct": 66.8,
                            "positions": []})
    rc, out = _run_cli(["markets-13f", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["fund"] == "BERKSHIRE HATHAWAY INC"
    assert payload["n_positions"] == 89
    assert payload["top10_pct"] == 66.8


def test_cli_markets_curve_json(monkeypatch, tmp_path, capsys):
    """markets-curve --json emits {ok, latest_date, curve, history_last_5}."""
    monkeypatch.setattr(institutional, "fetch_yield_curve",
                        lambda year=None, data_root=None: {"ok": True,
                            "source": "treasury.gov", "latest_date": "2026-08-24",
                            "curve": {"10Y": 4.7, "30Y": 5.23},
                            "history_last_5": []})
    rc, out = _run_cli(["markets-curve", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["latest_date"] == "2026-08-24"
    assert payload["curve"]["10Y"] == 4.7


def test_cli_markets_sentiment_json(monkeypatch, tmp_path, capsys):
    """markets-sentiment --json emits {ok, latest, history, n_days}."""
    monkeypatch.setattr(institutional, "fetch_crypto_sentiment",
                        lambda data_root=None: {"ok": True,
                            "source": "alternative.me",
                            "latest": {"value": 74, "classification": "Greed"},
                            "history": [], "n_days": 30})
    rc, out = _run_cli(["markets-sentiment", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["latest"]["value"] == 74
    assert payload["latest"]["classification"] == "Greed"


def test_cli_markets_onchain_json(monkeypatch, tmp_path, capsys):
    """markets-onchain --json emits {ok, network, market_price_usd, ...}."""
    monkeypatch.setattr(institutional, "fetch_onchain",
                        lambda data_root=None: {"ok": True, "network": "btc",
                            "market_price_usd": 79000.0, "hash_rate": 9.19e11,
                            "n_tx": 630371, "n_btc_mined": 45.9e9,
                            "minutes_between_blocks": 9.16,
                            "total_fees_btc": -45.9e9, "as_of": "2026-08-25Z"})
    rc, out = _run_cli(["markets-onchain", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["network"] == "btc"
    assert payload["market_price_usd"] == 79000.0


def test_cli_markets_institutional_json(monkeypatch, tmp_path, capsys):
    """markets-institutional AAPL --json emits {ok, slices:{...}}."""
    monkeypatch.setattr(institutional, "gather_institutional_context",
                        lambda s, data_root=None: {"ok": True, "symbol": s,
                            "slices": {"fundamentals": {"ok": True},
                                       "macro_curve": {"ok": True},
                                       "onchain": {"ok": False,
                                                    "error": "test"}}})
    rc, out = _run_cli(["markets-institutional", "AAPL", "--json",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    payload = json.loads(out.strip().splitlines()[-1])
    assert payload["ok"] is True
    assert payload["symbol"] == "AAPL"
    assert payload["slices"]["fundamentals"]["ok"] is True
    assert payload["slices"]["onchain"]["ok"] is False


def test_cli_markets_fundamentals_human_table(monkeypatch, tmp_path, capsys):
    """Human output has the symbol header, fp/fy/filed/revenue/EPS/accession table."""
    monkeypatch.setattr(institutional, "fetch_fundamentals",
                        lambda s, data_root=None: {"ok": True, "symbol": s,
                            "cik": "0000320193", "source": "sec_xbrl",
                            "periods": [{"fy": 2026, "fp": "Q3",
                                         "filed": "2026-07-31",
                                         "accn": "0000320193-26-000020",
                                         "revenue": 109417000000.0,
                                         "eps_diluted": 2.02}],
                            "latest_quarter": "Q3 FY2026", "n_quarters": 1})
    rc, out = _run_cli(["markets-fundamentals", "AAPL",
                        "--data-root", str(tmp_path)], capsys)
    assert rc == 0
    assert "FUNDAMENTALS" in out
    assert "AAPL" in out
    assert "sec_xbrl" in out
    assert "Q3" in out
    assert "109.42B" in out  # revenue formatted
    assert "0000320193-26-000020" in out  # accession preserved in human output


# =====================================================================
# Module shape + constants
# =====================================================================

def test_module_exposes_public_api():
    """The institutional module's public API matches the brief's contract."""
    assert hasattr(institutional, "fetch_fundamentals")
    assert hasattr(institutional, "fetch_institutional")
    assert hasattr(institutional, "fetch_yield_curve")
    assert hasattr(institutional, "fetch_crypto_sentiment")
    assert hasattr(institutional, "fetch_onchain")
    assert hasattr(institutional, "fetch_global_crypto")
    assert hasattr(institutional, "fetch_social")
    assert hasattr(institutional, "gather_institutional_context")


def test_module_constants_pinned():
    """TTL = 30 min, N_QUARTERS = 8, default BRK CIK = Berkshire."""
    assert TTL_S == 30 * 60
    assert N_QUARTERS == 8
    assert DEFAULT_BRK_CIK == "0001067983"
    assert len(DEFAULT_BRK_CIK) == 10  # zero-padded


def test_module_xbrl_concepts_cover_required_metrics():
    """The XBRL concept map covers the ~10 briefed metrics: revenue,
    net income, gross profit, operating income, EPS diluted/basic,
    total debt, stockholders equity, cash, FCF, operating cash flow."""
    fields = set(XBRL_CONCEPTS.values())
    for required in ("revenue", "net_income", "gross_profit",
                     "operating_income", "eps_diluted", "eps_basic",
                     "total_debt", "stockholders_equity", "cash",
                     "free_cash_flow", "operating_cash_flow"):
        assert required in fields, f"missing XBRL concept for {required}"


def test_module_curve_fields_cover_1m_to_30y():
    """The curve fields map covers 1M, 3M, 6M, 1Y, 2Y, 5Y, 10Y, 20Y, 30Y."""
    keys = set(CURVE_FIELDS.values())
    for tenor in ("1M", "3M", "6M", "1Y", "2Y", "5Y",
                  "10Y", "20Y", "30Y"):
        assert tenor in keys


def test_module_user_agents_match_brief():
    """SEC uses 'Gold Desk Research research@example.com' UA; Yahoo uses
    the Mozilla UA."""
    assert institutional.EDGAR_UA == "Gold Desk Research research@example.com"
    assert "Mozilla" in institutional.YAHOO_UA
    assert institutional.REDDIT_UA == institutional.YAHOO_UA
    assert institutional.TREASURY_UA == institutional.YAHOO_UA
