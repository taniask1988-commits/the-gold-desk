"""MARKET GAUNTLET multi-market plane tests. No network: the chart
fetch is monkeypatched with canned v8/chart payloads everywhere, and
an autouse fixture makes the screener seam raise offline.

Round-2 coverage (GAUNTLET-P2-BUILDER): FX pip precision, dual-range
detail (daily vs 5d change), sparkline fallback, movers, and the
expanded registry (9 sectors / 67 symbols).

Round-3 coverage (GAUNTLET-P4-BUILDER): bar-derived range_5d_change_pct
(the 5d chartPreviousClose lies for 24/7 assets), whole-market movers
from the Yahoo predefined screener (market_movers + renamed
watchlist_movers), and inverse FX pairs (inr/usd → inverted USDINR=X,
ad-hoc jpy/eur anchored on the better-quoted side).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.markets import board as mb  # noqa: E402
from gold_desk.markets import registry as reg  # noqa: E402

SECTOR_KEYS = ["indices", "us", "etfs", "india", "commodities",
               "forex", "rates", "volatility", "crypto"]
N_SYMBOLS = 67


@pytest.fixture(autouse=True)
def _offline_screener(monkeypatch):
    """Keep the suite offline (round-3): fetch_board now merges whole-
    market movers, so the screener seam raises by default. Movers tests
    re-patch mb._fetch_screener or mb.fetch_market_movers with canned
    data."""
    def _dead(scr_id, count=12):
        raise RuntimeError(f"offline test: {scr_id}")
    monkeypatch.setattr(mb, "_fetch_screener", _dead)


def canned_chart(price=105.0, prev=100.0, n=6, currency="USD",
                 instrument="EQUITY", dp=2):
    """A minimal but faithful v8/chart chart.result[0] payload.

    dp controls the precision of the intermediate closes (FX payloads
    need 5 or the canned values collapse to 2dp)."""
    step = (price - prev) / max(n - 1, 1)
    closes = [round(prev + step * i, dp) for i in range(n)]
    closes[-1] = price
    return {
        "meta": {
            "currency": currency,
            "chartPreviousClose": prev,
            "previousClose": prev,
            "regularMarketPrice": price,
            "regularMarketTime": 1755800000,
            "instrumentType": instrument,
            "shortName": "Canned Corp",
            "longName": "Canned Corporation Ltd",
        },
        "timestamp": [1755800000 + i * 900 for i in range(n)],
        "indicators": {"quote": [{
            "open": [c - 0.5 for c in closes],
            "high": [c + 1.0 for c in closes],
            "low": [c - 1.0 for c in closes],
            "close": closes,
        }]},
    }


def fake_fetch(payloads: dict | None = None, default=None, counter=None,
               calls: list | None = None):
    """Board._fetch_chart replacement.

    Payload keys are either SYMBOL (returned for every range) or
    (SYMBOL, RANGE) tuples for range-aware canned responses — used by
    the dual-range detail and sparkline-fallback tests. A None payload
    raises (simulated network failure); a callable is invoked lazily.
    Falls back to `default`, else a fresh canned_chart().
    """
    def _fake(symbol, range_="1d", interval="15m"):
        if counter is not None:
            counter["n"] += 1
        if calls is not None:
            calls.append((symbol, range_, interval))
        if payloads is not None:
            for key in ((symbol, range_), symbol):
                if key in payloads:
                    p = payloads[key]
                    if callable(p):
                        return p()
                    if p is None:
                        raise RuntimeError(f"net down for {symbol}")
                    return p
        if default is not None:
            return default
        return canned_chart()
    return _fake


def canned_screener_quotes(sym_pcts):
    """Raw screener quote dicts (shape live-probed 2026-08-25: symbol /
    shortName / regularMarketPrice / regularMarketChangePercent)."""
    return [
        {"symbol": s, "shortName": f"{s} Inc",
         "longName": f"{s} Incorporated",
         "regularMarketPrice": 10.0 + i,
         "regularMarketChangePercent": pct,
         "regularMarketChange": 1.0}
        for i, (s, pct) in enumerate(sym_pcts)
    ]


GAINERS = [("TOP", 14.93), ("NVDA", 4.11), ("SMCI", 3.76)]
LOSERS = [("AAOI", -13.77), ("CRWD", -8.2), ("PYPL", -5.4)]


# ------------------------------------------------------------------ registry
def test_normalize_core_aliases():
    assert reg.normalize("btc") == "BTC-USD"
    assert reg.normalize("Bitcoin") == "BTC-USD"
    assert reg.normalize("gold") == "GC=F"
    assert reg.normalize("XAUUSD") == "GC=F"
    assert reg.normalize("nifty") == "^NSEI"
    assert reg.normalize("NIFTY50") == "^NSEI"
    assert reg.normalize("reliance") == "RELIANCE.NS"
    assert reg.normalize("aapl") == "AAPL"
    assert reg.normalize("EUR/USD") == "EURUSD=X"
    assert reg.normalize("  MSFT ") == "MSFT"
    assert reg.normalize("zz-not-a-thing") is None
    assert reg.normalize("") is None


def test_find():
    entry = reg.find("AAPL")
    assert entry and entry["symbol"] == "AAPL"
    assert entry["name"] == "Apple"
    assert entry["sector"] == "us"
    assert reg.find("aapl")["symbol"] == "AAPL"
    assert reg.find("NOPE-XYZ") is None


def test_all_symbols_covers_nine_sectors():
    syms = reg.all_symbols()
    sectors = {s["sector"] for s in syms}
    assert sectors == set(SECTOR_KEYS)
    assert len(syms) == N_SYMBOLS
    for s in syms:
        assert s["symbol"] and s["name"] and s["sector"]
    # no duplicate symbols
    assert len({s["symbol"] for s in syms}) == N_SYMBOLS


def test_registry_round2_expansion():
    """Round-2 coverage: volatility, rates (+DXY), ETFs, PGMs, ags."""
    by_sector: dict[str, list[str]] = {}
    for s in reg.all_symbols():
        by_sector.setdefault(s["sector"], []).append(s["symbol"])
    assert by_sector["volatility"] == ["^VIX"]
    assert set(by_sector["rates"]) == {"^TNX", "^FVX", "^IRX", "DX-Y.NYB"}
    assert set(by_sector["etfs"]) == {"SPY", "QQQ", "IWM", "GLD",
                                      "SLV", "EEM", "VXX"}
    assert {"PL=F", "PA=F", "ALI=F", "ZC=F", "ZW=F", "ZS=F",
            "KC=F", "SB=F"} <= set(by_sector["commodities"])


def test_normalize_round2_aliases():
    """The critic's coverage misses must all resolve now."""
    checks = {
        "vix": "^VIX", "fear index": "^VIX", "^vix": "^VIX",
        "dxy": "DX-Y.NYB", "dollar index": "DX-Y.NYB",
        "usdx": "DX-Y.NYB", "dx-y.nyb": "DX-Y.NYB",
        "10y": "^TNX", "us 10 year": "^TNX", "us 10y": "^TNX",
        "ten year": "^TNX", "us 10 year yield": "^TNX",
        "5y": "^FVX", "us 5 year": "^FVX",
        "13w": "^IRX", "tbill": "^IRX", "t-bill": "^IRX",
        "spy": "SPY", "qqq": "QQQ", "iwm": "IWM", "gld": "GLD",
        "slv": "SLV", "eem": "EEM", "vxx": "VXX",
        "russell 2000": "IWM", "russell": "IWM",
        "platinum": "PL=F", "xptusd": "PL=F", "xpt": "PL=F",
        "palladium": "PA=F",
        "corn": "ZC=F", "wheat": "ZW=F", "soybeans": "ZS=F",
        "coffee": "KC=F", "sugar": "SB=F",
        "aluminum": "ALI=F", "aluminium": "ALI=F",
    }
    for user, canon in checks.items():
        assert reg.normalize(user) == canon, user


