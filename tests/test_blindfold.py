"""§16 row 6 — blindfold: equity/pnl/streak/budget keys can never appear in
a context pack, at any nesting depth; the builder itself fails closed."""
from __future__ import annotations

import pytest

from gold_desk.context_pack import FORBIDDEN_KEYS, audit_forbidden, build_pack
from gold_desk.data.model import wrap
from conftest import MONDAY, good_candidate, make_constitution
from datetime import timezone


def _obs():
    decision = MONDAY.replace(hour=8, tzinfo=timezone.utc)
    bars = [wrap("bar", decision.replace(hour=h), {"c": 2400.0}) for h in range(6)]
    feats = [wrap("feature", decision, {"atr14": 4.0})]
    cal = [wrap("calendar", decision, {"title": "CPI", "impact": "high"})]
    news = [wrap("news", decision, {"headline": "CPI in line"})]
    return decision, bars, feats, cal, news


def test_pack_contains_no_forbidden_keys():
    decision, bars, feats, cal, news = _obs()
    pack = build_pack(make_constitution(), good_candidate(), bars, feats, cal, news)
    assert audit_forbidden(pack.to_dict()) == []
    # the must_not_know declaration is allowed to NAME the concepts (§4.4);
    # nowhere else in the pack may they appear
    d = pack.to_dict()
    declaration = d.pop("must_not_know")
    assert any("equity" in s for s in declaration)   # declaration really declares
    blob = str(d).lower()
    for word in ("equity", "balance", "daily_pnl", "budget", "streak",
                 "trades_today", "challenge"):
        assert word not in blob, word


def test_scrub_removes_nested_leak():
    payload = {"ok": 1, "account": {"equity": 10}, "list": [{"daily_pnl": -5}]}
    from gold_desk.context_pack import _scrub
    clean = _scrub(payload)
    assert audit_forbidden(clean) == []
    assert clean["ok"] == 1


def test_builder_strips_poisoned_input():
    """§4.4: forbidden fields are STRIPPED IN PYTHON even if present upstream.
    The pack builds fine; the poison is simply gone — never trusted to the
    model."""
    decision, bars, feats, cal, news = _obs()
    poisoned = [wrap("news", decision, {"headline": "x", "equity": 12345.0})]
    pack = build_pack(make_constitution(), good_candidate(), bars, feats,
                      cal, poisoned)
    blob = str(pack.to_dict())
    assert "12345" not in blob
    assert audit_forbidden(pack.to_dict()) == []


def test_scrub_would_fail_closed_if_a_key_survived():
    from gold_desk.context_pack import ContextPack
    evil = ContextPack(decision_ts="t", candidate={"equity": 1.0})
    with pytest.raises(RuntimeError):
        # simulate the internal audit path directly
        leak = audit_forbidden(evil.to_dict())
        if leak:
            raise RuntimeError(f"blindfold violation: {leak}")


def test_asof_future_items_never_enter_pack():
    from datetime import timedelta
    decision, bars, feats, cal, news = _obs()
    future_cal = [wrap("calendar", decision + timedelta(hours=3),
                       {"title": "FOMC", "impact": "high"})]
    pack = build_pack(make_constitution(), good_candidate(), bars, feats,
                      future_cal, news)
    assert pack.calendar == []


def test_forbidden_list_is_present_for_ci():
    assert "equity" in FORBIDDEN_KEYS and "challenge" in FORBIDDEN_KEYS
