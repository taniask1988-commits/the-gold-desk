"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface PortfolioResult {
  ok: boolean;
  method?: string;
  symbols?: string[];
  weights?: Record<string, number>;
  risk_contributions?: Record<string, number>;
  expected_returns?: Record<string, number>;
  volatilities?: Record<string, number>;
  portfolio_vol?: number;
  diversification_ratio?: number;
  expected_return?: number;
  n_observations?: number;
  source?: string;
  // method extras
  lambda_risk?: number;
  max_weight?: number;
  n_candidates?: number;
  seed?: number;
  objective?: number;
  iterations?: number;
  converged?: boolean;
  tol?: number;
  quasi_diagonal_order?: string[];
  merges?: Array<{ clusters: string[][]; distance: number }>;
  error?: string;
}

const METHODS = ["mv", "rp", "hrp"] as const;
const METHOD_LABELS: Record<string, string> = {
  mv: "mean-variance",
  rp: "risk parity (erc)",
  hrp: "hierarchical risk parity",
};
const CHART_W = 560;

function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "n/a";
  return `${(v * 100).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 3): string {
  if (v === null || v === undefined) return "n/a";
  return v.toFixed(digits);
}

/** Horizontal weight / risk-contribution bar — pure SVG, no chart lib. */
function BarRow({
  label,
  value,
  maxAbs,
  color,
  right,
}: {
  label: string;
  value: number;
  maxAbs: number;
  color: string;
  right: string;
}) {
  const frac = maxAbs > 0 ? Math.min(1, Math.abs(value) / maxAbs) : 0;
  return (
    <div className="flex items-center gap-2 text-[9.5px] tabular-nums">
      <span className="w-16 shrink-0 truncate text-[#9aa6b3]" title={label}>{label}</span>
      <div className="relative h-2.5 flex-1 overflow-hidden rounded-sm bg-[#12161f]">
        <div
          className="absolute inset-y-0 left-0 rounded-sm"
          style={{ width: `${frac * 100}%`, backgroundColor: color }}
        />
      </div>
      <span className="w-14 shrink-0 text-right" style={{ color }}>{right}</span>
    </div>
  );
}

function PortfolioPanelImpl() {
  const [data, setData] = useState<PortfolioResult | null>(null);
  const [method, setMethod] = useState<string>("mv");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (m: string) => {
    setBusy(true);
    try {
      const r = await fetch(`/api/desk/portfolio?method=${m}`).then((x) => x.json());
      setData(r as PortfolioResult);
    } catch {
      setData({ ok: false, error: "transport failure" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(method), 0);
    return () => clearTimeout(kick);
  }, [load, method]);

  const symbols = data?.symbols || [];
  const weights = data?.weights || {};
  const rc = data?.risk_contributions || {};
  const maxW = Math.max(1e-9, ...symbols.map((s) => Math.abs(weights[s] || 0)));
  const maxRC = Math.max(1e-9, ...symbols.map((s) => Math.abs(rc[s] || 0)));

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Portfolio construction</span>
        <span className="gdc-kicker">
          weights · risk contributions · diversification ratio — spy / gc=f / btc-usd, 90d aligned
        </span>
        <div className="ml-auto flex items-center gap-1.5 text-[9.5px]">
          {METHODS.map((m) => (
            <button
              key={m}
              onClick={() => setMethod(m)}
              title={METHOD_LABELS[m]}
              className={`rounded px-1.5 py-0.5 ${method === m ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
            >{m}</button>
          ))}
          <button
            onClick={() => void load(method)}
            disabled={busy}
            className="ml-1 rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[#76828e] hover:text-[#f4f7fa] disabled:opacity-50"
          >{busy ? "running…" : "rerun"}</button>
        </div>
      </div>

      {data && !data.ok && (
        <div className="text-[10px] italic text-[#f85149]">
          {data.error || "optimization failed — Yahoo daily bars unreachable?"}
        </div>
      )}

      {data?.ok && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>method: <span className="gdc-data text-[#9aa6b3]">{METHOD_LABELS[data.method || ""] || data.method}</span></span>
            <span>source: <span className="gdc-data text-[#9aa6b3]">{data.source || "—"}</span></span>
            <span>observations: <span className="gdc-data text-[#9aa6b3]">{data.n_observations}</span></span>
            {data.method === "mv" && (
              <span>μᵀw − {num(data.lambda_risk, 1)}·wᵀΣw · cap {pct(data.max_weight)} · {data.n_candidates} candidates · seed {data.seed}</span>
            )}
            {data.method === "rp" && (
              <span>erc: converged in {data.iterations} sweeps (tol {data.tol})</span>
            )}
            {data.method === "hrp" && data.quasi_diagonal_order && (
              <span>quasi-diag: <span className="gdc-data text-[#9aa6b3]">{data.quasi_diagonal_order.join(" → ")}</span></span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">portfolio vol</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#f4f7fa]">{pct(data.portfolio_vol, 2)}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">diversification ratio</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#f4f7fa]">{num(data.diversification_ratio, 3)}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">expected return / obs</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#3fb950]">{pct(data.expected_return, 3)}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">assets</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#f4f7fa]">{symbols.length}</div>
            </div>
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <div className="rounded bg-[#12161f] p-2.5">
              <div className="gdc-kicker mb-1.5 text-[#9aa6b3]">weights (Σ = 1{data.method === "mv" ? `, each ≤ ${pct(data.max_weight)}` : ""})</div>
              <div className="space-y-1.5">
                {symbols.map((s) => (
                  <BarRow
                    key={s}
                    label={s}
                    value={weights[s] || 0}
                    maxAbs={maxW}
                    color="#d9a343"
                    right={pct(weights[s] || 0)}
                  />
                ))}
              </div>
            </div>
            <div className="rounded bg-[#12161f] p-2.5">
              <div className="gdc-kicker mb-1.5 text-[#9aa6b3]">risk contributions (% of portfolio variance, Σ = 1)</div>
              <div className="space-y-1.5">
                {symbols.map((s) => {
                  const v = rc[s] || 0;
                  return (
                    <BarRow
                      key={s}
                      label={s}
                      value={v}
                      maxAbs={maxRC}
                      color={v >= 0 ? "#58a6ff" : "#f85149"}
                      right={pct(v)}
                    />
                  );
                })}
              </div>
              {symbols.length > 1 && (
                <div className="mt-1.5 text-[8px] italic text-[#76828e]">
                  negative contribution = hedging asset (reduces portfolio variance)
                </div>
              )}
            </div>
          </div>

          {data.method === "hrp" && data.merges && data.merges.length > 0 && (
            <div className="text-[8.5px] text-[#76828e]">
              single-linkage merges:{" "}
              {data.merges.map((m, i) => (
                <span key={i} className="gdc-data text-[#9aa6b3]">
                  {m.clusters[0].join("+")}|{m.clusters[1].join("+")}@{m.distance.toFixed(3)}{" "}
                </span>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export const PortfolioPanel = memo(PortfolioPanelImpl);
