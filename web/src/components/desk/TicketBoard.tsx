"use client";

import { memo } from "react";
import type { TicketDTO } from "./useDeskData";

const STAGES = ["PENDING_SEND", "SENT", "FILL/HUMAN_SKIP/EXPIRED"];

function stageOf(t: TicketDTO): number {
  if (["FILL", "HUMAN_SKIP", "TICKET_EXPIRED", "CANCELLED"].includes(t.status)) return 2;
  if (t.status === "SENT") return 1;
  return 0;
}

const STATUS_COLOR: Record<string, string> = {
  FILL: "#3fb950",
  HUMAN_SKIP: "#8b949e",
  TICKET_EXPIRED: "#d29922",
  SENT: "#ffd873",
  PENDING_SEND: "#8b949e",
  CANCELLED: "#8b949e",
};

function TicketRow({ t }: { t: TicketDTO }) {
  const color = STATUS_COLOR[t.status] ?? "#8b949e";
  const st = stageOf(t);
  return (
    <div className="rounded-xl border border-white/[0.08] bg-white/[0.03] p-3 transition-colors hover:border-white/[0.14] hover:bg-white/[0.045]">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className={`gdc-chip font-bold ${t.side === "buy" ? "text-[#3fb950] border-[#3fb950]/30" : "text-[#f85149] border-[#f85149]/30"}`}>
          {t.side.toUpperCase()}
        </span>
        <span className="text-[10px] text-[#98a3af]">{t.decision_ts.slice(0, 16).replace("T", " ")}</span>
        <span className="gdc-display-num text-[14px] text-[#f4f7fa]">
          {t.entry.toFixed(2)} <span className="text-[9px] font-medium tracking-[0.1em] text-[#76828e]">E</span>
          <span className="mx-1.5 text-[#76828e]">·</span>
          <span className="text-[#f85149]">{t.stop.toFixed(2)}</span> <span className="text-[9px] font-medium tracking-[0.1em] text-[#76828e]">S</span>
          <span className="mx-1.5 text-[#76828e]">·</span>
          <span className="text-[#3fb950]">{t.target.toFixed(2)}</span> <span className="text-[9px] font-medium tracking-[0.1em] text-[#76828e]">T</span>
        </span>
        <span className="gdc-data text-[9.5px] text-[#8a95a1]">
          {t.lots} lots · {t.rr}R · risk ${t.riskMoney}
        </span>
        <span className="gdc-chip ml-auto font-bold" style={{ color, borderColor: color + "44" }}>
          {t.status}
        </span>
        {t.pnl !== null && (
          <span className="gdc-display-num text-[14px]" style={{ color: t.pnl >= 0 ? "#3fb950" : "#f85149" }}>
            {t.pnl >= 0 ? "+" : ""}${t.pnl.toFixed(2)} ({t.exitReason})
          </span>
        )}
      </div>
      <div className="mt-2 flex items-center gap-1.5">
        {STAGES.map((s, i) => (
          <div key={s} className="flex items-center gap-1.5">
            <div
              className="h-[3px] w-16 rounded-full"
              style={{ background: i <= st ? color : "#1b222b" }}
            />
            {i < STAGES.length - 1 && (
              <div className="h-[3px] flex-1" style={{ background: i < st ? color : "#1b222b" }} />
            )}
          </div>
        ))}
        <span className="ml-1 text-[8.5px] text-[#8a95a1]">{t.ticket_id.slice(-8)} · veto: {t.veto}</span>
      </div>
    </div>
  );
}

const TicketRowMemo = memo(TicketRow);

function TicketBoardImpl({ tickets }: { tickets: TicketDTO[] }) {
  return (
    <div className="gdc-panel overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Ticket lifecycle</span>
        </div>
        <span className="text-[9px] text-[#98a3af]">
          {tickets.length} ISSUED · PYTHON OWNS THE PACKAGE · YOU PASTE OR SKIP
        </span>
      </div>
      <div className="gdc-scroll max-h-[420px] space-y-2 overflow-y-auto p-3">
        {tickets.length === 0 && (
          <div className="py-6 text-center text-xs text-[#8a95a1]">No tickets — the desk did its job: nothing.</div>
        )}
        {tickets.map((t) => (
          <TicketRowMemo key={t.ticket_id} t={t} />
        ))}
      </div>
    </div>
  );
}

export const TicketBoard = memo(TicketBoardImpl);
