"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface AssetRow {
  symbol: string;
  pnl: number;
  pct_of_total: number;
  n_trades: number;
  win_rate: number;
}

interface SetupRow {
  setup: string;
  pnl: number;
  n_trades: number;
  win_rate: number;
}

interface HourRow {
  hour: number;
  session: string;
  pnl: number;
  n_trades: number;
  win_rate: number;
}

interface AttributionResult {
  ok: boolean;
  source?: string;
  n_trades?: number;
  n_wins?: number;
  n_losses?: number;
  win_rate?: number;
  total_pnl?: number;
  gross_profit?: number;
  gross_loss?: number;
  profit_factor?: number | null;
  by_asset?: AssetRow[];
  by_setup?: SetupRow[];
  by_hour?: HourRow[];
  n_unparsed_timestamps?: number;
  reconstruction?: {
    matched: number;
    n_entry_fills: number;
    n_exit_fills: number;
    open_or_unmatched: number;
    unmatched_exits: number;
  };
  error?: string;
}

const TABS = ["by asset", "by setup", "by hour"] as const;
type Tab = (typeof TABS)[number];
const CHART_W = 560;

function money(v: number | null | undefined, digits = 2): string {
  if (v === null || v === undefined) return "n/a";
  return `${v >= 0 ? "+" : ""}${v.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}`;
}

function pnlColor(v: number | null | undefined): string {
  if (v === null || v === undefined) return "#76828e";
  return v >= 0 ? "#3fb950" : "#f85149";
}

/** Pure-SVG 24-bucket hourly P&L bar chart — no chart library (charter rule). */
function HourlyChart({ hours }: { hours: HourRow[] }) {
  const H = 120;
  const maxAbs = Math.max(1e-9, ...hours.map((h) => Math.abs(h.pnl)));
  const zeroY = H / 2;
  const barW = CHART_W / hours.length - 1;
  return (
    <svg viewBox={`0 0 ${CHART_W} ${H}`} className="w-full" preserveAspectRatio="none" style={{ height: H }}>
      <line x1={0} y1={zeroY} x2={CHART_W} y2={zeroY} stroke="#76828e" strokeWidth={0.5} strokeDasharray="3 3" opacity={0.6} />
      {hours.map((h, i) => {
        const x = i * (CHART_W / hours.length) + 0.5;
        const mag = (Math.abs(h.pnl) / maxAbs) * (H / 2 - 6);
        const up = h.pnl >= 0;
        return (
          <rect
            key={h.hour}
            x={x}
            y={up ? zeroY - mag : zeroY}
            width={Math.max(1, barW)}
            height={Math.max(h.n_trades ? 1 : 0, mag)}
            fill={h.n_trades === 0 ? "#2a3040" : up ? "#3fb950" : "#f85149"}
            opacity={h.n_trades === 0 ? 0.4 : 0.9}
          />
        );
      })}
    </svg>
  );
}

