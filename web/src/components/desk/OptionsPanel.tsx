"use client";

/**
 * GAUNTLET 7 Piece 2 — OptionsPanel: Greeks + IV surface/skew + OI wall +
 * max pain + put/call + IV rank, computed keyless in Python
 * (/api/desk/options → cli options-analytics). Every number carries its
 * source/delay badge — honest labels, never fabricated. Fail-soft: a dead
 * chain renders the error, never a fake surface.
 */

import { useEffect, useMemo, useState } from "react";

import { ExportCsvButton } from "./ExportCsvButton";

type GreekRow = {
  contract: string; kind: string; expiry: string; strike: number;
  price: number; delta: number; gamma: number; vega: number; theta: number;
  rho: number; iv: number; open_interest: number; volume: number;
};
type SmilePoint = { strike: number; iv: number; kind: string };
type OptionsPayload = {
  rate_assumption?: string;
  ok?: boolean; symbol?: string; provider?: string; data_delay?: string;
  spot?: number; spot_derived?: boolean; contracts?: number;
  greeks_grid?: GreekRow[];
  iv_surface?: { expiries: { expiry: string; t_years: number; smiles: SmilePoint[] }[]; term_structure: { expiry: string; t_years: number; atm_iv: number | null }[] };
  skew?: { atm_iv?: number; risk_reversal_25d?: number; butterfly_25d?: number; note?: string } | null;
  iv_rank?: { iv_rank?: number; iv_percentile?: number; basis?: string } | null;
  max_pain?: { max_pain?: number | null } | null;
  put_call_ratio_volume?: { put_call_ratio?: number | null };
  put_call_ratio_oi?: { put_call_ratio?: number | null };
  oi_wall?: { strike: number; call_oi: number; put_oi: number; total_oi: number }[];
  errors?: string[]; stale_error?: boolean; stale_reason?: string;
  error?: string;
};

const GOLD = "#c8a04b";
const INK = "#f4f7fa";
const DIM = "#76828e";
const LINE = "#1a1f2c";
const GREEN = "#3fb950";
const RED = "#f85149";
const CYAN = "#39c5cf";

