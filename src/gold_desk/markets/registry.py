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

Round-4 (GAUNTLET4-R2-BUILDER): `UNIVERSE` — the 24-instrument
multi-asset desk universe (8 sector groups), with `sector_of()`,
`universe_entry()`, `universe_symbols()`, `DEFAULT_WATCHLIST` (the
original 8) and `resolve_symbols()` (None → 8, list → subset, all →
24). The old SECTORS/ALIASES/normalize()/find()/resolve_pair() API is
untouched — every R1-R3 call site keeps working.
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


# =====================================================================
# R4-2 — UNIVERSE: the 24-instrument multi-asset desk universe
# (GAUNTLET4-R2-BUILDER). The R3 charter's 8-instrument watchlist is the
# DEFAULT_WATCHLIST (first 8 entries, order preserved); the R4 charter
# expands coverage 8 → 24 across 8 sector groups, all keyless Yahoo:
#
#   metals    GC=F SI=F HG=F          (COMEX gold/silver/copper)
#   energy    CL=F NG=F               (NYMEX WTI/natgas)
#   ag        ZW=F ZC=F               (CBOT wheat/corn)
#   indices   ES=F NQ=F YM=F RTY=F    (CME/CBOT equity index futures)
#   fx        EURUSD=X GBPUSD=X USDJPY=X AUDUSD=X USDCAD=X  (24/5)
#   rates     ^TNX ^FVX ^TYX DX-Y.NYB (10y/5y/30y yields + dollar index)
#   crypto    BTC-USD ETH-USD SOL-USD (24/7)
#   vol       ^VIX                    (CBOE)
#
# Every entry: {symbol, name, sector, calendar, exchange}. The sector
# labels feed the web panel's filter chips; `calendar` mirrors the
# SESSION_CALENDARS labels for the original 8 and extends the same
# convention to the 16 new instruments.
# =====================================================================
UNIVERSE: list[dict] = [
    # --- the original 8 (R3 charter watchlist — order preserved) ---
    {"symbol": "GC=F", "name": "Gold", "sector": "metals",
     "calendar": "COMEX", "exchange": "COMEX"},
    {"symbol": "ES=F", "name": "S&P 500 E-mini", "sector": "indices",
     "calendar": "CME", "exchange": "CME"},
    {"symbol": "^TNX", "name": "US 10Y Yield", "sector": "rates",
     "calendar": "US BOND", "exchange": "CBOE"},
    {"symbol": "DX-Y.NYB", "name": "Dollar Index", "sector": "rates",
     "calendar": "ICE", "exchange": "ICE"},
    {"symbol": "BTC-USD", "name": "Bitcoin", "sector": "crypto",
     "calendar": "24/7", "exchange": "crypto"},
    {"symbol": "^VIX", "name": "VIX", "sector": "vol",
     "calendar": "CBOE", "exchange": "CBOE"},
    {"symbol": "CL=F", "name": "WTI Crude", "sector": "energy",
     "calendar": "NYMEX", "exchange": "NYMEX"},
    {"symbol": "EURUSD=X", "name": "EUR/USD", "sector": "fx",
     "calendar": "24/5", "exchange": "FX"},
    # --- R4-2 expansion: metals ---
    {"symbol": "SI=F", "name": "Silver", "sector": "metals",
     "calendar": "COMEX", "exchange": "COMEX"},
    {"symbol": "HG=F", "name": "Copper", "sector": "metals",
     "calendar": "COMEX", "exchange": "COMEX"},
    # --- R4-2 expansion: energy ---
    {"symbol": "NG=F", "name": "Nat Gas", "sector": "energy",
     "calendar": "NYMEX", "exchange": "NYMEX"},
    # --- R4-2 expansion: agriculture ---
    {"symbol": "ZW=F", "name": "Wheat", "sector": "ag",
     "calendar": "CBOT", "exchange": "CBOT"},
    {"symbol": "ZC=F", "name": "Corn", "sector": "ag",
     "calendar": "CBOT", "exchange": "CBOT"},
    # --- R4-2 expansion: equity index futures ---
    {"symbol": "NQ=F", "name": "Nasdaq 100 E-mini", "sector": "indices",
     "calendar": "CME", "exchange": "CME"},
    {"symbol": "YM=F", "name": "Dow E-mini", "sector": "indices",
     "calendar": "CBOT", "exchange": "CBOT"},
    {"symbol": "RTY=F", "name": "Russell 2000 E-mini", "sector": "indices",
     "calendar": "CME", "exchange": "CME"},
    # --- R4-2 expansion: FX majors (24/5) ---
    {"symbol": "GBPUSD=X", "name": "GBP/USD", "sector": "fx",
     "calendar": "24/5", "exchange": "FX"},
    {"symbol": "USDJPY=X", "name": "USD/JPY", "sector": "fx",
     "calendar": "24/5", "exchange": "FX"},
    {"symbol": "AUDUSD=X", "name": "AUD/USD", "sector": "fx",
     "calendar": "24/5", "exchange": "FX"},
    {"symbol": "USDCAD=X", "name": "USD/CAD", "sector": "fx",
     "calendar": "24/5", "exchange": "FX"},
    # --- R4-2 expansion: rates curve + dollar ---
    {"symbol": "^FVX", "name": "US 5Y Yield", "sector": "rates",
     "calendar": "US BOND", "exchange": "CBOE"},
    {"symbol": "^TYX", "name": "US 30Y Yield", "sector": "rates",
     "calendar": "US BOND", "exchange": "CBOE"},
    # --- R4-2 expansion: crypto majors (24/7) ---
    {"symbol": "ETH-USD", "name": "Ethereum", "sector": "crypto",
     "calendar": "24/7", "exchange": "crypto"},
    {"symbol": "SOL-USD", "name": "Solana", "sector": "crypto",
     "calendar": "24/7", "exchange": "crypto"},
]

