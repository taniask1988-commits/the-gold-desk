"""§16 row 3 + Doc 5 — every hard-reject row of the gate table (§7.2),
table-driven, fake contract only."""
from __future__ import annotations

from datetime import timedelta

import pytest

from gold_desk.risk_gate import evaluate_gate
from gold_desk.data.model import Quote
from conftest import (MONDAY, bar_at, good_account, good_candidate,
                      good_market, good_quote, make_constitution)

NOW = MONDAY.replace(hour=8) + timedelta(hours=0)


def gate(constitution, cand=None, acct=None, market=None, quote=None, now=None,
         kill_switch=False, degraded=False, atr14=4.0,
         spread_at_candidate=None, engine_spec_hash=None):
    return evaluate_gate(
        constitution,
        cand or good_candidate(),
        acct or good_account(),
        market or good_market(),
        quote or good_quote(),
        now or NOW,
        atr14=atr14, kill_switch=kill_switch, degraded=degraded,
        spread_at_candidate=spread_at_candidate,
        engine_spec_hash=engine_spec_hash,
    )


TABLE = [
    # (name, mutation, expected_code)
    ("KILL_SWITCH", lambda kw: kw.update(kill_switch=True), "KILL_SWITCH"),
    ("DEGRADED", lambda kw: kw.update(degraded=True), "DEGRADED"),
    ("CONSTITUTION_BLOCKED",
     lambda kw: kw.update(constitution=make_constitution(
         **{"broker.contract_size": "BLOCKED"})), "CONSTITUTION_BLOCKED"),
    ("TICKET_EXPIRED",
     lambda kw: kw.update(cand=good_candidate(
         expiry_ts=(NOW - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))),
     "TICKET_EXPIRED"),
    ("SESSION", lambda kw: kw.update(market=good_market(session="ny")), "SESSION"),
    ("NEWS_UNAVAILABLE",
     lambda kw: kw.update(market=good_market(news_available=False)),
     "NEWS_UNAVAILABLE"),
    ("NEWS_BLACKOUT",
     lambda kw: kw.update(market=good_market(news_blackout_active=True)),
     "NEWS_BLACKOUT"),
    ("STALE_DATA",
     lambda kw: kw.update(market=good_market(bar_close_ts="2026-06-01T05:00:00Z")),
     "STALE_DATA"),
    ("SPREAD", lambda kw: kw.update(quote=good_quote(spread=0.55)), "SPREAD"),
    # SPREAD_WIDENED: spread=0.40 is below the 0.45 absolute cap, but with
    # spread_at_candidate=0.20, the widening (0.40 > 0.20 * 1.5 = 0.30) fires
    # the renamed SPREAD_BLOWOUT reject (M3).
    ("SPREAD_BLOWOUT",
     lambda kw: kw.update(
         cand=good_candidate(spread_at_candidate=0.20),
         quote=good_quote(spread=0.40), spread_at_candidate=0.20),
     "SPREAD_BLOWOUT"),
    ("BUDGET",
     lambda kw: kw.update(acct=good_account(daily_pnl=-150.0)), "BUDGET"),
    ("MAX_TRADES", lambda kw: kw.update(acct=good_account(trades_today=2)),
     "MAX_TRADES"),
    ("CONSEC_LOSS", lambda kw: kw.update(acct=good_account(consecutive_losses=2)),
     "CONSEC_LOSS"),
    ("OPEN_POSITION",
     lambda kw: kw.update(acct=good_account(
         open_positions=[{"symbol": "XAUUSD", "open": True}])),
     "OPEN_POSITION"),
    ("STOP_TOO_TIGHT",
     lambda kw: kw.update(cand=good_candidate(stop_distance=0.5, stop=2409.5,
                                              target=2412.0)), "STOP_TOO_TIGHT"),
    ("RR_FLOOR",
     lambda kw: kw.update(cand=good_candidate(stop_distance=6.0, stop=2404.0,
                                              target=2412.0)), "RR_FLOOR"),
    ("SIZE_INVALID",
     lambda kw: kw.update(acct=good_account(equity=100.0, balance=100.0)),
     "SIZE_INVALID"),
]


@pytest.mark.parametrize("name,mutate,expected", TABLE,
                         ids=[row[0] for row in TABLE])
def test_gate_table(name, mutate, expected):
    kwargs = dict(constitution=make_constitution())
    mutate(kwargs)
    decision = gate(**kwargs)
    assert decision.action == "REJECT", f"{name}: got {decision.action}"
    assert decision.code == expected, f"{name}: got {decision.code}"


def test_gate_approves_and_sizes_correctly():
    decision = gate(make_constitution())
    assert decision.action == "APPROVE"
    # hand calc: equity 10000 * 0.0025 = $25 risk; stop 6.0 * $100/lot = $600/lot
    # raw = 25/600 = 0.041666 lots -> floor to step = 0.04
    assert decision.lots == pytest.approx(0.04)
    assert decision.risk_money == pytest.approx(25.0)


def test_no_partial_approval_exists():
    decision = gate(make_constitution())
    assert "approve_with_reduction" not in decision.reasons[0].lower()
    assert decision.action in ("APPROVE", "REJECT")   # boolean only


def test_max_lot_refused_not_clipped():
    huge = make_constitution(**{"broker.max_lot": 0.02})
    decision = gate(huge)
    assert decision.action == "REJECT" and decision.code == "SIZE_INVALID"


# ---- H3: spec_hash guard -----------------------------------------------------

def test_spec_hash_mismatch_rejects_with_source_mismatch():
    """A candidate whose spec_hash differs from the engine's currently-loaded
    setup version is rejected with SOURCE_MISMATCH (was a silent no-op before
    the H3 fix — `cand.spec_hash != cand.spec_hash` always evaluated False)."""
    cand = good_candidate(spec_hash="a" * 64)
    decision = gate(make_constitution(), cand=cand,
                   engine_spec_hash="b" * 64)
    assert decision.action == "REJECT"
    assert decision.code == "SOURCE_MISMATCH"
    assert "spec_hash" in decision.reasons[0]


def test_spec_hash_match_approves():
    """When engine_spec_hash equals cand.spec_hash, the guard does NOT reject
    — the candidate flows through to approval."""
    cand = good_candidate(spec_hash="a" * 64)
    decision = gate(make_constitution(), cand=cand,
                   engine_spec_hash="a" * 64)
    assert decision.action == "APPROVE"


def test_spec_hash_guard_silent_when_engine_hash_unset():
    """If the orchestrator doesn't pass engine_spec_hash (None), the guard
    must not fire — backwards compatible with old callers."""
    cand = good_candidate(spec_hash="a" * 64)
    decision = gate(make_constitution(), cand=cand, engine_spec_hash=None)
    assert decision.action == "APPROVE"
