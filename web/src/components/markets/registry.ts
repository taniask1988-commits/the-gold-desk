/** MARKET GAUNTLET symbol registry — client mirror of
 *  src/gold_desk/markets/registry.py (same 9 sectors / 67 symbols,
 *  same order). Lets the command palette and the monitors/alerts
 *  pickers work with ZERO network (the board itself still comes from
 *  /api/desk/markets). Keep in sync with the python registry — the
 *  order there is the display order here. */

export interface RegistryEntry {
  symbol: string;
  name: string;
  sector: string;
}

export const SECTOR_ORDER = [
  "indices",
  "us",
  "etfs",
  "india",
  "commodities",
  "forex",
  "rates",
  "volatility",
  "crypto",
] as const;

export const SECTOR_LABELS: Record<string, string> = {
  indices: "Indices",
  us: "US Equities",
  etfs: "ETFs",
  india: "India Equities",
  commodities: "Commodities",
  forex: "Forex",
  rates: "Rates & Dollar",
  volatility: "Volatility",
  crypto: "Crypto",
};

const R: Array<[string, string, Array<[string, string]>]> = [
  [
    "indices",
    "Indices",
    [
      ["^GSPC", "S&P 500"],
      ["^IXIC", "Nasdaq"],
      ["^DJI", "Dow 30"],
      ["^NSEI", "NIFTY 50"],
      ["^NSEBANK", "Bank Nifty"],
      ["^BSESN", "Sensex"],
      ["^FTSE", "FTSE 100"],
      ["^GDAXI", "DAX"],
      ["^N225", "Nikkei 225"],
      ["^HSI", "Hang Seng"],
    ],
  ],
  [
    "us",
    "US Equities",
    [
      ["AAPL", "Apple"],
      ["MSFT", "Microsoft"],
      ["NVDA", "Nvidia"],
      ["GOOGL", "Alphabet"],
      ["AMZN", "Amazon"],
      ["META", "Meta"],
      ["TSLA", "Tesla"],
    ],
  ],
  [
    "etfs",
    "ETFs",
    [
      ["SPY", "S&P 500 ETF"],
      ["QQQ", "Nasdaq 100 ETF"],
      ["IWM", "Russell 2000 ETF"],
      ["GLD", "Gold ETF"],
      ["SLV", "Silver ETF"],
      ["EEM", "Emerging Mkts ETF"],
      ["VXX", "VIX Short-Term ETF"],
    ],
  ],
  [
    "india",
    "India Equities",
    [
      ["RELIANCE.NS", "Reliance"],
      ["TCS.NS", "TCS"],
      ["HDFCBANK.NS", "HDFC Bank"],
      ["INFY.NS", "Infosys"],
      ["ICICIBANK.NS", "ICICI"],
      ["SBIN.NS", "SBI"],
      ["BHARTIARTL.NS", "Airtel"],
      ["ITC.NS", "ITC"],
      ["LT.NS", "L&T"],
      ["TMCV.NS", "Tata Motors"],
    ],
  ],
  [
    "commodities",
    "Commodities",
    [
      ["GC=F", "Gold"],
      ["SI=F", "Silver"],
      ["PL=F", "Platinum"],
      ["PA=F", "Palladium"],
      ["HG=F", "Copper"],
      ["ALI=F", "Aluminum"],
      ["CL=F", "WTI Crude"],
      ["BZ=F", "Brent"],
      ["NG=F", "Nat Gas"],
      ["ZC=F", "Corn"],
      ["ZW=F", "Wheat"],
      ["ZS=F", "Soybeans"],
      ["KC=F", "Coffee"],
      ["SB=F", "Sugar"],
    ],
  ],
  [
    "forex",
    "Forex",
    [
      ["EURUSD=X", "EUR/USD"],
      ["GBPUSD=X", "GBP/USD"],
      ["USDJPY=X", "USD/JPY"],
      ["AUDUSD=X", "AUD/USD"],
      ["USDCAD=X", "USD/CAD"],
      ["USDCHF=X", "USD/CHF"],
      ["NZDUSD=X", "NZD/USD"],
      ["USDINR=X", "USD/INR"],
    ],
  ],
  [
    "rates",
    "Rates & Dollar",
    [
      ["^TNX", "US 10Y Yield"],
      ["^FVX", "US 5Y Yield"],
      ["^IRX", "US 13W Yield"],
      ["DX-Y.NYB", "Dollar Index"],
    ],
  ],
  ["volatility", "Volatility", [["^VIX", "VIX"]]],
  [
    "crypto",
    "Crypto",
    [
      ["BTC-USD", "Bitcoin"],
      ["ETH-USD", "Ethereum"],
      ["SOL-USD", "Solana"],
      ["XRP-USD", "XRP"],
      ["BNB-USD", "BNB"],
      ["DOGE-USD", "Dogecoin"],
    ],
  ],
];

