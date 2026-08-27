"""R4-1 — autonomous watch loop + alert engine tests.

Hand-computed math throughout (no scipy, no network):

* every rule kind: fires / not-fires with hand-computed ATR, volume,
  % move and correlation values;
* AlertEngine cooldown: fire → suppressed within cooldown → re-fires
  at/after the boundary; per-rule independence; persisted map;
* evaluate_rules determinism + purity (inputs never mutated);
* is_session_open: COMEX weekend closed, BTC always open, NYMEX hours
  + maintenance break, NY day-session calendars, 24/5 FX;
* WatchLoop.run_once with a mocked fetcher: fired alert journaled with
  reason code ALERT_FIRED (read back from the JSONL journal), fired
  log persisted, Telegram mocked delivery (and silent skip when
  unconfigured), cooldown across sweeps, session-gated polling;
* run_daemon: 2 ticks with mocked clock/sleep, max_ticks exit,
  KeyboardInterrupt clean exit;
* fail-soft: fetch raises → sweep logged (AlertSweepFailed), no crash,
  next sweep proceeds;
* AlertStore: CRUD round-trip, stable minted ids, fired-log cap 500,
  ack, engine-state and loop-state persistence;
* CLI: alerts-add / alerts / alerts-rm / alerts --ack / watch-loop
  --status --json against a temp data root (no network).
"""
from __future__ import annotations

import copy
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.events import Journal  # noqa: E402
from gold_desk.markets.multi_asset import INSTRUMENT_ORDER  # noqa: E402
from gold_desk.telegram_io import TelegramIO  # noqa: E402
from gold_desk.watch.alerts import (AlertEngine, AlertEvent,  # noqa: E402
                                    AlertRule, atr_now_and_base,
                                    evaluate_rules, update_corr_baselines,
                                    volume_now_and_base)
from gold_desk.watch.loop import (WatchLoop, calendar_for,  # noqa: E402
                                  default_rules, is_session_open,
                                  session_map, watch_status)
from gold_desk.watch.store import FIRED_LOG_CAP, AlertStore  # noqa: E402

WED = datetime(2026, 1, 7, 15, 0, tzinfo=timezone.utc)    # Wednesday 15:00
SAT = datetime(2026, 1, 10, 15, 0, tzinfo=timezone.utc)   # Saturday
SUN3 = datetime(2026, 1, 11, 3, 0, tzinfo=timezone.utc)   # Sunday 03:00
SUN23 = datetime(2026, 1, 11, 23, 0, tzinfo=timezone.utc)  # Sunday 23:00


# --------------------------------------------------------------- helpers
def bar(i: int, o: float, h: float, l: float, c: float, v: float) -> dict:
    return {"ts": (1_700_000_000 + i * 900) * 1000,
            "o": o, "h": h, "l": l, "c": c, "v": v}


def flat_bars(n: int, price: float = 100.0, tr: float = 1.0,
              v: float = 100.0) -> list[dict]:
    """n bars of constant price with per-bar true range `tr`."""
    return [bar(i, price, price + tr / 2, price - tr / 2, price, v)
            for i in range(n)]


def snap(assets: dict, as_of: str = "2026-01-07T15:00:00Z") -> dict:
    return {"ok": True, "as_of": as_of, "assets": assets, "errors": []}


def asset(price=None, prev_close=None, bars=None) -> dict:
    return {"symbol": "X", "price": price, "prev_close": prev_close,
            "bars": bars or []}


def corr_matrix(pairs: dict) -> dict:
    """Build a symmetric matrix result like compute_correlation()."""
    matrix: dict[str, dict] = {}
    for (a, b), v in pairs.items():
        matrix.setdefault(a, {})[b] = v
        matrix.setdefault(b, {})[a] = v
    return {"ok": True, "matrix": matrix}


class FakeTelegram:
    """Mock-friendly sender: token/chat_id present = configured."""

    def __init__(self, configured: bool = True):
        self.token = "fake-token" if configured else None
        self.chat_id = "42" if configured else None
        self.sent: list[str] = []

    def send_message(self, text: str, decision_ts=None) -> str:
        self.sent.append(text)
        return "telegram"


def quote(price: float, prev_close: float, bars=None) -> dict:
    return {"ok": True, "symbol": "S", "price": price,
            "prev_close": prev_close,
            "change": round(price - prev_close, 6),
            "change_pct": round((price - prev_close) / prev_close * 100, 4),
            "currency": "USD", "market_time": 1_700_000_000,
            "bars": bars or [], "source": "yahoo:test"}


def canned_fetcher(quotes: dict):
    """Fetcher returning canned quotes; records the polled symbol lists."""
    calls: list[list[str]] = []

    def _fetch(symbols: list[str]) -> dict:
        calls.append(list(symbols))
        return {s: quotes.get(s, {"ok": False, "error": "not in mock"})
                for s in symbols}
    _fetch.calls = calls
    return _fetch


