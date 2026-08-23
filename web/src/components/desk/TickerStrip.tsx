"use client";

import { useEffect, useState } from "react";
import type { BarDTO, OverviewDTO } from "./useDeskData";

interface LiveSpot {
  ok: boolean;
  price: number;
  prev_close: number | null;
  source: string;
  market_time: number | null;
  reference?: { source: string; price: number; as_of: number };
}

function Spark({ data, color = "#e8b440", w = 120, h = 34 }: { data: number[]; color?: string; w?: number; h?: number }) {
  if (data.length < 2) return <svg width={w} height={h} />;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * (h - 4) - 2}`);
  return (
    <svg width={w} height={h} className="overflow-visible">
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.4" strokeLinejoin="round" />
      <circle cx={w} cy={Number(pts[pts.length - 1].split(",")[1])} r="2" fill={color} />
    </svg>
  );
}

export function TickerStrip({
  bars, overview, livePrice,
}: {
  bars: BarDTO[];
  overview: OverviewDTO | null;
  livePrice: number | null;
}) {
  const [spot, setSpot] = useState<LiveSpot | null>(null);
  useEffect(() => {
    let dead = false;
    const load = () =>
      fetch("/api/desk/price")
        .then((r) => r.json())
        .then((d: LiveSpot) => { if (!dead && d.ok) setSpot(d); })
        .catch(() => {});
    void load();
    const t = setInterval(() => void load(), 60_000);
    return () => { dead = true; clearInterval(t); };
  }, []);

  const last = bars[bars.length - 1];
  const journalPrice = livePrice ?? last?.c ?? 0;
  const price = spot?.price ?? journalPrice;
  const dayOpen = spot?.prev_close ?? (() => {
    const dayBars = bars.filter((b) => b.ts_close.slice(0, 10) === last?.ts_close.slice(0, 10));
    return dayBars[0]?.o ?? journalPrice;
  })();
  const dayChange = price - dayOpen;
  const pct = (dayChange / (dayOpen || 1)) * 100;
  const up = dayChange >= 0;
  const closes = bars.slice(-96).map((b) => b.c);
  const atr =
    overview && closes.length > 15
      ? (() => {
          let trSum = 0;
          const n = Math.min(14, bars.length - 1);
          for (let i = bars.length - n; i < bars.length; i++) {
            trSum += Math.max(
              bars[i].h - bars[i].l,
              Math.abs(bars[i].h - bars[i - 1].c),
              Math.abs(bars[i].l - bars[i - 1].c),
            );
          }
          return trSum / n;
        })()
      : null;
  const acc = overview?.account;

  const stats: Array<{ label: string; value: string; color?: string }> = [
    { label: "ATR·14 H1", value: atr ? atr.toFixed(2) : "—" },
    { label: "Paper balance", value: acc ? `$${acc.balance.toFixed(0)}` : "—", color: acc && acc.balance >= 10000 ? "#3fb950" : "#f85149" },
    { label: "Day P&L", value: acc ? `${acc.dailyPnl >= 0 ? "+" : ""}${acc.dailyPnl.toFixed(2)}` : "—", color: acc && acc.dailyPnl >= 0 ? "#3fb950" : "#f85149" },
    { label: "Win / loss", value: acc ? `${acc.wins} : ${acc.losses}` : "—" },
    { label: "Tickets", value: String(overview?.ticketsIssued ?? "—") },
    { label: "Bars", value: overview ? overview.barsProcessed.toLocaleString() : "—" },
  ];

  return (
    <div className="z-40 border-b border-white/[0.08] bg-white/[0.02] backdrop-blur-2xl">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-10 gap-y-4 px-4 py-4 sm:px-7">
        <div className="flex items-center gap-5">
          <div className="flex flex-col gap-2 pb-0.5">
            <span className="flex items-center gap-2 text-[9px] font-semibold uppercase tracking-[0.28em] leading-none text-[#8a95a1]">
              XAU / USD
              {spot ? (
                <span className="flex items-center gap-1 rounded-full border border-[#3fb950]/35 bg-[#3fb950]/[0.08] px-1.5 py-[1px] text-[7.5px] tracking-[0.14em] text-[#3fb950]">
                  <span className="gdc-live-dot h-1 w-1 rounded-full bg-[#3fb950]" /> LIVE
                </span>
              ) : (
                <span className="rounded-full border border-[#d29922]/35 bg-[#d29922]/[0.08] px-1.5 py-[1px] text-[7.5px] tracking-[0.14em] text-[#d29922]">DEMO</span>
              )}
            </span>
            {spot && (
              <span className="text-[7.5px] uppercase tracking-[0.14em] text-[#76828e]">
                {spot.source.split(" ")[0]} · {spot.market_time
                  ? new Date(spot.market_time * 1000).toISOString().slice(11, 16) + " UTC"
                  : ""}
              </span>
            )}
            <span
              className={`gdc-display-num text-[44px] leading-[0.95] ${
                up ? "text-[#3fb950] gdc-glow-green" : "text-[#f85149] gdc-glow-red"
              }`}
            >
              {price.toFixed(2)}
            </span>
          </div>
          <div className="flex flex-col gap-1 pt-4">
            <span className={`text-[13px] font-medium tabular-nums ${up ? "text-[#3fb950]" : "text-[#f85149]"}`}>
              {up ? "▲" : "▼"} {Math.abs(dayChange).toFixed(2)}
            </span>
            <span className={`text-[11px] font-medium tabular-nums ${up ? "text-[#3fb950]/80" : "text-[#f85149]/80"}`}>
              {pct >= 0 ? "+" : ""}{pct.toFixed(2)}% today
            </span>
          </div>
        </div>
        <Spark data={closes} color={up ? "#3fb950" : "#f85149"} w={150} h={40} />
        <div className="flex flex-wrap items-center gap-x-8 gap-y-3">
          {stats.map((s) => (
            <div key={s.label} className="flex flex-col justify-between gap-1.5 pb-0.5">
              <div className="text-[8.5px] font-semibold uppercase tracking-[0.2em] leading-none text-[#76828e]">{s.label}</div>
              <div className="gdc-display-num text-[19px] leading-[1] text-[#f4f7fa]" style={{ color: s.color }}>
                {s.value}
              </div>
            </div>
          ))}
        </div>
        <div className="ml-auto hidden flex-col items-end gap-1 lg:flex">
          <span className="text-[8.5px] font-semibold uppercase tracking-[0.2em] text-[#76828e]">Equity curve</span>
          <Spark data={acc?.equityCurve?.map((p) => p.equity) ?? []} color="#e8b440" w={140} h={38} />
        </div>
      </div>
    </div>
  );
}