# -------------------------------------------------------------------- board
def test_board_shape_and_math(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    assert out["cache_hit"] is False
    assert [s["key"] for s in out["sectors"]] == SECTOR_KEYS
    row = out["sectors"][0]["rows"][0]
    for k in ("symbol", "name", "sector", "price", "prev_close", "change",
              "change_pct", "currency", "points", "points_source", "ts"):
        assert k in row
    assert row["price"] == 105.0
    assert row["prev_close"] == 100.0
    assert row["change"] == 5.0
    assert row["change_pct"] == 5.0  # (105-100)/100
    assert row["points"][-1] == 105.0
    assert row["points_source"] == "1d"
    assert row["ts"] == (1755800000 + 5 * 900) * 1000
    assert out["errors"] == []


def test_fx_pip_precision(monkeypatch, tmp_path):
    """Round-1 defect 1: FX published at 2dp destroyed pips (EURUSD=X
    showed 1.17 — 41 pips gone; USDCAD row contradicted itself)."""
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        "EURUSD=X": canned_chart(price=1.165909, prev=1.164871,
                                 n=6, dp=5),
        "USDJPY=X": canned_chart(price=159.32123, prev=159.28456,
                                 n=6, dp=5),
    }, default=canned_chart()))
    out = mb.fetch_board(data_root=tmp_path)
    rows = {r["symbol"]: r for s in out["sectors"] for r in s["rows"]}

    e = rows["EURUSD=X"]
    assert e["price"] == 1.16591               # 5dp, not 1.17
    assert e["price"] != round(e["price"], 2)  # >=4 decimals of info
    assert e["prev_close"] == 1.16487
    assert e["change"] == 0.00104              # exactly price - prev
    assert abs((e["price"] - e["prev_close"]) - e["change"]) < 1e-9
    assert e["points"][-1] == 1.16591          # sparkline keeps pips too

    j = rows["USDJPY=X"]
    assert j["price"] == 159.321               # JPY-style: 3dp sensible
    assert j["prev_close"] == 159.285
    assert j["change"] == 0.036
    assert abs((j["price"] - j["prev_close"]) - j["change"]) < 1e-9

    # every published FX row is internally consistent
    for r in rows.values():
        if r["symbol"].endswith("=X") and r["change"] is not None:
            assert abs((r["price"] - r["prev_close"]) - r["change"]) < 1e-6


