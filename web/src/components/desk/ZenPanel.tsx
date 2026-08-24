"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface ZenModel {
  context_window: number | null;
  supports_reasoning: boolean;
  supports_vision: boolean;
  deprecated: boolean;
}

interface ZenCatalog {
  schema: string;
  default: string | null;
  synced_ts: number | null;
  source: string;
  zen_served?: number;
  models: Record<string, ZenModel>;
}

interface BenchRun {
  ok?: boolean;
  decision?: string;
  reason?: string;
  model?: string;
  scenario?: string;
  expected?: string;
  match?: boolean;
  latency_ms?: number | null;
  error?: boolean;
  ts?: string;
}

const SCENARIOS: Array<{ id: string; label: string; hint: string }> = [
  { id: "clean", label: "CLEAN", hint: "no news 6h — expect ENDORSE" },
  { id: "news", label: "NEWS +12m", hint: "CPI lands 12 min after entry — expect VETO" },
  { id: "stale", label: "STALE TS", hint: "future-dated calendar — expect VETO" },
];

function ZenPanelImpl() {
  const [catalog, setCatalog] = useState<ZenCatalog | null>(null);
  const [bench, setBench] = useState<BenchRun[]>([]);
  const [scenario, setScenario] = useState("news");
  const [running, setRunning] = useState(false);
  const [last, setLast] = useState<BenchRun | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/desk/zen").then((x) => x.json());
      if (r.ok) {
        setCatalog(r.catalog as ZenCatalog | null);
        setBench((r.bench ?? []) as BenchRun[]);
      }
    } catch {
      /* keep last */
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const runDry = useCallback(async () => {
    setRunning(true);
    setLast(null);
    try {
      const r = await fetch("/api/desk/zen/veto-dry", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ scenario }),
      }).then((x) => x.json());
      setLast(r as BenchRun);
      void load(); // refresh bench history
    } catch (e) {
      setLast({ ok: false, error: true, reason: (e as Error).message });
    } finally {
      setRunning(false);
    }
  }, [scenario, load]);

  const models = catalog
    ? Object.entries(catalog.models).sort(([a], [b]) => a.localeCompare(b))
    : [];
  const def = catalog?.default ?? null;

  return (
    <div className="gdc-panel overflow-hidden">
      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.07] px-4 py-2">
        <div className="flex flex-wrap items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">OpenCode Zen</span>
          <span className="gdc-kicker">free models · keyless</span>
        </div>
        <span className="gdc-chip border-[#3fb950]/30 text-[#3fb950]">
          <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
          {catalog ? `${models.length} free` : "syncing…"}
        </span>
        <span className="gdc-chip text-[#98a3af]">veto: {def ?? "—"}</span>
        {catalog?.zen_served && (
          <span className="gdc-chip text-[#98a3af]">
            serves {catalog.zen_served}
          </span>
        )}
        <span className="ml-auto text-[8.5px] tracking-[0.1em] text-[#98a3af]">
          phase 2 only in live loop · bench = offline research (L10)
        </span>
      </div>

      <div className="grid gap-3 p-3 lg:grid-cols-[1fr_1.25fr]">
        {/* catalog */}
        <div className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-3">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="gdc-kicker !text-[#c8a04b]">
              Auto-discovered catalog
            </span>
            <span className="text-[8.5px] text-[#98a3af]">
              {catalog?.source ?? "—"} · opencode.ai/zen/v1
            </span>
          </div>
          <div className="gdc-scroll max-h-[168px] space-y-[3px] overflow-y-auto pr-1">
            {models.map(([id, m]) => {
              const isDef = id === def;
              const ctx = m.context_window
                ? `${Math.round(m.context_window / 1000)}k`
                : "?";
              return (
                <div
                  key={id}
                  className={`flex items-center gap-2 rounded px-1.5 py-1 text-[10px] transition-colors ${
                    isDef ? "bg-[#c8a04b]/[0.07] text-[#c8a04b]" : "text-[#aab4bf] hover:bg-white/[0.03]"
                  }`}
                >
                  <span className="gdc-data truncate text-[10px]">{id}</span>
                  {isDef && <span className="gdc-chip border-[#c8a04b]/30 px-1 py-0 text-[7.5px] text-[#c8a04b]">DEFAULT</span>}
                  <span className="ml-auto flex shrink-0 items-center gap-1.5 text-[8.5px]">
                    {m.supports_reasoning && <span className="text-[#39c5cf]">REASONING</span>}
                    {m.deprecated && <span className="text-[#d29922]">DEPRECATED</span>}
                    <span className="tabular-nums text-[#98a3af]">{ctx}</span>
                  </span>
                </div>
              );
            })}
            {models.length === 0 && (
              <div className="py-4 text-center text-[10px] text-[#8a95a1]">
                run `python -m gold_desk.cli zen` to sync the catalog
              </div>
            )}
          </div>
          <div className="mt-2 text-[8.5px] leading-relaxed text-[#98a3af]">
            Zen /v1/models ∩ models.dev · only free (cost 0/0) · tool-calling ·
            new models auto-appear on next sync, dead ones drop out.
          </div>
        </div>

        {/* bench */}
        <div className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-3">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <span className="gdc-kicker !text-[#c8a04b]">
              Veto research bench
            </span>
            <div className="ml-auto flex items-center gap-1">
              {SCENARIOS.map((s) => (
                <button
                  key={s.id}
                  title={s.hint}
                  onClick={() => setScenario(s.id)}
                  className={`rounded-full border px-2.5 py-0.5 text-[9px] transition-colors ${
                    scenario === s.id
                      ? "border-[#c8a04b]/35 bg-[#c8a04b]/[0.08] text-[#c8a04b]"
                      : "border-white/[0.07] text-[#98a3af] hover:border-white/[0.14] hover:text-[#aab4bf]"
                  }`}
                >
                  {s.label}
                </button>
              ))}
              <button
                onClick={runDry}
                disabled={running}
                className="gdc-chip cursor-pointer border-[#c8a04b]/30 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.07] disabled:opacity-40"
              >
                {running ? ".RUNNING… free model thinking…" : "▶ RUN DRY VETO"}
              </button>
            </div>
          </div>

          {last && (
            <div className="mb-2 rounded-xl border border-white/[0.09] bg-white/[0.035] p-2.5 text-[10px]">
              <div className="flex items-center gap-2">
                <span
                  className="gdc-chip font-bold"
                  style={{
                    color: last.decision === "ENDORSE" ? "#3fb950" : "#f85149",
                    borderColor:
                      (last.decision === "ENDORSE" ? "#3fb950" : "#f85149") + "44",
                  }}
                >
                  {last.decision ?? "—"}
                </span>
                <span className="text-[#98a3af]">
                  expected {last.expected} ·{" "}
                  {last.match ? (
                    <span className="text-[#3fb950]">MATCH</span>
                  ) : (
                    <span className="text-[#d29922]">DIVERGE</span>
                  )}
                  {last.latency_ms != null && ` · ${(last.latency_ms / 1000).toFixed(1)}s`}
                </span>
                <span className="ml-auto truncate text-[#8a95a1]">{last.model}</span>
              </div>
              {last.reason && (
                <div className="mt-1 line-clamp-3 text-[#aab4bf]">{last.reason}</div>
              )}
            </div>
          )}

          <div className="gdc-scroll max-h-[150px] overflow-y-auto pr-1">
            <table className="gdc-data w-full text-[9.5px] tabular-nums">
              <thead className="sticky top-0 bg-[#0b0e13]/95 text-left text-[#8a95a1]">
                <tr>
                  <th className="py-1 font-normal">TS</th>
                  <th className="py-1 font-normal">SCENARIO</th>
                  <th className="py-1 font-normal">MODEL</th>
                  <th className="py-1 font-normal">DECISION</th>
                  <th className="py-1 text-right font-normal">VS EXP</th>
                </tr>
              </thead>
              <tbody>
                {bench.map((b, i) => (
                  <tr key={i} className="border-t border-white/[0.05]">
                    <td className="py-[3px] text-[#98a3af]">
                      {(b.ts ?? "").slice(11, 19)}
                    </td>
                    <td className="py-[3px] text-[#aab4bf]">{b.scenario}</td>
                    <td className="max-w-[10rem] truncate py-[3px] text-[#aab4bf]">
                      {b.model}
                    </td>
                    <td
                      className="py-[3px] font-bold"
                      style={{ color: b.decision === "ENDORSE" ? "#3fb950" : "#f85149" }}
                    >
                      {b.error ? "FAIL-CLOSED" : b.decision}
                    </td>
                    <td className="py-[3px] text-right">
                      {b.match ? (
                        <span className="text-[#3fb950]">✓</span>
                      ) : (
                        <span className="text-[#d29922]">≠</span>
                      )}
                    </td>
                  </tr>
                ))}
                {bench.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-4 text-center text-[#8a95a1]">
                      no bench runs yet — press RUN DRY VETO (uses a free model,
                      costs $0)
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
}

export const ZenPanel = memo(ZenPanelImpl);
