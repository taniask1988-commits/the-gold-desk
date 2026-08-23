// Market Driver Engine — taxonomy from download/gold_desk_v1/docs/MARKET_DRIVERS.md
// Values are DETERMINISTIC SIMULATIONS seeded by day+tick (clearly watermarked).
// Real feeds are a data-plane task; this board proves the telemetry surface.

export type Stance = "TAILWIND" | "HEADWIND" | "NEUTRAL";
export type Tier = 1 | 2 | 3 | 4;

export interface DriverDef {
  id: string;
  tier: Tier;
  name: string;
  unit: string;
  format: (v: number) => string;
  neutral: [number, number]; // NEUTRAL band; above/below → stance by `upIs`
  upIs: "TAILWIND" | "HEADWIND"; // which stance a HIGH value implies
  why: string;
  display: string; // short value explanation for the UI
}

export interface DriverReading {
  id: string;
  tier: Tier;
  name: string;
  unit: string;
  value: number;
  delta: number;
  stance: Stance;
  why: string;
  display: string;
  formatted: string;
  history: number[];
}

function mulberry32(a: number) {
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

export const DRIVERS: DriverDef[] = [
  {
    id: "D1",
    tier: 1,
    name: "10y Real Yield (TIPS)",
    unit: "%",
    format: (v) => v.toFixed(2) + "%",
    neutral: [1.6, 1.9],
    upIs: "HEADWIND",
    why: "Gold's opportunity cost; historical corr ≈ −0.7",
    display: "gravity well",
  },
  {
    id: "D2",
    tier: 1,
    name: "DXY Dollar Index",
    unit: "idx",
    format: (v) => v.toFixed(2),
    neutral: [98.5, 100.5],
    upIs: "HEADWIND",
    why: "Gold priced in USD; strong dollar = headwind",
    display: "the denominator",
  },
  {
    id: "D3",
    tier: 1,
    name: "Fed Path (1M T-bill)",
    unit: "%",
    format: (v) => v.toFixed(2) + "%",
    neutral: [3.75, 4.05],
    upIs: "HEADWIND",
    why: "Front-end rate = market's near-policy expectation (Treasury 1-Mo)",
    display: "rate expectations",
  },
  {
    id: "D4",
    tier: 1,
    name: "10y Breakeven Infl.",
    unit: "%",
    format: (v) => v.toFixed(2) + "%",
    neutral: [2.25, 2.45],
    upIs: "TAILWIND",
    why: "Inflation up with flat nominals = real yields down",
    display: "stagflation sensor",
  },
  {
    id: "D5",
    tier: 2,
    name: "COT Managed-Money Net",
    unit: "k lots",
    format: (v) => v.toFixed(0) + "k",
    neutral: [150, 240],
    upIs: "HEADWIND", // extremes = crowded
    why: "Crowded net-longs liquidate; flips mark regime turns",
    display: "positioning",
  },
  {
    id: "D6",
    tier: 2,
    name: "ETF Flows (30d)",
    unit: "t",
    format: (v) => (v > 0 ? "+" : "") + v.toFixed(0) + "t",
    neutral: [-20, 40],
    upIs: "TAILWIND",
    why: "Cleanest public daily demand signal",
    display: "visible demand",
  },
  {
    id: "D7",
    tier: 2,
    name: "Central Bank Buying",
    unit: "t/qtr",
    format: (v) => v.toFixed(0) + "t",
    neutral: [180, 260],
    upIs: "TAILWIND",
    why: "Structural bid; decouples gold from real yields",
    display: "the era's bid",
  },
  {
    id: "D8",
    tier: 2,
    name: "COMEX–LBMA EFP",
    unit: "$",
    format: (v) => "$" + v.toFixed(2),
    neutral: [8, 22],
    upIs: "TAILWIND", // wide EFP = physical scarcity premium
    why: "Blowouts = physical stress (Mar-2020 echo)",
    display: "stress gauge",
  },
  {
    id: "D9",
    tier: 3,
    name: "Event Risk (hrs to print)",
    unit: "h",
    format: (v) => v.toFixed(0) + "h",
    neutral: [8, 999],
    upIs: "HEADWIND", // close event = danger for new entries
    why: "Hours to NFP (first Friday 13:30 UTC) — blackout windows in the constitution",
    display: "blackout clock",
  },
  {
    id: "D10",
    tier: 3,
    name: "VIX Risk Regime",
    unit: "idx",
    format: (v) => v.toFixed(1),
    neutral: [14, 20],
    upIs: "TAILWIND",
    why: "Safe-haven bid depth rises with VIX > 20",
    display: "fear gauge",
  },
  {
    id: "D11",
    tier: 4,
    name: "Session Liquidity",
    unit: "score",
    format: (v) => v.toFixed(0) + "/10",
    neutral: [5, 11],
    upIs: "TAILWIND",
    why: "LDN–NY overlap 13:00–17:00 UTC is the deep window",
    display: "microstructure",
  },
  {
    id: "D12",
    tier: 4,
    name: "Dealer Gamma Regime",
    unit: "GEX",
    format: (v) => (v > 0 ? "+" : "") + v.toFixed(0),
    neutral: [-25, 25],
    upIs: "HEADWIND", // negative gamma amplifies moves both ways
    why: "Past gamma-flip, hedging amplifies instead of absorbing",
    display: "pin vs amplify",
  },
  {
    id: "D13",
    tier: 4,
    name: "Spread Discipline",
    unit: "x min",
    format: (v) => v.toFixed(2) + "x",
    neutral: [0, 1.4],
    upIs: "HEADWIND",
    why: "Rollover/LDN-pre-open spread triples — retail bleeds here",
    display: "cost of doing business",
  },
];

const BASES: Record<string, number> = {
  D1: 1.74, D2: 99.4, D3: 3.9, D4: 2.34, D5: 196, D6: 12, D7: 222,
  D8: 14.5, D9: 19, D10: 16.8, D11: 7, D12: 12, D13: 1.1,
};

const VOL: Record<string, number> = {
  D1: 0.028, D2: 0.34, D3: 0.05, D4: 0.03, D5: 9, D6: 8, D7: 6,
  D8: 2.2, D9: 3.5, D10: 1.2, D11: 1.4, D12: 12, D13: 0.16,
};

export function stanceFor(def: DriverDef, v: number): Stance {
  if (v < def.neutral[0]) return def.upIs === "TAILWIND" ? "HEADWIND" : "TAILWIND";
  if (v > def.neutral[1]) return def.upIs;
  return "NEUTRAL";
}

export function simulateDrivers(dayKey: string, tick: number): DriverReading[] {
  return DRIVERS.map((def, idx) => {
    const rng = mulberry32(hashStr(`${dayKey}:${def.id}:${tick >> 3}`));
    const base = BASES[def.id];
    const vol = VOL[def.id];
    // slow random walk driven by tick (8 ticks per step for stability)
    const steps = 24;
    const history: number[] = [];
    let v = base;
    for (let s = 0; s < steps; s++) {
      v += (rng() - 0.5) * vol;
      v = v * 0.92 + base * 0.08; // mean-revert to base
      history.push(v);
    }
    const value = history[steps - 1];
    const prev = history[steps - 2] ?? value;
    const delta = value - prev;
    return {
      id: def.id,
      tier: def.tier,
      name: def.name,
      unit: def.unit,
      value,
      delta,
      stance: stanceFor(def, value),
      why: def.why,
      display: def.display,
      formatted: def.format(value),
      history,
    };
  });
}

// Composite gold-bias score from tier weights
export function compositeBias(drivers: DriverReading[]): number {
  const weight: Record<Tier, number> = { 1: 3, 2: 2.5, 3: 1.5, 4: 1 };
  let score = 0;
  let wsum = 0;
  for (const d of drivers) {
    const w = weight[d.tier];
    wsum += w;
    if (d.stance === "TAILWIND") score += w;
    else if (d.stance === "HEADWIND") score -= w;
  }
  const pct = ((score / wsum) * 100 + 100) / 2; // 0..100
  return Math.round(pct * 10) / 10;
}
