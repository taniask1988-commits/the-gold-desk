"""GAUNTLET 7 Piece 5 — pair analytics tests (pure math + offline seams).

The cointegration discriminator: a known-cointegrated synthetic pair
must produce a clearly more-negative ADF t-stat than an independent
random-walk pair.
"""
from __future__ import annotations

import json
import random

import pytest

from gold_desk.markets import pairs as pr


# ------------------------------------------------------------------ math
def test_ols_slope_intercept_exact():
    # y = 3x + 2
    xs = [1.0, 2.0, 3.0, 4.0, 5.0]
    ys = [5.0, 8.0, 11.0, 14.0, 17.0]
    a, b = pr.ols_slope_intercept(xs, ys)
    assert b == pytest.approx(3.0, abs=1e-12)
    assert a == pytest.approx(2.0, abs=1e-12)


def test_ols_rejects_zero_variance():
    with pytest.raises(ValueError):
        pr.ols_slope_intercept([1.0, 1.0, 1.0], [1.0, 2.0, 3.0])


def test_rolling_zscore_known_values():
    # linear ramp: last window [6..10] → mean 8, sample sd (ddof=1) √2.5
    ser = [float(i) for i in range(1, 11)]
    zs = pr.rolling_zscore(ser, 5)
    assert zs[3] is None and zs[4] is not None     # warm-up honored
    assert zs[-1]["mean"] == pytest.approx(8.0)
    assert zs[-1]["z"] == pytest.approx(2 / 2.5 ** 0.5, abs=1e-3)


def test_rolling_zscore_rejects_tiny_window():
    with pytest.raises(ValueError):
        pr.rolling_zscore([1.0] * 10, 3)


def test_eg_cointegrated_pair_vs_independent_walks():
    """The discriminator: AR(1)-residual pair ⇒ strongly negative t;
    independent random walks ⇒ not cointegrated."""
    random.seed(7)
    x, y, lvl = [], [], 0.0
    for _ in range(300):
        x.append(100 + len(x) * 0.1 + random.gauss(0, 1))
        lvl = 0.7 * lvl + random.gauss(0, 1)
        y.append(2 * x[-1] + 50 + lvl)
    eg_c = pr.eg_cointegration(y, x)
    w1 = [0.0]
    w2 = [0.0]
    for _ in range(299):
        w1.append(w1[-1] + random.gauss(0, 1))
        w2.append(w2[-1] + random.gauss(0, 1))
    w1 = [100 + v for v in w1]
    w2 = [100 + v for v in w2]
    eg_i = pr.eg_cointegration(w1, w2)
    # the cointegrated pair must be decisively more negative
    assert eg_c["adf_t_stat"] < eg_i["adf_t_stat"] - 3.0
    assert eg_c["verdict"].startswith("cointegrated")
    assert eg_i["verdict"].startswith("not cointegrated")
    assert eg_i["exploratory"] is True and "exploratory" in eg_i["note"]


def test_eg_hedge_ratio_recovery():
    random.seed(3)
    xs = [10.0 + i for i in range(120)]
    ys = [0.5 * x + 7.0 + random.gauss(0, 0.3) for x in xs]
    eg = pr.eg_cointegration(ys, xs)
    assert eg["beta"] == pytest.approx(0.5, abs=0.01)
    assert eg["alpha"] == pytest.approx(7.0, abs=0.1)


def test_eg_needs_enough_points():
    # ols accepts n>=3, but the ADF step needs >=10 → honest ValueError
    with pytest.raises(ValueError, match="adf needs"):
        pr.eg_cointegration([1.0, 2.0, 3.1, 2.9, 3.2], [1.0, 2.0, 3.0, 2.8, 3.3])


# ------------------------------------------------------------ pair plane
def _bars(dates_and_closes):
    import datetime as _dt
    bars = []
    for i, (d, c) in enumerate(dates_and_closes):
        ts = int(_dt.datetime.strptime(d, "%Y-%m-%d").replace(
            tzinfo=_dt.timezone.utc).timestamp() * 1000)
        bars.append({"ts": ts, "o": c, "h": c, "l": c, "c": c, "v": 1.0})
    return bars


