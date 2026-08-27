"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface ReplayScenario {
  scenario?: string;
  label?: string;
  mode?: string;
  ok?: boolean;
  cumulative?: number;
  worst_day?: number;
  worst_day_date?: string | null;
  max_drawdown?: number;
  n_days?: number;
  window?: { start?: string; end?: string };
  unshocked?: string[];
  error?: string;
  static?: { portfolio_shock?: number };
  portfolio_shock?: number;
}

interface RiskReport {
  ok: boolean;
  portfolio?: string;
  n_observations?: number;
  mean?: number | null;
  stdev?: number | null;
  var?: {
    parametric?: Record<string, number | null>;
    historical?: Record<string, number | null>;
    monte_carlo?: Record<string, number | null>;
  };
  expected_shortfall?: Record<string, number | null>;
  beta?: {
    beta?: number | null;
    alpha?: number | null;
    correlation?: number | null;
    r_squared?: number | null;
    n?: number | null;
  };
  stress?: {
    scenarios?: Array<{
      name: string;
      label: string;
      portfolio_shock: number;
      shocked?: string[];
      unshocked?: string[];
      yield_change_pp?: number;
    }>;
  };
  stress_replay?: {
    ok?: boolean;
    scenarios?: ReplayScenario[];
  };
  error?: string;
}

const METHODS: Array<{ key: keyof NonNullable<RiskReport["var"]>; label: string }> = [
  { key: "parametric", label: "parametric (gaussian)" },
  { key: "historical", label: "historical" },
  { key: "monte_carlo", label: "monte carlo (1000 paths)" },
];

function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "n/a";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
}

function shockColor(v: number): string {
  if (v < -0.10) return "#f85149";
  if (v < 0) return "#d9a343";
  return "#3fb950";
}