function AttributionPanelImpl() {
  const [data, setData] = useState<AttributionResult | null>(null);
  const [source, setSource] = useState<string>("journal");
  const [tab, setTab] = useState<Tab>("by asset");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async (src: string) => {
    setBusy(true);
    try {
      const r = await fetch(`/api/desk/pnl/attribution?source=${src}`).then((x) => x.json());
      setData(r as AttributionResult);
    } catch {
      setData({ ok: false, error: "transport failure" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(source), 0);
    return () => clearTimeout(kick);
  }, [load, source]);

  const hours = data?.by_hour || [];
  const assets = data?.by_asset || [];
  const setups = data?.by_setup || [];
  const recon = data?.reconstruction;

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">P&amp;L attribution</span>
        <span className="gdc-kicker">
          by asset · by setup · by hour (utc) with asia / london / ny sessions — conservation-exact
        </span>
        <div className="ml-auto flex items-center gap-1.5 text-[9.5px]">
          {["journal", "ledger"].map((s) => (
            <button
              key={s}
              onClick={() => setSource(s)}
              className={`rounded px-1.5 py-0.5 ${source === s ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
            >{s}</button>
          ))}
          <button
            onClick={() => void load(source)}
            disabled={busy}
            className="ml-1 rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[#76828e] hover:text-[#f4f7fa] disabled:opacity-50"
          >{busy ? "running…" : "rerun"}</button>
        </div>
      </div>

      {data && !data.ok && (
        <div className="text-[10px] italic text-[#f85149]">
          {data.error || "attribution failed"}
        </div>
      )}

      {data?.ok && (
        <>
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>source: <span className="gdc-data text-[#9aa6b3]">{data.source || "—"}</span></span>
            <span>trades: <span className="gdc-data text-[#9aa6b3]">{data.n_trades ?? 0}</span> ({data.n_wins ?? 0}W/{data.n_losses ?? 0}L · win {(((data.win_rate ?? 0) * 100).toFixed(1))}%)</span>
            <span>profit factor: <span className="gdc-data text-[#9aa6b3]">{data.profit_factor === null || data.profit_factor === undefined ? "n/a" : data.profit_factor.toFixed(2)}</span></span>
            {recon && (
              <span>journal join: <span className="gdc-data text-[#9aa6b3]">{recon.matched} matched</span>{recon.open_or_unmatched ? ` · ${recon.open_or_unmatched} open/unmatched` : ""}{recon.unmatched_exits ? ` · ${recon.unmatched_exits} orphan exits` : ""}</span>
            )}
          </div>

          <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">total p&amp;l</div>
              <div className="gdc-display-num mt-0.5 text-[13px]" style={{ color: pnlColor(data.total_pnl) }}>{money(data.total_pnl)}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">gross profit</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#3fb950]">{money(data.gross_profit)}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">gross loss</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#f85149]">{money(-(data.gross_loss ?? 0))}</div>
            </div>
            <div className="rounded bg-[#1a1f2c] px-2.5 py-2">
              <div className="gdc-kicker text-[7px]">win rate</div>
              <div className="gdc-display-num mt-0.5 text-[13px] text-[#f4f7fa]">{(((data.win_rate ?? 0) * 100).toFixed(1))}%</div>
            </div>
          </div>

          <div className="flex items-center gap-1.5 text-[9.5px]">
            {TABS.map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded px-1.5 py-0.5 ${tab === t ? "bg-[#1f2632] text-[#f4f7fa]" : "text-[#76828e] hover:text-[#9aa6b3]"}`}
              >{t}</button>
            ))}
          </div>

          {tab === "by asset" && (
            <div className="overflow-x-auto">
              <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
                <thead>
                  <tr className="text-left text-[#76828e]">
                    <th className="py-0.5 pr-3 font-normal">asset</th>
                    <th className="py-0.5 pr-3 text-right font-normal">p&amp;l</th>
                    <th className="py-0.5 pr-3 text-right font-normal">% of total</th>
                    <th className="py-0.5 pr-3 text-right font-normal">trades</th>
                    <th className="py-0.5 text-right font-normal">win rate</th>
                  </tr>
                </thead>
                <tbody>
                  {assets.map((r) => (
                    <tr key={r.symbol} className="border-t border-white/[0.05]">
                      <td className="py-0.5 pr-3 text-[#9aa6b3]">{r.symbol}</td>
                      <td className="py-0.5 pr-3 text-right" style={{ color: pnlColor(r.pnl) }}>{money(r.pnl)}</td>
                      <td className="py-0.5 pr-3 text-right text-[#9aa6b3]">{(r.pct_of_total * 100).toFixed(1)}%</td>
                      <td className="py-0.5 pr-3 text-right text-[#76828e]">{r.n_trades}</td>
                      <td className="py-0.5 text-right text-[#76828e]">{(r.win_rate * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                  {assets.length === 0 && (
                    <tr><td colSpan={5} className="py-1 text-[10px] italic text-[#76828e]">no closed trades in this source</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {tab === "by setup" && (
            <div className="overflow-x-auto">
              <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
                <thead>
                  <tr className="text-left text-[#76828e]">
                    <th className="py-0.5 pr-3 font-normal">setup</th>
                    <th className="py-0.5 pr-3 text-right font-normal">p&amp;l</th>
                    <th className="py-0.5 pr-3 text-right font-normal">trades</th>
                    <th className="py-0.5 text-right font-normal">win rate</th>
                  </tr>
                </thead>
                <tbody>
                  {setups.map((r) => (
                    <tr key={r.setup} className="border-t border-white/[0.05]">
                      <td className="py-0.5 pr-3 text-[#9aa6b3]">{r.setup}</td>
                      <td className="py-0.5 pr-3 text-right" style={{ color: pnlColor(r.pnl) }}>{money(r.pnl)}</td>
                      <td className="py-0.5 pr-3 text-right text-[#76828e]">{r.n_trades}</td>
                      <td className="py-0.5 text-right text-[#76828e]">{(r.win_rate * 100).toFixed(0)}%</td>
                    </tr>
                  ))}
                  {setups.length === 0 && (
                    <tr><td colSpan={4} className="py-1 text-[10px] italic text-[#76828e]">no closed trades in this source</td></tr>
                  )}
                </tbody>
              </table>
            </div>
          )}

          {tab === "by hour" && (
            <div className="rounded bg-[#12161f] p-2">
              <HourlyChart hours={hours} />
              <div className="mt-1 flex justify-between text-[8px] text-[#76828e]">
                <span>00 utc · asia</span>
                <span>08 · london</span>
                <span>13 · new york</span>
                <span>23 utc</span>
              </div>
              <div className="mt-2 overflow-x-auto">
                <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
                  <thead>
                    <tr className="text-left text-[#76828e]">
                      <th className="py-0.5 pr-3 font-normal">hour (utc)</th>
                      <th className="py-0.5 pr-3 font-normal">session</th>
                      <th className="py-0.5 pr-3 text-right font-normal">p&amp;l</th>
                      <th className="py-0.5 text-right font-normal">trades</th>
                    </tr>
                  </thead>
                  <tbody>
                    {hours.filter((h) => h.n_trades > 0).map((h) => (
                      <tr key={h.hour} className="border-t border-white/[0.05]">
                        <td className="py-0.5 pr-3 text-[#9aa6b3]">{String(h.hour).padStart(2, "0")}:00</td>
                        <td className="py-0.5 pr-3 text-[#76828e]">{h.session}</td>
                        <td className="py-0.5 pr-3 text-right" style={{ color: pnlColor(h.pnl) }}>{money(h.pnl)}</td>
                        <td className="py-0.5 text-right text-[#76828e]">{h.n_trades}</td>
                      </tr>
                    ))}
                    {hours.every((h) => h.n_trades === 0) && (
                      <tr><td colSpan={4} className="py-1 text-[10px] italic text-[#76828e]">no timestamped trades in this source</td></tr>
                    )}
                  </tbody>
                </table>
              </div>
              {!!data.n_unparsed_timestamps && (
                <div className="mt-1 text-[8px] italic text-[#76828e]">
                  {data.n_unparsed_timestamps} trade(s) with unparseable timestamps excluded from the hourly view
                </div>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export const AttributionPanel = memo(AttributionPanelImpl);
