"""H2 — PaperAccountStore PnL math uses point_value_per_lot, not a hardcoded
100. A 50-point contract (half-size) must produce exactly half the PnL of the
default 100-point contract for the same bar move."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from gold_desk.account import PaperAccountStore, PaperPosition  # noqa: E402
from gold_desk.clock import iso  # noqa: E402
from gold_desk.data.model import Bar  # noqa: E402
from gold_desk.events import Journal  # noqa: E402


def _bar(o, h, l, c, ts="2026-06-01T08:00:00Z", ts_close="2026-06-01T09:00:00Z"):
    return Bar(ts_open=ts, ts_close=ts_close, open=o, high=h, low=l, close=c,
               volume=100.0)


def _pos(side, entry, stop, target, lots=1.0):
    return PaperPosition(
        opened_ts="2026-06-01T08:00:00Z", side=side, entry=entry,
        stop=stop, target=target, lots=lots,
        time_stop_ts="2026-06-02T08:00:00Z", ticket_id="T1",
    )


def _store(tmp_path, point_value_per_lot):
    journal = Journal(tmp_path, "h" * 64)
    return PaperAccountStore(tmp_path, 10000.0, journal,
                            point_value_per_lot=point_value_per_lot)


def test_pnl_for_non_100_point_value_matches_formula():
    """For point_value_per_lot = 50, a long 1-lot position that moves +2.00
    from entry must close at +$100 PnL (= 1 * 2 * 50), not +$200 (the old
    hardcoded *100 answer)."""
    import shutil
    pv = 50.0
    tmp = Path("/tmp/gd-test-pv50")
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)
    store = _store(tmp, pv)
    store.open_position(_pos("buy", entry=2400.0, stop=2390.0,
                             target=2402.0, lots=1.0))
    # next bar hits the target at 2402 (entry + 2); stop not hit
    bar = _bar(o=2400.0, h=2403.0, l=2398.0, c=2402.0)
    closed = store.resolve_on_bar(bar)
    assert closed, "expected the position to close on target"
    pnl = closed[0]["pnl"]
    # direction(+1) * (exit 2402 - entry 2400) * lots 1 * point_value 50 = 100
    assert pnl == pytest.approx(100.0)
    assert pnl != pytest.approx(200.0)  # the old hardcoded answer would be 200


def test_pnl_scales_with_point_value():
    """A single bar move of +5 on a 1-lot long produces:
        point_value 100 -> +$500
        point_value  50 -> +$250
        point_value  25 -> +$125
    Linear in point_value_per_lot — no other surprise factors."""
    for pv, expected in [(100.0, 500.0), (50.0, 250.0), (25.0, 125.0)]:
        import shutil
        tmp = Path(f"/tmp/gd-test-pv-{int(pv)}")
        if tmp.exists():
            shutil.rmtree(tmp)
        tmp.mkdir(parents=True, exist_ok=True)
        store = _store(tmp, pv)
        store.open_position(_pos("buy", entry=2400.0, stop=2390.0,
                                 target=2410.0, lots=1.0))
        # bar moves up +5 from entry but neither hits target nor stop, then
        # time-stop closes at bar.close = 2405
        bar = _bar(o=2400.0, h=2408.0, l=2398.0, c=2405.0,
                   ts_close="2026-06-02T08:00:00Z")
        # set time_stop_ts to make time_stop fire on this bar
        store.account.positions[0].time_stop_ts = "2026-06-02T07:00:00Z"
        closed = store.resolve_on_bar(bar)
        assert closed, f"expected close at pv={pv}"
        assert closed[0]["pnl"] == pytest.approx(expected), \
            f"pv={pv}: got {closed[0]['pnl']}, expected {expected}"


def test_point_value_persisted_across_reload(tmp_path):
    """The point value chosen at construction is persisted in account.json
    so a reload picks it up — even if a caller passes a different default."""
    store = _store(tmp_path, 42.0)
    store.open_position(_pos("buy", entry=2400.0, stop=2390.0,
                             target=2410.0, lots=1.0))
    assert store.point_value_per_lot == 42.0
    # reload with a different default — should NOT clobber the persisted value
    store2 = PaperAccountStore(tmp_path, 10000.0, Journal(tmp_path, "h" * 64),
                              point_value_per_lot=999.0)
    assert store2.point_value_per_lot == 42.0


def test_account_corrupt_recovered_emits_event(tmp_path):
    """L6 — corrupt account.json triggers ACCOUNT_CORRUPT_RECOVERED, not a
    crash. The store starts fresh from starting_balance."""
    journal = Journal(tmp_path, "h" * 64)
    path = tmp_path / "account.json"
    path.write_text("{not valid json")
    store = PaperAccountStore(tmp_path, 10000.0, journal)
    assert store.account.balance == 10000.0
    events = Journal.read_events(tmp_path)
    assert any(e["kind"] == "AccountCorruptRecovered" for e in events)
    assert any(e.get("reason_code") == "ACCOUNT_CORRUPT_RECOVERED"
               for e in events)
