"""§16 row 4 + Doc 5 — lot math: hand calcs, floor-to-step, min/max refusal,
fake contract only."""
from __future__ import annotations

import pytest

from gold_desk.sizing import compute_lots, size_with_constitution
from conftest import make_constitution


def test_hand_calc_rounds_down_to_lot_step():
    # 10000 * 0.0025 = $25; 6.0 stop * $100/lot = $600 per lot
    # raw = 0.041666 -> floor(4.1666) steps of 0.01 -> 0.04
    r = compute_lots(10000.0, 0.0025, 6.0, 100.0, 0.01, 0.01, 20.0)
    assert r.ok and r.lots == pytest.approx(0.04)


def test_exact_step_no_rounding_needed():
    # 25 / (2.5 * 100) = 0.1 exactly
    r = compute_lots(10000.0, 0.0025, 2.5, 100.0, 0.01, 0.01, 20.0)
    assert r.ok and r.lots == pytest.approx(0.10)


def test_below_min_lot_rejects():
    r = compute_lots(100.0, 0.0025, 6.0, 100.0, 0.01, 0.01, 20.0)
    assert not r.ok and r.code == "SIZE_INVALID"
    assert "min_lot" in r.detail


def test_above_max_lot_rejects_never_clips():
    # raw = 100000/ (1*100) = 1000 lots > max 20
    r = compute_lots(100000.0, 1.0, 1.0, 100.0, 0.01, 0.01, 20.0)
    assert not r.ok and r.code == "SIZE_INVALID"
    assert "refused, not clipped" in r.detail


def test_zero_stop_rejects():
    r = compute_lots(10000.0, 0.0025, 0.0, 100.0, 0.01, 0.01, 20.0)
    assert not r.ok


def test_constitution_blocked_fails_closed():
    c = make_constitution(**{"broker.lot_step": "BLOCKED"})
    r = size_with_constitution(c, 10000.0, 6.0)
    assert not r.ok and "BLOCKED" in r.detail


def test_no_kelly_no_reduce_in_api_surface():
    """§16 'no reduce_size': the API simply has no such action. Checked at
    AST level (identifiers), so prose law-statements don't false-positive."""
    import ast
    from gold_desk import sizing, risk_gate
    for mod in (sizing, risk_gate):
        tree = ast.parse(open(mod.__file__).read())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
            if isinstance(node, ast.Attribute):
                names.append(node.attr)
            if isinstance(node, ast.Name):
                names.append(node.id)
            for n in names:
                assert "kelly" not in n.lower(), f"{mod.__name__}:{n}"
                assert "reduce" not in n.lower(), f"{mod.__name__}:{n}"
    from gold_desk.risk_gate import GateDecision
    gate = GateDecision()
    assert gate.action in ("REJECT",)
    assert not hasattr(gate, "reduce_size")
    assert not hasattr(gate, "approve_with_reduction")