export const REGISTRY: RegistryEntry[] = R.flatMap(([sector, , syms]) =>
  syms.map(([symbol, name]) => ({ symbol, name, sector })),
);

/** Common aliases for palette matching (subset of the python ALIASES
 *  table — the ones a human actually types). */
const ALIASES: Record<string, string> = {
  btc: "BTC-USD",
  xbt: "BTC-USD",
  bitcoin: "BTC-USD",
  eth: "ETH-USD",
  ethereum: "ETH-USD",
  sol: "SOL-USD",
  solana: "SOL-USD",
  xrp: "XRP-USD",
  ripple: "XRP-USD",
  bnb: "BNB-USD",
  doge: "DOGE-USD",
  dogecoin: "DOGE-USD",
  gold: "GC=F",
  xauusd: "GC=F",
  silver: "SI=F",
  platinum: "PL=F",
  palladium: "PA=F",
  copper: "HG=F",
  wti: "CL=F",
  crude: "CL=F",
  oil: "CL=F",
  brent: "BZ=F",
  natgas: "NG=F",
  corn: "ZC=F",
  wheat: "ZW=F",
  soybeans: "ZS=F",
  coffee: "KC=F",
  sugar: "SB=F",
  spx: "^GSPC",
  "s&p": "^GSPC",
  sp500: "^GSPC",
  nasdaq: "^IXIC",
  dow: "^DJI",
  nifty: "^NSEI",
  banknifty: "^NSEBANK",
  sensex: "^BSESN",
  ftse: "^FTSE",
  dax: "^GDAXI",
  nikkei: "^N225",
  hangseng: "^HSI",
  vix: "^VIX",
  "10y": "^TNX",
  "5y": "^FVX",
  "13w": "^IRX",
  dxy: "DX-Y.NYB",
  dollar: "DX-Y.NYB",
  eur: "EURUSD=X",
  eurusd: "EURUSD=X",
  gbp: "GBPUSD=X",
  cable: "GBPUSD=X",
  jpy: "USDJPY=X",
  inr: "USDINR=X",
  rupee: "USDINR=X",
  reliance: "RELIANCE.NS",
  tcs: "TCS.NS",
  tatamotors: "TMCV.NS",
};

export function registryLookup(symbol: string): RegistryEntry | null {
  const key = symbol.trim().toUpperCase();
  return REGISTRY.find((e) => e.symbol.toUpperCase() === key) ?? null;
}

/** Normalize loose input to a registry symbol (alias/exact/name),
 *  or null. Mirrors the python normalize() fast paths. */
export function registryNormalize(input: string): string | null {
  const key = input.trim().toLowerCase();
  if (!key) return null;
  if (ALIASES[key]) return ALIASES[key];
  const hit = REGISTRY.find(
    (e) =>
      e.symbol.toLowerCase() === key || e.name.toLowerCase() === key,
  );
  if (hit) return hit.symbol;
  for (const [alias, sym] of Object.entries(ALIASES)) {
    if (alias === key) return sym;
  }
  return null;
}
