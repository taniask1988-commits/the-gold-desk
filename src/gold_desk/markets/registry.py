"""MARKET GAUNTLET symbol registry — the universe of covered markets.

9 sectors / 67 symbols, every one verified live against the keyless
Yahoo v8/chart endpoint (range=1d&interval=15m):

    indices     ^GSPC ^IXIC ^DJI ^NSEI ^NSEBANK ^BSESN ^FTSE ^GDAXI
                ^N225 ^HSI
    us          AAPL MSFT NVDA GOOGL AMZN META TSLA
    etfs        SPY QQQ IWM GLD SLV EEM VXX
    india       RELIANCE.NS TCS.NS HDFCBANK.NS INFY.NS ICICIBANK.NS
                SBIN.NS BHARTIARTL.NS ITC.NS LT.NS TMCV.NS
                (Tata Motors: TATAMOTORS.NS 404s since the Oct-2025
                demerger — TMCV.NS is the continuing "Tata Motors Ltd")
    commodities GC=F SI=F PL=F PA=F HG=F CL=F BZ=F NG=F ALI=F
                ZC=F ZW=F ZS=F KC=F SB=F
    forex       EURUSD=X GBPUSD=X USDJPY=X AUDUSD=X USDCAD=X
                USDCHF=X NZDUSD=X USDINR=X
    rates       ^TNX ^FVX ^IRX DX-Y.NYB
    volatility  ^VIX
    crypto      BTC-USD ETH-USD SOL-USD XRP-USD BNB-USD DOGE-USD

Round-2 expansion (GAUNTLET-P2-BUILDER): the round-1 critic picked
TradingView on coverage — whole asset classes (VIX, US yields, DXY,
ETFs, platinum-group metals, agriculture futures) were absent. Every
added symbol was probed live before entering the registry; nothing
here 404s (candidates that do get dropped, per the brief).

`normalize()` maps human input ("btc", "gold", "nifty", "reliance",
"eur/usd", "apple", "vix", "dxy", "10y", "platinum", "corn") to the
canonical Yahoo symbol so the whole surface — CLI, web API, agent
desk — accepts the same sloppy user input.

Round-3 (GAUNTLET-P4-BUILDER): `resolve_pair()` adds FX-pair
resolution with a reciprocal fallback — "inr/usd" isn't a registry
pair, but its reciprocal USDINR=X is, so the pair resolves (the
caller serves the INVERTED quote: price=1/price). Pairs where neither
side is in the registry ("jpy/eur") resolve ad-hoc to the Yahoo symbol
and board.fetch_detail anchors on whichever side Yahoo quotes at
higher precision.
"""
from __future__ import annotations