def test_board_change_pct_negative_and_sub_dollar_rounding(
        monkeypatch, tmp_path):
    # DOGE at 0.1234 from 0.13 → ≈ −5.08%, price keeps 4dp
    def doge():
        return canned_chart(price=0.1234, prev=0.13, n=4)
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(payloads={"DOGE-USD": doge},
                                   default=canned_chart()))
    out = mb.fetch_board(data_root=tmp_path)
    row = next(r for s in out["sectors"] for r in s["rows"]
               if r["symbol"] == "DOGE-USD")
    assert row["price"] == 0.1234       # <1 → 4dp
    assert row["prev_close"] == 0.13
    assert row["change"] == round(0.1234 - 0.13, 4)
    assert row["change_pct"] == -5.08


def test_board_fail_soft_per_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(
        payloads={"ETH-USD": None, "^GSPC": None},   # these two raise
        default=canned_chart()))
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    assert sorted(out["errors"]) == ["ETH-USD", "^GSPC"]
    all_rows = [r for s in out["sectors"] for r in s["rows"]]
    assert len(all_rows) == N_SYMBOLS - 2
    syms = {r["symbol"] for r in all_rows}
    assert "ETH-USD" not in syms and "^GSPC" not in syms
    assert "BTC-USD" in syms and "AAPL" in syms and "RELIANCE.NS" in syms


def test_board_all_fail_never_raises(monkeypatch, tmp_path):
    def dead(symbol, range_="1d", interval="15m"):
        raise RuntimeError("network gone")
    monkeypatch.setattr(mb, "_fetch_chart", dead)
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is False
    assert "error" in out


def test_board_cache_hit(monkeypatch, tmp_path):
    counter = {"n": 0}
    # n=12 → rich 1d sparkline everywhere → no fallback refetches
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(n=12),
                                   counter=counter))
    out1 = mb.fetch_board(data_root=tmp_path)
    out2 = mb.fetch_board(data_root=tmp_path)
    assert counter["n"] == N_SYMBOLS         # fetched exactly once
    assert out1["cache_hit"] is False
    assert out2["cache_hit"] is True
    assert out2["sectors"] == out1["sectors"]
    cache_file = tmp_path / "cache" / "markets_board.json"
    assert cache_file.exists()
    on_disk = json.loads(cache_file.read_text())
    assert on_disk["ok"] is True and len(on_disk["sectors"]) == 9


def test_board_sector_filter(monkeypatch, tmp_path):
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart()))
    out = mb.fetch_board(data_root=tmp_path, sectors=["crypto"])
    assert [s["key"] for s in out["sectors"]] == ["crypto"]
    assert len(out["sectors"][0]["rows"]) == 6
    # filtered board uses its own cache file, unknown keys fall back to all
    assert (tmp_path / "cache" / "markets_board_crypto.json").exists()
    out_all = mb.fetch_board(data_root=tmp_path, sectors=["nope"])
    assert len(out_all["sectors"]) == 9


def test_board_meta_fallback_price(monkeypatch, tmp_path):
    # empty close array → meta.regularMarketPrice is the price
    payload = canned_chart(price=105.0, prev=100.0)
    payload["indicators"]["quote"][0]["close"] = []
    payload["timestamp"] = []
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=payload))
    out = mb.fetch_board(data_root=tmp_path)
    row = out["sectors"][0]["rows"][0]
    assert row["price"] == 105.0
    assert row["points"] == []
    assert row["ts"] == 1755800000 * 1000  # regularMarketTime fallback


# ------------------------------------------------------------ sparkline fix
def test_sparkline_fallback_sparse_1d(monkeypatch, tmp_path):
    """Round-1 defect 3: GC=F-style 1d/15m payload with 3 bars → the
    row refetches 5d/60m and sparks off those, labeled points_source."""
    sparse = canned_chart(price=4700.0, prev=4690.0, n=3)
    rich = canned_chart(price=4700.0, prev=4600.0, n=20)
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("GC=F", "1d"): sparse, ("GC=F", "5d"): rich,
    }, default=canned_chart()))
    out = mb.fetch_board(data_root=tmp_path)
    row = next(r for s in out["sectors"] for r in s["rows"]
               if r["symbol"] == "GC=F")
    assert row["points_source"] == "5d"
    assert len(row["points"]) == 20
    assert row["points"][-1] == 4700.0
    # quote fields still come from the 1d fetch — daily change intact
    assert row["prev_close"] == 4690.0
    assert row["change"] == 10.0
    assert row["change_pct"] == round(10.0 / 4690.0 * 100, 2)