def make_loop(tmp_path, rules, quotes, **kw) -> tuple[WatchLoop, object]:
    fetcher = canned_fetcher(quotes)
    loop = WatchLoop(data_root=tmp_path, rules=rules, fetcher=fetcher, **kw)
    return loop, fetcher


# ============================================================ price rules
class TestPriceRules:
    def test_price_above_fires(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        out = evaluate_rules([r], snap({"GC=F": asset(price=2010.0)}))
        assert len(out) == 1 and out[0].value == 2010.0
        assert out[0].threshold == 2000.0 and out[0].kind == "price_above"

    def test_price_above_boundary_touch_fires(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        out = evaluate_rules([r], snap({"GC=F": asset(price=2000.0)}))
        assert len(out) == 1

    def test_price_above_below_level_not_fires(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        assert evaluate_rules([r], snap({"GC=F": asset(price=1999.99)})) == []

    def test_price_below_fires(self):
        r = AlertRule("r1", "GC=F", "price_below", {"level": 1900.0})
        out = evaluate_rules([r], snap({"GC=F": asset(price=1899.5)}))
        assert len(out) == 1 and out[0].value == 1899.5

    def test_price_below_above_level_not_fires(self):
        r = AlertRule("r1", "GC=F", "price_below", {"level": 1900.0})
        assert evaluate_rules([r], snap({"GC=F": asset(price=1900.01)})) == []


# ============================================================= pct_move
class TestPctMove:
    def test_window1_fires_daily_move(self):
        # prev 2000 → price 2032 = +1.60% ≥ 1.5
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 1})
        out = evaluate_rules(
            [r], snap({"GC=F": asset(price=2032.0, prev_close=2000.0)}))
        assert len(out) == 1 and out[0].value == pytest.approx(1.6)

    def test_window1_under_threshold_not_fires(self):
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 1})
        assert evaluate_rules(
            [r], snap({"GC=F": asset(price=2020.0, prev_close=2000.0)})) == []

    def test_negative_move_fires_on_abs(self):
        # prev 2000 → price 1968 = −1.60%, |−1.6| ≥ 1.5 fires
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 1})
        out = evaluate_rules(
            [r], snap({"GC=F": asset(price=1968.0, prev_close=2000.0)}))
        assert len(out) == 1 and out[0].value == pytest.approx(-1.6)

    def test_window_n_uses_closes_series(self):
        # closes 100,100,102; price 102, window 2 → +2.0% ≥ 1.5
        bars = [bar(0, 100, 100, 100, 100, 1), bar(1, 100, 100, 100, 100, 1),
                bar(2, 102, 102, 102, 102, 1)]
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 2})
        out = evaluate_rules(
            [r], snap({"GC=F": asset(price=102.0, bars=bars)}))
        assert len(out) == 1 and out[0].value == pytest.approx(2.0)

    def test_window_n_insufficient_bars_fail_closed(self):
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 24})
        assert evaluate_rules(
            [r], snap({"GC=F": asset(price=110.0, bars=flat_bars(3))})) == []

    def test_missing_prev_close_fail_closed(self):
        r = AlertRule("r1", "GC=F", "pct_move",
                      {"threshold": 1.5, "window_bars": 1})
        assert evaluate_rules(
            [r], snap({"GC=F": asset(price=2032.0, prev_close=None)})) == []


# ============================================================= atr_spike
class TestAtrSpike:
    def test_atr_helpers_hand_computed(self):
        # 40 flat TR=1 bars + final TR=12 bar:
        # ATR_now = (19×1 + 12)/20 = 1.55; baseline ATR = 1.0
        bars = flat_bars(40)
        bars.append(bar(40, 100, 111.5, 99.5, 100, 100))
        now_atr, base = atr_now_and_base(bars)
        assert now_atr == pytest.approx(1.55)
        assert base == pytest.approx(1.0)

    def test_atr_spike_fires(self):
        bars = flat_bars(40)
        bars.append(bar(40, 100, 111.5, 99.5, 100, 100))
        r = AlertRule("r1", "BTC-USD", "atr_spike", {"k": 1.5})
        out = evaluate_rules([r], snap({
            "BTC-USD": asset(price=100.0, bars=bars)}))
        assert len(out) == 1
        assert out[0].value == pytest.approx(1.55)  # ratio vs baseline

    def test_atr_spike_not_fires_under_multiple(self):
        bars = flat_bars(40)
        bars.append(bar(40, 100, 111.5, 99.5, 100, 100))
        r = AlertRule("r1", "BTC-USD", "atr_spike", {"k": 1.6})
        assert evaluate_rules([r], snap({
            "BTC-USD": asset(price=100.0, bars=bars)})) == []

    def test_atr_spike_short_tape_fail_closed(self):
        r = AlertRule("r1", "BTC-USD", "atr_spike", {"k": 1.5})
        assert evaluate_rules([r], snap({
            "BTC-USD": asset(price=100.0, bars=flat_bars(30))})) == []

    def test_atr_spike_quiet_tape_not_fires(self):
        r = AlertRule("r1", "BTC-USD", "atr_spike", {"k": 1.5})
        assert evaluate_rules([r], snap({
            "BTC-USD": asset(price=100.0, bars=flat_bars(60))})) == []