SECTORS: dict[str, dict] = {
    "indices": {
        "label": "Indices",
        "symbols": [
            {"symbol": "^GSPC", "name": "S&P 500"},
            {"symbol": "^IXIC", "name": "Nasdaq"},
            {"symbol": "^DJI", "name": "Dow 30"},
            {"symbol": "^NSEI", "name": "NIFTY 50"},
            {"symbol": "^NSEBANK", "name": "Bank Nifty"},
            {"symbol": "^BSESN", "name": "Sensex"},
            {"symbol": "^FTSE", "name": "FTSE 100"},
            {"symbol": "^GDAXI", "name": "DAX"},
            {"symbol": "^N225", "name": "Nikkei 225"},
            {"symbol": "^HSI", "name": "Hang Seng"},
        ],
    },
    "us": {
        "label": "US Equities",
        "symbols": [
            {"symbol": "AAPL", "name": "Apple"},
            {"symbol": "MSFT", "name": "Microsoft"},
            {"symbol": "NVDA", "name": "Nvidia"},
            {"symbol": "GOOGL", "name": "Alphabet"},
            {"symbol": "AMZN", "name": "Amazon"},
            {"symbol": "META", "name": "Meta"},
            {"symbol": "TSLA", "name": "Tesla"},
        ],
    },
    "etfs": {
        "label": "ETFs",
        "symbols": [
            {"symbol": "SPY", "name": "S&P 500 ETF"},
            {"symbol": "QQQ", "name": "Nasdaq 100 ETF"},
            {"symbol": "IWM", "name": "Russell 2000 ETF"},
            {"symbol": "GLD", "name": "Gold ETF"},
            {"symbol": "SLV", "name": "Silver ETF"},
            {"symbol": "EEM", "name": "Emerging Mkts ETF"},
            {"symbol": "VXX", "name": "VIX Short-Term ETF"},
        ],
    },
    "india": {
        "label": "India Equities",
        "symbols": [
            {"symbol": "RELIANCE.NS", "name": "Reliance"},
            {"symbol": "TCS.NS", "name": "TCS"},
            {"symbol": "HDFCBANK.NS", "name": "HDFC Bank"},
            {"symbol": "INFY.NS", "name": "Infosys"},
            {"symbol": "ICICIBANK.NS", "name": "ICICI"},
            {"symbol": "SBIN.NS", "name": "SBI"},
            {"symbol": "BHARTIARTL.NS", "name": "Airtel"},
            {"symbol": "ITC.NS", "name": "ITC"},
            {"symbol": "LT.NS", "name": "L&T"},
            {"symbol": "TMCV.NS", "name": "Tata Motors"},
        ],
    },
    "commodities": {
        "label": "Commodities",
        "symbols": [
            {"symbol": "GC=F", "name": "Gold"},
            {"symbol": "SI=F", "name": "Silver"},
            {"symbol": "PL=F", "name": "Platinum"},
            {"symbol": "PA=F", "name": "Palladium"},
            {"symbol": "HG=F", "name": "Copper"},
            {"symbol": "ALI=F", "name": "Aluminum"},
            {"symbol": "CL=F", "name": "WTI Crude"},
            {"symbol": "BZ=F", "name": "Brent"},
            {"symbol": "NG=F", "name": "Nat Gas"},
            {"symbol": "ZC=F", "name": "Corn"},
            {"symbol": "ZW=F", "name": "Wheat"},
            {"symbol": "ZS=F", "name": "Soybeans"},
            {"symbol": "KC=F", "name": "Coffee"},
            {"symbol": "SB=F", "name": "Sugar"},
        ],
    },
    "forex": {
        "label": "Forex",
        "symbols": [
            {"symbol": "EURUSD=X", "name": "EUR/USD"},
            {"symbol": "GBPUSD=X", "name": "GBP/USD"},
            {"symbol": "USDJPY=X", "name": "USD/JPY"},
            {"symbol": "AUDUSD=X", "name": "AUD/USD"},
            {"symbol": "USDCAD=X", "name": "USD/CAD"},
            {"symbol": "USDCHF=X", "name": "USD/CHF"},
            {"symbol": "NZDUSD=X", "name": "NZD/USD"},
            {"symbol": "USDINR=X", "name": "USD/INR"},
        ],
    },
    "rates": {
        "label": "Rates & Dollar",
        "symbols": [
            {"symbol": "^TNX", "name": "US 10Y Yield"},
            {"symbol": "^FVX", "name": "US 5Y Yield"},
            {"symbol": "^IRX", "name": "US 13W Yield"},
            {"symbol": "DX-Y.NYB", "name": "Dollar Index"},
        ],
    },
    "volatility": {
        "label": "Volatility",
        "symbols": [
            {"symbol": "^VIX", "name": "VIX"},
        ],
    },
    "crypto": {
        "label": "Crypto",
        "symbols": [
            {"symbol": "BTC-USD", "name": "Bitcoin"},
            {"symbol": "ETH-USD", "name": "Ethereum"},
            {"symbol": "SOL-USD", "name": "Solana"},
            {"symbol": "XRP-USD", "name": "XRP"},
            {"symbol": "BNB-USD", "name": "BNB"},
            {"symbol": "DOGE-USD", "name": "Dogecoin"},
        ],
    },
}

