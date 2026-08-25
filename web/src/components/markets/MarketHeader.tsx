"use client";

import { memo, useEffect, useState } from "react";

/** Dual clock — UTC + IST (India is our differentiator). Client-only
 *  start (null until mounted) so SSR markup never mismatches. */
function MarketHeaderImpl({
  breadth,
  avgPct,
  symbolCount,
}: {
  breadth: { up: number; down: number; flat: number } | null;
  avgPct: number | null;
  symbolCount: number;
}) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    const kick = setTimeout(() => setNow(new Date()), 0);
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => {
      clearTimeout(kick);
      clearInterval(t);
    };
  }, []);

  const fmt = (tz: string) =>
    now
      ? new Intl.DateTimeFormat("en-GB", {
          timeZone: tz,
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
          hourCycle: "h23",
        }).format(now)
      : "--:--:--";
  const utc = fmt("UTC");
  const ist = fmt("Asia/Kolkata");

  const up = breadth?.up ?? 0;
  const down = breadth?.down ?? 0;
  const total = up + down + (breadth?.flat ?? 0);
  const upShare = total > 0 ? (up / total) * 100 : 50;

  return (
    <header className="sticky top-0 z-50 border-b border-[#1a1f2c] bg-[#08090d]">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-5 gap-y-2.5 px-4 py-3 sm:px-7">
        <div className="flex items-center gap-3.5">
          <div className="relative flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[#c8a04b]/40 bg-[#0f1219]">
            <span className="gdc-display text-[14px] font-semibold text-[#c8a04b]">MG</span>
          </div>
          <div className="min-w-0 leading-none">
            <h1 className="gdc-script text-[30px] leading-none text-[#f0e6d2] sm:text-[38px]">
              Market Gauntlet
            </h1>
            <div
              className="gdc-spec mt-1.5 flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1"
              style={{ opacity: 0.55, fontSize: "9.5px" }}
            >
              <span>Multi-Market · {symbolCount} Symbols · 9 Sectors</span>
              <span className="hidden h-[3px] w-[3px] rounded-full bg-[#e8b440]/60 sm:block" />
              <span className="hidden sm:inline">Free Keyless Feeds</span>
              <span className="hidden h-[3px] w-[3px] rounded-full bg-[#e8b440]/60 sm:block" />
              <span className="hidden text-[#6fa97a] sm:inline">India Desk</span>
            </div>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2 text-[10px]">
          <a
            href="/"
            className="gdc-chip border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
            aria-label="Back to the Gold Desk command deck"
          >
            <span aria-hidden>◂</span> Deck
          </a>
          <span className="gdc-chip text-[#aab4bf]">
            <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
            Live Board
          </span>
          {breadth && (
            <span className="gdc-chip gdc-data" title="advancers / decliners across all board rows">
              <span className="text-[#6fa97a]">{up} ▲</span>
              <span className="text-[#5a6272]">/</span>
              <span className="text-[#b85c5c]">{down} ▼</span>
              <span className="text-[#76828e]">breadth</span>
            </span>
          )}
          {avgPct != null && (
            <span
              className="gdc-chip gdc-data"
              style={{
                color: avgPct >= 0 ? "#6fa97a" : "#b85c5c",
                borderColor: avgPct >= 0 ? "rgba(111,169,122,0.35)" : "rgba(184,92,92,0.35)",
              }}
              title="average change_pct across the whole board"
            >
              AVG {avgPct >= 0 ? "+" : ""}
              {avgPct.toFixed(2)}%
            </span>
          )}
          <span className="gdc-chip gdc-data text-[#aab4bf]">
            <span className="text-[#f4f7fa]">{utc}</span>
            <span className="text-[#76828e]">UTC</span>
          </span>
          <span className="gdc-chip gdc-data border-[#c8a04b]/30 text-[#e2c074]" title="India Standard Time — the India desk clock">
            <span>{ist}</span>
            <span className="text-[#76828e]">IST</span>
          </span>
        </div>
      </div>

      {/* breadth bar — flat two-segment flex, no animation */}
      {breadth && total > 0 && (
        <div className="mx-auto max-w-[1600px] px-4 pb-2 sm:px-7">
          <div className="flex h-[3px] w-full overflow-hidden rounded-full bg-[#1a1f2c]">
            <div className="h-full bg-[#6fa97a]" style={{ width: `${upShare}%` }} />
            <div className="h-full bg-[#b85c5c]" style={{ width: `${100 - upShare}%` }} />
          </div>
        </div>
      )}
    </header>
  );
}

export const MarketHeader = memo(MarketHeaderImpl);