# Sector display groups (web filter chips) in canonical order.
UNIVERSE_SECTORS: list[str] = ["metals", "energy", "ag", "indices", "fx",
                               "rates", "crypto", "vol"]

# The original R3 8-instrument watchlist (DEFAULT constructor target —
# anything reading the old monitor API keeps getting exactly these 8).
DEFAULT_WATCHLIST: list[str] = [e["symbol"] for e in UNIVERSE[:8]]

# Fast symbol → universe-entry lookup (case-insensitive).
_UNIVERSE_BY_SYMBOL: dict[str, dict] = {
    e["symbol"].upper(): e for e in UNIVERSE}


def universe_symbols() -> list[str]:
    """All 24 UNIVERSE symbols in display order."""
    return [e["symbol"] for e in UNIVERSE]


def sector_of(symbol: str) -> str | None:
    """UNIVERSE sector group for a symbol ("metals", "energy", "ag",
    "indices", "fx", "rates", "crypto", "vol") or None when the symbol
    isn't in the 24-instrument universe."""
    entry = _UNIVERSE_BY_SYMBOL.get(str(symbol or "").strip().upper())
    return entry["sector"] if entry else None


def universe_entry(symbol: str) -> dict | None:
    """Full UNIVERSE entry {symbol, name, sector, calendar, exchange} or
    None for symbols outside the 24-instrument universe."""
    entry = _UNIVERSE_BY_SYMBOL.get(str(symbol or "").strip().upper())
    return dict(entry) if entry else None


def resolve_symbols(symbols=None, all: bool = False) -> list[str]:
    """Resolve a monitor symbol request to an ordered symbol list.

    * `symbols=None` (default) → DEFAULT_WATCHLIST (the original 8 —
      backward compatible with every R3 call site)
    * `all=True` or `symbols="all"` → all 24 UNIVERSE symbols
    * `symbols=[...]` → the requested list ordered by UNIVERSE position
      (unknown symbols are kept at the tail — the monitor fail-softs
      them like any dead feed rather than dropping them silently)
    Duplicate symbols are collapsed preserving first occurrence.
    """
    if all or (isinstance(symbols, str) and symbols.strip().lower() == "all"):
        return list(universe_symbols())
    if symbols is None:
        return list(DEFAULT_WATCHLIST)
    if isinstance(symbols, str):
        symbols = [s for s in
                   (p.strip() for p in symbols.split(",")) if s]
    out: list[str] = []
    seen: set[str] = set()
    for sym in symbols:
        key = str(sym).strip().upper()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    # order by UNIVERSE position; unknowns (case-corrected) go last
    pos = {e["symbol"].upper(): i for i, e in enumerate(UNIVERSE)}
    known = [s for s in out if s in pos]
    unknown = [s for s in out if s not in pos]
    return ([UNIVERSE[pos[s]]["symbol"] for s in sorted(known,
                                                        key=lambda s: pos[s])]
            + unknown)


# R3-1 BUILD 1 — session calendars for the 8-instrument multi-asset
# monitor (per the charter). Each entry pins a market calendar label +
# the session_mode the monitor uses to slice VWAP:
#   "fixed"     UTC hour windows (asia / london / overlap / ny / off)
#   "rolling24" last-24h bucket (BTC's 24/7 continuous tape)
# Extended in R4-2: session metadata now derives from UNIVERSE (the 16
# added instruments get the same fixed/rolling24 convention — only the
# crypto majors are 24/7), so SESSION_CALENDARS is materialized from it.
SESSION_MODES: dict[str, str] = {
    # 24/7 continuous tape → rolling 24h VWAP bucket
    "BTC-USD": "rolling24", "ETH-USD": "rolling24", "SOL-USD": "rolling24",
}
SESSION_CALENDARS: dict[str, dict] = {
    e["symbol"]: {
        "calendar": e["calendar"],
        "session_mode": SESSION_MODES.get(e["symbol"], "fixed"),
        "trading_hours": ("continuous (24/7)"
                          if SESSION_MODES.get(e["symbol"]) == "rolling24"
                          else "Mon-Fri, near-24h US ET (futures)"
                          if e["sector"] in ("metals", "energy", "ag",
                                             "indices")
                          else "Mon-Fri, NY session"),
    }
    for e in UNIVERSE
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
