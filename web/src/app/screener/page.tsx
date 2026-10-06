"use client";

/**
 * GAUNTLET 7 Piece 3 — /screener: technical + fundamental filter page.
 * The where-DSL is parsed/validated server-side (AST whitelist); this UI
 * only composes the string. Result table density is the Finviz bar.
 */

import { useState } from "react";
import Link from "next/link";

type Row = {
  symbol: string; passed: boolean; error?: string;
  missing_fields?: string[]; errors?: string[];
  fields: {
    price?: number; rsi?: number; sma20?: number; sma50?: number;
    sma200?: number; atr_pct?: number; change_30d?: number;
    realized_vol?: number; above_sma200?: boolean;
    revenue_growth?: number; net_income_margin?: number;
    fcf_margin?: number; quarters?: number;
  };
};
type ScreenPayload = {
  ok?: boolean; universe?: string; n_symbols?: number; n_matched?: number;
  where?: string; rows?: Row[]; n_errors?: number;
  errors_sample?: { symbol: string; error: string }[];
  errors?: string[]; error?: string;
};

const GOLD = "#c8a04b";
const INK = "#f4f7fa";
const DIM = "#76828e";
const LINE = "#1a1f2c";
const GREEN = "#3fb950";
const RED = "#f85149";

const PRESETS: { label: string; where: string; technicalOnly: boolean }[] = [
  { label: "OVERSOLD + TRENDING", where: "rsi < 35 and price > sma200", technicalOnly: true },
  { label: "GROWTH AT REASONABLE RSI", where: "revenue_growth > 15 and rsi < 70", technicalOnly: false },
  { label: "PULLBACK IN UPTREND", where: "above_sma200 and change_30d < 0 and rsi < 55", technicalOnly: true },
  { label: "HIGH VOLATILITY", where: "atr_pct > 2 and realized_vol > 0.3", technicalOnly: true },
  { label: "MARGIN MACHINE", where: "net_income_margin > 20 and fcf_margin > 15", technicalOnly: false },
];

function num(v?: number | null, digits = 2, suffix = ""): string {
  if (v == null || Number.isNaN(v)) return "—";
  return `${v.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits })}${suffix}`;
}