def test_sparkline_no_fallback_when_1d_rich(monkeypatch, tmp_path):
    calls: list = []
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(n=27),
                                   calls=calls))
    out = mb.fetch_board(data_root=tmp_path)
    rows = [r for s in out["sectors"] for r in s["rows"]]
    assert all(r["points_source"] == "1d" for r in rows)
    assert all(len(r["points"]) == mb.SPARK_POINTS for r in rows)
    # no 5d refetch happened at all
    assert all(r == "1d" for _, r, _ in calls)
    assert len(calls) == N_SYMBOLS


def test_sparkline_fallback_fail_soft(monkeypatch, tmp_path):
    """A dead 5d refetch keeps the sparse 1d points — never an error."""
    sparse = canned_chart(price=4700.0, prev=4690.0, n=3)

    def fail5d(symbol, range_="1d", interval="15m"):
        if range_ == "5d":
            raise RuntimeError("5d fetch down")
        return sparse
    monkeypatch.setattr(mb, "_fetch_chart", fail5d)
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    assert out["errors"] == []
    rows = [r for s in out["sectors"] for r in s["rows"]]
    assert all(r["points_source"] == "1d" for r in rows)
    assert all(len(r["points"]) == 3 for r in rows)


# ------------------------------------------------------------------- movers
def test_board_movers(monkeypatch, tmp_path):
    """Round-2 addition: top-5 gainers/losers across the whole board,
    computed locally from board rows."""
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        "BTC-USD": canned_chart(105.0, 100.0),   # +5.00%
        "TSLA": canned_chart(103.0, 100.0),      # +3.00%
        "AAPL": canned_chart(102.0, 100.0),      # +2.00%
        "ETH-USD": canned_chart(101.0, 100.0),   # +1.00%
        "MSFT": canned_chart(98.0, 100.0),       # -2.00%
        "DOGE-USD": canned_chart(99.0, 100.0),   # -1.00%
        "GC=F": canned_chart(97.0, 100.0),       # -3.00%
    }, default=canned_chart(100.5, 100.0)))
    out = mb.fetch_board(data_root=tmp_path)
    movers = out["movers"]
    assert set(movers) == {"gainers", "losers"}
    gainers, losers = movers["gainers"], movers["losers"]
    assert len(gainers) == 5 and len(losers) == 5
    # ranked, whole board (crypto + equities + commodities eligible)
    assert gainers[0]["symbol"] == "BTC-USD"
    assert gainers[0]["change_pct"] == 5.0
    assert [m["change_pct"] for m in gainers] == \
        sorted((m["change_pct"] for m in gainers), reverse=True)
    assert losers[0]["symbol"] == "GC=F"
    assert losers[0]["change_pct"] == -3.0
    assert [m["change_pct"] for m in losers] == \
        sorted(m["change_pct"] for m in losers)
    board_syms = {r["symbol"] for s in out["sectors"]
                  for r in s["rows"]}
    for m in gainers + losers:
        assert set(m) == {"symbol", "name", "sector",
                          "change_pct", "price"}
        assert m["symbol"] in board_syms


def test_movers_skips_rows_without_change_pct(monkeypatch, tmp_path):
    # a payload with no prev close at all → change_pct None → skipped
    p = canned_chart(price=105.0, prev=100.0, n=6)
    del p["meta"]["chartPreviousClose"]
    del p["meta"]["previousClose"]
    p["indicators"]["quote"][0]["close"] = [105.0]
    p["timestamp"] = [1755800000]
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(
        payloads={"NVDA": p}, default=canned_chart(102.0, 100.0)))
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    row = next(r for s in out["sectors"] for r in s["rows"]
               if r["symbol"] == "NVDA")
    assert row["change_pct"] is None
    movers = out["movers"]
    assert "NVDA" not in {m["symbol"] for m in movers["gainers"]}
    assert "NVDA" not in {m["symbol"] for m in movers["losers"]}


def test_movers_short_board(monkeypatch, tmp_path):
    # a filtered board still computes movers from its own rows
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    out = mb.fetch_board(data_root=tmp_path, sectors=["volatility"])
    assert [s["key"] for s in out["sectors"]] == ["volatility"]
    assert len(out["movers"]["gainers"]) == 1   # ^VIX is alone
    assert out["movers"]["gainers"][0]["symbol"] == "^VIX"


# ------------------------------------------------------------------- detail
def test_fetch_detail_dual_range(monkeypatch, tmp_path):
    """Round-1 defect 2: daily change must come from a 1d fetch — the
    5d chartPreviousClose is a 5-day-ago close, not yesterday's."""
    daily = canned_chart(price=105.0, prev=100.0, n=8)   # +5.00% 1d
    weekly = canned_chart(price=105.0, prev=95.0, n=10)  # +5.26% 5d
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("BTC-USD", "1d"): daily, ("BTC-USD", "5d"): weekly}))
    out = mb.fetch_detail("btc", data_root=tmp_path)   # alias + case
    assert out["ok"] is True
    assert out["symbol"] == "BTC-USD"
    assert out["sector"] == "crypto"
    # daily fields from the 1d fetch — NOT the 5d chartPreviousClose
    assert out["price"] == 105.0
    assert out["prev_close"] == 100.0
    assert out["change"] == 5.0
    assert out["change_pct"] == 5.0
    # 5d change separately labeled, from the 5d fetch
    assert out["range_5d_change_pct"] == round(10.0 / 95.0 * 100, 2)
    # bars from the 5d fetch
    assert len(out["bars"]) == 10
    bar = out["bars"][0]
    for k in ("ts", "o", "h", "l", "c"):
        assert k in bar
    assert bar["c"] == 95.0  # first weekly close = 5d prev by construction


