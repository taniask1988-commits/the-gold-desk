"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface BacktestTrade {
  side: string;
  entry: number;
  exit: number;
  pnl: number;
  reason: string;
  bars_held: number;
  entry_ts: string;
  exit_ts: string;
}

interface BacktestResult {
  ok: boolean;
  symbol?: string;
  range?: string;
  setup_id?: string;
  setup_version?: string;
  n_bars?: number;
  first_bar?: string;
  last_bar?: string;
  seed?: number;
  equity_start?: number;
  equity_end?: number;
  total_return?: number;
  sharpe?: number | null;
  sortino?: number | null;
  max_drawdown?: number | null;
  calmar?: number | null;
  buy_hold_return?: number | null;
  n_trades?: number;
  n_wins?: number;
  n_losses?: number;
  hit_rate?: number | null;
  profit_factor?: number | null;
  avg_win?: number | null;
  avg_loss?: number | null;
  n_days?: number;
  equity_curve?: number[];
  equity_curve_full_length?: number;
  equity_curve_sha256?: string;
  trades?: BacktestTrade[];
  error?: string;
}

const RANGES = ["1mo", "3mo", "6mo", "1y", "2y"] as const;
const CHART_W = 560;
const CHART_H = 120;

function pct(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "n/a";
  return `${v >= 0 ? "+" : ""}${(v * 100).toFixed(digits)}%`;
}

function num(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "n/a";
  return v.toFixed(digits);
}

function retColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "#76828e";
  return v >= 0 ? "#3fb950" : "#f85149";
}