# Human aliases → canonical Yahoo symbol (all upper-case keys).
ALIASES: dict[str, str] = {
    # crypto
    "BTC": "BTC-USD", "XBT": "BTC-USD", "BITCOIN": "BTC-USD",
    "ETH": "ETH-USD", "ETHEREUM": "ETH-USD", "ETHER": "ETH-USD",
    "SOL": "SOL-USD", "SOLANA": "SOL-USD",
    "XRP": "XRP-USD", "RIPPLE": "XRP-USD",
    "BNB": "BNB-USD", "BINANCE COIN": "BNB-USD",
    "DOGE": "DOGE-USD", "DOGECOIN": "DOGE-USD",
    # forex
    "EURUSD": "EURUSD=X", "EUR": "EURUSD=X",
    "GBPUSD": "GBPUSD=X", "GBP": "GBPUSD=X", "CABLE": "GBPUSD=X",
    "USDJPY": "USDJPY=X", "JPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X", "AUD": "AUDUSD=X",
    "USDCAD": "USDCAD=X", "CAD": "USDCAD=X", "LOONIE": "USDCAD=X",
    "USDCHF": "USDCHF=X", "CHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X", "NZD": "NZDUSD=X",
    "USDINR": "USDINR=X", "INR": "USDINR=X", "RUPEE": "USDINR=X",
    # commodities — metals
    "GOLD": "GC=F", "XAUUSD": "GC=F", "XAU": "GC=F", "GC": "GC=F",
    "SILVER": "SI=F", "XAGUSD": "SI=F", "XAG": "SI=F", "SI": "SI=F",
    "PLATINUM": "PL=F", "XPTUSD": "PL=F", "XPT": "PL=F", "PL": "PL=F",
    "PALLADIUM": "PA=F", "XPDUSD": "PA=F", "XPD": "PA=F", "PA": "PA=F",
    "COPPER": "HG=F", "ALUMINUM": "ALI=F", "ALUMINIUM": "ALI=F",
    # commodities — energy
    "WTI": "CL=F", "CRUDE": "CL=F", "OIL": "CL=F", "USOIL": "CL=F",
    "BRENT": "BZ=F", "UKOIL": "BZ=F",
    "NATGAS": "NG=F", "NAT GAS": "NG=F", "NATURAL GAS": "NG=F",
    # commodities — agriculture
    "CORN": "ZC=F", "WHEAT": "ZW=F",
    "SOYBEANS": "ZS=F", "SOYBEAN": "ZS=F",
    "COFFEE": "KC=F", "SUGAR": "SB=F",
    # indices
    "SP500": "^GSPC", "S&P": "^GSPC", "S&P 500": "^GSPC", "SPX": "^GSPC",
    "NASDAQ": "^IXIC", "NASDAQ COMPOSITE": "^IXIC",
    "DOW": "^DJI", "DOW JONES": "^DJI", "DJI": "^DJI",
    "NIFTY": "^NSEI", "NIFTY50": "^NSEI", "NIFTY 50": "^NSEI",
    "BANKNIFTY": "^NSEBANK", "BANK NIFTY": "^NSEBANK",
    "SENSEX": "^BSESN", "BSE": "^BSESN",
    "FTSE": "^FTSE", "FTSE100": "^FTSE", "FTSE 100": "^FTSE",
    "DAX": "^GDAXI",
    "NIKKEI": "^N225", "NIKKEI 225": "^N225",
    "HANGSENG": "^HSI", "HANG SENG": "^HSI",
    # volatility
    "VIX": "^VIX", "CBOE VIX": "^VIX", "VOLATILITY": "^VIX",
    "VOLATILITY INDEX": "^VIX", "FEAR INDEX": "^VIX",
    # rates / yields
    "TNX": "^TNX", "10Y": "^TNX", "10 YEAR": "^TNX", "TEN YEAR": "^TNX",
    "10Y YIELD": "^TNX", "US10Y": "^TNX", "US 10Y": "^TNX",
    "US 10 YEAR": "^TNX", "US 10 YEAR YIELD": "^TNX",
    "FVX": "^FVX", "5Y": "^FVX", "5 YEAR": "^FVX", "FIVE YEAR": "^FVX",
    "5Y YIELD": "^FVX", "US5Y": "^FVX", "US 5Y": "^FVX",
    "US 5 YEAR": "^FVX", "US 5 YEAR YIELD": "^FVX",
    "IRX": "^IRX", "13W": "^IRX", "13 WEEK": "^IRX", "13W YIELD": "^IRX",
    "TBILL": "^IRX", "T-BILL": "^IRX", "US 13 WEEK": "^IRX",
    "US 13W": "^IRX", "US 13W YIELD": "^IRX",
    # dollar index
    "DXY": "DX-Y.NYB", "DXY INDEX": "DX-Y.NYB", "USDX": "DX-Y.NYB",
    "DOLLAR INDEX": "DX-Y.NYB", "USD INDEX": "DX-Y.NYB",
    "US DOLLAR INDEX": "DX-Y.NYB", "DOLLAR": "DX-Y.NYB",
    # ETFs
    "S&P 500 ETF": "SPY",
    "NASDAQ 100 ETF": "QQQ",
    "RUSSELL": "IWM", "RUSSELL 2000": "IWM",
    "RUSSELL 2000 ETF": "IWM",
    "GOLD ETF": "GLD", "SILVER ETF": "SLV",
    "EMERGING MARKETS": "EEM", "EMERGING MARKETS ETF": "EEM",
    "VIX ETF": "VXX", "VOLATILITY ETF": "VXX",
    # India single names resolve via the ".NS" heuristic (RELIANCE etc.)
    # — except the demerged Tata Motors, mapped to its continuing entity
    "TATAMOTORS": "TMCV.NS", "TATAMOTORS.NS": "TMCV.NS",
    "TATA MOTORS": "TMCV.NS",
}