def test_fetch_detail_fx_precision(monkeypatch, tmp_path):
    fx = canned_chart(price=1.165909, prev=1.164871, n=6, dp=5)
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(payloads={"EURUSD=X": fx}))
    out = mb.fetch_detail("eurusd", data_root=tmp_path)
    assert out["ok"] is True
    assert out["price"] == 1.16591
    assert out["prev_close"] == 1.16487
    assert out["change"] == 0.00104
    assert out["range_5d_change_pct"] == round(
        (1.165909 - 1.164871) / 1.164871 * 100, 2)


def test_fetch_detail_daily_down_degrades(monkeypatch, tmp_path):
    """1d fetch dead but 5d alive → bars + 5d change still served."""
    weekly = canned_chart(price=105.0, prev=95.0, n=10)

    def flaky(symbol, range_="1d", interval="15m"):
        if range_ == "1d":
            raise RuntimeError("1d fetch down")
        return weekly
    monkeypatch.setattr(mb, "_fetch_chart", flaky)
    out = mb.fetch_detail("btc", data_root=tmp_path)
    assert out["ok"] is True
    assert out["price"] is None and out["change_pct"] is None
    assert len(out["bars"]) == 10
    assert out["range_5d_change_pct"] == round(10.0 / 95.0 * 100, 2)


def test_fetch_detail_fail_soft(monkeypatch, tmp_path):
    # unknown symbol: no network at all, clean error
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart()))
    out = mb.fetch_detail("zzz-not-a-symbol", data_root=tmp_path)
    assert out["ok"] is False
    assert "unknown symbol" in out["error"]

    # known symbol but network dead → fail-soft, never raises
    def dead(symbol, range_="5d", interval="30m"):
        raise RuntimeError("net down")
    monkeypatch.setattr(mb, "_fetch_chart", dead)
    out2 = mb.fetch_detail("btc", data_root=tmp_path)
    assert out2["ok"] is False
    assert out2["symbol"] == "btc"


# ------------------------------------------------- round-3: 5d bar-derived
def test_detail_range_5d_bar_derived_not_meta(monkeypatch, tmp_path):
    """Round-3 defect 1: for 24/7 assets Yahoo's 5d chartPreviousClose
    anchors near YESTERDAY (BTC's 5d cp was 78,335 against a
    73,699→80,484 bar series — the old code printed +2.74%). The 5d
    change must come from the served BARS: first-bar close → last
    close."""
    daily = canned_chart(price=80484.0, prev=78982.0, n=10)   # +1.90% 1d
    weekly = canned_chart(price=80484.0, prev=73699.0, n=12)
    weekly["meta"]["chartPreviousClose"] = 78335.0            # the lie
    weekly["meta"]["previousClose"] = 78335.0
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("BTC-USD", "1d"): daily, ("BTC-USD", "5d"): weekly}))
    out = mb.fetch_detail("btc", data_root=tmp_path)
    assert out["ok"] is True
    # 1d chain untouched (the round-2 fix is preserved)
    assert out["change_pct"] == round(
        (80484.0 - 78982.0) / 78982.0 * 100, 2)
    # 5d anchored on the FIRST BAR'S CLOSE, not the lying meta value
    assert out["range_5d_change_pct"] == round(
        (80484.0 - 73699.0) / 73699.0 * 100, 2)              # ≈ +9.21
    assert out["range_5d_change_pct"] != round(
        (80484.0 - 78335.0) / 78335.0 * 100, 2)              # not +2.74
    assert out["bars"][0]["c"] == 73699.0        # the close, not the open
    assert out["bars"][0]["o"] == 73698.5        # (open would say +9.21+ε)


def test_detail_range_5d_closes_fallback(monkeypatch, tmp_path):
    """No bar carries full OHLC (opens all null) → the 5d change falls
    back to the raw series closes — still never to meta."""
    weekly = canned_chart(price=105.0, prev=95.0, n=8)
    weekly["indicators"]["quote"][0]["open"] = [None] * 8
    weekly["meta"]["chartPreviousClose"] = 99.5    # would say +5.53%
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("GC=F", "1d"): canned_chart(price=105.0, prev=100.0, n=6),
        ("GC=F", "5d"): weekly}))
    out = mb.fetch_detail("gold", data_root=tmp_path)
    assert out["ok"] is True
    assert out["bars"] == []
    assert out["range_5d_change_pct"] == round(
        (105.0 - 95.0) / 95.0 * 100, 2)                      # +10.53


