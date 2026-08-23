"use client";

import { useEffect, useState } from "react";
import { sessionOfHour } from "./useDeskData";

export function HeaderBar({
  phase, demo, hash, composite, spanDays, onChat, chatOpen,
}: {
  phase: number;
  demo: boolean;
  hash: string | null;
  composite: number;
  spanDays: number;
  onChat: () => void;
  chatOpen: boolean;
}) {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => {
    // clock starts client-side only (no hydration mismatch)
    const kick = setTimeout(() => setNow(new Date()), 0);
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => { clearTimeout(kick); clearInterval(t); };
  }, []);
  const utc = now ? now.toISOString().slice(11, 19) : "--:--:--";
  const hour = now ? Number(now.toISOString().slice(11, 13)) : 0;
  const session = sessionOfHour(hour);
  const biasColor =
    composite >= 60 ? "#3fb950" : composite <= 40 ? "#f85149" : "#d29922";

  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.08] bg-[#08090d]/70 backdrop-blur-2xl">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-5 gap-y-2 px-4 py-3 sm:px-7">
        <div className="flex items-center gap-3.5">
          <div className="relative flex h-9 w-9 items-center justify-center rounded-xl border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#e8b440]/5 shadow-[0_0_28px_rgba(232,180,64,0.30)]">
            <span className="gdc-display text-[16px] font-semibold text-[#e8b440] gdc-glow-gold">Au</span>
          </div>
          <div className="leading-none">
            <h1 className="gdc-display text-[19px] font-semibold tracking-[0.02em] text-white">
              Gold Desk Command
            </h1>
            <div className="mt-1.5 flex items-center gap-2 text-[9.5px] font-medium tracking-[0.18em] text-[#8a95a1] uppercase">
              <span>XAUUSD · H1</span>
              <span className="h-[3px] w-[3px] rounded-full bg-[#e8b440]/60" />
              <span>Fail-closed</span>
              <span className="h-[3px] w-[3px] rounded-full bg-[#e8b440]/60" />
              <span>Human-in-the-loop</span>
            </div>
          </div>
        </div>

        <div className="ml-auto flex flex-wrap items-center gap-2 text-[10px]">
          <button
            onClick={onChat}
            aria-label="Chat with The Desk"
            className={`gdc-chip cursor-pointer border-[#e8b440]/35 transition-all hover:bg-[#e8b440]/[0.12] ${
              chatOpen ? "bg-[#e8b440]/[0.12] text-[#e8b440]" : "text-[#e8b440]"
            }`}
          >
            <span className="gdc-display text-[12px] italic">The Desk</span>
            <span aria-hidden>✦</span> chat
          </button>
          <span className="gdc-chip text-[#aab4bf]">
            <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
            Phase {phase} · No LLM
          </span>
          <span className="gdc-chip border-[#e8b440]/30 text-[#e8b440]">{session}</span>
          <span className="gdc-chip gdc-data text-[#aab4bf]">
            <span className="text-[#f4f7fa]">{utc}</span>
            <span className="text-[#76828e]">UTC</span>
          </span>
          <span className="gdc-chip" style={{ color: biasColor, borderColor: biasColor + "44" }}>
            Driver bias <span className="gdc-display-num text-[12px] font-semibold">{composite.toFixed(0)}</span>/100
          </span>
          {hash && (
            <span className="gdc-chip gdc-data hidden text-[#76828e] md:inline-flex" title="constitution content hash">
              ⛓ {hash}
            </span>
          )}
          <span className="gdc-chip text-[#8a95a1]">{spanDays}d journal</span>
          {demo && (
            <span className="gdc-chip border-[#d29922]/40 text-[#d29922]">
              Demo feed
            </span>
          )}
        </div>
      </div>
    </header>
  );
}
