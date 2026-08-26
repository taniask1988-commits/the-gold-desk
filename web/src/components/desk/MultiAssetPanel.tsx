"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface AssetSnapshot {
  symbol: string;
  name: string;
  calendar: string;
  price: number | null;
  prev_close: number | null;
  change_pct: number | null;
  session: string;
  session_vwap: number | null;
  session_relative_pct: number | null;
  session_open_pct: number | null;
  sparkline: number[];
  live: boolean;
  source: string;
  fetched_at: number;
  error: string | null;
}

interface MultiSnapshot {
  ok: boolean;
  as_of?: string;
  assets?: Record<string, AssetSnapshot>;
  errors?: string[];
  cache_hit?: boolean;
}

interface CorrMatrix {
  ok: boolean;
  window?: number;
  method?: string;
  symbols?: string[];
  matrix?: Record<string, Record<string, number | null>>;
}

function fmtPrice(p: number | null | undefined): string {
  if (p === null || p === undefined) return "—";
  if (Math.abs(p) >= 1000) return p.toLocaleString(undefined, { maximumFractionDigits: 2 });
  if (Math.abs(p) >= 10) return p.toFixed(3);
  if (Math.abs(p) >= 1) return p.toFixed(4);
  return p.toFixed(5);
}

function pctStr(p: number | null | undefined, digits = 2): string {
  if (p === null || p === undefined) return "—";
  return `${p >= 0 ? "+" : ""}${p.toFixed(digits)}%`;
}

function pctColor(p: number | null | undefined): string {
  if (p === null || p === undefined) return "#76828e";
  if (p > 0) return "#3fb950";
  if (p < 0) return "#f85149";
  return "#76828e";
}

