"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface LineageRow {
  ident: string;
  generation: number;
  birth_op: string;
  parent: string | null;
  is_fitness: number | null;
  oos_fitness: number | null;
  is_trades: number;
  status: string;
}

interface HeadRow {
  ident?: string;
  genome?: Record<string, number>;
  is_fitness?: number | null;
  oos_fitness?: number | null;
  is_trades?: number;
  oos_trades?: number;
  birth_op?: string;
  overfit_gap?: number | null;
  oos_reject_reason?: string;
}

interface EvolveStatus {
  ok: boolean;
  archive_path?: string;
  n_individuals?: number;
  n_generations?: number;
  statuses?: Record<string, number>;
  birth_ops?: Record<string, number>;
  last_verdict?: string;
  last_verdict_reason?: string;
  last_champion?: HeadRow | null;
  last_incumbent?: HeadRow | null;
  lineage_tail?: LineageRow[];
  error?: string;
}

interface LessonRow {
  lesson_id: string;
  text: string;
  symbol: string;
  regime: string;
  confidence: number;
  support: number;
  contradict: number;
  age_days: number;
}

interface LessonsResult {
  ok: boolean;
  active?: LessonRow[];
  n_total?: number;
  error?: string;
}

const fmt = (v: number | null | undefined, digits = 3): string =>
  v === null || v === undefined ? "n/a" : (v >= 0 ? "+" : "") + v.toFixed(digits);

function verdictTone(v?: string): string {
  if (v === "PROMOTE") return "bg-[#12261c] text-[#3fb950]";
  if (v === "NO_VIABLE_CANDIDATE") return "bg-[#2a1518] text-[#f85149]";
  return "bg-[#241f12] text-[#d29922]";
}

