"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import type { DeskPersona, DeskPm, DeskReport } from "../types";
import { GREEN, RED, fmtAsOf } from "../lib";

/** signal → chip palette (flat colors, no gradients — deck system). */
function signalStyle(signal: string): { color: string; border: string; bg: string } {
  if (signal === "bullish")
    return { color: GREEN, border: "rgba(111,169,122,0.45)", bg: "rgba(111,169,122,0.10)" };
  if (signal === "bearish")
    return { color: RED, border: "rgba(184,92,92,0.45)", bg: "rgba(184,92,92,0.10)" };
  return { color: "#8a93a6", border: "#2a3040", bg: "rgba(138,147,166,0.06)" };
}

/** One analyst row: role, signal chip, confidence bar, thesis, evidence. */
function PersonaRowImpl({ p }: { p: DeskPersona }) {
  const s = signalStyle(p.signal);
  return (
    <li className="border-b border-[#1a1f2c] px-1 py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span
          className="gdc-data shrink-0 text-[10px] font-semibold uppercase tracking-[0.12em] text-[#aab4bf]"
          title={p.abstained ? `abstained · model ${p.model || "n/a"}` : `model ${p.model || "n/a"} · ${p.latency_ms}ms`}
        >
          {p.role}
        </span>
        <span
          className="gdc-data shrink-0 rounded-sm border px-1.5 py-[2px] text-[9px] font-bold uppercase tracking-[0.14em]"
          style={{ color: s.color, borderColor: s.border, background: s.bg }}
        >
          {p.abstained ? "abstained" : p.signal}
        </span>
        {/* confidence bar */}
        <span className="flex min-w-[110px] flex-1 items-center gap-2">
          <span className="relative h-[5px] flex-1 overflow-hidden rounded-full bg-[#1a1f2c]">
            <span
              className="absolute inset-y-0 left-0 rounded-full"
              style={{
                width: `${Math.max(0, Math.min(100, p.confidence))}%`,
                background: p.abstained ? "#3a4150" : s.color,
                opacity: p.abstained ? 0.5 : 0.85,
              }}
            />
          </span>
          <span className="gdc-data w-[30px] shrink-0 text-right text-[10px] tabular-nums text-[#8a93a6]">
            {p.confidence}%
          </span>
        </span>
      </div>
      <p
        className={`mt-1.5 text-[11.5px] leading-snug ${
          p.abstained ? "italic text-[#8a93a6]" : "text-[#c6cedb]"
        }`}
      >
        {p.thesis}
      </p>
      {(p.key_evidence ?? []).length > 0 && (
        <ul className="mt-1 flex flex-col gap-0.5">
          {p.key_evidence.map((ev, i) => (
            <li
              key={i}
              className="flex items-baseline gap-1.5 text-[10px] leading-snug text-[#8a93a6]"
            >
              <span className="text-[#c8a04b]">·</span>
              {ev}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
const PersonaRow = memo(PersonaRowImpl);

/** PM consensus panel — the desk's synthesized view. */
function PmPanelImpl({ pm, report }: { pm: DeskPm; report: DeskReport }) {
  const s = signalStyle(pm.consensus === "mixed" ? "neutral" : pm.consensus);
  return (
    <div className="mt-3 rounded-sm border border-[#1a1f2c] bg-white/[0.02] px-3.5 py-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="gdc-data text-[10px] font-semibold uppercase tracking-[0.14em] text-[#c8a04b]">
          PM — The Portfolio Manager
        </span>
        <span
          className="gdc-data rounded-sm border px-2 py-[3px] text-[10px] font-bold uppercase tracking-[0.14em]"
          style={{ color: s.color, borderColor: s.border, background: s.bg }}
        >
          {pm.consensus}
        </span>
        <span className="flex min-w-[130px] flex-1 items-center gap-2" title={`conviction ${pm.conviction}/100`}>
          <span className="relative h-[5px] flex-1 overflow-hidden rounded-full bg-[#1a1f2c]">
            <span
              className="absolute inset-y-0 left-0 rounded-full"
              style={{
                width: `${Math.max(0, Math.min(100, pm.conviction))}%`,
                background: s.color,
                opacity: 0.85,
              }}
            />
          </span>
          <span className="gdc-data w-[46px] shrink-0 text-right text-[10px] tabular-nums text-[#8a93a6]">
            {pm.conviction}/100
          </span>
        </span>
        {pm.mechanical && (
          <span
            className="gdc-data rounded-sm border border-[#d29922]/40 px-1.5 py-[2px] text-[8.5px] font-semibold uppercase tracking-[0.12em] text-[#d29922]"
            title="the PM model was unreachable — this is the labeled mechanical majority vote"
          >
            mechanical vote
          </span>
        )}
      </div>
      <p className="mt-2 text-[11.5px] leading-relaxed text-[#c6cedb]">{pm.summary}</p>
      {pm.disagreements && (
        <p className="mt-1.5 text-[10.5px] leading-snug text-[#8a93a6]">
          <span className="font-semibold uppercase tracking-[0.1em] text-[#6f7987]">splits:</span>{" "}
          {pm.disagreements}
        </p>
      )}
      {(pm.risk_flags ?? []).length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {pm.risk_flags.map((f, i) => (
            <span
              key={i}
              className="rounded-sm border border-[#d29922]/25 bg-[#d29922]/[0.06] px-1.5 py-[2px] text-[9.5px] leading-snug text-[#d29922]"
            >
              ⚠ {f}
            </span>
          ))}
        </div>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[9px] uppercase tracking-[0.14em] text-[#6f7987]">
        <span>{(report.personas ?? []).length} analysts</span>
        {(report.abstained ?? 0) > 0 && (
          <span className="text-[#d29922]">{report.abstained} abstained</span>
        )}
        <span>{((report.elapsed_ms ?? 0) / 1000).toFixed(1)}s</span>
        {report.model && <span>model {report.model}</span>}
        {report.as_of && <span>{fmtAsOf(report.as_of)}</span>}
      </div>
    </div>
  );
}
const PmPanel = memo(PmPanelImpl);

/** The multi-analyst desk section (piece 4): five personas + a PM judge
 *  the symbol on demand — 6 LLM calls over the keyless Zen provider,
 *  runs take tens of seconds (spinner state on the button), the result
 *  is cached in component state ONLY (never refetched, never persisted;
 *  a fresh judgment needs a fresh click). GAUNTLET-P13: autoRun fires
 *  once on mount — the command palette "run desk <symbol>" lands on
 *  /markets/<sym>?desk=1 and the desk starts itself. */
function AnalystDeskImpl({ symbol, autoRun = false }: { symbol: string; autoRun?: boolean }) {
  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<DeskReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const autoFired = useRef(false);

  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      const r = await fetch(`/api/desk/desk?symbol=${encodeURIComponent(symbol)}`, {
        cache: "no-store",
      })
        .then((res) => res.json())
        .catch(() => null);
      if (r && r.ok) {
        setReport(r as DeskReport);
      } else {
        setError((r && r.error) || "desk unavailable — try again");
      }
    } finally {
      setRunning(false);
    }
  }, [symbol]);

  useEffect(() => {
    if (autoRun && !autoFired.current) {
      autoFired.current = true;
      void run();
    }
  }, [autoRun, run]);

  return (
    <section className="gdc-panel px-3.5 pb-3 pt-3" aria-label="Analyst desk">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          Analyst Desk
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        <button
          onClick={run}
          disabled={running}
          className="gdc-chip cursor-pointer border-[#c8a04b]/45 px-3.5 py-1 text-[10.5px] font-semibold text-[#e2c074] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-wait disabled:opacity-80"
          aria-label={`Run the five-analyst desk on ${symbol}`}
        >
          {running ? (
            <span className="flex items-center gap-2">
              <span className="gdc-live-dot h-[7px] w-[7px] rounded-full bg-[#c8a04b]" />
              Deskmates thinking…
            </span>
          ) : report ? (
            "Run again"
          ) : (
            "Run analyst desk ▸"
          )}
        </button>
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          5 personas · PM consensus · ~10-60s
        </span>
      </div>

      <p className="mb-2 text-[10px] leading-snug text-[#6f7987]">
        A technician, macro strategist, news analyst, sentiment reader and
        risk manager judge {symbol} in parallel from the same live context —
        then a portfolio manager synthesizes the consensus. Advisory only;
        never a trade signal.
      </p>

      {error && (
        <div className="mb-2 rounded-sm border border-[#B85C5C]/30 bg-[#B85C5C]/[0.06] px-3 py-2 text-[11px] text-[#D98484]">
          ⚠ {error}
        </div>
      )}

      {report && (
        <>
          <ul>
            {(report.personas ?? []).map((p) => (
              <PersonaRow key={p.name} p={p} />
            ))}
          </ul>
          {report.pm && <PmPanel pm={report.pm} report={report} />}
        </>
      )}
    </section>
  );
}

export const AnalystDesk = memo(AnalystDeskImpl);