function RiskPanelImpl() {
  const [data, setData] = useState<RiskReport | null>(null);
  const [replay, setReplay] = useState<RiskReport | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/desk/risk").then((x) => x.json());
      setData(r as RiskReport);
    } catch {
      setData({ ok: false, error: "transport failure" });
    }
  }, []);

  const loadReplay = useCallback(async () => {
    setReplayBusy(true);
    setReplayError(null);
    try {
      const r = await fetch("/api/desk/risk?replay=1").then((x) => x.json());
      if (r && r.ok) setReplay(r as RiskReport);
      else setReplayError((r && r.error) || "replay failed");
    } catch {
      setReplayError("transport failure");
    } finally {
      setReplayBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), 300_000);
    return () => { clearTimeout(kick); clearInterval(t); };
  }, [load]);

  const varBlock = data?.var;
  const es = data?.expected_shortfall || {};
  const beta = data?.beta;
  const scenarios = data?.stress?.scenarios || [];
  const replayScenarios = replay?.stress_replay?.scenarios || [];

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Risk engine</span>
        <span className="gdc-kicker">
          var · expected shortfall · beta · stress (gfc / covid / 2022) — deterministic, seed-pinned
        </span>
        <span className="ml-auto flex items-center gap-2 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
          {data?.ok ? <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" /> : null}
          {data?.ok ? "computed" : data === null ? "loading…" : "unreachable"}
        </span>
      </div>

      <div className="flex flex-wrap items-center gap-2 border-b border-white/[0.08] pb-2">
        <button
          onClick={() => void loadReplay()}
          disabled={replayBusy}
          className="rounded bg-[#1f2632] px-3 py-1.5 text-[10px] uppercase tracking-[0.15em] text-[#f4f7fa] hover:bg-[#273040] disabled:opacity-50"
        >{replayBusy ? "replaying windows…" : "stress replay — real 2008 / 2020 / 2022 paths (r4-3)"}</button>
        {replayError && <span className="text-[9px] italic text-[#d9a343]">{replayError}</span>}
      </div>

      {data && !data.ok && (
        <div className="text-[10px] italic text-[#f85149]">
          {data.error || "risk report failed — Yahoo daily bars unreachable?"}
        </div>
      )}

      {data?.ok && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>portfolio: <span className="text-[#9aa6b3]">{data.portfolio || "—"}</span></span>
            <span>observations: <span className="gdc-data text-[#9aa6b3]">{data.n_observations}</span></span>
            <span>μ: <span className="gdc-data text-[#9aa6b3]">{data.mean !== null && data.mean !== undefined ? pct(data.mean, 3) : "n/a"}</span></span>
            <span>σ: <span className="gdc-data text-[#9aa6b3]">{data.stdev !== null && data.stdev !== undefined ? pct(data.stdev, 3) : "n/a"}</span></span>
          </div>

          <div className="overflow-x-auto">
            <table className="gdc-data w-full border-collapse text-[10px] tabular-nums">
              <thead>
                <tr className="text-left text-[#76828e]">
                  <th className="py-1 pr-4 font-normal">VaR method</th>
                  <th className="py-1 pr-4 text-right font-normal">95%</th>
                  <th className="py-1 text-right font-normal">99%</th>
                </tr>
              </thead>
              <tbody>
                {METHODS.map((m) => {
                  const row = varBlock?.[m.key] || {};
                  return (
                    <tr key={m.key} className="border-t border-white/[0.05]">
                      <td className="py-1 pr-4 text-[#9aa6b3]">{m.label}</td>
                      <td className="py-1 pr-4 text-right text-[#f85149]">{pct(row["95"])}</td>
                      <td className="py-1 text-right text-[#f85149]">{pct(row["99"])}</td>
                    </tr>
                  );
                })}
                <tr className="border-t border-white/[0.05]">
                  <td className="py-1 pr-4 text-[#9aa6b3]">expected shortfall 95% (historical tail mean)</td>
                  <td className="py-1 pr-4 text-right text-[#d9a343]">{pct(es.historical_95)}</td>
                  <td />
                </tr>
                <tr className="border-t border-white/[0.05]">
                  <td className="py-1 pr-4 text-[#9aa6b3]">expected shortfall 99% (historical tail mean)</td>
                  <td className="py-1 pr-4 text-right text-[#d9a343]">{pct(es.historical_99)}</td>
                  <td />
                </tr>
              </tbody>
            </table>
          </div>

          {beta && beta.beta !== null && beta.beta !== undefined && (
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
              <span>beta vs benchmark: <span className="gdc-data text-[#f4f7fa]">{beta.beta.toFixed(4)}</span></span>
              <span>α/period: <span className="gdc-data text-[#9aa6b3]">{beta.alpha?.toFixed(6)}</span></span>
              <span>ρ: <span className="gdc-data text-[#9aa6b3]">{beta.correlation?.toFixed(3)}</span></span>
              <span>R²: <span className="gdc-data text-[#9aa6b3]">{beta.r_squared?.toFixed(3)}</span></span>
              <span>n: <span className="gdc-data text-[#9aa6b3]">{beta.n}</span></span>
            </div>
          )}

          {scenarios.length > 0 && (
            <div className="space-y-1.5">
              <div className="gdc-kicker text-[#9aa6b3]">stress scenarios (static vectors)</div>
              <div className="grid gap-2 sm:grid-cols-3">
                {scenarios.map((s) => (
                  <div key={s.name} className="rounded bg-[#1a1f2c] p-2.5">
                    <div className="gdc-data text-[9px] uppercase tracking-[0.12em] text-[#76828e]">{s.label}</div>
                    <div className="gdc-display-num mt-1 text-[16px]" style={{ color: shockColor(s.portfolio_shock) }}>
                      {pct(s.portfolio_shock)}
                    </div>
                    {s.yield_change_pp !== undefined && (
                      <div className="text-[8.5px] text-[#76828e]">10y yield {s.yield_change_pp >= 0 ? "+" : ""}{s.yield_change_pp}pp</div>
                    )}
                    {s.unshocked && s.unshocked.length > 0 && (
                      <div className="mt-0.5 text-[8px] italic text-[#76828e]">unshocked: {s.unshocked.join(", ")}</div>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {replayScenarios.length > 0 && (
            <div className="space-y-1.5">
              <div className="gdc-kicker text-[#9aa6b3]">
                stress replay — real historical daily-return paths (r4-3)
              </div>
              <div className="overflow-x-auto">
                <table className="gdc-data w-full border-collapse text-[10px] tabular-nums">
                  <thead>
                    <tr className="text-left text-[#76828e]">
                      <th className="py-1 pr-4 font-normal">window</th>
                      <th className="py-1 pr-4 text-right font-normal">mode</th>
                      <th className="py-1 pr-4 text-right font-normal">cumulative</th>
                      <th className="py-1 pr-4 text-right font-normal">worst day</th>
                      <th className="py-1 pr-4 text-right font-normal">max dd</th>
                      <th className="py-1 pr-4 text-right font-normal">static</th>
                      <th className="py-1 font-normal">unshocked</th>
                    </tr>
                  </thead>
                  <tbody>
                    {replayScenarios.map((s) => {
                      const hist = s.mode === "historical";
                      const stat = s.static?.portfolio_shock ?? s.portfolio_shock ?? 0;
                      return (
                        <tr key={s.scenario || s.label} className="border-t border-white/[0.05]">
                          <td className="py-1 pr-4 text-[#9aa6b3]">
                            {s.label}
                            {s.window?.start && (
                              <span className="text-[#76828e]"> ({s.window.start} → {s.window.end})</span>
                            )}
                          </td>
                          <td className="py-1 pr-4 text-right text-[#7ab5e0]">
                            {hist ? `historical · ${s.n_days}d` : s.mode === "fallback" ? "static fallback" : "static"}
                          </td>
                          <td className="py-1 pr-4 text-right" style={{ color: shockColor(s.cumulative ?? 0) }}>
                            {hist ? pct(s.cumulative) : "—"}
                          </td>
                          <td className="py-1 pr-4 text-right text-[#f85149]">
                            {hist && s.worst_day_date ? `${pct(s.worst_day)} (${s.worst_day_date})` : "—"}
                          </td>
                          <td className="py-1 pr-4 text-right text-[#d9a343]">
                            {hist ? pct(s.max_drawdown) : "—"}
                          </td>
                          <td className="py-1 pr-4 text-right text-[#76828e]">{pct(stat)}</td>
                          <td className="py-1 text-[8px] italic text-[#76828e]">
                            {(s.unshocked || []).join(", ") || "—"}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
              <div className="text-[8.5px] italic text-[#76828e]">
                daily equity path = Σ w·r compounded over each symbol's own historical bars; symbols without data in the window (e.g. BTC in 2008) contribute 0 and are listed unshocked.
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export const RiskPanel = memo(RiskPanelImpl);
