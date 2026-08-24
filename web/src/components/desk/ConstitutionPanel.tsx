"use client";

import { memo } from "react";

const LAWS: Array<[string, string]> = [
  ["L1", "Blindfold — veto never sees PnL, budget, streaks, or score"],
  ["L2", "Python owns the package — entry, stop, target, expiry"],
  ["L3", "Veto is binary — ENDORSE or VETO, nothing else"],
  ["L4", "Fixed fraction — no Kelly, no reduce_size"],
  ["L5", "Fail closed — timeout, bad JSON, stale data → no trade"],
  ["L6", "Endorsement ≠ fill — gate re-checks at ticket time"],
  ["L7", "Write-only memory — no retrieval in the live loop"],
  ["L8", "Telegram quiet, journal loud — every bar gets a code"],
  ["L9", "Human is the only agent — the rest is a pipeline"],
  ["L10", "Simulator precedes LLM — the exam comes first"],
  ["L11", "No market facts from LLM memory — data comes from the plane"],
  ["L12", "Constitution is human-owned — never mutated at runtime"],
  ["L13", "Fail closed on time — late paste = dead ticket, no chase"],
  ["L14", "Challenge survival beats return"],
  ["L15", "Scope is v1 — pyramids, RL, live broker: dead"],
];

export function LawsMarquee() {
  const items = [...LAWS, ...LAWS];
  return (
    <div className="overflow-hidden border-t border-[#1a1f2c] bg-[#0f1219] py-2">
      <div className="gdc-marquee-track">
        {items.map(([id, text], i) => (
          <span key={i} className="mx-6 flex items-center gap-2 text-[9px] tracking-[0.12em]">
            <span className="font-bold text-[#c8a04b]">{id}</span>
            <span className="text-[#a5afba]">{text}</span>
            <span className="text-[#232c36]">◆</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function ConstitutionPanelImpl({
  hash, blockedCount, phase,
}: {
  hash: string | null;
  // M4: null while loading or when the harness is unreachable — show a
  // loading/unknown state instead of a hardcoded "30 BLOCKED" number.
  blockedCount: number | null;
  phase: number | null;
}) {
  const blockedUnknown = blockedCount === null;
  const blocked = blockedCount ?? 0;
  return (
    <div className="gdc-panel gdc-thermal-line overflow-hidden">
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-accent text-[20px] text-[#f4f7fa]">Constitution &amp; laws</span>
          <span className="gdc-spec">human-owned · hashed · immutable</span>
        </div>
        <span
          className={`gdc-chip ${
            blockedUnknown
              ? "text-[#76828e] border-[#76828e]/30"
              : blocked > 0
                ? "text-[#d29922] border-[#d29922]/30"
                : "text-[#3fb950] border-[#3fb950]/30"
          }`}
        >
          {blockedUnknown
            ? "LOADING…"
            : blocked > 0
              ? `FAIL-CLOSED · ${blocked} BLOCKED`
              : "TRADE-CAPABLE"}
        </span>
      </div>
      <div className="grid gap-3 p-3 md:grid-cols-2">
        <div className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-4">
          <div className="gdc-spec mb-2">DOC 1 · STATUS</div>
          <div className="space-y-1.5 text-[10.5px]">
            <div className="flex justify-between">
              <span className="text-[#aab4bf]">content hash</span>
              <span className="gdc-data text-[#e8b440]">{hash ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#aab4bf]">phase</span>
              <span className="text-[#e9edf2]">
                {phase === null ? "—" : phase} — zero LLM in the live loop
              </span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#aab4bf]">execution boundary</span>
              <span className="text-[#e9edf2]">telegram → human paste</span>
            </div>
            <div className="flex justify-between">
              <span className="text-[#aab4bf]">promotion path</span>
              <span className="text-[#e9edf2]">frozen simulator battery only</span>
            </div>
            <div className="mt-2 rounded-lg border border-[#e8b440]/25 bg-[#e8b440]/[0.06] p-2.5 text-[9.5px] leading-relaxed text-[#d29922]">
              This UI is READ-ONLY telemetry over the journal. It cannot size, trade,
              mutate the constitution, or enter the decision loop. Owner-approved
              extension of ROADMAP rung 4.
            </div>
          </div>
        </div>
        <div className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-4">
          <div className="gdc-spec mb-2">THE 15 LAWS (ENFORCED IN CODE)</div>
          <div className="gdc-scroll max-h-[168px] space-y-[3px] overflow-y-auto pr-1">
            {LAWS.map(([id, text]) => (
              <div key={id} className="flex gap-2 text-[9.5px] leading-relaxed">
                <span className="w-5 shrink-0 font-bold text-[#c8a04b]">{id}</span>
                <span className="text-[#a5afba]">{text}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

export const ConstitutionPanel = memo(ConstitutionPanelImpl);
