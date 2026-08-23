"use client";

import { useState } from "react";
import { REASON_COLORS, type ReplayDTO } from "./useDeskData";

export function ReplayPanel({
  replay, days, day, setDay,
}: {
  replay: ReplayDTO | null;
  days: string[];
  day: string;
  setDay: (d: string) => void;
}) {
  const [sel, setSel] = useState<string | null>(null);
  const bars = replay?.bars ?? [];
  const selected = bars.find((b) => b.decisionTs === sel) ?? null;
  const ticketDays = new Set(
    (replay?.tickets ?? []).map((t) => String(t.decision_ts ?? "").slice(0, 10)),
  );

  return (
    <div className="gdc-panel flex h-full flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Day replay</span>
          <span className="gdc-kicker">why this bar</span>
        </div>
        <select
          value={day}
          onChange={(e) => setDay(e.target.value)}
          className="gdc-scroll ml-auto max-w-[10rem] cursor-pointer rounded-full border border-white/[0.12] bg-white/[0.05] px-3 py-1 text-[10px] text-[#e9edf2] backdrop-blur-md outline-none transition-colors focus:border-[#e8b440]/40 hover:border-white/[0.2]"
        >
          {days.map((d) => (
            <option key={d} value={d}>
              {d}
              {ticketDays.has(d) ? " ●" : ""}
            </option>
          ))}
        </select>
        <span className="text-[9px] text-[#98a3af]">● ticket day</span>
      </div>
      <div className="gdc-scroll max-h-[340px] flex-1 overflow-y-auto">
        <table className="gdc-data w-full text-[10px] tabular-nums">
          <thead className="sticky top-0 bg-[#0b0e13]/85 text-[#8a95a1] backdrop-blur-xl">
            <tr className="border-b border-white/[0.07] text-left">
              <th className="px-3 py-1.5 font-normal">BAR (UTC)</th>
              <th className="px-1 py-1.5 font-normal">O</th>
              <th className="px-1 py-1.5 font-normal">H</th>
              <th className="px-1 py-1.5 font-normal">L</th>
              <th className="px-1 py-1.5 font-normal">C</th>
              <th className="px-3 py-1.5 text-right font-normal">TERMINAL CODE</th>
            </tr>
          </thead>
          <tbody>
            {bars.map((b) => {
              const color = b.code ? REASON_COLORS[b.code] ?? "#8b949e" : "#4d5761";
              const isSel = sel === b.decisionTs;
              return (
                <tr
                  key={b.decisionTs}
                  onClick={() => setSel(isSel ? null : b.decisionTs)}
                  className={`cursor-pointer border-b border-white/[0.05] transition-colors hover:bg-white/[0.03] ${isSel ? "bg-[#e8b440]/[0.07]" : ""}`}
                >
                  <td className="px-3 py-1 text-[#aab4bf]">{b.decisionTs.slice(11, 16)}</td>
                  <td className="px-1 py-1">{b.o.toFixed(2)}</td>
                  <td className="px-1 py-1 text-[#3fb950]/80">{b.h.toFixed(2)}</td>
                  <td className="px-1 py-1 text-[#f85149]/80">{b.l.toFixed(2)}</td>
                  <td className="px-1 py-1">{b.c.toFixed(2)}</td>
                  <td className="px-3 py-1 text-right font-bold" style={{ color }}>
                    {b.code}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {selected && (
        <div className="border-t border-white/[0.07] bg-white/[0.03] px-4 py-2">
          <div className="text-[9px] tracking-[0.16em] text-[#98a3af]">
            WHY THIS BAR — {selected.decisionTs.slice(11, 16)} UTC
          </div>
          <div className="mt-1 space-y-0.5">
            {selected.story.map((s, i) => (
              <div key={i} className="flex gap-2 text-[10px]">
                <span className="w-[7rem] shrink-0 text-[#aab4bf]">{s.kind}</span>
                {s.code && (
                  <span className="font-bold" style={{ color: REASON_COLORS[s.code] ?? "#8b949e" }}>
                    [{s.code}]
                  </span>
                )}
                <span className="text-[#aab4bf]">{s.detail}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
