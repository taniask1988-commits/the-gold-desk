"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";

/**
 * AgentPanel — the research sidecar surface (P5 web deck integration).
 * Lists cited research reports from data/research/*.md + the recent agent
 * event stream from the journal. Everything read-only, matching the
 * sidecar's laws (L11-L14): research proposes, the human decides.
 */

type ResearchSource = { n: number; url: string; title: string; fetched_ts: string };

type Report = {
  file: string;
  asset: string;
  run_id: string;
  generated_ts: string;
  confidence: string;
  thesis: string;
  models: string[] | string;
  sources: ResearchSource[];
  body: string;
  error?: string;
};

type AgentEvent = {
  ts: string;
  kind: string;
  payload: Record<string, unknown>;
  model_id?: string | null;
};

const CONF_COLORS: Record<string, string> = {
  high: "text-emerald-300",
  medium: "text-amber-300",
  low: "text-rose-300",
};

function AgentPanel() {
  const [reports, setReports] = useState<Report[]>([]);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [autonomy, setAutonomy] = useState("L1");
  const [open, setOpen] = useState<string | null>(null);
  const [detail, setDetail] = useState<Report | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/desk/research", { cache: "no-store" });
      const j = await r.json();
      if (j.ok) {
        setReports(j.reports || []);
        setEvents(j.agentEvents || []);
        setAutonomy(j.autonomy || "L1");
        setErr(null);
      } else {
        setErr(j.error || "research feed unavailable");
      }
    } catch {
      setErr("research feed unreachable");
    }
  }, []);

  useEffect(() => {
    // kick the first fetch on the next tick — load's setState happens
    // post-await, but the synchronous call itself trips
    // react-hooks/set-state-in-effect, so route it through a timer
    // callback like the interval below (GAUNTLET-P15)
    const t0 = setTimeout(load, 0);
    const t = setInterval(() => void load(), 30_000);
    return () => {
      clearTimeout(t0);
      clearInterval(t);
    };
  }, [load]);

  const openReport = useCallback(async (file: string) => {
    if (open === file) { setOpen(null); setDetail(null); return; }
    try {
      const r = await fetch(`/api/desk/research?file=${encodeURIComponent(file)}`,
        { cache: "no-store" });
      const j = await r.json();
      if (j.ok) { setOpen(file); setDetail(j.report); }
    } catch { /* soft fail */ }
  }, [open]);

  const lastRun = useMemo(() => {
    const started = events.filter((e) => e.kind === "AgentRunStarted").at(0);
    const finished = events.filter((e) => e.kind === "AgentRunFinished").at(0);
    return { started, finished };
  }, [events]);

  return (
    <section className="gdc-panel flex flex-col gap-3">
      <header className="flex items-baseline justify-between gap-3">
        <div>
          <h2 className="text-[11px] font-bold tracking-[0.14em] text-[var(--gold)]">
            RESEARCH SIDECAR
          </h2>
          <p className="text-[9.5px] tracking-[0.1em] text-[var(--ink-dim)]">
            CITED REPORTS · AGENT AUDIT TRAIL · L11-L14 ENFORCED
          </p>
        </div>
        <span className="rounded-full border border-[var(--hairline)] px-2 py-0.5
                         text-[9px] tracking-[0.12em] text-[var(--ink-dim)]">
          AUTONOMY {autonomy}
        </span>
      </header>

      {err && (
        <p className="text-[10.5px] text-rose-300">{err}</p>
      )}

      {/* recent agent activity */}
      <div className="max-h-28 overflow-y-auto rounded-lg border border-[var(--hairline)]
                      bg-[var(--panel)] p-2 font-mono text-[9.5px] leading-relaxed">
        {events.length === 0 ? (
          <p className="text-[var(--ink-dim)]">
            no agent runs yet — try: python -m gold_desk.cli research XAUUSD
          </p>
        ) : (
          events.slice(0, 12).map((e, i) => (
            <p key={i} className="truncate">
              <span className="text-[var(--ink-dim)]">{e.ts?.slice(5, 16)} </span>
              <span className="text-[var(--gold)]">{e.kind.replace(/^Agent/, "")}</span>{" "}
              <span className="text-[var(--ink)]">
                {typeof e.payload?.asset === "string" ? e.payload.asset : ""}
                {typeof e.payload?.status === "string" ? ` ${e.payload.status}` : ""}
                {typeof e.payload?.steps === "number" ? ` ${e.payload.steps} steps` : ""}
                {typeof e.payload?.sources === "number" ? ` ${e.payload.sources} src` : ""}
              </span>
            </p>
          ))
        )}
      </div>

      {/* reports list */}
      <div className="flex flex-col gap-2">
        {reports.length === 0 ? (
          <p className="text-[10.5px] text-[var(--ink-dim)]">
            No research reports yet. Generate one:
            <code className="ml-1 text-[var(--gold)]">
              python -m gold_desk.cli research BTC --depth 2
            </code>
          </p>
        ) : (
          reports.map((r) => (
            <div key={r.file}>
              <button
                onClick={() => openReport(r.file)}
                className="w-full rounded-lg border border-[var(--hairline)]
                           bg-[var(--panel)] p-2.5 text-left transition-colors
                           hover:border-[var(--gold)]"
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="font-mono text-[12px] font-bold text-[var(--gold-bright)]">
                    {r.asset || r.file}
                  </span>
                  <span className={`text-[9px] font-bold tracking-[0.1em] ${
                    CONF_COLORS[r.confidence] || "text-[var(--ink-dim)]"}`}>
                    {r.confidence?.toUpperCase()}
                  </span>
                </div>
                {r.thesis && (
                  <p className="mt-1 line-clamp-2 text-[10.5px] leading-snug
                               text-[var(--ink)]">{r.thesis}</p>
                )}
                <p className="mt-1 text-[9px] text-[var(--ink-dim)]">
                  {r.generated_ts?.slice(0, 16).replace("T", " ")} ·{" "}
                  {(r.sources as ResearchSource[])?.length || 0} sources ·{" "}
                  {Array.isArray(r.models) ? r.models.join(", ") : r.models}
                </p>
              </button>

              {open === r.file && detail && (
                <div className="mt-1.5 max-h-72 overflow-y-auto rounded-lg border
                                border-[var(--gold)] bg-[var(--panel)] p-3">
                  <pre className="whitespace-pre-wrap break-words font-mono
                                  text-[10px] leading-relaxed text-[var(--ink)]">
                    {detail.body}
                  </pre>
                  {detail.sources?.length > 0 && (
                    <div className="mt-3 border-t border-[var(--hairline)] pt-2">
                      <p className="text-[9px] font-bold tracking-[0.12em]
                                   text-[var(--gold)]">SOURCES</p>
                      {detail.sources.map((s) => (
                        <p key={s.n} className="mt-1 truncate text-[9.5px]
                                                text-[var(--ink-dim)]">
                          [{s.n}] {s.title} — {s.url}
                        </p>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      <footer className="text-[9px] leading-relaxed text-[var(--ink-dim)]">
        Sidecar is read-only and journalled; reports propose, never execute.
        Autonomy ladder: L1 manual · L2 watchlist · L3 drafts (human approves).
      </footer>
    </section>
  );
}

export default memo(AgentPanel);