function EvolutionPanelImpl() {
  const [status, setStatus] = useState<EvolveStatus | null>(null);
  const [lessons, setLessons] = useState<LessonsResult | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [evRes, lesRes] = await Promise.all([
        fetch("/api/desk/evolve", { cache: "no-store" }),
        fetch("/api/desk/lessons?symbol=all&regime=all", { cache: "no-store" }),
      ]);
      setStatus(await evRes.json());
      setLessons(await lesRes.json());
    } catch {
      setStatus({ ok: false, error: "evolve fetch failed" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    void load();
    const t = setTimeout(() => void load(), 1200); // retry once for cold python
    return () => clearTimeout(t);
  }, [load]);

  const champ = status?.last_champion;
  const inc = status?.last_incumbent;

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex items-baseline gap-3">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Evolve · self-improving desk</span>
        <span className="gdc-kicker">
          parameter evolution · walk-forward gate · temporal lessons — the engine proposes, evidence disposes
        </span>
        <div className="ml-auto flex items-center gap-1.5 text-[9.5px]">
          <button
            onClick={() => void load()}
            disabled={busy}
            className="rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[#76828e] hover:text-[#f4f7fa] disabled:opacity-50"
          >{busy ? "loading…" : "refresh"}</button>
        </div>
      </div>

      {!status?.ok && (
        <div className="text-[10px] italic text-[#76828e]">
          no evolution archive yet — run <span className="gdc-data text-[#9aa6b3]">python -m gold_desk.cli evolve-run</span> (the engine never writes the live spec; runs are operator actions)
        </div>
      )}

      {status?.ok && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>archive: <span className="gdc-data text-[#9aa6b3]">{status.n_individuals ?? 0} individuals · {status.n_generations ?? 0} generations</span></span>
            <span>verdict:
              <span className={`ml-1 rounded px-1.5 py-0.5 text-[8.5px] ${verdictTone(status.last_verdict)}`}>
                {status.last_verdict ?? "—"}
              </span>
            </span>
            {status.last_verdict_reason && (
              <span className="italic">{status.last_verdict_reason}</span>
            )}
          </div>

          {(champ || inc) && (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {[
                { label: "incumbent (shipped GUESS)", row: inc },
                { label: "champion (evolved)", row: champ },
              ].map(({ label, row }) => (
                <div key={label} className="rounded border border-white/5 bg-black/20 p-2">
                  <div className="text-[9px] uppercase tracking-wider text-[#76828e]">{label}</div>
                  <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 text-[9.5px] tabular-nums">
                    <span className="text-[#76828e]">in-sample fitness</span>
                    <span className="gdc-data text-[#9aa6b3]">{fmt(row?.is_fitness)}</span>
                    <span className="text-[#76828e]">out-of-sample fitness</span>
                    <span className="gdc-data text-[#d29922]">{fmt(row?.oos_fitness)}</span>
                    <span className="text-[#76828e]">trades (train+test)</span>
                    <span className="gdc-data text-[#9aa6b3]">{row?.is_trades ?? 0}+{row?.oos_trades ?? 0}</span>
                    <span className="text-[#76828e]">born by</span>
                    <span className="gdc-data text-[#9aa6b3]">{row?.birth_op ?? "seed"}</span>
                    {row?.overfit_gap !== undefined && row?.overfit_gap !== null && (
                      <>
                        <span className="text-[#76828e]">overfit gap (IS−OOS)</span>
                        <span className={`gdc-data ${(row.overfit_gap ?? 0) > 0.5 ? "text-[#f85149]" : "text-[#3fb950]"}`}>{fmt(row.overfit_gap)}</span>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}

          {(status.lineage_tail?.length ?? 0) > 0 && (
            <div className="overflow-x-auto">
              <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
                <thead>
                  <tr className="text-left text-[#76828e]">
                    <th className="py-0.5 pr-3 font-normal">ident</th>
                    <th className="py-0.5 pr-3 font-normal">gen</th>
                    <th className="py-0.5 pr-3 font-normal">birth op</th>
                    <th className="py-0.5 pr-3 text-right font-normal">IS fitness</th>
                    <th className="py-0.5 pr-3 text-right font-normal">trades</th>
                    <th className="py-0.5 pr-3 font-normal">status</th>
                  </tr>
                </thead>
                <tbody>
                  {status.lineage_tail!.slice(-8).map((r) => (
                    <tr key={r.ident + r.generation} className="border-t border-white/5">
                      <td className="py-0.5 pr-3 text-[#9aa6b3]">{r.ident.slice(0, 10)}…</td>
                      <td className="py-0.5 pr-3 text-[#76828e]">{r.generation}</td>
                      <td className="py-0.5 pr-3 text-[#76828e]">{r.birth_op}</td>
                      <td className="py-0.5 pr-3 text-right text-[#9aa6b3]">{fmt(r.is_fitness)}</td>
                      <td className="py-0.5 pr-3 text-right text-[#76828e]">{r.is_trades}</td>
                      <td className="py-0.5 pr-3">
                        <span className={`rounded px-1 py-0.5 text-[8px] ${r.status === "champion" ? "bg-[#12261c] text-[#3fb950]" : r.status === "retired" ? "bg-[#2a1518] text-[#f85149]" : "bg-[#1a1f2c] text-[#76828e]"}`}>{r.status}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div className="mt-1 text-[8.5px] text-[#76828e]">
                OOS is measured for the two finalists only — probing every candidate on the test tail would be selection on the test set.
              </div>
            </div>
          )}
        </>
      )}

      <div className="border-t border-white/5 pt-2">
        <div className="flex items-baseline gap-2">
          <span className="gdc-display text-[12px] italic text-[#f4f7fa]">Temporal lessons</span>
          <span className="gdc-kicker">validity windows · evidence counters · contradiction retirement</span>
          <span className="ml-auto text-[9.5px] text-[#76828e]">
            {lessons?.active?.length ?? 0} active of {lessons?.n_total ?? 0}
          </span>
        </div>
        {(lessons?.active?.length ?? 0) === 0 ? (
          <div className="mt-1 text-[10px] italic text-[#76828e]">
            no active lessons — add via <span className="gdc-data text-[#9aa6b3]">python -m gold_desk.cli lessons add --text … --symbol …</span>
          </div>
        ) : (
          <div className="mt-1 space-y-1">
            {lessons!.active!.slice(0, 8).map((l) => (
              <div key={l.lesson_id} className="flex items-center gap-2 text-[9.5px]">
                <span className={`gdc-data w-14 text-right tabular-nums ${l.confidence >= 0 ? "text-[#3fb950]" : "text-[#f85149]"}`}>
                  {l.confidence >= 0 ? "+" : ""}{l.confidence.toFixed(3)}
                </span>
                <div className="h-1.5 w-20 shrink-0 overflow-hidden rounded bg-[#1a1f2c]">
                  <div
                    className={`h-full ${l.confidence >= 0 ? "bg-[#3fb950]" : "bg-[#f85149]"}`}
                    style={{ width: `${Math.min(100, Math.abs(l.confidence) * 100)}%` }}
                  />
                </div>
                <span className="gdc-data text-[#76828e]">{l.symbol}</span>
                <span className="gdc-data text-[#76828e]">{l.regime}</span>
                <span className="truncate text-[#9aa6b3]">{l.text}</span>
                <span className="ml-auto shrink-0 text-[8.5px] text-[#76828e]">
                  s{l.support}/c{l.contradict} · {l.age_days.toFixed(0)}d
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export const EvolutionPanel = memo(EvolutionPanelImpl);