# ------------------------------------------------- round-3: market movers
def test_slim_screener_quote():
    q = {"symbol": "TOP", "shortName": "TOP Financial",
         "regularMarketPrice": 16.24,
         "regularMarketChangePercent": 14.9328}
    assert mb._slim_screener_quote(q) == {
        "symbol": "TOP", "name": "TOP Financial",
        "price": 16.24, "change_pct": 14.93}
    # nothing to rank on → skipped
    assert mb._slim_screener_quote({"symbol": "X"}) is None
    assert mb._slim_screener_quote(
        {"symbol": "X", "regularMarketChangePercent": None}) is None


def test_fetch_market_movers_canned_and_cached(monkeypatch, tmp_path):
    """Whole-market movers from the keyless screener: two calls (one
    per side), slim quotes, 120s cache."""
    counter = {"n": 0}

    def fake_screener(scr_id, count=12):
        counter["n"] += 1
        return canned_screener_quotes(
            GAINERS if scr_id == "day_gainers" else LOSERS)
    monkeypatch.setattr(mb, "_fetch_screener", fake_screener)
    mm = mb.fetch_market_movers(data_root=tmp_path)
    assert mm["ok"] is True
    assert counter["n"] == 2                    # one call per side
    assert [m["symbol"] for m in mm["gainers"]] == [s for s, _ in GAINERS]
    assert [m["change_pct"] for m in mm["losers"]] == [p for _, p in LOSERS]
    for m in mm["gainers"] + mm["losers"]:
        assert set(m) == {"symbol", "name", "price", "change_pct"}
    assert (tmp_path / "cache" / "markets_movers.json").exists()
    # 120s TTL: second call is a cache hit, no refetch
    mm2 = mb.fetch_market_movers(data_root=tmp_path)
    assert mm2["cache_hit"] is True
    assert counter["n"] == 2


def test_fetch_market_movers_fail_soft(monkeypatch, tmp_path):
    def dead(scr_id, count=12):
        raise RuntimeError("screener gated")
    monkeypatch.setattr(mb, "_fetch_screener", dead)
    mm = mb.fetch_market_movers(data_root=tmp_path)
    assert mm["ok"] is False
    assert "error" in mm


def test_board_merges_market_and_watchlist_movers(monkeypatch, tmp_path):
    """Round-3 defect 2: the board carries BOTH strips — market_movers
    (whole market, symbols outside the registry) and watchlist_movers
    (the registry's own top-5, round-2 "movers" renamed; the old key
    stays as a back-compat alias)."""
    monkeypatch.setattr(mb, "fetch_market_movers",
                        lambda data_root="data": {
                            "ok": True,
                            "gainers": [{"symbol": "TOP",
                                         "name": "TOP Financial",
                                         "price": 16.24,
                                         "change_pct": 14.93}],
                            "losers": [{"symbol": "AAOI",
                                        "name": "Applied Optoelectronics",
                                        "price": 107.63,
                                        "change_pct": -13.77}]})
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        "BTC-USD": canned_chart(105.0, 100.0),   # +5.0 watchlist gainer
        "GC=F": canned_chart(97.0, 100.0),       # -3.0 watchlist loser
    }, default=canned_chart(100.5, 100.0)))
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    # whole-market strip present, with symbols OUTSIDE the registry
    assert out["market_movers"]["gainers"][0]["symbol"] == "TOP"
    assert out["market_movers"]["losers"][0]["symbol"] == "AAOI"
    board_syms = {r["symbol"] for s in out["sectors"] for r in s["rows"]}
    assert "TOP" not in board_syms and "AAOI" not in board_syms
    # watchlist strip renamed; old key kept as alias
    assert out["watchlist_movers"]["gainers"][0]["symbol"] == "BTC-USD"
    assert out["watchlist_movers"]["losers"][0]["symbol"] == "GC=F"
    assert out["movers"] == out["watchlist_movers"]


def test_board_watchlist_movers_when_screener_down(monkeypatch, tmp_path):
    """Screener unreachable (autouse fixture) → no market_movers key
    (fail-soft), the watchlist strip still served under both keys."""
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    out = mb.fetch_board(data_root=tmp_path)
    assert out["ok"] is True
    assert "market_movers" not in out
    assert len(out["watchlist_movers"]["gainers"]) == 5
    assert len(out["watchlist_movers"]["losers"]) == 5
    assert out["movers"] == out["watchlist_movers"]


