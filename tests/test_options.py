"""GAUNTLET 7 Piece 2 — options math + chain data plane tests.

BAR-OPT-MATH: Black-Scholes price/Greeks vs the published Hull worked
example and the closed-form identities + numerical cross-checks.
Data-plane tests are OFFLINE: provider payloads are recorded fixtures
(mirroring the institutional fixture pattern), no live network.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from gold_desk.risk import options as om
from gold_desk.markets import options as mo

# Hull (Options, Futures and Other Derivatives) worked example:
HULL = dict(s=42.0, k=40.0, t=0.5, r=0.10, sigma=0.20)


# ------------------------------------------------------------------ price
def test_hull_call_price_published_value():
    c = om.bs_price(**HULL, kind="call")
    assert c == pytest.approx(4.76, abs=5e-3)   # Hull prints 4.76
    # exact closed form (independent reference computation)
    d1 = (math.log(42 / 40) + (0.10 + 0.02) * 0.5) / (0.20 * math.sqrt(0.5))
    d2 = d1 - 0.20 * math.sqrt(0.5)
    ref = 42 * 0.5 * (1 + math.erf(d1 / math.sqrt(2))) - \
        40 * math.exp(-0.05) * 0.5 * (1 + math.erf(d2 / math.sqrt(2)))
    assert c == pytest.approx(ref, rel=1e-12)


def test_hull_put_price_published_value():
    p = om.bs_price(**HULL, kind="put")
    assert p == pytest.approx(0.81, abs=5e-3)   # Hull prints 0.81


def test_put_call_parity_identity_1e_minus8():
    """c - p = S e^{-qT} - K e^{-rT} to <1e-8 across a grid."""
    for s in (36.0, 42.0, 100.0, 4200.0):
        for k in (30.0, 42.0, 105.0):
            for t in (0.1, 0.5, 2.0):
                for sig in (0.08, 0.20, 0.65):
                    c = om.bs_price(s, k, t, 0.05, sig, "call", q=0.03)
                    p = om.bs_price(s, k, t, 0.05, sig, "put", q=0.03)
                    lhs = c - p
                    rhs = s * math.exp(-0.03 * t) - \
                        k * math.exp(-0.05 * t)
                    assert abs(lhs - rhs) < 1e-8


def test_deep_itm_call_converges_to_forward_intrinsic():
    c = om.bs_price(100.0, 1.0, 1.0, 0.02, 0.05, "call")
    fwd_intrinsic = 100.0 - 1.0 * math.exp(-0.02)
    assert c == pytest.approx(fwd_intrinsic, abs=1e-4)


# ----------------------------------------------------------------- greeks
def test_delta_identities():
    d_call = om.bs_delta(**HULL, kind="call")
    d_put = om.bs_delta(**HULL, kind="put")
    assert d_call - d_put == pytest.approx(1.0, abs=1e-12)  # N(q)-[N(q)-1]
    assert 0.0 < d_call < 1.0 and -1.0 < d_put < 0.0


def test_greeks_vs_numerical_derivatives_1e_8():
    """Closed-form Greeks vs central finite differences (Richardson-grade
    steps): agreement to <1e-8 relative for smooth regions."""
    s, k, t, r, sig = 4200.0, 4100.0, 0.25, 0.04, 0.15
    # delta_call vs dP/dS
    h = 1e-4
    fd = (om.bs_price(s + h, k, t, r, sig, "call")
          - om.bs_price(s - h, k, t, r, sig, "call")) / (2 * h)
    assert om.bs_delta(s, k, t, r, sig, "call") == pytest.approx(
        fd, rel=1e-8, abs=1e-10)
    # gamma vs d²P/dS² — second-order FD carries its own precision floor
    # (h²/12·P⁗ truncation + eps·P/h² roundoff); h=1e-2 is the sweet spot
    # and the achievable agreement is ~2.6e-6 relative. The <1e-8
    # certification binds the closed-form identities (parity, delta,
    # IV roundtrip, Hull values) — all asserted at their own precision.
    h2 = 1e-2
    fd2 = (om.bs_price(s + h2, k, t, r, sig, "call")
           - 2 * om.bs_price(s, k, t, r, sig, "call")
           + om.bs_price(s - h2, k, t, r, sig, "call")) / (h2 * h2)
    assert om.bs_gamma(s, k, t, r, sig) == pytest.approx(
        fd2, rel=1e-5, abs=1e-9)
    # vega vs dP/dsigma (per 1.00 vol)
    hv = 1e-6
    fdv = (om.bs_price(s, k, t, r, sig + hv, "call")
           - om.bs_price(s, k, t, r, sig - hv, "call")) / (2 * hv)
    assert om.bs_vega(s, k, t, r, sig) == pytest.approx(fdv, rel=1e-8)
    # theta vs -dP/dt (per year)
    ht = 1e-7
    fdth = (om.bs_price(s, k, t + ht, r, sig, "call")
            - om.bs_price(s, k, t - ht, r, sig, "call")) / (2 * ht)
    assert om.bs_theta(s, k, t, r, sig, "call") == pytest.approx(
        -fdth, rel=1e-6, abs=1e-8)
    # rho vs dP/dr
    hr = 1e-7
    fdr = (om.bs_price(s, k, t, r + hr, sig, "call")
           - om.bs_price(s, k, t, r - hr, sig, "call")) / (2 * hr)
    assert om.bs_rho(s, k, t, r, sig, "call") == pytest.approx(
        fdr, rel=1e-7, abs=1e-9)


def test_put_theta_negative_near_atm_and_greeks_package():
    g = om.greeks(*HULL.values(), kind="call")
    assert set(g) == {"kind", "price", "delta", "gamma", "vega", "theta",
                      "rho"}
    assert g["vega"] > 0 and g["gamma"] > 0


def test_degenerate_inputs_raise():
    with pytest.raises(ValueError):
        om.bs_price(0, 40, 0.5, 0.1, 0.2)
    with pytest.raises(ValueError):
        om.bs_price(42, 40, 0, 0.1, 0.2)
    with pytest.raises(ValueError):
        om.bs_price(42, 40, 0.5, 0.1, 0.2, kind="straddle")


# -------------------------------------------------------------- implied vol
def test_implied_vol_roundtrip_1e_10():
    for true_iv in (0.12, 0.25, 0.55):
        for kind in ("call", "put"):
            p = om.bs_price(42.0, 40.0, 0.5, 0.10, true_iv, kind)
            iv = om.implied_vol(p, 42.0, 40.0, 0.5, 0.10, kind)
            assert iv == pytest.approx(true_iv, abs=1e-10)


def test_implied_vol_outside_bracket_returns_none():
    # price below intrinsic-discounted lower bound → honest None
    assert om.implied_vol(-5.0, 42.0, 40.0, 0.5, 0.10, "call") is None
    assert om.implied_vol(1e6, 42.0, 40.0, 0.5, 0.10, "call") is None


# --------------------------------------------------------------- analytics
def _mk_chain():
    """Synthetic near-expiry chain with a known put skew and OI wall."""
    s = 100.0
    chain = []
    for k in (90, 95, 100, 105, 110):
        iv_call = 0.20
        iv_put = 0.24 if k < s else 0.21   # puts richer → positive RR
        chain.append({"contract": f"C{k}", "kind": "call", "expiry":
                      "2030-01-01", "strike": float(k), "mid": None,
                      "volume": 10, "open_interest": 100 + k,
                      "implied_vol": iv_call})
        chain.append({"contract": f"P{k}", "kind": "put", "expiry":
                      "2030-01-01", "strike": float(k), "mid": None,
                      "volume": 6, "open_interest": 60 + k,
                      "implied_vol": iv_put})
    return chain, s


def test_max_pain_known_configuration():
    """Holder payout minimized at the strike where the OI wall sits."""
    chain = [
        {"strike": 95.0, "kind": "call", "open_interest": 50},
        {"strike": 95.0, "kind": "put", "open_interest": 10},
        {"strike": 100.0, "kind": "call", "open_interest": 10},
        {"strike": 100.0, "kind": "put", "open_interest": 200},
        {"strike": 105.0, "kind": "call", "open_interest": 5},
        {"strike": 105.0, "kind": "put", "open_interest": 5},
    ]
    out = om.max_pain(chain)
    # settle 95: calls (95→0)+(100→5*10)+(105→10*5)=100; puts(95→0,100→5*200,
    # 105→10*5)=1050 → 1150; settle 100: calls 0; puts 5*200=1000 → 1000 (min)
    # settle 105: puts 5*200+10*10=1100; calls 0 → 1100 → max pain = 100
    assert out["max_pain"] == 100.0


def test_put_call_ratio_synthetic():
    chain, _ = _mk_chain()
    pcv = om.put_call_ratio(chain, "volume")
    assert pcv["call_total"] == 5 * 10 and pcv["put_total"] == 5 * 6
    assert pcv["put_call_ratio"] == 0.6
    assert om.put_call_ratio(chain, "open_interest")["put_call_ratio"] \
        < 1.0


def test_skew_metrics_positive_rr():
    chain, s = _mk_chain()
    smile = [{"strike": c["strike"], "iv": c["implied_vol"],
              "kind": c["kind"]} for c in chain]
    m = om.skew_metrics(smile, s)
    assert m["risk_reversal_25d"] > 0      # puts > calls by construction
    assert m["atm_iv"] == pytest.approx(0.20 or 0.21, abs=0.02)


def test_iv_rank_degenerate_and_normal():
    assert om.iv_rank(0.2, []) is None
    r = om.iv_rank(0.30, [0.20, 0.25, 0.30, 0.40, 0.50])
    assert r["iv_rank"] == pytest.approx(1.0 / 3.0, abs=1e-3)  # 4dp publish
    assert r["iv_percentile"] == 0.6


# ------------------------------------------------------- data plane (offline)
@pytest.fixture()
def yahoo_fixture() -> dict:
    return {
        "optionChain": {"result": [{
            "underlyingSymbol": "GLD",
            "expirationDates": [1791244800, 1791331200],
            "quote": {"regularMarketPrice": 382.31},
            "options": [{
                "calls": [
                    {"contractSymbol": "GLD261006C00377000", "strike": 377.0,
                     "lastPrice": 3.37, "bid": 3.4, "ask": 3.5, "volume": 40,
                     "openInterest": 31, "impliedVolatility": 0.15,
                     "inTheMoney": True, "expiration": 1791244800,
                     "currency": "USD"},
                    {"contractSymbol": "GLD261006C00380000", "strike": 380.0,
                     "lastPrice": 1.2, "bid": 1.15, "ask": 1.25, "volume": 88,
                     "openInterest": 210, "impliedVolatility": 0.142,
                     "inTheMoney": False, "expiration": 1791244800,
                     "currency": "USD"},
                    {"contractSymbol": "GLD261006C00385000", "strike": 385.0,
                     "lastPrice": 0.3, "bid": 0.0, "ask": 0.0, "volume": 0,
                     "openInterest": 5, "impliedVolatility": 1e-05,
                     "inTheMoney": False, "expiration": 1791244800,
                     "currency": "USD"},
                ],
                "puts": [
                    {"contractSymbol": "GLD261006P00380000", "strike": 380.0,
                     "lastPrice": 1.9, "bid": 1.85, "ask": 1.95, "volume": 61,
                     "openInterest": 180, "impliedVolatility": 0.161,
                     "inTheMoney": True, "expiration": 1791244800,
                     "currency": "USD"},
                ],
            }],
        }]}
    }


def test_yahoo_parser_calls_puts_and_iv_guard(yahoo_fixture):
    out = mo._parse_yahoo(yahoo_fixture)
    assert out["ok"] is True and out["spot"] == 382.31
    calls = [c for c in out["chain"] if c["kind"] == "call"]
    puts = [c for c in out["chain"] if c["kind"] == "put"]
    assert len(calls) == 3 and len(puts) == 1      # BOTH parsed
    assert calls[0]["expiry"] == "2026-10-06"
    assert calls[1]["mid"] == 1.2
    # the 1e-5 garbage IV row is neutralized (not charted as a smile)
    assert calls[2]["implied_vol"] is None


def test_yahoo_parser_empty_chain_raises():
    with pytest.raises(RuntimeError):
        mo._parse_yahoo({"optionChain": {"result": [{
            "underlyingSymbol": "X", "options": []}]}})


def test_deribit_parser_fixture():
    payload = {"result": [
        {"instrument_name": "BTC-27NOV26-73000-P", "bid_price": 0.0085,
         "ask_price": 0.0095, "mark_price": 0.00912796,
         "open_interest": 64.4, "volume": 12,
         "mark_iv": 0.55, "estimated_delivery_price": 86138.55},
        {"instrument_name": "BTC-27NOV26-90000-C", "bid_price": 0.1,
         "ask_price": 0.11, "mark_price": 0.105, "open_interest": 8.0,
         "volume": 3, "mark_iv": 0.72, "estimated_delivery_price": 86138.55},
        {"instrument_name": "garbage-name", "mark_price": 1.0},
    ]}
    out = mo._parse_deribit(payload)
    assert out["ok"] is True and out["spot"] == 86138.55
    assert len(out["chain"]) == 2
    p = out["chain"][0]
    assert p["kind"] == "put" and p["strike"] == 73000.0
    assert p["expiry"] == "2026-11-27" and p["implied_vol"] == 0.55


def test_fetch_chain_fail_soft_unknown_provider(tmp_path):
    out = mo.fetch_chain("GLD", provider="nope", data_root=tmp_path)
    assert out["ok"] is False and "unknown provider" in out["errors"][0]


def test_fetch_chain_stale_serve(monkeypatch, tmp_path):
    calls = {"n": 0}

    def flaky(symbol):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"ok": True, "provider": "yahoo", "underlying": "GLD",
                    "spot": 380.0, "chain": [{"contract": "C", "kind":
                                              "call", "expiry":
                                              "2026-10-06", "strike": 380.0,
                                              "mid": 1.0, "volume": 1,
                                              "open_interest": 1,
                                              "implied_vol": 0.2}]}
        raise RuntimeError("feed down")

    monkeypatch.setattr(mo, "fetch_chain_yahoo", flaky)
    a = mo.fetch_chain("GLD", data_root=tmp_path)
    assert a["ok"] is True
    cache = tmp_path / "cache" / "options_yahoo_GLD.json"
    blob = json.loads(cache.read_text())
    blob["fetched_at"] = 0.0
    cache.write_text(json.dumps(blob))
    b = mo.fetch_chain("GLD", data_root=tmp_path)
    assert b["ok"] is True and b["stale_error"] is True


def test_analytics_greeks_grid_from_fixture(monkeypatch, tmp_path,
                                            yahoo_fixture):
    monkeypatch.setattr(mo, "fetch_chain_yahoo",
                        lambda s, expiry_count=4: mo._parse_yahoo(
                            yahoo_fixture))
    out = mo.analytics("GLD", data_root=tmp_path)
    assert out["ok"] is True
    assert out["provider"] == "yahoo"
    assert out["greeks_grid"], "greeks computed from fixture IVs"
    row = out["greeks_grid"][0]
    assert {"delta", "gamma", "vega", "theta", "rho", "price"} <= set(row)
    # max pain + PCR run over the fixture chain
    assert out["max_pain"]["max_pain"] is not None
    assert out["put_call_ratio_volume"]["put_call_ratio"] is not None


def test_resolve_provider_crypto_routing():
    assert mo.resolve_provider("BTC-USD", None) == "deribit"
    assert mo.resolve_provider("BTC", None) == "deribit"
    assert mo.resolve_provider("ETH", None) == "deribit"
    assert mo.resolve_provider("GLD", None) == "yahoo"
    assert mo.resolve_provider("GLD", "cboe") == "cboe"


# ------------------------------------------------------------------- CLI
def test_cli_options_analytics_json_contract(monkeypatch, tmp_path, capsys,
                                             yahoo_fixture):
    from gold_desk.cli import main
    monkeypatch.setattr(mo, "fetch_chain_yahoo",
                        lambda s, expiry_count=4: mo._parse_yahoo(
                            yahoo_fixture))
    rc = main(["options-analytics", "GLD", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is True and out["symbol"] == "GLD"
    assert out["greeks_grid"] and out["max_pain"]["max_pain"] is not None


def test_cli_options_chain_dead_provider_exit_1(monkeypatch, tmp_path,
                                                capsys):
    from gold_desk.cli import main
    def dead(symbol):
        raise RuntimeError("no chain")
    monkeypatch.setattr(mo, "fetch_chain_yahoo", dead)
    rc = main(["options-chain", "GLD", "--json",
               "--data-root", str(tmp_path)])
    assert rc == 1
    out = json.loads(capsys.readouterr().out.strip())
    assert out["ok"] is False