/** Pure-SVG equity line chart — no 3rd-party chart library (charter rule). */
function EquityChart({ curve, start }: { curve: number[]; start: number }) {
  if (curve.length < 2) {
    return <div className="text-[10px] italic text-[#76828e]">not enough points to plot</div>;
  }
  const min = Math.min(...curve);
  const max = Math.max(...curve);
  const pad = (max - min) * 0.08 || 1;
  const lo = min - pad;
  const hi = max + pad;
  const range = hi - lo || 1;
  const step = CHART_W / (curve.length - 1);
  const y = (v: number) => CHART_H - ((v - lo) / range) * CHART_H;
  const path = curve
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(1)},${y(v).toFixed(1)}`)
    .join(" ");
  // baseline = starting equity (the strategy's flat reference)
  const baseY = y(start);
  const up = curve[curve.length - 1] >= start;
  const lineColor = up ? "#3fb950" : "#f85149";
  return (
    <svg viewBox={`0 0 ${CHART_W} ${CHART_H}`} className="w-full" preserveAspectRatio="none" style={{ height: CHART_H }}>
      <line x1={0} y1={baseY} x2={CHART_W} y2={baseY} stroke="#76828e" strokeWidth={0.5} strokeDasharray="3 3" opacity={0.6} />
      <path d={path} fill="none" stroke={lineColor} strokeWidth={1.4} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

function Stat({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
      <div className="gdc-kicker text-[7px]">{label}</div>
      <div className="gdc-display-num mt-0.5 text-[13px]" style={{ color: color || "#f4f7fa" }}>{value}</div>
    </div>
  );
}

function BacktestPanelImpl() {
  const [data, setData] = useState<BacktestResult | null>(null);
  const [rangeKey, setRangeKey] = useState<string>("1y");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (rk: string) => {
    setBusy(true);
    try {
      const r = await fetch(`/api/desk/backtest?bars=${rk}`).then((x) => x.json());
      setData(r as BacktestResult);
    } catch {
      setData({ ok: false, error: "transport failure" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(rangeKey), 0);
    return () => clearTimeout(kick);
  }, [load, rangeKey]);

  const curve = data?.equity_curve || [];
  const start = data?.equity_start ?? 100_000;
  const trades = data?.trades || [];
  const beatsBH =
    data?.total_return !== undefined && data?.buy_hold_return !== undefined &&
    data.total_return !== null && data.buy_hold_return !== null
      ? data.total_return - data.buy_hold_return
      : null;

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Backtest</span>
        <span className="gdc-kicker">
          GUESS london-range-breakout vs {data?.symbol || "GC=F"} 1h bars · mechanical exits · 1% risk — deterministic
        </span>
        <div className="ml-auto flex items-center gap-1.5 text-[9.5px]">
          {RANGES.map((rk) => (
            <button
              key={rk}
              onClick={() => setRangeKey(rk)}
              className={`rounded px-1.5 py-0.5 ${rangeKey === rk ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
            >{rk}</button>
          ))}
          <button
            onClick={() => void load(rangeKey)}
            disabled={busy}
            className="ml-1 rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[#76828e] hover:text-[#f4f7fa] disabled:opacity-50"
          >{busy ? "running…" : "rerun"}</button>
        </div>
      </div>

      {data && !data.ok && (
        <div className="text-[10px] italic text-[#f85149]">
          {data.error || "backtest failed — hourly bars unreachable?"}
        </div>
      )}

      {data?.ok && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>bars: <span className="gdc-data text-[#9aa6b3]">{data.n_bars}</span></span>
            <span>window: <span className="gdc-data text-[#9aa6b3]">{data.first_bar?.slice(0, 10)} → {data.last_bar?.slice(0, 10)}</span></span>
            <span>setup: <span className="gdc-data text-[#9aa6b3]">{data.setup_id} v{data.setup_version}</span></span>
            <span>days: <span className="gdc-data text-[#9aa6b3]">{data.n_days}</span></span>
            <span>determinism: seed <span className="gdc-data text-[#9aa6b3]">{data.seed}</span> · sha256 <span className="gdc-data text-[#9aa6b3]">{data.equity_curve_sha256?.slice(0, 12)}…</span></span>
          </div>

          <div className="rounded bg-[#12161f] p-2">
            <EquityChart curve={curve} start={start} />
            <div className="mt-1 flex justify-between text-[8px] text-[#76828e]">
              <span>equity {start.toLocaleString()} → {(data.equity_end ?? 0).toLocaleString()}</span>
              <span>
                last {data.equity_curve_full_length ?? curve.length} bars
                {(data.equity_curve_full_length ?? 0) > curve.length ? " (truncated for payload)" : ""}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            <Stat label="total return" value={pct(data.total_return)} color={retColor(data.total_return)} />
            <Stat label="buy & hold" value={pct(data.buy_hold_return)} color={retColor(data.buy_hold_return)} />
            <Stat
              label="vs buy & hold"
              value={beatsBH === null ? "n/a" : pct(beatsBH)}
              color={retColor(beatsBH)}
            />
            <Stat label="max drawdown" value={pct(data.max_drawdown)} color="#d9a343" />
            <Stat label="sharpe" value={num(data.sharpe)} />
            <Stat label="sortino" value={num(data.sortino)} />
            <Stat label="calmar" value={num(data.calmar)} />
            <Stat label="trades" value={`${data.n_trades ?? 0} (${data.n_wins ?? 0}W/${data.n_losses ?? 0}L)`} />
            <Stat label="hit rate" value={data.hit_rate === null || data.hit_rate === undefined ? "n/a" : pct(data.hit_rate, 1)} />
            <Stat
              label="profit factor"
              value={data.profit_factor === null || data.profit_factor === undefined ? "n/a" : data.profit_factor === Infinity ? "∞" : num(data.profit_factor)}
            />
            <Stat label="avg win" value={data.avg_win === null || data.avg_win === undefined ? "n/a" : `$${data.avg_win.toLocaleString()}`} color="#3fb950" />
            <Stat label="avg loss" value={data.avg_loss === null || data.avg_loss === undefined ? "n/a" : `$${data.avg_loss.toLocaleString()}`} color="#f85149" />
          </div>

          {trades.length > 0 && (
            <div className="overflow-x-auto">
              <div className="gdc-kicker mb-1 text-[#9aa6b3]">trades (last 6)</div>
              <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
                <thead>
                  <tr className="text-left text-[#76828e]">
                    <th className="py-0.5 pr-3 font-normal">side</th>
                    <th className="py-0.5 pr-3 text-right font-normal">entry</th>
                    <th className="py-0.5 pr-3 text-right font-normal">exit</th>
                    <th className="py-0.5 pr-3 text-right font-normal">pnl</th>
                    <th className="py-0.5 pr-3 font-normal">reason</th>
                    <th className="py-0.5 text-right font-normal">bars</th>
                  </tr>
                </thead>
                <tbody>
                  {trades.slice(-6).map((t, i) => (
                    <tr key={i} className="border-t border-white/[0.05]">
                      <td className="py-0.5 pr-3 text-[#9aa6b3]">{t.side}</td>
                      <td className="py-0.5 pr-3 text-right text-[#9aa6b3]">{t.entry.toFixed(2)}</td>
                      <td className="py-0.5 pr-3 text-right text-[#9aa6b3]">{t.exit.toFixed(2)}</td>
                      <td className="py-0.5 pr-3 text-right" style={{ color: retColor(t.pnl) }}>
                        {t.pnl >= 0 ? "+" : ""}{t.pnl.toFixed(0)}
                      </td>
                      <td className="py-0.5 pr-3 text-[#76828e]">{t.reason}</td>
                      <td className="py-0.5 text-right text-[#76828e]">{t.bars_held}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </div>
  );
}

export const BacktestPanel = memo(BacktestPanelImpl);
