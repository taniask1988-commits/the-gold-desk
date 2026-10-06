"use client";

/**
 * GAUNTLET 7 Piece 4 — MacroPanel: the macro dashboard (rates complex,
 * dollar, VIX, CPI path) + Treasury curve-history heatmap + the
 * gold-vs-real-yields overlay (the desk's #1 macro driver).
 * Sources labeled per number (fred_api | fred_csv | treasury.gov).
 */

import { useEffect, useMemo, useState } from "react";

import { ExportCsvButton } from "./ExportCsvButton";

const GOLD = "#c8a04b";
const INK = "#f4f7fa";
const DIM = "#76828e";
const LINE = "#1a1f2c";
const GREEN = "#3fb950";
const RED = "#f85149";

type Obs = { date: string; value: number };
type SeriesOut = { ok?: boolean; source?: string; observations?: Obs[]; errors?: string[]; stale_error?: boolean };
type Dash = {
  ok?: boolean; n_series_ok?: number; n_series?: number;
  snapshot?: Record<string, { date: string; value: number } | null>;
  labels?: Record<string, string>;
  series_errors?: Record<string, string[]>;
  cpi_yoy?: { latest?: { date: string; value: number } | null; series_tail?: Obs[] };
  gold_vs_real?: { ok?: boolean; rolling_correlation?: number; rolling_correlation_window?: number; n_days?: number; source?: string; errors?: string[] };
  sources?: string[];
};
type Curve = {
  ok?: boolean; tenors?: string[]; matrix?: Record<string, number | string>[];
  source?: string; errors?: string[];
};

export function MacroPanel() {
  const [dash, setDash] = useState<Dash | null>(null);
  const [curve, setCurve] = useState<Curve | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let dead = false;
    Promise.all([
      fetch("/api/desk/macro").then((r) => r.json()),
      fetch("/api/desk/curve-history?years=1").then((r) => r.json()),
    ])
      .then(([d, c]) => { if (!dead) { setDash(d); setCurve(c); } })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, []);

  const snapshotRows = useMemo(() => {
    if (!dash?.snapshot) return [];
    return Object.entries(dash.snapshot)
      .filter(([, v]) => v != null)
      .map(([sid, v]) => ({ sid, ...v! }));
  }, [dash]);

  return (
    <section id="macro" className="gdc-panel px-4 py-3" aria-label="Macro dashboard">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 mb-2">
        <h2 className="gdc-spec" style={{ fontSize: 10, color: GOLD }}>
          MACRO — RATES, DOLLAR, CPI, GOLD↔REAL YIELDS
        </h2>
        <span className="h-px flex-1" style={{ background: LINE }} />
        <ExportCsvButton kind="macro" label="EXPORT CSV" />
        <span className="gdc-data text-[9px]" style={{ color: DIM }}>
          {dash ? `${dash.n_series_ok}/${dash.n_series} FRED series ok` : "loading…"}
          {dash?.sources?.length ? ` · ${dash.sources.join(", ")}` : ""}
        </span>
      </div>

      {loading && <p className="gdc-data text-[10px]" style={{ color: DIM }}>fetching macro plane…</p>}

      {!loading && dash?.ok && (
        <>
          {snapshotRows.length > 0 && (
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-2 mb-3">
              {snapshotRows.map((r) => (
                <div key={r.sid} className="gdc-chip px-2.5 py-1.5 overflow-hidden" style={{ border: `1px solid ${LINE}` }}
                  title={`${dash.labels?.[r.sid] ?? r.sid} @ ${r.date} (FRED)`}>
                  <div className="gdc-kicker truncate" style={{ color: DIM }}>{r.sid}</div>
                  <div className="gdc-data text-[13px]" style={{ color: INK }}>{r.value}</div>
                  <div className="text-[8px] truncate" style={{ color: DIM }}>{r.date}</div>
                </div>
              ))}
            </div>
          )}

          {dash.cpi_yoy?.latest && (
            <p className="gdc-data text-[10px] mb-2" style={{ color: DIM }}>
              CPI YoY <span style={{ color: INK }}>{dash.cpi_yoy.latest.value}%</span> @ {dash.cpi_yoy.latest.date}
              {" · "}gold↔real-yield corr({dash.gold_vs_real?.rolling_correlation_window}d):{" "}
              <span style={{ color: (dash.gold_vs_real?.rolling_correlation ?? 0) < 0 ? RED : GREEN }}>
                {dash.gold_vs_real?.rolling_correlation}
              </span>
              {" · "}{dash.gold_vs_real?.n_days} aligned days
            </p>
          )}
          {((dash.series_errors && Object.keys(dash.series_errors).length > 0)) && (
            <p className="gdc-data text-[9px] mb-2" style={{ color: DIM }}
              title="series errors (fail-soft, per series)">
              FRED series unavailable (fail-soft): {Object.keys(dash.series_errors ?? {}).join(", ")} — set FRED_API_KEY for the reliable path
            </p>
          )}
        </>
      )}
      {!loading && dash && !dash.ok && (
        <p className="gdc-data text-[10px] mb-2" style={{ color: DIM }}>
          FRED series unreachable from this host (fail-soft, honest error — no fabrication): set FRED_API_KEY or serve from a non-datacenter IP. The Treasury.gov keyless plane below never needs FRED.
        </p>
      )}

      {!loading && curve?.ok && (
        <CurveHeatmap curve={curve} />
      )}
    </section>
  );
}

