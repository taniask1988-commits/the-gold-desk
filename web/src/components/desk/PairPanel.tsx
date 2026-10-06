"use client";

/**
 * GAUNTLET 7 Piece 5 — PairPanel: spread/ratio chart with rolling
 * z-score ±2σ bands + Engle-Granger cointegration stat (exploratory,
 * labeled). Pure-math over two keyless bar series.
 */

import { useEffect, useMemo, useState } from "react";

import { ExportCsvButton } from "./ExportCsvButton";

const GOLD = "#c8a04b";
const INK = "#f4f7fa";
const DIM = "#76828e";
const LINE = "#1a1f2c";
const GREEN = "#3fb950";
const RED = "#f85149";
const CYAN = "#39c5cf";

type ZPoint = { z: number; mean: number; sd: number } | null;
type PairPayload = {
  ok?: boolean; a?: string; b?: string; mode?: string; window?: number;
  spread_label?: string; dates?: string[]; spread?: number[];
  zscore?: ZPoint[]; zscore_now?: number; band?: number;
  hedge?: { alpha: number; beta: number };
  cointegration?: { adf_t_stat: number; verdict: string; critical_values: Record<string, number>; exploratory: boolean; note: string };
  n_days?: number; errors?: string[]; error?: string; source?: string;
};

