"""§16 row 2 — forming bar: indicator and setup paths can never see an
incomplete/future H1 bar."""
from __future__ import annotations

import pytest

from gold_desk.features.indicators import assert_closed, atr
from gold_desk.setup.engine import SetupEngine
from conftest import MONDAY, bar_at


def test_assert_closed_raises_on_future_bar():
    bars = [bar_at(MONDAY, h) for h in range(0, 8)]
    decision = bars[-1].close_dt
    assert_closed(bars, decision)     # all closed: fine
    future = bar_at(MONDAY, 8)
    with pytest.raises(AssertionError):
        assert_closed(bars + [future], decision)


def test_atr_refuses_future_tail():
    bars = [bar_at(MONDAY, h) for h in range(0, 16)]
    decision = bars[-1].close_dt
    with pytest.raises(AssertionError):
        atr(bars + [bar_at(MONDAY, 16)], 14, decision)


def test_engine_never_sees_forming_bar():
    engine = SetupEngine()
    bars = [bar_at(MONDAY, h, c=2400.0 + h) for h in range(0, 9)]
    decision = bars[-1].close_dt
    with pytest.raises(AssertionError):
        engine.evaluate(bars + [bar_at(MONDAY, 9)], decision)