function CurveHeatmap({ curve }: { curve: Curve }) {
  const tenors = curve.tenors ?? [];
  const matrix = curve.matrix ?? [];
  if (!tenors.length || !matrix.length) return null;
  // color scale: 2.5% (green) → 5.5% (red) span of yields
  const color = (v: number) => {
    const t = Math.min(1, Math.max(0, (v - 2.5) / 3.0));
    return `rgba(${Math.round(63 + t * (248 - 63))},${Math.round(185 - t * (185 - 81))},${Math.round(80 + t * (73 - 80))},${0.25 + 0.5 * t})`;
  };
  const recent = matrix.slice(-90);   // last ~90 trading days
  const rowH = 12, labW = 64, cellW = 26;
  const w = labW + recent.length * cellW;
  const h = 20 + tenors.length * rowH;
  return (
    <div className="mt-2">
      <div className="flex items-center gap-2 mb-1">
        <span className="gdc-kicker" style={{ color: DIM }}>
          TREASURY CURVE HISTORY — LAST {recent.length} TRADING DAYS
        </span>
        <span className="gdc-data text-[9px]" style={{ color: DIM }}>
          source {curve.source} · green low yields → red high
        </span>
      </div>
      <div className="gdc-scroll overflow-x-auto">
        <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} style={{ maxWidth: "none" }}>
          {tenors.map((t, ti) => (
            <g key={t}>
              <text x={0} y={20 + ti * rowH + 9} fill={DIM} fontSize={9}>{t}</text>
              {recent.map((row, ri) => {
                const v = row[t];
                return (
                  <rect key={ri} x={labW + ri * cellW} y={14 + ti * rowH}
                    width={cellW - 1.5} height={rowH - 1.5}
                    fill={typeof v === "number" ? color(v) : "#141821"}>
                    <title>{`${row.date} ${t}: ${v ?? "n/a"}`}</title>
                  </rect>
                );
              })}
            </g>
          ))}
          <text x={0} y={9} fill={DIM} fontSize={8}>
            {recent[0]?.date} → {recent[recent.length - 1]?.date}
          </text>
        </svg>
      </div>
      {/* legend + latest-value row (values readable, not color-only) */}
      <div className="flex flex-wrap items-center gap-2 mt-1">
        <svg viewBox="0 0 120 10" width={120} height={10} aria-label="yield color scale legend">
          <defs>
            <linearGradient id="gdc-yield-scale" x1="0" x2="1">
              <rect x={0} y={0} width={40} height={10} fill={color(2.5)} />
              <rect x={40} y={0} width={40} height={10} fill={color(4.0)} />
              <rect x={80} y={0} width={40} height={10} fill={color(5.5)} />
            </linearGradient>
          </defs>
          <rect x={0} y={0} width={120} height={10} fill="url(#gdc-yield-scale)" />
        </svg>
        <span className="gdc-data text-[9px]" style={{ color: DIM }}>
          2.5% → 5.5% · hover any cell for date+value · latest:
        </span>
        <span className="gdc-data text-[9px]" style={{ color: INK }}>
          {tenors.map((t) => {
            const last = recent[recent.length - 1];
            return `${t} ${last?.[t] ?? "—"}`;
          }).join(" · ")}
        </span>
      </div>
    </div>
  );
}