# ========================================================== volume_spike
class TestVolumeSpike:
    def test_volume_helpers_hand_computed(self):
        bars = flat_bars(21, v=100.0)
        bars[-1] = bar(20, 100, 100.5, 99.5, 100, 500.0)
        v_now, v_base = volume_now_and_base(bars)
        assert v_now == 500.0 and v_base == 100.0

    def test_volume_spike_fires(self):
        bars = flat_bars(21, v=100.0)
        bars[-1] = bar(20, 100, 100.5, 99.5, 100, 500.0)
        r = AlertRule("r1", "CL=F", "volume_spike", {"k": 4.0})
        out = evaluate_rules([r], snap({
            "CL=F": asset(price=100.0, bars=bars)}))
        assert len(out) == 1 and out[0].value == pytest.approx(5.0)

    def test_volume_spike_strict_inequality(self):
        # 400 > 4×100 is False — exactly-at-threshold does not fire
        bars = flat_bars(21, v=100.0)
        bars[-1] = bar(20, 100, 100.5, 99.5, 100, 400.0)
        r = AlertRule("r1", "CL=F", "volume_spike", {"k": 4.0})
        assert evaluate_rules([r], snap({
            "CL=F": asset(price=100.0, bars=bars)})) == []

    def test_volume_spike_zero_mean_fail_closed(self):
        bars = flat_bars(21, v=0.0)
        bars[-1] = bar(20, 100, 100.5, 99.5, 100, 5.0)
        r = AlertRule("r1", "CL=F", "volume_spike", {"k": 4.0})
        assert evaluate_rules([r], snap({
            "CL=F": asset(price=100.0, bars=bars)})) == []


# ============================================================= corr_flip
class TestCorrFlip:
    def test_corr_flip_fires_on_sign_flip(self):
        r = AlertRule("r1", "GC=F", "corr_flip",
                      {"other": "DX-Y.NYB", "prev_corr": 0.4})
        corr = corr_matrix({("GC=F", "DX-Y.NYB"): -0.12})
        out = evaluate_rules([r], snap({"GC=F": asset(price=2000.0)}), corr)
        assert len(out) == 1 and out[0].value == pytest.approx(-0.12)

    def test_corr_flip_same_sign_not_fires(self):
        r = AlertRule("r1", "GC=F", "corr_flip",
                      {"other": "DX-Y.NYB", "prev_corr": -0.3})
        corr = corr_matrix({("GC=F", "DX-Y.NYB"): -0.2})
        assert evaluate_rules([r], snap({"GC=F": asset(2000.0)}), corr) == []

    def test_corr_flip_no_baseline_not_fires(self):
        r = AlertRule("r1", "GC=F", "corr_flip", {"other": "DX-Y.NYB"})
        corr = corr_matrix({("GC=F", "DX-Y.NYB"): -0.2})
        assert evaluate_rules([r], snap({"GC=F": asset(2000.0)}), corr) == []

    def test_corr_flip_missing_matrix_not_fires(self):
        r = AlertRule("r1", "GC=F", "corr_flip",
                      {"other": "DX-Y.NYB", "prev_corr": 0.4})
        assert evaluate_rules([r], snap({"GC=F": asset(2000.0)}), None) == []

    def test_corr_flip_symmetric_lookup(self):
        # matrix only carries the other→sym direction
        r = AlertRule("r1", "GC=F", "corr_flip",
                      {"other": "DX-Y.NYB", "prev_corr": 0.4})
        matrix = {"DX-Y.NYB": {"GC=F": -0.12}}
        out = evaluate_rules([r], snap({"GC=F": asset(2000.0)}),
                             {"ok": True, "matrix": matrix})
        assert len(out) == 1

    def test_update_corr_baselines_records_value(self):
        r = AlertRule("r1", "GC=F", "corr_flip",
                      {"other": "DX-Y.NYB", "prev_corr": 0.4})
        updated = update_corr_baselines(
            [r], corr_matrix({("GC=F", "DX-Y.NYB"): -0.12}))
        assert updated[0].params["prev_corr"] == pytest.approx(-0.12)
        # original rule untouched (purity)
        assert r.params["prev_corr"] == 0.4