# ------------------------------------------------- round-3: inverse FX
def test_normalize_inverse_pairs():
    """Round-3 defect 3: pair inputs resolve to the RECIPROCAL registry
    pair when the direct side isn't covered."""
    assert reg.normalize("inr/usd") == "USDINR=X"
    assert reg.normalize("inrusd") == "USDINR=X"       # AABBB form
    assert reg.normalize("INR-USD") == "USDINR=X"      # dash form
    assert reg.normalize("jpy/usd") == "USDJPY=X"
    assert reg.normalize("usd/eur") == "EURUSD=X"
    # direct slash forms stay direct
    assert reg.normalize("eur/usd") == "EURUSD=X"
    assert reg.normalize("usd/jpy") == "USDJPY=X"
    # aliases still beat the pair heuristic
    assert reg.normalize("xauusd") == "GC=F"
    assert reg.normalize("silver") == "SI=F"
    # no registry side → normalize stays None (ad-hoc is fetch_detail's
    # job); garbage stays garbage
    assert reg.normalize("jpy/eur") is None
    assert reg.normalize("zz/zzz") is None
    assert reg.normalize("zz-not-a-thing") is None


def test_resolve_pair_shapes():
    assert reg.resolve_pair("eur/usd") == ("EURUSD=X", False, True)
    assert reg.resolve_pair("inr/usd") == ("USDINR=X", True, True)
    assert reg.resolve_pair("jpy/eur") == ("JPYEUR=X", False, False)
    assert reg.resolve_pair("jpyeur") == ("JPYEUR=X", False, False)
    assert reg.resolve_pair("btc") is None
    assert reg.resolve_pair("") is None


def test_detail_inverse_pair_reciprocal_math(monkeypatch, tmp_path):
    """Round-3 defect 3: 'inr/usd' serves the INVERTED USDINR=X quote
    (price=1/price, change_pct=(1/p−1/q)/(1/q)·100 exactly, labeled
    'INR/USD (derived)')."""
    daily = canned_chart(price=95.717, prev=95.600, n=6, dp=5)
    weekly = canned_chart(price=95.717, prev=95.000, n=8, dp=5)
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("USDINR=X", "1d"): daily, ("USDINR=X", "5d"): weekly}))
    out = mb.fetch_detail("inr/usd", data_root=tmp_path)
    assert out["ok"] is True
    assert out["symbol"] == "USDINR=X"
    assert out["name"] == "INR/USD (derived)"
    assert out["derived"] is True
    assert out["derived_from"] == "USDINR=X"
    assert out["sector"] == "forex"
    assert out["currency"] == "USD"          # quote ccy of the derived pair
    # reciprocal math, one digit finer than the fetched pair
    assert out["price"] == round(1.0 / 95.717, 6)       # 0.010448
    assert out["prev_close"] == round(1.0 / 95.600, 6)  # 0.010460
    assert out["change_pct"] == round(
        (1.0 / 95.717 - 1.0 / 95.600) / (1.0 / 95.600) * 100, 2)  # -0.12
    # inverted bars: high/low swap under 1/x, still h >= l
    assert len(out["bars"]) == 8
    assert all(b["h"] >= b["l"] for b in out["bars"])
    assert out["bars"][0]["c"] == round(1.0 / 95.0, 6)
    # 5d change bar-derived on the INVERTED (published) series
    first_c = round(1.0 / 95.0, 6)
    last_c = round(1.0 / 95.717, 6)
    assert out["range_5d_change_pct"] == round(
        (last_c - first_c) / first_c * 100, 2)


def test_detail_adhoc_pair_reciprocal_of_better_quoted_side(
        monkeypatch, tmp_path):
    """jpy/eur: neither side in the registry — both directions are
    fetched and we anchor on the better-quoted side (Yahoo's inverse
    pairs publish ~4dp: JPYEUR=X 0.0054 vs EURJPY=X 169.5), serving
    the derived reciprocal."""
    weak = canned_chart(price=0.0054, prev=0.0054, n=6, dp=4)    # 2 sf
    strong_d = canned_chart(price=169.50, prev=169.20, n=6, dp=2)
    strong_w = canned_chart(price=169.50, prev=168.10, n=8, dp=2)
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("JPYEUR=X", "1d"): weak,
        ("EURJPY=X", "1d"): strong_d, ("EURJPY=X", "5d"): strong_w}))
    out = mb.fetch_detail("jpy/eur", data_root=tmp_path)
    assert out["ok"] is True
    assert out["symbol"] == "EURJPY=X"          # anchored on the fine side
    assert out["name"] == "JPY/EUR (derived)"
    assert out["derived"] is True
    assert out["price"] == round(1.0 / 169.50, 6)
    assert out["prev_close"] == round(1.0 / 169.20, 6)
    assert out["change_pct"] == round(
        (1.0 / 169.50 - 1.0 / 169.20) / (1.0 / 169.20) * 100, 2)


def test_detail_adhoc_pair_direct_when_better(monkeypatch, tmp_path):
    """eur/jpy: the direct side is the well-quoted one — served direct,
    no inversion, no derived flags."""
    strong_d = canned_chart(price=169.50, prev=169.20, n=6, dp=2)
    strong_w = canned_chart(price=169.50, prev=168.10, n=8, dp=2)
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("EURJPY=X", "1d"): strong_d, ("EURJPY=X", "5d"): strong_w,
        ("JPYEUR=X", "1d"): None}))       # reciprocal not served
    out = mb.fetch_detail("eur/jpy", data_root=tmp_path)
    assert out["ok"] is True
    assert out["symbol"] == "EURJPY=X"
    assert "derived" not in out
    assert out["price"] == 169.5
    assert out["change_pct"] == round((169.5 - 169.2) / 169.2 * 100, 2)


