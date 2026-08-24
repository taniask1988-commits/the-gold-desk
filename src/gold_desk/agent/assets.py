"""Asset registry (P3 §5.2) — human-owned, like the constitution.

config/assets.yaml maps symbols to data sources so the desk's research
layer covers gold AND crypto (CoinGecko/DefiLlama/Binance public APIs,
all keyless). Adding a token = adding a YAML row.

Runtime name->id resolution for unknown tokens is handled by
token_search() so the human can say "research this random memecoin".
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import yaml

from .tools import tool

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ASSETS = {
    "assets": {
        "XAUUSD": {"class": "metal", "spot_tool": "feeds",
                   "ohlc_symbol": "GC=F", "fallback": "PAXGUSDT"},
        "BTC": {"class": "crypto", "spot_tool": "coingecko",
                "id": "bitcoin", "ohlc_symbol": "BTC/USDT"},
        "ETH": {"class": "crypto", "spot_tool": "coingecko",
                "id": "ethereum", "ohlc_symbol": "ETH/USDT"},
        "SOL": {"class": "crypto", "spot_tool": "coingecko",
                "id": "solana", "ohlc_symbol": "SOL/USDT"},
    }
}


def load_assets() -> dict:
    path = REPO_ROOT / "config" / "assets.yaml"
    if path.exists():
        try:
            return yaml.safe_load(path.read_text()) or DEFAULT_ASSETS
        except yaml.YAMLError:
            return DEFAULT_ASSETS
    return DEFAULT_ASSETS


def _http_json(url: str, timeout: float = 10.0) -> dict:
    import urllib.request
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (gold-desk research)",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _cg(id_or_symbol: str) -> dict:
    """CoinGecko simple price for a coingecko id."""
    data = _http_json(
        f"https://api.coingecko.com/api/v3/simple/price"
        f"?ids={id_or_symbol}&vs_currencies=usd"
        f"&include_24hr_change=true&include_last_updated_at=true")
    row = (data.get(id_or_symbol) or {})
    return row


def _cg_id_for(symbol: str) -> str:
    """Map a ticker (BTC) or arbitrary name to a coingecko id."""
    sym = symbol.strip().lower()
    assets = load_assets().get("assets") or {}
    for key, cfg in assets.items():
        if key.lower() == sym and cfg.get("spot_tool") == "coingecko":
            return cfg["id"]
    # unknown token: search
    try:
        results = _http_json(
            "https://api.coingecko.com/api/v3/search?query="
            + _urlquote(sym))["coins"][:5]
        for r in results:
            if r.get("symbol", "").lower() == sym or r.get(
                    "name", "").lower() == sym:
                return r["id"]
        if results:
            return results[0]["id"]
    except Exception:
        pass
    return sym  # last resort: treat the input as an id


def _urlquote(s: str) -> str:
    import urllib.parse
    return urllib.parse.quote(s)


def spot_for(symbol: str) -> dict:
    """Spot price for any registry asset or coingecko-resolvable token."""
    try:
        cid = _cg_id_for(symbol)
        row = _cg(cid)
        usd = row.get("usd")
        if usd is None:
            return {"ok": False, "error": f"no price for {symbol}"}
        return {
            "ok": True, "price": usd,
            "change_24h_pct": row.get("usd_24h_change"),
            "source": f"coingecko:{cid}",
            "market_time": row.get("last_updated_at"),
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def _binance_symbol(symbol: str) -> str:
    sym = symbol.strip().upper()
    assets = load_assets().get("assets") or {}
    cfg = assets.get(sym) or {}
    pair = cfg.get("ohlc_symbol") or f"{sym}/USDT"
    return pair.replace("/", "")


def ohlc_for(symbol: str, limit: int = 48) -> dict:
    """Hourly OHLC for crypto via Binance public klines."""
    try:
        pair = _binance_symbol(symbol)
        raw = _http_json(
            f"https://api.binance.com/api/v3/klines?symbol={pair}"
            f"&interval=1h&limit={min(limit, 200)}")
        bars = [{"ts": k[0], "o": float(k[1]), "h": float(k[2]),
                 "l": float(k[3]), "c": float(k[4])} for k in raw]
        return {"ok": True, "source": f"binance:{pair}",
                "interval": "1h", "bars": bars}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ------------------------------------------------------------------ tools


@tool("Token/coin profile from CoinGecko: name, symbol, market cap, "
      "volume, links. Accepts ticker (BTC), name (bitcoin) or id.",
      returns="dict")
def token_profile(symbol: str) -> dict:
    try:
        cid = _cg_id_for(symbol)
        data = _http_json(
            f"https://api.coingecko.com/api/v3/coins/{_urlquote(cid)}"
            "?localization=false&tickers=false&market_data=true"
            "&community_data=false&developer_data=false")
        m = data.get("market_data") or {}
        return {
            "ok": True, "id": cid,
            "name": data.get("name"), "symbol": data.get("symbol"),
            "genesis_date": data.get("genesis_date"),
            "market_cap_usd": (m.get("market_cap") or {}).get("usd"),
            "total_volume_usd": (m.get("total_volume") or {}).get("usd"),
            "ath_usd": (m.get("ath") or {}).get("usd"),
            "ath_change_pct": (m.get("ath_change_percentage") or {}).get("usd"),
            "categories": (data.get("categories") or [])[:6],
            "homepage": ((data.get("links") or {}).get("homepage") or [None])[0],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@tool("Protocol TVL series from DefiLlama. protocol: slug like "
      "'lido' or 'aave'. Returns current TVL + 7d/30d change.",
      returns="dict")
def tvl_series(protocol: str) -> dict:
    try:
        slug = protocol.strip().lower().replace(" ", "-")
        data = _http_json(
            f"https://api.llama.fi/protocol/{_urlquote(slug)}")
        tvl = data.get("currentChainTvls") or {}
        total = sum(v for v in tvl.values() if isinstance(v, (int, float)))
        hist = data.get("tvl") or []
        def at(days: int):
            cutoff = time.time() - days * 86400
            prev = hist[0] if hist else {"totalLiquidityUSD": 0}
            for h in hist:
                if h.get("date", 0) >= cutoff:
                    return h
                prev = h
            return prev
        d7, d30 = at(7), at(30)
        return {
            "ok": True, "protocol": slug,
            "name": data.get("name"),
            "tvl_now_usd": round(total) if total else data.get("tvl"),
            "tvl_7d_ago_usd": round(d7.get("totalLiquidityUSD", 0)),
            "tvl_30d_ago_usd": round(d30.get("totalLiquidityUSD", 0)),
            "chains": sorted(tvl.keys())[:10],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@tool("Perp funding rate + open interest for a symbol like 'BTC'. "
      "Public exchange endpoints (Binance).",
      returns="dict")
def funding_oi(symbol: str) -> dict:
    try:
        pair = _binance_symbol(symbol)
        funding = _http_json(
            f"https://fapi.binance.com/fapi/v1/premiumIndex?symbol={pair}")
        oi_raw = _http_json(
            f"https://fapi.binance.com/fapi/v1/openInterest?symbol={pair}")
        rate = float(funding.get("lastFundingRate", 0)) * 100
        return {
            "ok": True, "symbol": pair,
            "funding_rate_pct": round(rate, 5),
            "mark_price": float(funding.get("markPrice", 0)),
            "open_interest": float(oi_raw.get("openInterest", 0)),
            "annualized_funding_pct": round(rate * 3 * 365, 2),
            "note": "positive funding = longs pay shorts",
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@tool("Resolve a token name/ticker to its CoinGecko id. Use first when "
      "researching an unfamiliar token so other crypto tools work.",
      returns="dict")
def token_search(query: str) -> dict:
    try:
        results = _http_json(
            "https://api.coingecko.com/api/v3/search?query="
            + _urlquote(query)).get("coins") or []
        return {"ok": True, "matches": [
            {"id": r.get("id"), "symbol": r.get("symbol"),
             "name": r.get("name"), "rank": r.get("market_cap_rank")}
            for r in results[:8]
        ]}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def asset_tools() -> list:
    """P3 crypto tool set."""
    return [token_profile, tvl_series, funding_oi, token_search]