# ======================================================== evaluate misc
class TestEvaluateMisc:
    def test_disabled_rule_never_fires(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0},
                      enabled=False)
        assert evaluate_rules([r], snap({"GC=F": asset(price=2100.0)})) == []

    def test_symbol_absent_no_exception_no_event(self):
        r = AlertRule("r1", "SI=F", "price_above", {"level": 20.0})
        assert evaluate_rules([r], snap({"GC=F": asset(price=2100.0)})) == []

    def test_none_price_fail_closed(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        assert evaluate_rules([r], snap({"GC=F": asset(price=None)})) == []

    def test_determinism_same_inputs_same_events(self):
        rules = [AlertRule("r1", "GC=F", "price_above", {"level": 2000.0}),
                 AlertRule("r2", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        s = snap({"GC=F": asset(price=2100.0, prev_close=2000.0)})
        a = evaluate_rules(rules, s)
        b = evaluate_rules(rules, s)
        assert [e.to_dict() for e in a] == [e.to_dict() for e in b]
        assert len(a) == 2

    def test_purity_inputs_not_mutated(self):
        rules = [AlertRule("r1", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        s = snap({"GC=F": asset(price=2032.0, prev_close=2000.0,
                                bars=flat_bars(5))})
        rules_before = copy.deepcopy([r.to_dict() for r in rules])
        s_before = copy.deepcopy(s)
        evaluate_rules(rules, s)
        assert [r.to_dict() for r in rules] == rules_before
        assert s == s_before

    def test_fired_at_comes_from_snapshot_as_of(self):
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        out = evaluate_rules([r], snap({"GC=F": asset(price=2100.0)},
                                       as_of="2026-01-07T15:00:00Z"))
        assert out[0].fired_at == "2026-01-07T15:00:00Z"
        assert out[0].snapshot_ref["price"] == 2100.0

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            AlertRule("r1", "GC=F", "not_a_kind", {})


# =============================================================== engine
class TestAlertEngine:
    def _ev(self, rid="r1"):
        return AlertEvent(rule_id=rid, symbol="GC=F", kind="price_above",
                          message="m", value=1.0, threshold=1.0,
                          fired_at="2026-01-07T15:00:00Z")

    def test_first_fire_true(self):
        eng = AlertEngine([AlertRule("r1", "GC=F", "price_above", {},
                                     cooldown_minutes=60)])
        assert eng.fire(self._ev(), WED) is True

    def test_suppressed_within_cooldown(self):
        eng = AlertEngine([AlertRule("r1", "GC=F", "price_above", {},
                                     cooldown_minutes=60)])
        assert eng.fire(self._ev(), WED) is True
        assert eng.fire(self._ev(), WED + timedelta(minutes=30)) is False

    def test_refires_at_cooldown_boundary(self):
        eng = AlertEngine([AlertRule("r1", "GC=F", "price_above", {},
                                     cooldown_minutes=60)])
        eng.fire(self._ev(), WED)
        assert eng.fire(self._ev(), WED + timedelta(minutes=60)) is True

    def test_rules_independent(self):
        rules = [AlertRule("r1", "GC=F", "price_above", {},
                           cooldown_minutes=60),
                 AlertRule("r2", "ES=F", "price_above", {},
                           cooldown_minutes=60)]
        eng = AlertEngine(rules)
        assert eng.fire(self._ev("r1"), WED) is True
        assert eng.fire(self._ev("r2"), WED) is True
        assert eng.fire(self._ev("r1"), WED + timedelta(minutes=1)) is False
        assert eng.fire(self._ev("r2"), WED + timedelta(minutes=1)) is False

    def test_state_round_trip(self):
        eng = AlertEngine([AlertRule("r1", "GC=F", "price_above", {},
                                     cooldown_minutes=60)])
        eng.fire(self._ev(), WED)
        state = eng.to_dict()
        eng2 = AlertEngine()
        eng2.from_dict(state)
        assert eng2.fire(self._ev(), WED + timedelta(minutes=5)) is False
        assert eng2.last_fired("r1") == eng.last_fired("r1")

    def test_unparsable_stamp_treated_as_never_fired(self):
        eng = AlertEngine(last_fired={"r1": "not-a-date"})
        assert eng.fire(self._ev(), WED) is True


# ========================================================== session open
class TestSessionOpen:
    def test_comex_closed_on_weekend(self):
        assert is_session_open("COMEX", SAT) is False
        assert is_session_open(calendar_for("GC=F"), SUN3) is False

    def test_comex_open_weekday(self):
        assert is_session_open("COMEX", WED) is True

    def test_btc_always_open(self):
        assert is_session_open("24/7", SAT) is True
        assert is_session_open(calendar_for("BTC-USD"), SUN3) is True
        assert is_session_open("24/7", WED) is True

    def test_nymex_hours_and_break(self):
        assert is_session_open("NYMEX", WED) is True
        # 21:00-22:00 UTC maintenance break (≈17:00-18:00 ET)
        assert is_session_open("NYMEX", WED.replace(hour=21, minute=30)) \
            is False
        assert is_session_open("NYMEX", WED.replace(hour=22)) is True

    def test_ny_day_session_calendars(self):
        # US BOND / ICE / CBOE ≈ 14:00-21:00 UTC day session
        assert is_session_open("US BOND", WED.replace(hour=12)) is False
        assert is_session_open("US BOND", WED.replace(hour=20)) is True
        assert is_session_open("ICE", SAT) is False
        assert is_session_open("CBOE", WED.replace(hour=20)) is True

    def test_fx_24_5_calendar(self):
        assert is_session_open("24/5", SAT) is False
        assert is_session_open("24/5", SUN23) is True   # Sunday reopen
        assert is_session_open("24/5", SUN3) is False
        assert is_session_open("24/5", WED) is True

    def test_unknown_calendar_conservative_monday_friday(self):
        assert is_session_open(None, WED) is True
        assert is_session_open("WEIRD", SAT) is False

    def test_session_map_covers_8_instruments(self):
        m = session_map(WED)
        assert set(m) == set(INSTRUMENT_ORDER)
        assert m["GC=F"] is True and m["BTC-USD"] is True


# ============================================================ watch loop
class TestWatchLoop:
    def _loop(self, tmp_path, rules, quotes, **kw):
        return make_loop(tmp_path, rules, quotes, **kw)

    def test_run_once_fires_and_journals_alert_fired(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        quotes = {"GC=F": quote(2032.0, 2000.0)}
        loop, _ = self._loop(tmp_path, rules, quotes)
        fired = loop.run_once(now=WED)
        assert len(fired) == 1 and fired[0].rule_id == "gc-up"
        # read the journal back: AlertFired + reason code ALERT_FIRED
        events = Journal.read_events(tmp_path)
        hit = [e for e in events if e.get("kind") == "AlertFired"]
        assert len(hit) == 1
        assert hit[0]["reason_code"] == "ALERT_FIRED"
        assert hit[0]["payload"]["rule_id"] == "gc-up"

    def test_fired_log_persisted_in_store(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        loop, _ = self._loop(tmp_path, rules, {"GC=F": quote(2032.0, 2000.0)})
        loop.run_once(now=WED)
        fired = AlertStore(tmp_path).list_fired()
        assert len(fired) == 1
        assert fired[0]["rule_id"] == "gc-up"
        assert fired[0]["channel"] == "none"   # no telegram configured
        assert fired[0]["ack"] is False

    def test_telegram_mock_receives_message(self, tmp_path):
        tg = FakeTelegram(configured=True)
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        loop, _ = self._loop(tmp_path, rules, {"GC=F": quote(2032.0, 2000.0)},
                             telegram=tg)
        fired = loop.run_once(now=WED)
        assert len(fired) == 1 and len(tg.sent) == 1
        assert "GC=F" in tg.sent[0] and "+1.60%" in tg.sent[0]
        assert AlertStore(tmp_path).list_fired()[0]["channel"] == "telegram"

    def test_no_telegram_config_skipped_silently(self, tmp_path, capsys):
        # a real TelegramIO without env token/chat → skip, no console spam
        tg = TelegramIO(Journal(tmp_path, "test-hash"))
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        loop, _ = self._loop(tmp_path, rules, {"GC=F": quote(2032.0, 2000.0)},
                             telegram=tg)
        fired = loop.run_once(now=WED)
        assert len(fired) == 1
        out = capsys.readouterr().out
        assert "+1.60%" not in out   # nothing printed — silent skip

    def test_cooldown_across_sweeps(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1},
                           cooldown_minutes=60)]
        quotes = {"GC=F": quote(2032.0, 2000.0)}
        loop, _ = self._loop(tmp_path, rules, quotes)
        assert len(loop.run_once(now=WED)) == 1
        # 10 minutes later: same alert suppressed by cooldown
        assert loop.run_once(now=WED + timedelta(minutes=10)) == []
        # 2 hours later: re-fires
        assert len(loop.run_once(now=WED + timedelta(hours=2))) == 1

    def test_cooldown_persists_across_loop_restarts(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1},
                           cooldown_minutes=60)]
        quotes = {"GC=F": quote(2032.0, 2000.0)}
        loop1, _ = self._loop(tmp_path, rules, quotes)
        loop1.run_once(now=WED)
        # a NEW loop (fresh engine, same store) must respect the cooldown
        loop2, _ = self._loop(tmp_path, rules, quotes)
        assert loop2.run_once(now=WED + timedelta(minutes=5)) == []
        assert len(loop2.run_once(now=WED + timedelta(hours=2))) == 1

    def test_session_gated_closed_instrument_not_polled_or_evaluated(
            self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        quotes = {"GC=F": quote(2032.0, 2000.0)}
        loop, fetcher = self._loop(tmp_path, rules, quotes)
        fired = loop.run_once(now=SAT)          # COMEX closed Saturday
        assert fired == []
        # R4-2 note: fetch happens in batches — GC=F must be absent from
        # EVERY batch, not just the last one
        polled = [s for call in fetcher.calls for s in call]
        assert fetcher.calls and "GC=F" not in polled
        assert "GC=F" not in loop._polled

    def test_session_gated_open_instrument_polled(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        loop, fetcher = self._loop(tmp_path, rules,
                                   {"GC=F": quote(2032.0, 2000.0)})
        loop.run_once(now=WED)
        # R4-2 note: fetch happens in batches — GC=F may land in any batch
        polled = [s for call in fetcher.calls for s in call]
        assert "GC=F" in polled

    def test_fail_soft_fetch_failure_then_recovery(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        holder = {"quotes": {"GC=F": quote(2032.0, 2000.0)},
                  "fail": True}

        def flaky(symbols):
            if holder["fail"]:
                raise RuntimeError("yahoo down")
            return {s: holder["quotes"].get(s, {"ok": False,
                                                 "error": "not in mock"})
                    for s in symbols}

        loop = WatchLoop(data_root=tmp_path, rules=rules, fetcher=flaky)
        assert loop.run_once(now=WED) == []            # no crash
        assert loop.last_error and "yahoo down" in loop.last_error
        events = Journal.read_events(tmp_path)
        assert any(e.get("kind") == "AlertSweepFailed" for e in events)
        holder["fail"] = False                          # next sweep proceeds
        assert len(loop.run_once(now=WED + timedelta(minutes=1))) == 1

    def test_corr_flip_loop_flow(self, tmp_path):
        # baseline recorded on sweep 1 (no fire), flips on sweep 2
        rules = [AlertRule("gc-dxy", "GC=F", "corr_flip",
                           {"other": "DX-Y.NYB"}, cooldown_minutes=60)]
        corr_state = {"v": 0.4}
        loop, _ = self._loop(
            tmp_path, rules, {"GC=F": quote(2000.0, 1990.0)},
            correlation_provider=lambda: corr_matrix(
                {("GC=F", "DX-Y.NYB"): corr_state["v"]}))
        assert loop.run_once(now=WED) == []            # baseline only
        corr_state["v"] = -0.12
        fired = loop.run_once(now=WED + timedelta(minutes=10))
        assert len(fired) == 1 and fired[0].kind == "corr_flip"


# ============================================================== daemon
class TestDaemon:
    def test_two_ticks_max_ticks_exits_cleanly(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1},
                           cooldown_minutes=0)]
        sleeps: list[float] = []
        loop, _ = make_loop(tmp_path, rules,
                            {"GC=F": quote(2032.0, 2000.0)},
                            sleeper=sleeps.append)
        fired = loop.run_daemon(interval_seconds=300, max_ticks=2)
        assert loop.ticks == 2
        assert sleeps == [300.0]      # sleeps between ticks, not after
        assert len(fired) == 2        # cooldown 0 → both ticks fire

    def test_daemon_cooldown_suppresses_second_tick(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1},
                           cooldown_minutes=60)]
        loop, _ = make_loop(tmp_path, rules,
                            {"GC=F": quote(2032.0, 2000.0)},
                            sleeper=lambda s: None)
        fired = loop.run_daemon(interval_seconds=60, max_ticks=2)
        assert loop.ticks == 2 and len(fired) == 1

    def test_daemon_keyboard_interrupt_clean_exit(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]

        def sleeper(s):
            raise KeyboardInterrupt

        loop, _ = make_loop(tmp_path, rules,
                            {"GC=F": quote(2032.0, 2000.0)},
                            sleeper=sleeper)
        fired = loop.run_daemon(interval_seconds=300)   # no max_ticks
        assert loop.ticks == 1 and len(fired) == 1      # exited after 1st


# ================================================================ store
class TestAlertStore:
    def test_rules_crud_round_trip(self, tmp_path):
        store = AlertStore(tmp_path)
        r = AlertRule("r1", "GC=F", "price_above", {"level": 2000.0})
        store.add_rule(r)
        loaded = store.load_rules()
        assert len(loaded) == 1 and loaded[0].to_dict() == r.to_dict()
        assert store.remove_rule("r1") is True
        assert store.load_rules() == []
        assert store.remove_rule("r1") is False

    def test_add_rule_mints_stable_ids(self, tmp_path):
        store = AlertStore(tmp_path)
        a = store.add_rule(AlertRule("", "GC=F", "pct_move", {}))
        b = store.add_rule(AlertRule("", "GC=F", "pct_move", {}))
        c = store.add_rule(AlertRule("", "BTC-USD", "atr_spike", {}))
        assert (a.id, b.id) == ("GC=F:pct_move:1", "GC=F:pct_move:2")
        assert c.id == "BTC-USD:atr_spike:1"

    def test_fired_log_cap_500(self, tmp_path):
        store = AlertStore(tmp_path)
        for i in range(FIRED_LOG_CAP + 2):
            store.append_fired(AlertEvent(
                rule_id=f"r{i % 3}", symbol="GC=F", kind="pct_move",
                message=f"m{i}", value=1.0, threshold=1.0, fired_at="t"))
        fired = store.list_fired()
        assert len(fired) == FIRED_LOG_CAP
        assert fired[0]["message"] == "m2"      # oldest two evicted
        assert fired[-1]["message"] == f"m{FIRED_LOG_CAP + 1}"

    def test_ack_alert(self, tmp_path):
        store = AlertStore(tmp_path)
        row = store.append_fired(AlertEvent(
            "r1", "GC=F", "pct_move", "m", 1.0, 1.0, "t"))
        assert store.ack_alert(row["event_id"]) is True
        assert store.list_fired()[0]["ack"] is True
        assert store.ack_alert("no-such-id") is False

    def test_engine_and_loop_state_persistence(self, tmp_path):
        store = AlertStore(tmp_path)
        store.save_last_fired({"r1": "2026-01-07T15:00:00Z"})
        store.save_state({"last_sweep": "2026-01-07T15:00:00Z",
                          "ticks": 3})
        assert store.load_last_fired() == {"r1": "2026-01-07T15:00:00Z"}
        assert store.load_state()["ticks"] == 3

    def test_save_rules_round_trip(self, tmp_path):
        store = AlertStore(tmp_path)
        rules = default_rules()
        store.save_rules(rules)
        loaded = store.load_rules()
        assert [r.to_dict() for r in loaded] == [r.to_dict() for r in rules]


# ==================================================== defaults + status
class TestDefaultsAndStatus:
    def test_default_rule_pack_sensible(self):
        rules = default_rules()
        assert len(rules) >= 8
        ids = [r.id for r in rules]
        assert len(ids) == len(set(ids))           # unique ids
        kinds = {r.kind for r in rules}
        assert {"pct_move", "atr_spike", "corr_flip"} <= kinds
        syms = {r.symbol for r in rules}
        assert {"GC=F", "BTC-USD", "ES=F", "^VIX", "CL=F"} <= syms
        # the charter's examples exist verbatim
        gc = [r for r in rules if r.symbol == "GC=F"
              and r.kind == "pct_move"][0]
        assert gc.params["threshold"] == 1.5
        btc_atr = [r for r in rules if r.symbol == "BTC-USD"
                   and r.kind == "atr_spike"][0]
        assert btc_atr.params["k"] == 2.5
        corr = [r for r in rules if r.kind == "corr_flip"][0]
        assert corr.symbol == "GC=F" and corr.params["other"] == "DX-Y.NYB"

    def test_watch_status_fresh_root(self, tmp_path):
        st = watch_status(tmp_path)
        assert st["ok"] is True and st["running"] is False
        assert st["last_sweep"] is None
        assert set(st["sessions"]) == set(INSTRUMENT_ORDER)
        assert st["rules_count"] >= 8

    def test_watch_status_after_sweep(self, tmp_path):
        rules = [AlertRule("gc-up", "GC=F", "pct_move",
                           {"threshold": 1.5, "window_bars": 1})]
        loop, _ = make_loop(tmp_path, rules,
                            {"GC=F": quote(2032.0, 2000.0)})
        loop.run_once(now=WED)
        st = watch_status(tmp_path)
        assert st["running"] is True
        assert st["last_sweep"] == "2026-01-07T15:00:00Z"
        assert st["next_sweep"] == "2026-01-07T15:05:00Z"  # +300s default
        assert st["ticks"] == 1 and st["last_error"] is None
        assert st["sessions"]["GC=F"] is True


# ================================================================== CLI
class TestWatchCli:
    def _run(self, argv, capsys):
        from gold_desk.cli import main
        rc = main(argv)
        out = capsys.readouterr().out
        return rc, out

    def test_alerts_add_then_list_then_rm(self, tmp_path, capsys):
        rc, out = self._run(
            ["alerts-add", "--symbol", "GC=F", "--kind", "pct_move",
             "--threshold", "1.5", "--window", "24",
             "--data-root", str(tmp_path), "--json"], capsys)
        assert rc == 0
        added = json.loads(out)
        assert added["ok"] and added["rule"]["id"] == "GC=F:pct_move:1"
        assert added["rule"]["params"] == {"threshold": 1.5,
                                           "window_bars": 24}
        rc, out = self._run(["alerts", "--data-root", str(tmp_path),
                             "--json"], capsys)
        listed = json.loads(out)
        assert listed["rules_count"] == 1
        assert listed["rules"][0]["symbol"] == "GC=F"
        rc, out = self._run(["alerts-rm", "--id", "GC=F:pct_move:1",
                             "--data-root", str(tmp_path), "--json"], capsys)
        assert rc == 0 and json.loads(out)["ok"] is True
        rc, _ = self._run(["alerts-rm", "--id", "GC=F:pct_move:1",
                           "--data-root", str(tmp_path), "--json"], capsys)
        assert rc == 1

    def test_alerts_ack_flag(self, tmp_path, capsys):
        store = AlertStore(tmp_path)
        row = store.append_fired(AlertEvent(
            "r1", "GC=F", "pct_move", "m", 1.0, 1.0, "t"))
        rc, out = self._run(["alerts", "--ack", row["event_id"],
                             "--data-root", str(tmp_path), "--json"], capsys)
        assert rc == 0 and json.loads(out)["ok"] is True
        assert store.list_fired()[0]["ack"] is True

    def test_watch_loop_status_json(self, tmp_path, capsys):
        rc, out = self._run(["watch-loop", "--status",
                             "--data-root", str(tmp_path), "--json"], capsys)
        st = json.loads(out)
        assert rc == 0 and st["ok"] is True
        assert set(st["sessions"]) == set(INSTRUMENT_ORDER)

    def test_watch_loop_dry_run_fires_with_mocked_fetch(self, tmp_path,
                                                        capsys, monkeypatch):
        from gold_desk.markets import multi_asset
        # Saturday snapshot for BTC (24/7 — polled even on weekends):
        # prev 100000 → 108000 = +8% ≥ the 4% BTC rule in the pack
        monkeypatch.setattr(
            multi_asset, "_TEST_QUOTES",
            {"BTC-USD": quote(108000.0, 100000.0, flat_bars(45))})
        rc, out = self._run(["watch-loop", "--dry-run",
                             "--data-root", str(tmp_path), "--json"],
                            capsys)
        result = json.loads(out)
        assert rc == 0 and result["ok"] is True
        kinds = [f["kind"] for f in result["fired"]]
        assert "pct_move" in kinds            # BTC daily move fired
        assert result["last_error"] is None


# --- R4-1 regression: param-spelling tolerance (level vs threshold) ------
# CLI `alerts-add --threshold` and web POST write params.threshold; the
# default rule pack writes params.level. evaluate_rules must arm a
# price_above/price_below rule from EITHER spelling.

def test_price_above_accepts_threshold_spelling():
    from gold_desk.watch.alerts import AlertRule, evaluate_rules
    snap = {"as_of": "2026-08-27T00:00:00Z",
            "assets": {"GC=F": {"price": 4690.5}}}
    rule = AlertRule(id="r1", symbol="GC=F", kind="price_above",
                     params={"threshold": 4000.0})
    ev = evaluate_rules([rule], snap)
    assert len(ev) == 1 and ev[0].value == 4690.5


def test_price_above_accepts_level_spelling():
    from gold_desk.watch.alerts import AlertRule, evaluate_rules
    snap = {"as_of": "2026-08-27T00:00:00Z",
            "assets": {"GC=F": {"price": 4690.5}}}
    rule = AlertRule(id="r2", symbol="GC=F", kind="price_above",
                     params={"level": 4000.0})
    ev = evaluate_rules([rule], snap)
    assert len(ev) == 1 and ev[0].threshold == 4000.0


def test_price_below_accepts_threshold_spelling():
    from gold_desk.watch.alerts import AlertRule, evaluate_rules
    snap = {"as_of": "2026-08-27T00:00:00Z",
            "assets": {"GC=F": {"price": 3900.0}}}
    rule = AlertRule(id="r3", symbol="GC=F", kind="price_below",
                     params={"threshold": 4000.0})
    ev = evaluate_rules([rule], snap)
    assert len(ev) == 1 and ev[0].value == 3900.0


def test_price_above_no_params_never_fires():
    from gold_desk.watch.alerts import AlertRule, evaluate_rules
    snap = {"as_of": "2026-08-27T00:00:00Z",
            "assets": {"GC=F": {"price": 4690.5}}}
    rule = AlertRule(id="r4", symbol="GC=F", kind="price_above", params={})
    assert evaluate_rules([rule], snap) == []


# --- R4 exit-critic D4: rules on universe symbols outside the default 8 --
def test_rule_on_universe_symbol_outside_watchlist_gets_polled(tmp_path):
    """A rule on SI=F (universe #9) must extend the monitor's sweep —
    before D4 it was accepted but never evaluated."""
    import datetime as _dt
    from gold_desk.watch.loop import WatchLoop
    from gold_desk.watch.alerts import AlertRule

    def fetch(symbols):
        return {s: {"ok": True, "price": 31.0,
                    "regularMarketPreviousClose": 30.0,
                    "shortName": s, "name": s}
                for s in symbols}

    wed = _dt.datetime(2026, 8, 26, 14, 0, tzinfo=_dt.timezone.utc)
    loop = WatchLoop(data_root=tmp_path,
                     rules=[AlertRule("si-up", "SI=F", "price_above",
                                      {"level": 30.5})],
                     fetcher=fetch)
    assert "SI=F" not in loop.monitor.symbols          # starts uncovered
    fired = loop.run_once(now=wed)
    assert "SI=F" in loop.monitor.symbols              # D4: now covered
    assert len(fired) == 1 and fired[0].symbol == "SI=F"


def test_cover_rule_symbols_idempotent(tmp_path):
    from gold_desk.watch.loop import WatchLoop
    from gold_desk.watch.alerts import AlertRule

    loop = WatchLoop(data_root=tmp_path, rules=[])
    base = loop.monitor.symbols
    loop._cover_rule_symbols([])                       # nothing to add
    assert loop.monitor.symbols == base
    loop._cover_rule_symbols([AlertRule("gc", "GC=F", "price_above",
                                        {"level": 1.0})])
    assert loop.monitor.symbols == base                # GC=F already there