def test_detail_unknown_pair_fails_clean(monkeypatch, tmp_path):
    """A pair neither side of which resolves anywhere → clean fail-soft
    (both candidate directions raise)."""
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("ABCDEF=X", "1d"): None, ("DEFABC=X", "1d"): None}))
    out = mb.fetch_detail("abc/def", data_root=tmp_path)
    assert out["ok"] is False


# ---------------------------------------------------------------------- CLI
def test_cli_markets_json(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart()))
    rc = main(["markets", "--json", "--data-root", str(tmp_path)])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["ok"] is True
    assert len(payload["sectors"]) == 9
    assert set(payload["movers"]) == {"gainers", "losers"}


def test_cli_markets_board_shows_new_sectors_and_movers(
        monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    rc = main(["markets", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    # new round-2 sectors are printed
    assert "ETFS" in out
    assert "RATES & DOLLAR" in out
    assert "VOLATILITY" in out
    assert "^VIX" in out and "DX-Y.NYB" in out
    # movers section at the end (round-3: renamed WATCHLIST MOVERS)
    assert "WATCHLIST MOVERS" in out
    assert "gainers" in out and "losers" in out


def test_cli_markets_prints_both_movers_sections(
        monkeypatch, tmp_path, capsys):
    """Round-3: the CLI prints the whole-market strip (Yahoo screener)
    AND the watchlist strip, with no footer note when both are live."""
    from gold_desk.cli import main
    monkeypatch.setattr(mb, "fetch_market_movers",
                        lambda data_root="data": {
                            "ok": True,
                            "gainers": [{"symbol": "TOP",
                                         "name": "TOP Financial",
                                         "price": 16.24,
                                         "change_pct": 14.93}],
                            "losers": [{"symbol": "AAOI",
                                        "name": "AAOI Inc",
                                        "price": 107.63,
                                        "change_pct": -13.77}]})
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    rc = main(["markets", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "MARKET MOVERS" in out
    assert "WATCHLIST MOVERS" in out
    assert "TOP +14.93%" in out and "AAOI -13.77%" in out
    assert "whole-market movers need the Yahoo screener" not in out


def test_cli_markets_movers_footer_note_when_screener_down(
        monkeypatch, tmp_path, capsys):
    """Footer constraint note when the screener is unreachable (the
    autouse fixture makes it fail) — watchlist movers still shown."""
    from gold_desk.cli import main
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart(105.0, 100.0)))
    rc = main(["markets", "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "WATCHLIST MOVERS" in out
    assert "whole-market movers need the Yahoo screener" in out
    assert "showing watchlist movers" in out


def test_cli_markets_detail_derived_pair(monkeypatch, tmp_path, capsys):
    """The CLI prints the derived reciprocal at its finer precision
    and labels the derivation."""
    from gold_desk.cli import main
    daily = canned_chart(price=95.717, prev=95.600, n=6, dp=5)
    weekly = canned_chart(price=95.717, prev=95.000, n=8, dp=5)
    monkeypatch.setattr(mb, "_fetch_chart", fake_fetch(payloads={
        ("USDINR=X", "1d"): daily, ("USDINR=X", "5d"): weekly}))
    rc = main(["markets", "--symbol", "inr/usd",
               "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "INR/USD (derived)" in out
    assert "derived from: USDINR=X" in out
    assert "0.010447" in out          # 6dp derived price, not 0.0104
    assert "-0.12%" in out


def test_cli_markets_detail_and_bad_sector(monkeypatch, tmp_path, capsys):
    from gold_desk.cli import main
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(default=canned_chart()))
    rc = main(["markets", "--symbol", "gold",
               "--data-root", str(tmp_path)])
    captured = capsys.readouterr().out
    assert rc == 0
    assert "GC=F" in captured           # alias resolved + printed
    assert "change (1d)" in captured     # dual-range labels
    assert "change (5d)" in captured

    rc2 = main(["markets", "crypto", "--data-root", str(tmp_path)])
    out2 = capsys.readouterr().out
    assert rc2 == 0
    assert "BTC-USD" in out2

    rc3 = main(["markets", "fiber_optics", "--data-root", str(tmp_path)])
    out3 = capsys.readouterr().out
    assert rc3 == 1
    assert "unknown sector" in out3


def test_cli_markets_detail_fx_precision(monkeypatch, tmp_path, capsys):
    """The CLI must PRINT the pips it now keeps (round-1 showed 1.17)."""
    from gold_desk.cli import main
    fx = canned_chart(price=1.165909, prev=1.164871, n=6, dp=5)
    monkeypatch.setattr(mb, "_fetch_chart",
                        fake_fetch(payloads={"EURUSD=X": fx}))
    rc = main(["markets", "--symbol", "eurusd",
               "--data-root", str(tmp_path)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "1.16591" in out
    assert "1.17 " not in out
