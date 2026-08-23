"""§5.3 gold paranoia — quality checks: outlier, TZ misalign, spread, weekend
gap, missing bar."""
from __future__ import annotations

from datetime import timedelta

from gold_desk.data.quality import (check_quality, gap_check,
                                    missing_bar_check)
from conftest import MONDAY, bar_at, good_quote


def test_outlier_price_rejected():
    # 2.5% single-bar move exceeds 2.0% bound
    bar = bar_at(MONDAY, 7, o=2400.0, c=2460.0)
    q = good_quote()
    report = check_quality(bar, q, bar.close_dt, {"max_bar_lag_minutes": 5,
                                                  "outlier_return_abs_pct": 2.0})
    assert not report.ok and report.code == "OUTLIER_PRICE"


def test_malformed_ohlc_rejected():
    bar = bar_at(MONDAY, 7, o=2400.0, h=2390.0, l=2405.0, c=2401.0)
    report = check_quality(bar, good_quote(), bar.close_dt,
                           {"max_bar_lag_minutes": 5})
    assert not report.ok and report.code == "OUTLIER_PRICE"


def test_tz_misalign_on_offhour_bar():
    bar = bar_at(MONDAY, 7)
    from gold_desk.data.model import Bar
    from gold_desk.clock import iso
    bad = Bar(ts_open=iso(bar.open_dt.replace(minute=13)), ts_close=bar.ts_close,
              open=2400, high=2401, low=2399, close=2400)
    report = check_quality(bad, good_quote(), bad.close_dt,
                           {"max_bar_lag_minutes": 5})
    assert not report.ok and report.code == "TZ_MISALIGN"


def test_spread_explosion_rejected():
    bar = bar_at(MONDAY, 7)
    wide = good_quote(spread=0.9)
    report = check_quality(bar, wide, bar.close_dt,
                           {"max_bar_lag_minutes": 5, "max_spread": 0.45})
    assert not report.ok and report.code == "SPREAD"


def test_stale_data_rejected():
    bar = bar_at(MONDAY, 7)
    late = bar.close_dt + timedelta(minutes=30)
    report = check_quality(bar, good_quote(), late, {"max_bar_lag_minutes": 5})
    assert not report.ok and report.code == "STALE_DATA"


def test_weekend_gap_detected_as_outlier():
    friday = bar_at(MONDAY - timedelta(days=3), 20, c=2400.0)
    monday = bar_at(MONDAY, 0, o=2490.0, c=2491.0)   # 3.75% gap
    limits = {"outlier_return_abs_pct": 2.0}
    report = gap_check(friday.close, monday, limits)
    assert not report.ok and report.code == "OUTLIER_PRICE"


def test_friday_to_monday_not_missing_bar():
    from datetime import datetime, timezone
    fri = bar_at(datetime(2026, 5, 29, tzinfo=timezone.utc), 23)
    mon = bar_at(MONDAY, 0)
    report = missing_bar_check([fri, mon], mon.close_dt)
    assert report.ok


def test_intraday_missing_bar_detected():
    b1 = bar_at(MONDAY, 6)
    b3 = bar_at(MONDAY, 8)   # 07:00 bar missing
    report = missing_bar_check([b1, b3], b3.close_dt)
    assert not report.ok and report.code == "MISSING_BAR"