def test_pair_analytics_aligns_dates_and_hedges(monkeypatch, tmp_path):
    import gold_desk.markets.pairs as pr_mod
    dates_a = [(f"2026-{m:02d}-{dd:02d}", 100.0 + 10 * m + dd)
               for m in range(1, 11) for dd in (1, 15)]
    dates_b = [(d, c * 0.5 + (0.02 if i % 2 else -0.02))
               for i, (d, c) in enumerate(dates_a)]   # tiny honest noise
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates_a if s.endswith("A") else
                            (dates_b if s.endswith("B") else [])))
    out = pr.pair_analytics("TESTA", "TESTB", window=8, data_root=tmp_path,
                            mode="spread")
    assert out["ok"] is True and out["n_days"] == len(dates_a)
    # the ±0.02 honest noise this test injects into b caps beta's
    # attainable precision (measured deviation 6.4e-4, deterministic);
    # tolerance reflects that — the noise-free control below pins 1e-9
    assert out["hedge"]["beta"] == pytest.approx(2.0, abs=1e-2)
    assert out["zscore_now"] is not None
    assert abs(out["zscore_now"]) < 5


def test_pair_hedge_exact_without_noise(monkeypatch, tmp_path):
    """Noise-free control: OLS hedge recovers the true ratio to <1e-9."""
    import gold_desk.markets.pairs as pr_mod
    dates_a = [(f"2026-{m:02d}-{dd:02d}", 100.0 + 10 * m + dd)
               for m in range(1, 11) for dd in (1, 15)]
    dates_b = [(d, c * 0.5) for d, c in dates_a]
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates_a if s.endswith("A") else dates_b))
    out = pr.pair_analytics("TESTA", "TESTB", window=8, data_root=tmp_path,
                            mode="spread")
    assert out["hedge"]["beta"] == pytest.approx(2.0, abs=1e-9)
    assert out["hedge"]["alpha"] == pytest.approx(0.0, abs=1e-9)


def test_pair_analytics_insufficient_overlap_fails_soft(monkeypatch,
                                                        tmp_path):
    import gold_desk.markets.pairs as pr_mod
    dates_a = [(f"2026-{m:02d}-01", 100.0) for m in range(1, 13)]
    dates_b = [(f"2026-{m:02d}-01", 50.0) for m in range(1, 4)]
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates_a if s.endswith("A") else dates_b))
    out = pr.pair_analytics("TESTA", "TESTB", window=60,
                            data_root=tmp_path)
    assert out["ok"] is False
    assert "aligned" in out["errors"][0]


def test_pair_analytics_ratio_mode(monkeypatch, tmp_path):
    import gold_desk.markets.pairs as pr_mod
    dates = [(f"2026-{m:02d}-{dd:02d}", 100.0 + m)
             for m in range(1, 13) for dd in (1, 15)]
    dates_b = [(d, 10.0) for d, _ in dates]
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates if s.endswith("A") else dates_b))
    out = pr.pair_analytics("TESTA", "TESTB", window=6, data_root=tmp_path,
                            mode="ratio")
    assert out["ok"] is True
    assert out["spread_label"] == "TESTA/TESTB"
    # ratio series → z-score defined on all but warm-up
    assert out["zscore"][-1] is not None


# ------------------------------------------------------------------- CLI
def test_cli_pair_json_contract(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    import gold_desk.markets.pairs as pr_mod
    dates = [(f"2026-{m:02d}-{dd:02d}", 100.0 + m)
             for m in range(1, 11) for dd in (1, 8, 15, 22)]
    # a little deterministic noise so the fit is not degenerate
    dates_b = [(d, c / 2 + (0.01 if i % 3 else -0.01))
               for i, (d, c) in enumerate(dates)]
    monkeypatch.setattr(pr_mod, "fetch_daily_bars",
                        lambda s, r, dr: _bars(
                            dates if s == "TESTA" else dates_b))
    rc = main(["pair", "TESTA", "TESTB", "--window", "30", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True
    assert out["cointegration"]["exploratory"] is True