function SparklineIV({ smile, spot }: { smile: SmilePoint[]; spot: number }) {
  if (smile.length < 2) return null;
  const w = 560, h = 130, pad = 26;
  // robust y-scale: 5th–95th percentile of IV so a single deep-ITM
  // outlier (Yahoo serves 200%+ garbage on illiquid wings) cannot crush
  // the smile; outliers still plot, clipped, honestly
  const ivs = [...smile.map((p) => p.iv)].sort((a, b) => a - b);
  const q = (p: number) => ivs[Math.min(ivs.length - 1, Math.floor(p * ivs.length))];
  let y0 = q(0.05), y1 = q(0.95);
  if (y1 - y0 < 0.01) { const m = (y0 + y1) / 2; y0 = m - 0.005; y1 = m + 0.005; }
  const pad2 = (y1 - y0) * 0.08;
  y0 = Math.max(0, y0 - pad2); y1 = y1 + pad2;
  const xs = smile.map((p) => p.strike);
  const x0 = Math.min(...xs), x1 = Math.max(...xs);
  const X = (v: number) => pad + ((v - x0) / (x1 - x0 || 1)) * (w - 2 * pad);
  const Y = (v: number) => h - pad - ((Math.min(Math.max(v, y0), y1) - y0) / (y1 - y0)) * (h - 2 * pad);
  const calls = smile.filter((p) => p.kind === "call");
  const puts = smile.filter((p) => p.kind === "put");
  const path = (pts: SmilePoint[]) => pts.map((p, i) => `${i ? "L" : "M"}${X(p.strike).toFixed(1)},${Y(p.iv).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 130 }}>
      {[0.25, 0.5, 0.75].map((f) => (
        <line key={f} x1={pad} x2={w - pad} y1={pad + f * (h - 2 * pad)} y2={pad + f * (h - 2 * pad)} stroke={LINE} strokeWidth={0.5} />
      ))}
      <line x1={X(spot)} y1={4} x2={X(spot)} y2={h - 4} stroke={GOLD} strokeDasharray="3,3" strokeWidth={1} />
      <text x={Math.min(X(spot) + 4, w - 70)} y={14} fill={GOLD} fontSize={8}>spot {spot}</text>
      <path d={path(calls)} fill="none" stroke={GREEN} strokeWidth={1.4} />
      <path d={path(puts)} fill="none" stroke={RED} strokeWidth={1.4} />
      {/* strike ticks: 5 evenly spaced strikes */}
      {[0, 0.25, 0.5, 0.75, 1].map((f) => (
        <text key={f} x={pad + f * (w - 2 * pad)} y={h - 6} fill={DIM} fontSize={8} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>
          {(x0 + f * (x1 - x0)).toFixed(0)}
        </text>
      ))}
      <text x={4} y={12} fill={DIM} fontSize={8}>{(y1 * 100).toFixed(1)}%</text>
      <text x={4} y={h - pad} fill={DIM} fontSize={8}>{(y0 * 100).toFixed(1)}%</text>
    </svg>
  );
}

function OIWall({ rows, maxPain }: { rows: NonNullable<OptionsPayload["oi_wall"]>; maxPain?: number | null }) {
  if (!rows.length) return null;
  const w = 560, h = 150, pad = 26;
  const max = Math.max(...rows.map((r) => r.total_oi)) || 1;
  const bw = (w - 2 * pad) / rows.length;
  const mpX = maxPain != null && maxPain >= rows[0].strike && maxPain <= rows[rows.length - 1].strike
    ? pad + ((maxPain - rows[0].strike) / (rows[rows.length - 1].strike - rows[0].strike || 1)) * (w - 2 * pad)
    : null;
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 150 }}>
      {[0.5, 1].map((f) => (
        <g key={f}>
          <line x1={pad} x2={w - pad} y1={h - pad - f * (h - 2 * pad)} y2={h - pad - f * (h - 2 * pad)} stroke={LINE} strokeWidth={0.5} />
          <text x={2} y={h - pad - f * (h - 2 * pad) + 3} fill={DIM} fontSize={7}>{Math.round(f * max)}</text>
        </g>
      ))}
      {rows.map((r, i) => {
        const hc = (r.call_oi / max) * (h - 2 * pad);
        const hp = (r.put_oi / max) * (h - 2 * pad);
        const x = pad + i * bw;
        return (
          <g key={r.strike}>
            <rect x={x} y={h - pad - hc} width={bw * 0.8} height={hc} fill={GREEN} opacity={0.75} />
            <rect x={x} y={h - pad - hp} width={bw * 0.8} height={hp} fill={RED} opacity={0.55} />
          </g>
        );
      })}
      {mpX != null && (
        <g>
          <line x1={mpX} y1={2} x2={mpX} y2={h - pad} stroke={GOLD} strokeWidth={1} strokeDasharray="3,2" />
          <text x={Math.min(mpX + 3, w - 60)} y={10} fill={GOLD} fontSize={8}>max pain {maxPain}</text>
        </g>
      )}
      {[0, 0.5, 1].map((f) => (
        <text key={f} x={pad + f * (w - 2 * pad)} y={h - 6} fill={DIM} fontSize={8} textAnchor={f === 0 ? "start" : f === 1 ? "end" : "middle"}>
          {(rows[0].strike + f * (rows[rows.length - 1].strike - rows[0].strike)).toFixed(0)}
        </text>
      ))}
      <text x={w - pad} y={10} fill={DIM} fontSize={7} textAnchor="end">peak OI {Math.round(max)}</text>
    </svg>
  );
}

export function OptionsPanel({ symbol }: { symbol: string }) {
  const [data, setData] = useState<OptionsPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [expiry, setExpiry] = useState<string | null>(null);

  useEffect(() => {
    let dead = false;
    setLoading(true);
    fetch(`/api/desk/options?symbol=${encodeURIComponent(symbol)}`)
      .then((r) => r.json())
      .then((j: OptionsPayload) => { if (!dead) setData(j); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [symbol]);

  const smiles = useMemo(() => {
    const exps = data?.iv_surface?.expiries ?? [];
    if (!exps.length) return [];
    const pick = expiries_find(exps, expiry) ?? exps[0];
    return pick.smiles ?? [];
  }, [data, expiry]);

  const expiryOptions = data?.iv_surface?.expiries ?? [];
  const greeksAtm = useMemo(() => {
    const rows = data?.greeks_grid ?? [];
    const spot = data?.spot ?? 0;
    if (!rows.length || !spot) return [];
    return [...rows]
      .sort((a, b) => Math.abs(a.strike - spot) - Math.abs(b.strike - spot))
      .slice(0, 8)
      .sort((a, b) => a.strike - b.strike || a.kind.localeCompare(b.kind));
  }, [data]);

  if (loading) {
    return (
      <section className="gdc-panel px-4 py-3" aria-label="Options analytics">
        <h2 className="gdc-spec" style={{ fontSize: 10, color: GOLD }}>OPTIONS ANALYTICS</h2>
        <p className="gdc-data text-[10px] mt-2" style={{ color: DIM }}>loading chain…</p>
      </section>
    );
  }
  if (!data?.ok) {
    const why = data?.error || (data?.errors || []).join(" · ") || "chain unavailable (fail-soft)";
    return (
      <section className="gdc-panel px-4 py-3" aria-label="Options analytics">
        <h2 className="gdc-spec" style={{ fontSize: 10, color: GOLD }}>OPTIONS ANALYTICS</h2>
        <p className="gdc-data text-[10px] mt-2" style={{ color: DIM }}>{why}</p>
      </section>
    );
  }

  const mp = data.max_pain?.max_pain;
  const pcv = data.put_call_ratio_volume?.put_call_ratio;
  const pco = data.put_call_ratio_oi?.put_call_ratio;
  const skew = data.skew;
  const ivr = data.iv_rank;

  return (
    <section className="gdc-panel px-4 py-3" aria-label="Options analytics">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
        <h2 className="gdc-spec" style={{ fontSize: 10, color: GOLD }}>
          OPTIONS ANALYTICS — {data.symbol}
        </h2>
        <span className="h-px flex-1" style={{ background: LINE }} />
        <span className="gdc-data text-[9px]" style={{ color: DIM }}
          title={`computed in Python (closed-form Black-Scholes) from a ${data.provider} chain — ${data.data_delay}${data.rate_assumption ? ' — ' + data.rate_assumption : ''}`}>
          {data.provider} · {data.data_delay} · {data.contracts} contracts
          {data.stale_error ? " · STALE" : ""}
        </span>
        {data.rate_assumption && (
          <span className="gdc-data text-[9px] w-full" style={{ color: DIM }}
            title="honest modeling assumption — r=0.0 in per-contract greeks">
            {data.rate_assumption}
          </span>
        )}
        <ExportCsvButton kind="options" params={{ symbol: data.symbol }} />
      </div>

      {/* stat grid */}
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 mb-3">
        <Stat label="SPOT" value={data.spot != null ? String(data.spot) : "—"}
          sub={data.spot_derived ? "derived (no feed quote)" : "feed quote"} />
        <Stat label="MAX PAIN" value={mp != null ? String(mp) : "—"} sub="holder-payout minimum" gold />
        <Stat label="PUT/CALL VOL" value={pcv != null ? String(pcv) : "—"} sub="shares-weighted" />
        <Stat label="ATM IV" value={skew?.atm_iv != null ? `${(skew.atm_iv * 100).toFixed(1)}%` : "—"} sub="nearest expiry" />
        <Stat label="PUT/CALL OI" value={pco != null ? String(pco) : "—"} sub="open interest" />
        <Stat label="RR 25Δ" value={skew?.risk_reversal_25d != null ? `${(skew.risk_reversal_25d * 100).toFixed(2)}pp` : "—"} sub="put − call wing IV" />
        <Stat label="IV RANK" value={ivr?.iv_rank != null ? `${(ivr.iv_rank * 100).toFixed(1)}%` : "—"} sub="chain IV range" />
      </div>

      <div className="grid gap-3 lg:grid-cols-2">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="gdc-kicker" style={{ color: DIM }}>IV SKEW — CALLS/PUTS BY STRIKE</span>
            {expiryOptions.length > 1 && (
              <select
                className="gdc-chip text-[9px] bg-transparent"
                style={{ color: DIM, border: `1px solid ${LINE}` }}
                value={expiry ?? expiryOptions[0].expiry}
                onChange={(e) => setExpiry(e.target.value)}
                aria-label="expiry selector"
              >
                {expiryOptions.map((e) => (
                  <option key={e.expiry} value={e.expiry}>{e.expiry}</option>
                ))}
              </select>
            )}
          </div>
          <SparklineIV smile={smiles} spot={data.spot ?? 0} />
        </div>
        <div>
          <span className="gdc-kicker" style={{ color: DIM }}>OPEN INTEREST WALL</span>
          <OIWall rows={data.oi_wall ?? []} maxPain={mp} />
        </div>
      </div>

      {/* greeks table around ATM */}
      <div className="mt-3">
        <span className="gdc-kicker" style={{ color: DIM }}>GREEKS — NEAREST ATM (computed, closed-form BS · theta per day)</span>
        <div className="gdc-scroll mt-1 overflow-x-auto">
          <table className="w-full text-[10px] tabular-nums" style={{ color: INK }}>
            <thead>
              <tr className="text-left" style={{ color: DIM }}>
                <th className="py-1 pr-2 font-normal">contract</th>
                <th className="pr-2 font-normal">type</th>
                <th className="pr-2 font-normal">expiry</th>
                <th className="pr-2 font-normal">strike</th>
                <th className="pr-2 font-normal">iv</th>
                <th className="pr-2 font-normal">delta</th>
                <th className="pr-2 font-normal">gamma</th>
                <th className="pr-2 font-normal">vega</th>
                <th className="pr-2 font-normal">theta/d</th>
                <th className="pr-2 font-normal">OI</th>
              </tr>
            </thead>
            <tbody>
              {greeksAtm.map((g) => (
                <tr key={g.contract} style={{ borderTop: `1px solid ${LINE}` }}>
                  <td className="py-0.5 pr-2">{g.contract}</td>
                  <td className="pr-2" style={{ color: g.kind === "call" ? GREEN : RED }}>{g.kind === "call" ? "CALL" : "PUT"}</td>
                  <td className="pr-2">{g.expiry}</td>
                  <td className="pr-2">{g.strike}</td>
                  <td className="pr-2">{g.iv != null ? `${(g.iv * 100).toFixed(1)}%` : "—"}</td>
                  <td className="pr-2">{g.delta?.toFixed(4)}</td>
                  <td className="pr-2">{g.gamma != null ? g.gamma.toFixed(6) : "—"}</td>
                  <td className="pr-2">{g.vega != null ? g.vega.toFixed(3) : "—"}</td>
                  <td className="pr-2">{g.theta != null ? (g.theta / 365).toFixed(4) : "—"}</td>
                  <td className="pr-2">{g.open_interest}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  );
}

function expiries_find(exps: { expiry: string; smiles?: SmilePoint[] }[], pick: string | null) {
  return exps.find((e) => e.expiry === pick) ?? null;
}

function Stat({ label, value, sub, gold = false }: {
  label: string; value: string; sub?: string; gold?: boolean;
}) {
  return (
    <div className="gdc-chip px-2.5 py-1.5 overflow-hidden" style={{ border: `1px solid ${LINE}` }}
      title={sub ? `${label}: ${sub}` : label}>
      <div className="gdc-kicker truncate" style={{ color: DIM }}>{label}</div>
      <div className="gdc-data text-[13px] truncate" style={{ color: gold ? GOLD : INK }}>{value}</div>
      {sub && <div className="text-[8px] truncate" style={{ color: DIM }}>{sub}</div>}
    </div>
  );
}