export function PairPanel({ defaultA = "gold", defaultB = "silver" }: {
  defaultA?: string; defaultB?: string;
}) {
  const [a, setA] = useState(defaultA);
  const [b, setB] = useState(defaultB);
  const [mode, setMode] = useState<"ratio" | "spread">("ratio");
  const [data, setData] = useState<PairPayload | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    setLoading(true);
    // D1 guard (final critic): an identical pair is degenerate — spread
    // is identically zero, z-score undefined. Fail honest, fetch nothing.
    if (a.trim().toUpperCase() === b.trim().toUpperCase()) {
      setData({ ok: false,
        errors: ["identical symbols — pick a different partner to form a spread"] });
      setLoading(false);
      return () => { dead = true; };
    }
    fetch(`/api/desk/pair?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}&mode=${mode}`)
      .then((r) => r.json())
      .then((j: PairPayload) => { if (!dead) setData(j); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [a, b, mode]);

  const chart = useMemo(() => {
    if (!data?.ok || !data.spread?.length) return null;
    const zs = data.zscore ?? [];
    const pts = data.spread.map((v, i) => ({
      d: data.dates![i], v, z: zs[i]?.z ?? null,
    })).filter((p) => p.z != null) as { d: string; v: number; z: number }[];
    if (pts.length < 2) return null;
    return pts;
  }, [data]);

  return (
    <section id="pair" className="gdc-panel px-4 py-3" aria-label="Pair analytics">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
        <h2 className="gdc-spec" style={{ fontSize: 10, color: GOLD }}>PAIR / RELATIVE VALUE</h2>
        <span className="h-px flex-1" style={{ background: LINE }} />
        <ExportCsvButton kind="pair" params={{ a, b, window: 60, mode }} />
        <input value={a} onChange={(e) => setA(e.target.value)}
          className="gdc-data text-[11px] px-2 py-0.5 bg-transparent w-24"
          style={{ border: `1px solid ${LINE}`, color: INK, borderRadius: 6 }}
          aria-label="first symbol" />
        <span style={{ color: DIM }} className="text-[11px]">/</span>        <input value={b} onChange={(e) => setB(e.target.value)}
          className="gdc-data text-[11px] px-2 py-0.5 bg-transparent w-24"
          style={{ border: `1px solid ${LINE}`, color: INK, borderRadius: 6 }}
          aria-label="second symbol" />
        {(["ratio", "spread"] as const).map((m) => (
          <button key={m} onClick={() => setMode(m)}
            className="gdc-chip text-[10px] px-2 py-0.5"
            style={{ border: `1px solid ${mode === m ? GOLD : LINE}`, color: mode === m ? GOLD : DIM, background: "transparent", cursor: "pointer" }}>
            {m.toUpperCase()}
          </button>
        ))}
      </div>

      {loading && <p className="gdc-data text-[10px]" style={{ color: DIM }}>computing pair analytics…</p>}
      {!loading && data && !data.ok && (
        <p className="gdc-data text-[10px]" style={{ color: DIM }}>
          {data.error || (data.errors || []).join(" · ")}
        </p>
      )}
      {!loading && data?.ok && chart && (
        <>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-2">
            <Kpi label="SPREAD NOW" value={fmt(chart[chart.length - 1].v)} sub={data.spread_label} gold />
            <Kpi label="Z-SCORE" value={String(data.zscore_now)} sub={`window ${data.window}d`} />
            <Kpi label="HEDGE β" value={String(data.hedge?.beta)} sub="OLS a ~ b" />
            <Kpi label="COINTEGRATION" value={`t ${data.cointegration?.adf_t_stat}`}
              sub={data.cointegration?.verdict} />
          </div>
          <PairChart pts={chart} band={data.band ?? 2} label={data.spread_label ?? ""} />
          <p className="gdc-data text-[9px] mt-1" style={{ color: DIM }}
            title={data.cointegration?.note}>
            Engle-Granger 2-step, zero-lag ADF · exploratory (never a gate) ·
            source {data.source} · {data.n_days} aligned daily closes
          </p>
        </>
      )}
    </section>
  );
}

function PairChart({ pts, band, label }: {
  pts: { d: string; v: number; z: number }[]; band: number; label: string;
}) {
  const w = 900, h = 200, pad = 30;
  const zs = pts.map((p) => p.z);
  const zMin = Math.min(...zs, -band - 0.5);
  const zMax = Math.max(...zs, band + 0.5);
  const X = (i: number) => pad + (i / (pts.length - 1)) * (w - 2 * pad);
  const Y = (z: number) => h - pad - ((z - zMin) / (zMax - zMin)) * (h - 2 * pad);
  const line = pts.map((p, i) => `${i ? "L" : "M"}${X(i).toFixed(1)},${Y(p.z).toFixed(1)}`).join(" ");
  return (
    <svg viewBox={`0 0 ${w} ${h}`} style={{ width: "100%", height: 200 }}>
      {[band, 0, -band].map((zv) => (
        <g key={zv}>
          <line x1={pad} x2={w - pad} y1={Y(zv)} y2={Y(zv)}
            stroke={zv === 0 ? LINE : GOLD} strokeDasharray={zv === 0 ? "" : "4,3"} strokeWidth={1} />
          <text x={w - pad + 4} y={Y(zv) + 3} fill={zv === 0 ? DIM : GOLD} fontSize={8}>
            {zv === 0 ? "0" : `±${band}σ`}
          </text>
        </g>
      ))}
      {pts.map((p, i) => (
        Math.abs(p.z) > band && i % 3 === 0 ? (
          <circle key={i} cx={X(i)} cy={Y(p.z)} r={1.6} fill={p.z > 0 ? RED : GREEN} />
        ) : null
      ))}
      <path d={line} fill="none" stroke={CYAN} strokeWidth={1.4} />
      <text x={pad} y={11} fill={DIM} fontSize={9}>{label} — z-score vs window mean (units σ)</text>
      <text x={pad} y={h - 6} fill={DIM} fontSize={8}>{pts[0].d}</text>
      <text x={w - pad} y={h - 6} fill={DIM} fontSize={8} textAnchor="end">{pts[pts.length - 1].d}</text>
    </svg>
  );
}

function Kpi({ label, value, sub, gold = false }: {
  label: string; value?: string; sub?: string; gold?: boolean;
}) {
  return (
    <div className="gdc-chip px-2.5 py-1.5 overflow-hidden" style={{ border: `1px solid ${LINE}` }}>
      <div className="gdc-kicker truncate" style={{ color: DIM }}>{label}</div>
      <div className="gdc-data text-[13px] truncate" style={{ color: gold ? GOLD : INK }}>{value ?? "—"}</div>
      {sub && <div className="text-[8px] truncate" style={{ color: DIM }}>{sub}</div>}
    </div>
  );
}

function fmt(v: number): string {
  if (Math.abs(v) >= 1000) return v.toLocaleString(undefined, { maximumFractionDigits: 2 });
  return String(Math.round(v * 10000) / 10000);
}
