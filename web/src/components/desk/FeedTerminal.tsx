"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { REASON_COLORS, type DeskEventDTO } from "./useDeskData";

const KIND_COLORS: Record<string, string> = {
  BarReceived: "#4d5761",
  FilterReject: "#d29922",
  NoSetup: "#6b7681",
  SetupCandidate: "#39c5cf",
  VetoDecision: "#8b949e",
  GateDecision: "#e8b440",
  TicketEvent: "#ffd873",
  TicketSendAttempt: "#8b949e",
  TicketSent: "#ffd873",
  HumanResponse: "#39c5cf",
  Fill: "#3fb950",
  Skip: "#8b949e",
  TicketExpired: "#d29922",
  DataQualityFailed: "#f85149",
  ProcessStart: "#8b949e",
  ProcessRecovered: "#8b949e",
  KillSwitch: "#f85149",
};

export function FeedTerminal({
  events, day, live, setLive, speed, setSpeed,
}: {
  events: DeskEventDTO[];
  day: string;
  live: boolean;
  setLive: (v: boolean) => void;
  speed: number;
  setSpeed: (v: number) => void;
}) {
  const [revealed, setRevealed] = useState(0);
  const [filter, setFilter] = useState<string>("ALL");
  const [paused, setPaused] = useState(false);
  const feedRef = useRef<HTMLDivElement>(null);
  const total = events.length;

  // progressive reveal engine — replays the day like a live wire.
  // (parent remounts this component with key={day}, so state resets per day)
  useEffect(() => {
    if (!live || paused || total === 0) return;
    if (revealed >= total) {
      const restart = setTimeout(() => setRevealed(0), 2600);
      return () => clearTimeout(restart);
    }
    const step = Math.max(1, Math.round(speed / 8));
    const t = setTimeout(() => setRevealed((r) => Math.min(total, r + step)), 1000 / speed);
    return () => clearTimeout(t);
  }, [live, paused, revealed, total, speed]);

  useEffect(() => {
    const el = feedRef.current;
    if (el && live) el.scrollTop = el.scrollHeight;
  }, [revealed, live]);

  const shown = useMemo(() => {
    const slice = events.slice(0, revealed);
    return filter === "ALL" ? slice : slice.filter((e) => e.kind === filter);
  }, [events, revealed, filter]);

  const kinds = useMemo(() => {
    const set = new Set(events.map((e) => e.kind));
    return ["ALL", ...Array.from(set).sort()];
  }, [events]);

  const progress = total ? (revealed / total) * 100 : 0;

  return (
    <div className="gdc-panel flex h-full flex-col overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Journal wire</span>
          {live && <span className="gdc-live-dot mb-1 h-1.5 w-1.5 rounded-full bg-[#3fb950]" />}
          <span className="gdc-kicker">{day}</span>
        </div>
        <span className="text-[9px] text-[#98a3af]">{day}</span>
        <div className="ml-auto flex items-center gap-1.5">
          <button
            onClick={() => setLive(!live)}
            className={`gdc-chip cursor-pointer transition-colors ${live ? "text-[#3fb950] border-[#3fb950]/30" : "text-[#aab4bf]"}`}
          >
            {live ? "● LIVE REPLAY" : "○ FROZEN"}
          </button>
          <button
            onClick={() => setPaused(!paused)}
            disabled={!live}
            className="gdc-chip cursor-pointer text-[#aab4bf] disabled:opacity-40"
          >
            {paused ? "▶" : "⏸"}
          </button>
          {[1, 4, 16].map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              disabled={!live}
              className={`gdc-chip cursor-pointer disabled:opacity-40 ${speed === s ? "border-[#e8b440]/30 text-[#e8b440]" : "text-[#98a3af]"}`}
            >
              {s}×
            </button>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-1.5 overflow-x-auto border-b border-white/[0.07] px-3 py-1.5 gdc-scroll">
        {kinds.map((k) => (
          <button
            key={k}
            onClick={() => setFilter(k)}
            className={`shrink-0 rounded-full border px-2.5 py-0.5 text-[9px] transition-all ${
              filter === k
                ? "border-[#e8b440]/35 bg-[#e8b440]/[0.08] text-[#e8b440] shadow-[0_0_14px_rgba(232,180,64,0.18)]"
                : "border-white/[0.07] text-[#98a3af] hover:border-white/[0.14] hover:text-[#aab4bf]"
            }`}
          >
            {k}
          </button>
        ))}
      </div>
      <div ref={feedRef} className="gdc-data gdc-scroll min-h-[280px] flex-1 overflow-y-auto px-4 py-2.5 text-[10px] leading-relaxed lg:max-h-[430px]">
        {shown.length === 0 && (
          <div className="py-6 text-center text-[#8a95a1]">— wire quiet · press LIVE REPLAY —</div>
        )}
        {shown.map((e) => {
          const rc = e.reason_code;
          const color = rc ? REASON_COLORS[rc] ?? "#8b949e" : KIND_COLORS[e.kind] ?? "#8b949e";
          const detail =
            (e.payload.detail as string) ??
            (e.payload.code as string) ??
            (e.payload.decision as string) ??
            (e.payload.channel as string) ??
            (e.payload.bar ? `O:${e.payload.bar.o} C:${e.payload.bar.c}` : "");
          return (
            <div key={e.event_id} className="gdc-feed-line flex gap-2 py-[1px] hover:bg-white/[0.03]">
              <span className="shrink-0 text-[#8a95a1]">{(e.decision_ts ?? e.ts).slice(11, 19)}</span>
              <span className="w-[6.5rem] shrink-0 truncate text-[9px] font-semibold" style={{ color: KIND_COLORS[e.kind] ?? "#8b949e", fontFamily: "var(--font-body)" }}>
                {e.kind}
              </span>
              {rc && (
                <span className="shrink-0 text-[8.5px] font-bold uppercase tracking-[0.08em]" style={{ color, fontFamily: "var(--font-body)" }}>
                  {rc}
                </span>
              )}
              <span className="truncate text-[#aab4bf]">{String(detail).slice(0, 90)}</span>
            </div>
          );
        })}
      </div>
      <div className="border-t border-white/[0.07] px-4 py-1.5">
        <div className="flex items-center justify-between text-[9px] text-[#98a3af]">
          <span>{revealed}/{total} events {filter !== "ALL" && `(filter: ${filter})`}</span>
          <span>{live && revealed >= total ? "↺ restarting…" : ""}</span>
        </div>
        <div className="mt-1 h-[3px] w-full overflow-hidden rounded bg-white/[0.06]">
          <div className="h-full bg-gradient-to-r from-[#7a5c1a] to-[#e8b440] transition-all" style={{ width: `${progress}%` }} />
        </div>
      </div>
    </div>
  );
}
