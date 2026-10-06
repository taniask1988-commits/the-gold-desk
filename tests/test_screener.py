"""GAUNTLET 7 Piece 3 — screener tests. Fully offline: the technical and
fundamental seams are monkeypatched with synthetic universes; no network.
"""
from __future__ import annotations

import json

import pytest

from gold_desk.markets import screener as sc


def _universe_rows():
    """Synthetic 4-symbol universe with known field values."""
    return {
        "CHEAP": {"price": 90.0, "rsi": 25.0, "sma200": 100.0,
                  "sma50": 95.0, "sma20": 92.0, "atr_pct": 1.2,
                  "change_30d": -8.0, "realized_vol": 0.2,
                  "above_sma200": False, "revenue_growth": 12.0,
                  "net_income_margin": 9.0, "fcf_margin": 7.0,
                  "quarters": 8},
        "GROWER": {"price": 210.0, "rsi": 55.0, "sma200": 150.0,
                   "sma50": 190.0, "sma20": 200.0, "atr_pct": 2.0,
                   "change_30d": 14.0, "realized_vol": 0.35,
                   "above_sma200": True, "revenue_growth": 35.0,
                   "net_income_margin": 22.0, "fcf_margin": 19.0,
                   "quarters": 8},
        "NORULES": {"price": 55.0, "rsi": 50.0, "sma200": 54.0,
                    "sma50": 55.0, "sma20": 55.0, "atr_pct": 0.9,
                    "change_30d": 0.5, "realized_vol": 0.12,
                    "above_sma200": True, "revenue_growth": None,
                    "net_income_margin": None, "fcf_margin": None,
                    "quarters": 0},
        "MOMO": {"price": 310.0, "rsi": 72.0, "sma200": 200.0,
                 "sma50": 260.0, "sma20": 300.0, "atr_pct": 3.0,
                 "change_30d": 22.0, "realized_vol": 0.5,
                 "above_sma200": True, "revenue_growth": 8.0,
                 "net_income_margin": -5.0, "fcf_margin": 2.0,
                 "quarters": 8},
    }


SYMBOLS = list(_universe_rows())


@pytest.fixture()
def offline_universe(monkeypatch, tmp_path):
    rows = _universe_rows()
    monkeypatch.setattr(sc, "all_symbols",
                        lambda: [{"symbol": s, "name": s, "sector": "test"}
                                 for s in SYMBOLS])
    monkeypatch.setattr(sc, "_technical_fields",
                        lambda sym, dr: dict(rows[sym]))
    monkeypatch.setattr(sc, "_fundamental_fields",
                        lambda sym, dr: {k: rows[sym][k] for k in
                                         ("revenue_growth",
                                          "net_income_margin", "fcf_margin",
                                          "quarters")})
    return tmp_path


# ------------------------------------------------------------------- DSL
def test_dsl_composition_and_or_not(offline_universe):
    # the charter's exact example shape: RSI + revenue growth + SMA
    out = sc.screen("rsi < 30 and revenue_growth > 10 and price > sma200",
                    data_root=offline_universe)
    assert out["n_matched"] == 0  # CHEAP has rsi<30+growth but price<sma200
    out = sc.screen("rsi < 30 or (revenue_growth > 30 and above_sma200)",
                    data_root=offline_universe)
    got = {r["symbol"] for r in out["rows"]}
    assert got == {"CHEAP", "GROWER"}
    out = sc.screen("not above_sma200 and rsi < 40",
                    data_root=offline_universe)
    assert {r["symbol"] for r in out["rows"]} == {"CHEAP"}


def test_dsl_missing_field_fails_row_reports_it(offline_universe):
    out = sc.screen("revenue_growth > 10", data_root=offline_universe)
    got = {r["symbol"] for r in out["rows"]}
    assert got == {"CHEAP", "GROWER"}   # NORULES growth None → excluded


def test_dsl_rejects_dangerous_constructs():
    for bad in ['__import__("os").system("ls")', "rsi.__class__",
                "lambda: 1", "price + 1 > 2", "[x for x in y]",
                "f({})" if False else "price(1)", "'a' * 10 == 'aa'"]:
        with pytest.raises(ValueError):
            sc.parse_where(bad)


def test_dsl_rejects_unknown_field():
    with pytest.raises(ValueError, match="unknown field"):
        sc.parse_where("mkt_cap > 1e9")


