"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface AlpacaAccount {
  status: string;
  equity: string | number;
  cash: string | number;
  buying_power: string | number;
  last_equity: string | number;
  unrealized_pl_today?: string | number;
  unrealized_plpc_today?: string | number;
}

interface Position {
  symbol: string;
  qty: string | number;
  avg_entry_price: string | number;
  current_price: string | number;
  unrealized_pl: string | number;
  side: string;
}

interface Order {
  id: string;
  symbol: string;
  qty: string | number;
  side: string;
  type: string;
  status: string;
  limit_price?: string | number | null;
  stop_price?: string | number | null;
  created_at?: string;
}

interface AlpacaSummary {
  ok: boolean;
  account?: AlpacaAccount;
  positions?: Position[];
  orders?: Order[];
  as_of?: string;
  reason_code?: string;
  blocked?: string;
  error?: string;
  message?: string;
}

function money(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return String(v);
  const sign = n < 0 ? "-" : "";
  return `${sign}$${Math.abs(n).toLocaleString(undefined, { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

function pnlColor(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return "#76828e";
  const n = typeof v === "string" ? parseFloat(v) : v;
  if (Number.isNaN(n)) return "#76828e";
  if (n > 0) return "#3fb950";
  if (n < 0) return "#f85149";
  return "#76828e";
}

function AlpacaPanelImpl() {
  const [data, setData] = useState<AlpacaSummary | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/desk/account/alpaca").then((x) => x.json());
      setData(r as AlpacaSummary);
    } catch {
      setData({ ok: false, error: "transport failure" });
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), 30_000);
    return () => { clearTimeout(kick); clearInterval(t); };
  }, [load]);

  const blocked = data?.ok === false && (data?.blocked === "CONSTITUTION_BLOCKED" || data?.reason_code === "ALPACA_CREDS_MISSING");
  const acc = data?.account;
  const positions = data?.positions || [];
  const orders = data?.orders || [];
  const todayPnl = acc?.unrealized_pl_today !== undefined
    ? (typeof acc.unrealized_pl_today === "string" ? parseFloat(acc.unrealized_pl_today) : acc.unrealized_pl_today)
    : undefined;

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Alpaca paper</span>
        <span className="gdc-kicker">live paper execution · keyless-with-paper-key · stdlib urllib</span>
        <span className="ml-auto flex items-center gap-2 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
          {data?.ok ? <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" /> : null}
          {blocked ? "fail-closed" : data?.ok ? "live" : data === null ? "loading…" : "rest unreachable"}
        </span>
      </div>
      {blocked ? (
        <div className="rounded border border-[#f85149]/40 bg-[#f85149]/10 px-3 py-4 text-[11.5px] text-[#f85149]">
          CONSTITUTION_BLOCKED — ALPACA_CREDS_MISSING
          <div className="mt-1 text-[10.5px] text-[#9aa6b3]">
            Paper keys are free at alpaca.markets. Set <code className="text-[#f4f7fa]">ALPACA_PAPER_KEY</code> +{" "}
            <code className="text-[#f4f7fa]">ALPACA_PAPER_SECRET</code> env vars to enable live paper execution. The
            existing synthetic `PaperAccountStore` continues to work as the fallback path.
          </div>
        </div>
      ) : !data?.ok ? (
        <div className="text-[11px] italic text-[#76828e]">
          alpaca paper unreachable: {data?.error || "unknown"}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <div className="rounded bg-[#0f1219] p-2.5">
              <div className="gdc-kicker text-[8px] uppercase tracking-wider text-[#76828e]">status</div>
              <div className="text-[12px] text-[#f4f7fa]">{acc?.status || "—"}</div>
            </div>
            <div className="rounded bg-[#0f1219] p-2.5">
              <div className="gdc-kicker text-[8px] uppercase tracking-wider text-[#76828e]">equity</div>
              <div className="text-[12px] text-[#f4f7fa]">{money(acc?.equity)}</div>
            </div>
            <div className="rounded bg-[#0f1219] p-2.5">
              <div className="gdc-kicker text-[8px] uppercase tracking-wider text-[#76828e]">cash</div>
              <div className="text-[12px] text-[#f4f7fa]">{money(acc?.cash)}</div>
            </div>
            <div className="rounded bg-[#0f1219] p-2.5">
              <div className="gdc-kicker text-[8px] uppercase tracking-wider text-[#76828e]">buying power</div>
              <div className="text-[12px] text-[#f4f7fa]">{money(acc?.buying_power)}</div>
            </div>
            <div className="rounded bg-[#0f1219] p-2.5">
              <div className="gdc-kicker text-[8px] uppercase tracking-wider text-[#76828e]">today P&L</div>
              <div style={{ color: pnlColor(todayPnl) }} className="text-[12px] tabular-nums">
                {money(todayPnl)}
              </div>
            </div>
          </div>
          <div className="space-y-2">
            <div className="gdc-kicker text-[10px] text-[#9aa6b3]">
              Open positions ({positions.length})
            </div>
            <div className="max-h-[200px] overflow-y-auto">
              <table className="gdc-data w-full border-collapse text-[10px]">
                <thead className="text-[#76828e]">
                  <tr>
                    <th className="px-2 py-1 text-left">symbol</th>
                    <th className="px-2 py-1 text-left">side</th>
                    <th className="px-2 py-1 text-right">qty</th>
                    <th className="px-2 py-1 text-right">avg entry</th>
                    <th className="px-2 py-1 text-right">current</th>
                    <th className="px-2 py-1 text-right">unrealized P&L</th>
                  </tr>
                </thead>
                <tbody>
                  {positions.length === 0 ? (
                    <tr><td colSpan={6} className="px-2 py-2 text-center italic text-[#76828e]">no open positions</td></tr>
                  ) : positions.map((p, i) => (
                    <tr key={i} className="border-t border-white/[0.05]">
                      <td className="px-2 py-1 text-[#f4f7fa]">{p.symbol}</td>
                      <td className="px-2 py-1 text-[#9aa6b3]">{p.side || "—"}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{String(p.qty || "—")}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{money(p.avg_entry_price)}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{money(p.current_price)}</td>
                      <td style={{ color: pnlColor(p.unrealized_pl) }} className="px-2 py-1 text-right tabular-nums">
                        {money(p.unrealized_pl)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
          <div className="space-y-2">
            <div className="gdc-kicker text-[10px] text-[#9aa6b3]">
              Open orders ({orders.length})
            </div>
            <div className="max-h-[180px] overflow-y-auto">
              <table className="gdc-data w-full border-collapse text-[10px]">
                <thead className="text-[#76828e]">
                  <tr>
                    <th className="px-2 py-1 text-left">id</th>
                    <th className="px-2 py-1 text-left">side</th>
                    <th className="px-2 py-1 text-left">symbol</th>
                    <th className="px-2 py-1 text-right">qty</th>
                    <th className="px-2 py-1 text-left">type</th>
                    <th className="px-2 py-1 text-right">price</th>
                    <th className="px-2 py-1 text-left">status</th>
                  </tr>
                </thead>
                <tbody>
                  {orders.length === 0 ? (
                    <tr><td colSpan={7} className="px-2 py-2 text-center italic text-[#76828e]">no open orders</td></tr>
                  ) : orders.slice(0, 20).map((o, i) => (
                    <tr key={i} className="border-t border-white/[0.05]">
                      <td className="px-2 py-1 font-mono text-[#76828e]">{(o.id || "").slice(0, 10)}</td>
                      <td style={{ color: o.side === "buy" ? "#3fb950" : "#f85149" }} className="px-2 py-1">{o.side || "—"}</td>
                      <td className="px-2 py-1 text-[#f4f7fa]">{o.symbol || "—"}</td>
                      <td className="px-2 py-1 text-right tabular-nums">{String(o.qty || "—")}</td>
                      <td className="px-2 py-1 text-[#9aa6b3]">{o.type || "—"}</td>
                      <td className="px-2 py-1 text-right tabular-nums">
                        {o.limit_price ? money(o.limit_price) : o.stop_price ? money(o.stop_price) : "mkt"}
                      </td>
                      <td className="px-2 py-1 text-[#9aa6b3]">{o.status || "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export const AlpacaPanel = memo(AlpacaPanelImpl);