function Sparkline({ points, color = "#76828e" }: { points: number[]; color?: string }) {
  if (points.length < 2) {
    return <div className="h-7 w-20 text-[8.5px] italic text-[#76828e]">no spark</div>;
  }
  const min = Math.min(...points);
  const max = Math.max(...points);
  const range = max - min || 1;
  const w = 80, h = 28;
  const step = w / (points.length - 1);
  const path = points
    .map((p, i) => {
      const x = i * step;
      const y = h - ((p - min) / range) * (h - 4) - 2;
      return `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} className="overflow-visible">
      <path d={path} fill="none" stroke={color} strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AssetCard({ asset }: { asset: AssetSnapshot }) {
  const live = asset.live;
  const chg = asset.change_pct;
  const rel = asset.session_relative_pct;
  return (
    <div className="gdc-panel flex flex-col gap-1.5 p-3" style={{ minWidth: 0 }}>
      <div className="flex items-baseline gap-2">
        <span className="gdc-data text-[10px] uppercase tracking-[0.14em] text-[#9aa6b3]">
          {asset.symbol}
        </span>
        <span className="truncate text-[10px] text-[#76828e]">{asset.name}</span>
        <span className="ml-auto rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[8.5px] uppercase tracking-wider text-[#76828e]">
          {asset.calendar}
        </span>
      </div>
      <div className="flex items-baseline gap-3">
        <span className="gdc-display text-[15px] text-[#f4f7fa]">
          {fmtPrice(asset.price)}
        </span>
        <span style={{ color: pctColor(chg) }} className="text-[11px] tabular-nums">
          {pctStr(chg)}
        </span>
        {live ? (
          <span className="ml-auto flex items-center gap-1 text-[8.5px] uppercase tracking-[0.15em] text-[#3fb950]">
            <span className="gdc-live-dot h-1 w-1 rounded-full bg-[#3fb950]" />
            live
          </span>
        ) : (
          <span className="ml-auto text-[8.5px] uppercase tracking-[0.15em] text-[#f85149]">
            {asset.error || "down"}
          </span>
        )}
      </div>
      <div className="flex items-center gap-2">
        <Sparkline points={asset.sparkline || []} color={pctColor(rel)} />
        <div className="ml-auto text-right">
          <div className="gdc-kicker text-[7.5px] uppercase tracking-[0.15em] text-[#76828e]">
            sess vwap {asset.session}
          </div>
          <div className="gdc-data text-[10px] text-[#9aa6b3]">{fmtPrice(asset.session_vwap)}</div>
          <div style={{ color: pctColor(rel) }} className="text-[10px] tabular-nums">
            {pctStr(rel, 3)} rel
          </div>
        </div>
      </div>
    </div>
  );
}

function CorrMatrixGrid({ corr }: { corr: CorrMatrix }) {
  const syms = corr.symbols || [];
  const matrix = corr.matrix || {};
  if (syms.length < 2) {
    return <div className="text-[11px] italic text-[#76828e]">correlation unavailable</div>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
        <thead>
          <tr>
            <th className="px-1 py-1 text-left text-[#76828e]"> </th>
            {syms.map((s) => (
              <th key={s} className="px-1 py-1 text-right text-[#9aa6b3]">{s}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {syms.map((r) => (
            <tr key={r}>
              <td className="px-1 py-1 text-right text-[#9aa6b3]">{r}</td>
              {syms.map((c) => {
                const v = matrix[r]?.[c];
                const cellColor = v === null || v === undefined
                  ? "#76828e"
                  : v > 0.5 ? "#3fb950"
                  : v < -0.3 ? "#f85149"
                  : v > 0 ? "#7ab5e0"
                  : "#76828e";
                return (
                  <td key={c} className="px-1 py-1 text-right" style={{ color: cellColor }}>
                    {v === null || v === undefined ? "—" : v.toFixed(3)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function MultiAssetPanelImpl() {
  const [snap, setSnap] = useState<MultiSnapshot | null>(null);
  const [corr, setCorr] = useState<CorrMatrix | null>(null);
  const [window, setWindow] = useState<number>(30);
  const [method, setMethod] = useState<"pearson" | "spearman">("pearson");

  const load = useCallback(async () => {
    try {
      const [s, c] = await Promise.all([
        fetch("/api/desk/markets/multi").then((x) => x.json()),
        fetch(`/api/desk/markets/correlation?window=${window}d&method=${method}`).then((x) => x.json()),
      ]);
      setSnap(s);
      setCorr(c);
    } catch {
      setSnap(null);
    }
  }, [window, method]);

  useEffect(() => {
    const kick = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), 60_000);
    return () => { clearTimeout(kick); clearInterval(t); };
  }, [load]);

  const assets = snap?.assets || {};
  const order = ["GC=F", "ES=F", "^TNX", "DX-Y.NYB", "BTC-USD", "^VIX", "CL=F", "EURUSD=X"];
  const errors = snap?.errors || [];

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Multi-asset monitor</span>
        <span className="gdc-kicker">
          gold · s&p e-mini · 10y yield · dxy · btc · vix · wti · eur/usd — keyless yahoo
        </span>
        <span className="ml-auto flex items-center gap-2 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
          {snap?.ok ? <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" /> : null}
          {snap?.ok ? (snap.cache_hit ? "cached" : "live") : snap === null ? "loading…" : "feed unreachable"}
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        {order.map((sym) => (
          <AssetCard key={sym} asset={assets[sym] || { symbol: sym, name: "", calendar: "", price: null, prev_close: null, change_pct: null, session: "off", session_vwap: null, session_relative_pct: null, session_open_pct: null, sparkline: [], live: false, source: "", fetched_at: 0, error: "loading" }} />
        ))}
      </div>
      {errors.length > 0 && (
        <div className="text-[9.5px] italic text-[#f85149]">
          failed symbols (fail-soft, not fatal): {errors.join(", ")}
        </div>
      )}
      <div className="space-y-2 border-t border-white/[0.08] pt-2">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="gdc-kicker text-[#9aa6b3]">cross-asset correlation</span>
          <span className="text-[10px] text-[#76828e]">{corr?.method || "pearson"} · {(corr?.window || window) + "d"}</span>
          <div className="ml-auto flex items-center gap-2 text-[9.5px]">
            <button
              onClick={() => setMethod("pearson")}
              className={`px-1.5 py-0.5 rounded ${method === "pearson" ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
            >pearson</button>
            <button
              onClick={() => setMethod("spearman")}
              className={`px-1.5 py-0.5 rounded ${method === "spearman" ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
            >spearman</button>
            {[30, 60, 90].map((w) => (
              <button
                key={w}
                onClick={() => setWindow(w)}
                className={`px-1.5 py-0.5 rounded ${window === w ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
              >{w}d</button>
            ))}
          </div>
        </div>
        {corr?.ok ? <CorrMatrixGrid corr={corr} /> : <div className="text-[11px] italic text-[#76828e]">loading matrix…</div>}
      </div>
    </div>
  );
}

export const MultiAssetPanel = memo(MultiAssetPanelImpl);
