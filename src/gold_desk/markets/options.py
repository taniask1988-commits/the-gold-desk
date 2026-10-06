"""GAUNTLET 7 Piece 2 — keyless options-chain data plane (display layer).

Providers (all keyless, probed live 2026-10):
  yahoo   : v7/finance/options with the public cookie+crumb session dance
            (no account, no key — the same public web-session class the
            board's v8/chart path uses)
  deribit : public get_book_summary_by_currency (BTC/ETH options, 24/7)
  cboe    : cdn.cboe.com delayed_quotes options JSON (may be IP-blocked
            in some environments — fail-soft, honest errors[])

Every fetch: short timeout, TTL cache, stale-serve on error, failures in
errors[]. Analytics (Greeks/max pain/PCR/IV rank/skew) are computed in
risk/options.py from whatever chain a provider serves.

Law boundary: display/education telemetry — NOT wired into the
orchestrator's decision loop.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

from ..risk import options as om

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) gold-desk/1.0"
YAHOO_OPTIONS_URL = ("https://query1.finance.yahoo.com/v7/finance/options/"
                     "{symbol}")
DERIBIT_URL = ("https://www.deribit.com/api/v2/public/"
               "get_book_summary_by_currency?currency={currency}&kind=option")
CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_{symbol}.json"

CHAIN_TTL_S = 300          # 5 minutes
CRYPTO_SYMBOLS = {"BTC", "ETH", "SOL", "XRP"}


# ----------------------------------------------------------------- http
def _http_get(url: str, timeout: float = 10.0,
              headers: dict | None = None) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, text/xml, */*",
        **(headers or {}),
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _cached_fetch(data_root: str | Path, name: str, ttl: int, fetch) -> dict:
    """House cache-through fetch: fresh → fetch → STALE-SERVE on error."""
    d = Path(data_root)
    (d / "cache").mkdir(parents=True, exist_ok=True)
    path = d / "cache" / name
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < ttl:
        cached["cached"] = True
        return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["stale_error"] = False
        path.write_text(json.dumps(fresh))
        return fresh
    except Exception as exc:  # noqa: BLE001 — stale-serve, never raise
        if cached:
            out = dict(cached)
            out["cached"] = False
            out["stale_error"] = True
            out["stale_reason"] = f"{type(exc).__name__}: {exc}"
            return out
        return {"ok": False, "errors": [f"{type(exc).__name__}: {exc}"],
                "fetched_at": time.time(), "stale_error": False}


# ------------------------------------------------------------ providers
def _yahoo_session_opener():
    """Public session dance: fc.yahoo.com drops the cookie, getcrumb
    echoes the crumb. No account, no key. The SAME opener must carry
    the cookie into the options request (a bare urlopen with a manual
    Cookie header gets 401 — the jar context matters)."""
    import http.cookiejar
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(jar))
    req0 = urllib.request.Request(
        "https://fc.yahoo.com", headers={"User-Agent": USER_AGENT})
    try:
        opener.open(req0, timeout=8).read()
    except Exception:  # noqa: BLE001 — fc.yahoo.com 404s AFTER setting
        pass                     # the cookie; the cookie is what matters
    if not jar:
        raise RuntimeError("no yahoo session cookie granted")
    req1 = urllib.request.Request(
        "https://query1.finance.yahoo.com/v1/test/getcrumb",
        headers={"User-Agent": USER_AGENT})
    with opener.open(req1, timeout=8) as resp:
        crumb = resp.read().decode().strip()
    if not crumb:
        raise RuntimeError("empty yahoo crumb")
    return opener, crumb


def _iso_expiry(epoch_seconds: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(
        int(epoch_seconds), tz=_dt.timezone.utc).strftime("%Y-%m-%d")


def _parse_yahoo(payload: dict) -> dict:
    result = (payload.get("optionChain") or {}).get("result") or []
    if not result:
        raise RuntimeError("empty yahoo optionChain")
    r0 = result[0]
    opts = r0.get("options") or []
    if not opts:
        raise RuntimeError("yahoo optionChain has no options group")
    exp_map = {int(ts): _iso_expiry(ts)
               for ts in (r0.get("expirationDates") or [])}
    chain: list[dict] = []
    # calls and puts BOTH live inside options[0] (the options array has
    # one element per fetched expiry-date request, not call/put groups)
    for grp, kind in ((opts[0].get("calls") or [], "call"),
                      (opts[0].get("puts") or [], "put")):
        for o in grp:
            exp = exp_map.get(int(o.get("expiration") or 0), "")
            bid, ask = o.get("bid"), o.get("ask")
            mid = round((bid + ask) / 2.0, 4) \
                if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) \
                and bid >= 0 and ask >= 0 and (bid + ask) > 0 else None
            if mid is None:
                lp = o.get("lastPrice")
                mid = float(lp) if isinstance(lp, (int, float)) and lp > 0 \
                    else None
            iv = (float(o["impliedVolatility"])
                  if o.get("impliedVolatility") else None)
            # Yahoo emits ~1e-5 garbage IV on deep-ITM/no-quote rows —
            # keep the number out of the surface (honest gap) rather
            # than charting a fake smile point
            if iv is not None and not (0.005 <= iv <= 5.0):
                iv = None
            chain.append({
                "contract": o.get("contractSymbol", ""),
                "kind": kind,
                "expiry": exp,
                "strike": float(o.get("strike") or 0),
                "bid": bid, "ask": ask, "mid": mid,
                "last": o.get("lastPrice"),
                "volume": o.get("volume") or 0,
                "open_interest": o.get("openInterest") or 0,
                "implied_vol": iv,
                "in_the_money": bool(o.get("inTheMoney")),
                "currency": o.get("currency", "USD"),
            })
    if not chain:
        raise RuntimeError("yahoo served an empty chain")
    spot = ((r0.get("quote") or {}).get("regularMarketPrice"))
    return {"ok": True, "provider": "yahoo", "underlying":
            r0.get("underlyingSymbol"), "spot": spot, "chain": chain}


def fetch_chain_yahoo(symbol: str, expiry_count: int = 4) -> dict:
    """Front expiry by default; `expiry_count` merges the nearest N
    expiries (one extra request each) so the surface/OI wall have depth.
    """
    opener, crumb = _yahoo_session_opener()

    def _get(url: str) -> dict:
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT, "Accept": "application/json"})
        with opener.open(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8", "replace"))

    first = _parse_yahoo(
        _get(f"{YAHOO_OPTIONS_URL.format(symbol=symbol)}?crumb={crumb}"))
    if expiry_count <= 1:
        return first
    # expirationDates on the first payload lists every listed expiry
    try:
        dates = _get(f"{YAHOO_OPTIONS_URL.format(symbol=symbol)}"
                     f"?crumb={crumb}")
        r0 = (dates.get("optionChain") or {}).get("result") or [{}]
        all_ts = [int(t) for t in (r0[0].get("expirationDates") or [])]
    except Exception:  # noqa: BLE001 — multi-expiry is best-effort
        all_ts = []
    merged = list(first["chain"])
    seen = {c["contract"] for c in merged}
    for ts in all_ts[1:expiry_count]:
        try:
            nxt = _parse_yahoo(
                _get(f"{YAHOO_OPTIONS_URL.format(symbol=symbol)}"
                     f"?date={ts}&crumb={crumb}"))
        except Exception:  # noqa: BLE001 — fail-soft per expiry
            continue
        for c in nxt["chain"]:
            if c["contract"] not in seen:
                merged.append(c)
                seen.add(c["contract"])
    if not merged:
        return first
    return {**first, "chain": merged,
            "expiries_fetched": min(expiry_count, max(len(all_ts), 1))}


def _parse_deribit(payload: dict) -> dict:
    rows = payload.get("result") or []
    if not rows:
        raise RuntimeError("empty deribit book summary")
    chain: list[dict] = []
    spot = None
    for r in rows:
        name = r.get("instrument_name", "")  # e.g. BTC-27NOV26-73000-P
        m = re.match(r"^([A-Z]+)-(\d{1,2}[A-Z]{3}\d{2})-(\d+)-([CP])$", name)
        if not m:
            continue
        _, exp_raw, strike_raw, kind_ch = m.groups()
        import datetime as _dt
        try:
            exp = _dt.datetime.strptime(exp_raw, "%d%b%y").strftime("%Y-%m-%d")
        except ValueError:
            continue
        mark = r.get("mark_price")
        iv = r.get("mark_iv")           # already a fraction (0.0-1.0+)
        if spot is None and r.get("estimated_delivery_price"):
            spot = float(r["estimated_delivery_price"])
        chain.append({
            "contract": name, "kind": "call" if kind_ch == "C" else "put",
            "expiry": exp, "strike": float(strike_raw),
            "bid": r.get("bid_price"), "ask": r.get("ask_price"),
            "mid": round(mark, 8) if isinstance(mark, (int, float)) else None,
            "last": r.get("last"),
            "volume": r.get("volume") or 0,
            "open_interest": r.get("open_interest") or 0,
            "implied_vol": float(iv) if isinstance(iv, (int, float)) else None,
            "in_the_money": None, "currency": "USD",
        })
    if not chain:
        raise RuntimeError("deribit served no parseable instruments")
    return {"ok": True, "provider": "deribit", "underlying": None,
            "spot": spot, "chain": chain}


def fetch_chain_deribit(currency: str = "BTC") -> dict:
    raw = _http_get(DERIBIT_URL.format(currency=currency.upper()))
    return _parse_deribit(json.loads(raw))


def fetch_chain_cboe(symbol: str) -> dict:
    raw = _http_get(CBOE_URL.format(symbol=symbol.upper().lstrip("^")))
    payload = json.loads(raw)
    data = payload.get("data") or {}
    rows = data.get("options") or []
    if not rows:
        raise RuntimeError("cboe served no options")
    # series names look like GLD261016C00380000
    rx = re.compile(r"^(?P<root>[A-Z]+)(?P<exp>\d{6})(?P<kind>[CP])"
                    r"(?P<strike>\d{8})$")
    chain: list[dict] = []
    for o in rows:
        m = rx.match(o.get("option", ""))
        if not m:
            continue
        import datetime as _dt
        exp = _dt.datetime.strptime(m["exp"], "%y%m%d").strftime("%Y-%m-%d")
        strike = int(m["strike"]) / 1000.0
        bid, ask = o.get("bid"), o.get("ask")
        mid = round((bid + ask) / 2.0, 4) \
            if isinstance(bid, (int, float)) and isinstance(ask, (int, float)) \
            and bid >= 0 and ask >= 0 and (bid + ask) > 0 else None
        chain.append({
            "contract": o.get("option", ""), "kind":
                "call" if m["kind"] == "C" else "put",
            "expiry": exp, "strike": strike,
            "bid": bid, "ask": ask, "mid": mid, "last": o.get("last_trade_price"),
            "volume": o.get("volume") or 0,
            "open_interest": o.get("open_interest") or 0,
            "implied_vol": (float(o["iv"]) / 100.0)
            if isinstance(o.get("iv"), (int, float)) else None,
            "in_the_money": None, "currency": "USD",
        })
    return {"ok": True, "provider": "cboe", "underlying": symbol.upper(),
            "spot": data.get("current_price"), "chain": chain}


# --------------------------------------------------------------- public
def resolve_provider(symbol: str, provider: str | None) -> str:
    if provider and provider != "auto":
        return provider
    core = symbol.upper().lstrip("^").split(".")[0].split("=")[0]
    core = core.split("-")[0]        # BTC-USD → BTC (registry-normalized)
    return "deribit" if core in CRYPTO_SYMBOLS else "yahoo"


def fetch_chain(symbol: str, provider: str | None = None,
                data_root: str | Path = "data") -> dict:
    """One option chain, fail-soft + stale-serve. provider=auto picks
    deribit for BTC/ETH/SOL/XRP, yahoo (cookie+crumb) otherwise."""
    sym = symbol.upper().strip()
    prov = resolve_provider(sym, provider)
    key = f"options_{prov}_{sym}.json"

    if prov == "yahoo":
        builder = lambda: fetch_chain_yahoo(sym)  # noqa: E731
    elif prov == "deribit":
        builder = lambda: fetch_chain_deribit(    # noqa: E731
            sym if sym in CRYPTO_SYMBOLS else "BTC")
    elif prov == "cboe":
        builder = lambda: fetch_chain_cboe(sym)   # noqa: E731
    else:
        return {"ok": False, "errors": [f"unknown provider: {prov}"]}

    out = _cached_fetch(data_root, key, CHAIN_TTL_S, builder)
    out.setdefault("source", prov)
    out.setdefault("data_delay", "delayed")
    return out


def analytics(symbol: str, provider: str | None = None,
              data_root: str | Path = "data",
              expiries_limit: int = 6) -> dict:
    """Chain → Greeks grid + IV surface + OI wall + max pain + PCR.

    Greeks are computed in pure Python (risk/options.py) from each
    contract's IV and the time to expiry — the feed supplies prices/IVs,
    never our Greeks.
    """
    from .registry import normalize

    norm = normalize(symbol) or symbol.upper()
    chain_out = fetch_chain(norm, provider, data_root)
    if not chain_out.get("ok"):
        return {**chain_out, "stage": "chain"}
    chain = chain_out["chain"]
    spot = chain_out.get("spot")
    if not spot:
        # derive spot from put-call parity-ish midpoint: use the strike
        # with the tightest call/put mid gap (fail-soft, labeled derived)
        strikes = sorted({c["strike"] for c in chain})
        spot = strikes[len(strikes) // 2] if strikes else None
        derived_spot = True
    else:
        derived_spot = False
    if not spot:
        return {"ok": False, "errors": ["no spot available for greeks"]}

    import datetime as _dt
    now = _dt.datetime.now(_dt.timezone.utc)
    for c in chain:
        if "T" in str(c.get("expiry", "")):
            exp_dt = _dt.datetime.fromisoformat(c["expiry"]).replace(
                tzinfo=_dt.timezone.utc)
        else:
            exp_dt = _dt.datetime.strptime(
                c["expiry"], "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc) \
            if c.get("expiry") else now
        c["t_years"] = max((exp_dt - now).total_seconds()
                           / (365.0 * 24 * 3600), 1e-6)

    expiries = sorted({c["expiry"] for c in chain})[:expiries_limit]
    greeks_grid: list[dict] = []
    for c in chain:
        if c["expiry"] not in expiries or c.get("implied_vol") is None:
            continue
        try:
            g = om.greeks(spot, c["strike"], c["t_years"], 0.0,
                          c["implied_vol"], c["kind"])
        except ValueError:
            continue
        greeks_grid.append({**g, "strike": c["strike"],
                            "expiry": c["expiry"],
                            "contract": c["contract"],
                            "iv": c["implied_vol"],
                            "open_interest": c["open_interest"],
                            "volume": c["volume"]})

    surface = om.iv_surface(chain, spot)
    expiries_out = surface["expiries"][:expiries_limit]
    term = surface["term_structure"][:expiries_limit]
    nearest_smile = expiries_out[0]["smiles"] if expiries_out else []
    skew = om.skew_metrics(nearest_smile, spot) if nearest_smile else None
    ivs_all = [c["implied_vol"] for c in chain if c.get("implied_vol")]
    atm_iv = term[0]["atm_iv"] if term and term[0]["atm_iv"] else None
    ivr = om.iv_rank(atm_iv, ivs_all) if atm_iv else None
    # iv_rank against the chain's own range is honest but weak — label it
    if ivr:
        ivr["basis"] = "this-chain IV range (no 52w history store yet)"

    return {
        "ok": True, "symbol": norm, "provider": chain_out["provider"],
        "source": chain_out.get("source"), "data_delay":
            chain_out.get("data_delay", "delayed"),
        "rate_assumption": "r=0.0 in per-contract greeks (no keyless "
                           "risk-free term structure wired per contract — "
                           "theta/rho carry that bias)",
        "cached": chain_out.get("cached", False),
        "stale_error": chain_out.get("stale_error", False),
        "stale_reason": chain_out.get("stale_reason"),
        "spot": spot, "spot_derived": derived_spot,
        "contracts": len(chain),
        "greeks_grid": greeks_grid,
        "iv_surface": {"expiries": expiries_out, "term_structure": term},
        "skew": skew,
        "iv_rank": ivr,
        "max_pain": om.max_pain(chain),
        "put_call_ratio_volume": om.put_call_ratio(chain, "volume"),
        "put_call_ratio_oi": om.put_call_ratio(chain, "open_interest"),
        "oi_wall": _oi_wall(chain, expiries),
    }


def _oi_wall(chain: list[dict], expiries: list[str]) -> list[dict]:
    """Open interest by strike for the near expiries (the OI 'wall')."""
    by_strike: dict[float, dict] = {}
    for c in chain:
        if c["expiry"] not in expiries:
            continue
        row = by_strike.setdefault(
            c["strike"], {"strike": c["strike"], "call_oi": 0.0,
                          "put_oi": 0.0})
        row[f"{c['kind']}_oi"] += float(c.get("open_interest") or 0)
    for row in by_strike.values():
        row["total_oi"] = row["call_oi"] + row["put_oi"]
    return sorted(by_strike.values(), key=lambda r: r["strike"])