# R3-1 BUILD 1 — session calendars for the 8-instrument multi-asset
# monitor (per the charter). Each entry pins a market calendar label +
# the session_mode the monitor uses to slice VWAP:
#   "fixed"     UTC hour windows (asia / london / overlap / ny / off)
#   "rolling24" last-24h bucket (BTC's 24/7 continuous tape)
SESSION_CALENDARS: dict[str, dict] = {
    "GC=F":      {"calendar": "COMEX",   "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, 18:00-17:00 US ET (nearly 23h)"},
    "ES=F":      {"calendar": "CME",     "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, 18:00-17:00 US ET (nearly 23h)"},
    "^TNX":      {"calendar": "US BOND", "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, NY session"},
    "DX-Y.NYB":  {"calendar": "ICE",     "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, NY session"},
    "BTC-USD":   {"calendar": "24/7",    "session_mode": "rolling24",
                  "trading_hours": "continuous (24/7)"},
    "^VIX":      {"calendar": "CBOE",    "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, NY session"},
    "CL=F":      {"calendar": "NYMEX",    "session_mode": "fixed",
                  "trading_hours": "Mon-Fri, NY session"},
    "EURUSD=X":  {"calendar": "24/5",    "session_mode": "fixed",
                  "trading_hours": "Sun 17:00 - Fri 17:00 US ET (24/5)"},
}


def all_symbols() -> list[dict]:
    """Flat registry: [{symbol, name, sector}, ...] in display order."""
    out = []
    for key, sec in SECTORS.items():
        for s in sec["symbols"]:
            out.append({"symbol": s["symbol"], "name": s["name"],
                        "sector": key})
    return out


def session_calendar(symbol: str) -> dict | None:
    """R3-1: session-calendar metadata for the 8 multi-asset
    instruments. Returns None for any other symbol — the monitor's
    per-asset session slice + UI badge comes from here.
    """
    return SESSION_CALENDARS.get(str(symbol or "").upper())