export default function ScreenerPage() {
  const [where, setWhere] = useState("rsi < 65 and price > sma200");
  const [universe, setUniverse] = useState<"registry" | "universe" | "sp500">("registry");
  const [technicalOnly, setTechnicalOnly] = useState(true);
  const [data, setData] = useState<ScreenPayload | null>(null);
  const [loading, setLoading] = useState(false);

  const run = () => {
    setLoading(true);
    const params = new URLSearchParams({
      where, universe,
      technical_only: technicalOnly ? "1" : "0",
    });
    fetch(`/api/desk/screen?${params}`)
      .then((r) => r.json())
      .then(setData)
      .finally(() => setLoading(false));
  };

  // GAUNTLET 7 Piece 7 — canonical CSV export: the file is built by the
  // SAME python screen() output the table renders (byte-identical by
  // construction; the builder is round-trip pinned in tests).
  const csvHref = data?.rows?.length
    ? `/api/desk/export?kind=screener&where=${encodeURIComponent(where)}&universe=${encodeURIComponent(universe)}${technicalOnly ? "&technical=1" : ""}`
    : null;

  return (
    <div className="gdc-root min-h-screen">
      <div className="mx-auto max-w-[1600px] px-4 py-5 sm:px-6">
        <header className="mb-4 flex flex-wrap items-center gap-x-4 gap-y-2">
          <h1 className="gdc-display text-[22px]" style={{ color: INK }}>Screener</h1>
          <span className="gdc-kicker" style={{ color: DIM }}>
            TECHNICAL + FUNDAMENTAL · SAFE EXPRESSION DSL · KEYLESS
          </span>
          <Link href="/" className="gdc-chip text-[10px] px-2 py-0.5 ml-auto" style={{ border: `1px solid ${LINE}`, color: DIM }}>← DECK</Link>
        </header>

        {/* filter bar */}
        <div className="gdc-panel p-3 mb-4">
          <div className="flex flex-wrap gap-2 items-center">
            <input
              value={where}
              onChange={(e) => setWhere(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") run(); }}
              placeholder='e.g. rsi < 30 and revenue_growth > 10 and price > sma200'
              className="gdc-data flex-1 min-w-[320px] bg-transparent text-[12px] px-3 py-2"
              style={{ border: `1px solid ${LINE}`, color: INK, borderRadius: 8 }}
              aria-label="filter expression"
            />
            <select value={universe} onChange={(e) => setUniverse(e.target.value as "registry")}
              className="gdc-chip text-[11px] px-2 py-2 bg-transparent"
              style={{ border: `1px solid ${LINE}`, color: DIM }}
              aria-label="universe">
              <option value="registry">registry (67)</option>
              <option value="universe">universe (24)</option>
              <option value="sp500">S&P 500</option>
            </select>
            <label className="gdc-chip text-[10px] px-2 py-2 flex items-center gap-1.5"
              style={{ border: `1px solid ${LINE}`, color: DIM }}>
              <input type="checkbox" checked={!technicalOnly}
                onChange={(e) => setTechnicalOnly(!e.target.checked)}
                aria-label="include fundamentals" />
              FUNDAMENTALS
            </label>
            <button onClick={run} disabled={loading}
              className="gdc-chip text-[11px] px-4 py-2"
              style={{ border: `1px solid ${GOLD}`, color: GOLD, background: "transparent", cursor: "pointer" }}>
              {loading ? "SCANNING…" : "RUN SCREEN"}
            </button>
          </div>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {PRESETS.map((p) => (
              <button key={p.label}
                onClick={() => { setWhere(p.where); setTechnicalOnly(p.technicalOnly); }}
                className="gdc-chip text-[9px] px-2 py-0.5"
                style={{ border: `1px solid ${LINE}`, color: DIM, background: "transparent", cursor: "pointer" }}
                title={p.where}>
                {p.label}
              </button>
            ))}
          </div>
          <p className="text-[9px] mt-2 gdc-data" style={{ color: DIM }}>
            fields: price rsi sma20 sma50 sma200 atr_pct change_30d realized_vol above_sma200 revenue_growth net_income_margin fcf_margin quarters ·
            operators: &lt; &gt; &lt;= &gt;= = != and or not · calls/scripts rejected server-side
          </p>
        </div>

        {/* results */}
        {data?.ok && (
          <div className="gdc-panel p-3">
            <div className="flex flex-wrap items-center gap-3 mb-2">
              <span className="gdc-kicker" style={{ color: GOLD }}>
                {data.n_matched} / {data.n_symbols} MATCHED
              </span>
              <span className="gdc-data text-[10px]" style={{ color: DIM }}>
                {data.universe} · {data.where}
                {data.n_errors ? ` · ${data.n_errors} errored (fail-soft)` : ""}
              </span>
              {csvHref && (
                <a href={csvHref} download={`screen-${Date.now()}.csv`}
                  className="gdc-chip text-[10px] px-2 py-0.5 ml-auto"
                  style={{ border: `1px solid ${LINE}`, color: GOLD }}>
                  EXPORT CSV
                </a>
              )}
            </div>
            <div className="gdc-scroll overflow-x-auto">
              <table className="w-full text-[11px] tabular-nums" style={{ color: INK }}>
                <thead>
                  <tr className="text-left gdc-kicker" style={{ color: DIM }}>
                    <th className="py-1 pr-3 font-normal">SYMBOL</th>
                    <th className="pr-3 font-normal">PRICE</th>
                    <th className="pr-3 font-normal">RSI</th>
                    <th className="pr-3 font-normal">30D %</th>
                    <th className="pr-3 font-normal">VS SMA200</th>
                    <th className="pr-3 font-normal">ATR %</th>
                    {!technicalOnly && <th className="pr-3 font-normal">REV GROWTH</th>}
                    {!technicalOnly && <th className="pr-3 font-normal">NI MARGIN</th>}
                    {!technicalOnly && <th className="pr-3 font-normal">FCF MARGIN</th>}
                  </tr>
                </thead>
                <tbody>
                  {(data.rows ?? []).map((r) => {
                    const f = r.fields;
                    const vsSma = f.price && f.sma200
                      ? (f.price / f.sma200 - 1) * 100 : null;
                    return (
                      <tr key={r.symbol} style={{ borderTop: `1px solid ${LINE}` }}>
                        <td className="py-0.5 pr-3">
                          <Link href={`/markets/${encodeURIComponent(r.symbol)}`} style={{ color: GOLD }}>
                            {r.symbol}
                          </Link>
                        </td>
                        <td className="pr-3">{num(f.price)}</td>
                        <td className="pr-3" style={{ color: (f.rsi ?? 50) < 30 ? GREEN : (f.rsi ?? 50) > 70 ? RED : INK }}>
                          {num(f.rsi, 1)}
                        </td>
                        <td className="pr-3" style={{ color: (f.change_30d ?? 0) >= 0 ? GREEN : RED }}>
                          {num(f.change_30d, 2, "%")}
                        </td>
                        <td className="pr-3" style={{ color: (vsSma ?? 0) >= 0 ? GREEN : RED }}>
                          {num(vsSma, 1, "%")}
                        </td>
                        <td className="pr-3">{num(f.atr_pct)}</td>
                        {!technicalOnly && <td className="pr-3">{num(f.revenue_growth, 1, "%")}</td>}
                        {!technicalOnly && <td className="pr-3">{num(f.net_income_margin, 1, "%")}</td>}
                        {!technicalOnly && <td className="pr-3">{num(f.fcf_margin, 1, "%")}</td>}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            {!data.rows?.length && (
              <p className="gdc-data text-[11px] py-4 text-center" style={{ color: DIM }}>
                no symbols match — ease up on the filters
              </p>
            )}
          </div>
        )}
        {data && !data.ok && (
          <div className="gdc-panel p-4">
            <p className="gdc-data text-[11px]" style={{ color: RED }}>
              {data.error || (data.errors || []).join(" · ")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

function toCsv(rows: Row[]): string {
  const head = "symbol,price,rsi,sma20,sma50,sma200,atr_pct,change_30d,realized_vol,above_sma200,revenue_growth,net_income_margin,fcf_margin,quarters";
  const lines = rows.map((r) => {
    const f = r.fields;
    return [r.symbol, f.price, f.rsi, f.sma20, f.sma50, f.sma200, f.atr_pct,
      f.change_30d, f.realized_vol, f.above_sma200, f.revenue_growth,
      f.net_income_margin, f.fcf_margin, f.quarters].map(csvCell).join(",");
  });
  return [head, ...lines].join("\n") + "\n";
}

function csvCell(v: unknown): string {
  if (v == null) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