def test_dsl_negative_numbers_and_chained_compare():
    passed, _ = sc.evaluate_where("revenue_growth > -50 and rsi < 100",
                                  {"revenue_growth": -10.0, "rsi": 55.0})
    assert passed is True
    passed2, _ = sc.evaluate_where("30 < rsi < 60", {"rsi": 55.0})
    assert passed2 is True


def test_dsl_syntax_error_raises():
    with pytest.raises(ValueError, match="syntax error"):
        sc.parse_where("rsi <")


# ------------------------------------------------------- universe routing
def test_screen_never_fetches_unresolved_symbols(monkeypatch, tmp_path):
    """Universe contract: only registry symbols are ever probed."""
    seen: list[str] = []
    registry = [{"symbol": s, "name": s, "sector": "test"} for s in
                ("AAA", "BBB")]
    monkeypatch.setattr(sc, "all_symbols", lambda: registry)
    monkeypatch.setattr(sc, "_technical_fields",
                        lambda sym, dr: seen.append(sym) or
                        {"price": 10.0, "rsi": 40.0})
    monkeypatch.setattr(sc, "_fundamental_fields",
                        lambda sym, dr: {"revenue_growth": None,
                                         "quarters": 0})
    out = sc.screen("rsi < 50", data_root=tmp_path)
    assert seen == ["AAA", "BBB"]          # exactly the registry, nothing else
    assert out["n_symbols"] == 2


def test_screen_fail_soft_per_symbol(monkeypatch, tmp_path):
    monkeypatch.setattr(sc, "all_symbols",
                        lambda: [{"symbol": s, "name": s, "sector": "t"}
                                 for s in ("GOOD", "DEAD")])
    def _tech(sym, dr):
        if sym == "DEAD":
            raise RuntimeError("feed down")
        return {"price": 10.0, "rsi": 40.0}
    monkeypatch.setattr(sc, "_technical_fields", _tech)
    monkeypatch.setattr(sc, "_fundamental_fields",
                        lambda sym, dr: {"revenue_growth": None,
                                         "quarters": 0})
    out = sc.screen("rsi < 50", data_root=tmp_path)
    assert out["ok"] is True and out["n_errors"] == 1
    assert out["errors_sample"][0]["symbol"] == "DEAD"
    assert {r["symbol"] for r in out["rows"]} == {"GOOD"}


def test_screen_bad_filter_fails_fast(monkeypatch, tmp_path):
    with pytest.raises(ValueError):
        sc.screen("__import__('os')", data_root=tmp_path)


# --------------------------------------------------------------- S&P 500
def test_sp500_parser_and_cache(monkeypatch, tmp_path):
    html = ("<table><tr><td><a title=\"AAPL\" href=\"/wiki/AAPL\">x</a>"
            "</td></tr>" * 5 + "<tr><td><a title=\"MSFT\">y</a></td></tr>"
            "</table>")
    calls = {"n": 0}

    class FakeResp:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return html.encode()

    def fake_urlopen(req, timeout=15):
        calls["n"] += 1
        return FakeResp()

    import urllib.request as _ur
    import gold_desk.markets.screener as sc_mod
    monkeypatch.setattr(_ur, "urlopen", fake_urlopen)
    # the sanity gate wants >=400 symbols; bypass by patching the gate
    monkeypatch.setattr(sc_mod, "_sp500_symbols",
                        lambda dr: sc._sp500_symbols_raw(dr, min_count=1))
    syms = sc_mod._sp500_symbols(tmp_path)
    assert set(syms) == {"AAPL", "MSFT"}
    assert calls["n"] == 1
    # second call is served from the 7d cache — no second fetch
    syms2 = sc_mod._sp500_symbols(tmp_path)
    assert calls["n"] == 1 and syms2 == syms


# ------------------------------------------------------------------- CLI
def test_cli_screen_json_contract(offline_universe, capsys):
    from gold_desk.cli import main
    rc = main(["screen", "--where", "revenue_growth > 30",
               "--json", "--data-root", str(offline_universe)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True and out["n_matched"] == 1
    assert out["rows"][0]["symbol"] == "GROWER"


def test_cli_screen_bad_filter_exit_1(tmp_path, capsys):
    from gold_desk.cli import main
    rc = main(["screen", "--where", "__import__('os')",
               "--json", "--data-root", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False and out["errors"]