def find(symbol: str) -> dict | None:
    """Exact (case-insensitive) symbol lookup → registry entry or None."""
    if not symbol:
        return None
    key = str(symbol).strip().upper()
    for entry in all_symbols():
        if entry["symbol"].upper() == key:
            return entry
    return None


def resolve_pair(user_input: str):
    """FX-pair resolution with a reciprocal fallback (round-3 defect 3).

    Accepts the A/B slash form ("inr/usd", "eur-usd") and the bare
    concatenated AABBB form ("inrusd"). Returns a tuple

        (yahoo_symbol, inverted, in_registry)

    or None when the input isn't pair-shaped (both codes must be
    3-letter currencies):

    * direct registry pair  — "eur/usd"   → ("EURUSD=X",  False, True)
    * reciprocal registry
      pair (the round-3 fix)— "inr/usd"   → ("USDINR=X",  True,  True)
      the caller must serve the INVERTED quote (price=1/price);
      "inrusd"/"jpy/usd"/"usd/eur" behave the same way
    * ad-hoc Yahoo pair     — "jpy/eur"  → ("JPYEUR=X",  False, False)
      neither side is in the registry; the caller fetches it from
      Yahoo live (trying the reciprocal too) — see board.fetch_detail

    "inr/usd" used to return None (round-2 critic: unknown symbol)
    even though Yahoo quotes its reciprocal USDINR=X natively.
    """
    if not user_input:
        return None
    key = str(user_input).strip().upper()
    if not key:
        return None
    parts = None
    dashed = key.replace("-", "/")
    if dashed.count("/") == 1:
        base, quote = dashed.split("/")
        if len(base) == 3 and len(quote) == 3 \
                and base.isalpha() and quote.isalpha():
            parts = (base, quote)
    elif len(key) == 6 and key.isalpha():
        parts = (key[:3], key[3:])
    if not parts:
        return None
    base, quote = parts
    direct = find(f"{base}{quote}=X")
    if direct:
        return direct["symbol"], False, True
    reciprocal = find(f"{quote}{base}=X")
    if reciprocal:
        return reciprocal["symbol"], True, True
    return f"{base}{quote}=X", False, False


def normalize(user_input: str) -> str | None:
    """Map sloppy human input to a canonical Yahoo registry symbol.

    Order: exact symbol → alias table → exact name → pair heuristic
    (EUR/USD → EURUSD=X; INR/USD → the reciprocal USDINR=X) → suffix
    heuristics (.NS, -USD, =F, ^ prefix) → partial name match.
    Case-insensitive. Returns None when nothing in the registry
    matches (ad-hoc pairs like "jpy/eur" resolve only through
    resolve_pair()/fetch_detail, never here).
    """
    if not user_input:
        return None
    key = str(user_input).strip().upper()
    if not key:
        return None
    # 1. exact symbol
    hit = find(key)
    if hit:
        return hit["symbol"]
    # 2. alias table
    if key in ALIASES:
        return ALIASES[key]
    # 3. exact name
    for entry in all_symbols():
        if entry["name"].upper() == key:
            return entry["symbol"]
    # 4. currency-pair heuristic: EUR/USD, eur-usd → EURUSD=X (direct);
    #    INR/USD, inrusd → the reciprocal USDINR=X (round-3 — the
    #    caller inverts the quote via resolve_pair)
    pr = resolve_pair(key)
    if pr and pr[2]:            # registry pair, direct or reciprocal
        return pr[0]
    # 5. suffix heuristics (India NSE, crypto, futures, indices)
    for cand in (f"{key}.NS", f"{key}-USD", f"{key}=F", f"^{key}"):
        hit = find(cand)
        if hit:
            return hit["symbol"]
    # 6. partial name match (last resort)
    for entry in all_symbols():
        if key in entry["name"].upper():
            return entry["symbol"]
    return None
