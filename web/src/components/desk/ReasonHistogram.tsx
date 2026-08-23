"use client";

import { useState } from "react";
import { REASON_COLORS } from "./useDeskData";

export function ReasonHistogram({
  dayHist, allHist, scope, setScope,
}: {
  dayHist: Record<string, number>;
  allHist: Record<string, number>;
  scope: "DAY" | "ALL";
  setScope: (s: "DAY" | "ALL") => void;
}) {
  const [hover, setHover] = useState<string | null>(null);
  const hist = (scope === "DAY" ? dayHist : allHist) ?? {};
  const entries = Object.entries(hist);
  const max = Math.max(...entries.map(([, v]) => v), 1);
  const total = entries.reduce((a, [, v]) => a + v, 0);
  return (
    <div className="gdc-panel flex h-full flex-col overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Reason codes</span>
          <span className="gdc-kicker">every bar ends with one</span>
        </div>
        <div className="flex items-center gap-1">
          {(["DAY", "ALL"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setScope(s)}
              className={`rounded-full border px-2.5 py-0.5 text-[9px] transition-all ${
                scope === s
                  ? "border-[#e8b440]/35 bg-[#e8b440]/[0.08] text-[#e8b440] shadow-[0_0_14px_rgba(232,180,64,0.18)]"
                  : "border-white/[0.07] text-[#98a3af] hover:border-white/[0.14] hover:text-[#aab4bf]"
              }`}
            >
              {s === "DAY" ? "DAY VIEW" : "ALL-TIME"}
            </button>
          ))}
        </div>
      </div>
      <div className="gdc-scroll flex-1 space-y-[3px] overflow-y-auto p-3 lg:max-h-[300px]">
        {entries.length === 0 && (
          <div className="py-6 text-center text-[#8a95a1]">no bars this scope yet</div>
        )}
        {entries.map(([code, n]) => {
          const color = REASON_COLORS[code] ?? "#8b949e";
          const w = (n / max) * 100;
          return (
            <div
              key={code}
              className="group flex items-center gap-2 text-[10px]"
              onMouseEnter={() => setHover(code)}
              onMouseLeave={() => setHover(null)}
            >
              <span className={`w-[9.5rem] shrink-0 truncate text-right ${hover === code ? "text-[#e9edf2]" : "text-[#aab4bf]"}`}>
                {code}
              </span>
              <div className="relative h-[14px] flex-1 overflow-hidden rounded-full bg-white/[0.05] ring-1 ring-inset ring-white/[0.05]">
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{ width: `${w}%`, background: `linear-gradient(90deg, ${color}55, ${color})`, boxShadow: hover === code ? `0 0 12px ${color}44` : "none" }}
                />
              </div>
              <span className="gdc-display-num w-12 shrink-0 text-right text-[15px]" style={{ color }}>
                {n}
              </span>
              <span className="w-10 shrink-0 text-right tabular-nums text-[#8a95a1]">
                {((n / total) * 100).toFixed(0)}%
              </span>
            </div>
          );
        })}
      </div>
      <div className="border-t border-white/[0.07] px-4 py-1.5 text-[8.5px] tracking-[0.1em] text-[#98a3af]">
        {scope === "DAY" ? "This day" : "Whole journal"} · the histogram is the diagnosis — “no edge” vs “spread filter ate London open”
      </div>
    </div>
  );
}
