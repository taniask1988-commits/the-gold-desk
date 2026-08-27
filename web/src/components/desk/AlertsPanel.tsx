"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface RuleRow {
  id: string;
  symbol: string;
  kind: string;
  params: Record<string, number | string>;
  enabled: boolean;
  cooldown_minutes: number;
  note: string;
}

interface FiredRow {
  event_id: string;
  rule_id: string;
  symbol: string;
  kind: string;
  message: string;
  value: number | null;
  threshold: number | null;
  fired_at: string;
  wall_fired_at?: string;
  channel: string;
  ack: boolean;
}

interface AlertsResult {
  ok: boolean;
  rules?: RuleRow[];
  fired?: FiredRow[];
  rules_count?: number;
  fired_count?: number;
  error?: string;
}

interface WatchStatus {
  ok: boolean;
  running?: boolean;
  as_of?: string;
  last_sweep?: string | null;
  next_sweep?: string | null;
  interval_seconds?: number | null;
  ticks?: number;
  last_error?: string | null;
  rules_count?: number;
  sessions?: Record<string, boolean>;
  n_open_sessions?: number;
  fired_logged?: number;
  error?: string;
}

const SYMBOLS = ["GC=F", "ES=F", "^TNX", "DX-Y.NYB", "BTC-USD", "^VIX", "CL=F", "EURUSD=X"];
const KINDS = [
  { id: "pct_move", label: "% move" },
  { id: "price_above", label: "price ≥" },
  { id: "price_below", label: "price ≤" },
  { id: "atr_spike", label: "ATR spike ×" },
  { id: "volume_spike", label: "volume spike ×" },
  { id: "corr_flip", label: "corr flip ↔" },
] as const;

function paramsSummary(r: RuleRow): string {
  const p = r.params || {};
  if (r.kind === "pct_move") return `±${p.threshold ?? "?"}% / ${p.window_bars ?? 1} bar`;
  if (r.kind === "price_above" || r.kind === "price_below") return `${p.level ?? "?"}`;
  if (r.kind === "atr_spike" || r.kind === "volume_spike") return `${p.k ?? "?"}× mean`;
  if (r.kind === "corr_flip") return `vs ${p.other ?? "?"}`;
  return JSON.stringify(p);
}

function relTime(iso?: string | null): string {
  if (!iso) return "—";
  const t = Date.parse(iso.includes("T") ? iso : iso.replace(" ", "T") + "Z");
  if (Number.isNaN(t)) return iso;
  const s = Math.round((Date.now() - t) / 1000);
  if (s < 0) return "just now";
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

function kindColor(kind: string): string {
  switch (kind) {
    case "pct_move": return "#d29922";
    case "price_above": case "price_below": return "#58a6ff";
    case "atr_spike": case "volume_spike": return "#f0883e";
    case "corr_flip": return "#bc8cff";
    default: return "#9aa6b3";
  }
}

function AlertsPanelImpl() {
  const [data, setData] = useState<AlertsResult | null>(null);
  const [status, setStatus] = useState<WatchStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  // add-rule form state
  const [fSymbol, setFSymbol] = useState("GC=F");
  const [fKind, setFKind] = useState<string>("pct_move");
  const [fThreshold, setFThreshold] = useState("1.5");
  const [fWindow, setFWindow] = useState("1");
  const [fLevel, setFLevel] = useState("");
  const [fK, setFK] = useState("2.5");
  const [fOther, setFOther] = useState("DX-Y.NYB");

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [a, s] = await Promise.all([
        fetch("/api/desk/alerts?limit=25").then((x) => x.json()),
        fetch("/api/desk/watch/status").then((x) => x.json()),
      ]);
      setData(a as AlertsResult);
      setStatus(s as WatchStatus);
    } catch {
      setData({ ok: false, error: "transport failure" });
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(), 0);
    return () => clearTimeout(kick);
  }, [load]);

  const addRule = useCallback(async () => {
    setMsg(null);
    const body: Record<string, unknown> = { action: "add", symbol: fSymbol, kind: fKind };
    if (fKind === "pct_move") {
      body.threshold = fThreshold;
      body.window = fWindow;
    } else if (fKind === "price_above" || fKind === "price_below") {
      body.level = fLevel;
    } else if (fKind === "atr_spike" || fKind === "volume_spike") {
      body.k = fK;
    } else if (fKind === "corr_flip") {
      body.other = fOther;
    }
    try {
      const r = await fetch("/api/desk/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((x) => x.json());
      setMsg(r.ok ? `rule added: ${r.rule?.id ?? "?"}` : `add failed: ${r.error ?? "?"}`);
      await load();
    } catch {
      setMsg("add failed: transport failure");
    }
  }, [fSymbol, fKind, fThreshold, fWindow, fLevel, fK, fOther, load]);

  const removeRule = useCallback(async (id: string) => {
    setMsg(null);
    try {
      const r = await fetch(`/api/desk/alerts?id=${encodeURIComponent(id)}`, { method: "DELETE" })
        .then((x) => x.json());
      setMsg(r.ok ? `removed: ${id}` : `remove failed: ${r.error ?? "?"}`);
      await load();
    } catch {
      setMsg("remove failed: transport failure");
    }
  }, [load]);

  const ackAlert = useCallback(async (eventId: string) => {
    setMsg(null);
    try {
      const r = await fetch("/api/desk/alerts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "ack", event_id: eventId }),
      }).then((x) => x.json());
      setMsg(r.ok ? "acked" : `ack failed: ${r.error ?? "?"}`);
      await load();
    } catch {
      setMsg("ack failed: transport failure");
    }
  }, [load]);

  const rules = data?.rules || [];
  const fired = (data?.fired || []).slice().reverse(); // newest first
  const sessions = status?.sessions || {};

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">Watch · alerts</span>
        <span className="gdc-kicker">
          autonomous loop — price / % / atr / volume / corr-flip rules · cooldown dedup · journal + telegram push
        </span>
        <div className="ml-auto flex items-center gap-1.5 text-[9.5px]">
          <button
            onClick={() => void load()}
            disabled={busy}
            className="rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[#76828e] hover:text-[#f4f7fa] disabled:opacity-50"
          >{busy ? "loading…" : "refresh"}</button>
        </div>
      </div>

      {data && !data.ok && (
        <div className="text-[10px] italic text-[#f85149]">{data.error || "alerts failed"}</div>
      )}

      {data?.ok && (
        <>
          {/* loop status strip */}
          <div className="flex flex-wrap gap-x-5 gap-y-1 text-[9.5px] text-[#76828e]">
            <span>loop: <span className="gdc-data text-[#9aa6b3]">{status?.running ? "recorded sweeps" : "idle"}</span></span>
            <span>last sweep: <span className="gdc-data text-[#9aa6b3]">{relTime(status?.last_sweep)}</span></span>
            <span>next: <span className="gdc-data text-[#9aa6b3]">{status?.next_sweep ? relTime(status.next_sweep).replace(" ago", "") : "—"}</span></span>
            <span>ticks: <span className="gdc-data text-[#9aa6b3]">{status?.ticks ?? 0}</span></span>
            <span>rules: <span className="gdc-data text-[#9aa6b3]">{data.rules_count ?? rules.length}</span></span>
            <span>fired logged: <span className="gdc-data text-[#9aa6b3]">{status?.fired_logged ?? data.fired_count ?? 0}</span></span>
            {status?.last_error && (
              <span className="italic text-[#f85149]">last error: {status.last_error}</span>
            )}
          </div>

          {/* session chips */}
          <div className="flex flex-wrap items-center gap-1.5">
            {Object.entries(sessions).map(([sym, open]) => (
              <span
                key={sym}
                className={`rounded px-1.5 py-0.5 text-[8.5px] tabular-nums ${open ? "bg-[#12261c] text-[#3fb950]" : "bg-[#1a1f2c] text-[#76828e]"}`}
              >
                {sym} {open ? "● open" : "○ closed"}
              </span>
            ))}
          </div>

          {/* rule table */}
          <div className="overflow-x-auto">
            <table className="gdc-data w-full border-collapse text-[9.5px] tabular-nums">
              <thead>
                <tr className="text-left text-[#76828e]">
                  <th className="py-0.5 pr-3 font-normal">rule</th>
                  <th className="py-0.5 pr-3 font-normal">symbol</th>
                  <th className="py-0.5 pr-3 font-normal">kind</th>
                  <th className="py-0.5 pr-3 font-normal">trigger</th>
                  <th className="py-0.5 pr-3 text-right font-normal">cooldown</th>
                  <th className="py-0.5 pr-3 font-normal">note</th>
                  <th className="py-0.5 text-right font-normal"></th>
                </tr>
              </thead>
              <tbody>
                {rules.map((r) => (
                  <tr key={r.id} className="border-t border-white/[0.05]">
                    <td className="py-0.5 pr-3 text-[#9aa6b3]">{r.id}</td>
                    <td className="py-0.5 pr-3 text-[#9aa6b3]">{r.symbol}</td>
                    <td className="py-0.5 pr-3" style={{ color: kindColor(r.kind) }}>{r.kind}</td>
                    <td className="py-0.5 pr-3 text-[#9aa6b3]">{paramsSummary(r)}</td>
                    <td className="py-0.5 pr-3 text-right text-[#76828e]">{r.cooldown_minutes}m</td>
                    <td className="py-0.5 pr-3 text-[#76828e]">{r.note}</td>
                    <td className="py-0.5 text-right">
                      <button
                        onClick={() => void removeRule(r.id)}
                        className="rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[8.5px] text-[#76828e] hover:text-[#f85149]"
                      >rm</button>
                    </td>
                  </tr>
                ))}
                {rules.length === 0 && (
                  <tr><td colSpan={7} className="py-1 text-[10px] italic text-[#76828e]">no rules — add one below</td></tr>
                )}
              </tbody>
            </table>
          </div>

          {/* add-rule form */}
          <div className="flex flex-wrap items-center gap-2 rounded bg-[#12161f] px-2.5 py-2 text-[9.5px]">
            <span className="gdc-kicker text-[7.5px]">add rule</span>
            <select value={fSymbol} onChange={(e) => setFSymbol(e.target.value)}
              className="rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none">
              {SYMBOLS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
            <select value={fKind} onChange={(e) => setFKind(e.target.value)}
              className="rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none">
              {KINDS.map((k) => <option key={k.id} value={k.id}>{k.label}</option>)}
            </select>
            {fKind === "pct_move" && (
              <>
                <label className="text-[#76828e]">±% <input value={fThreshold} onChange={(e) => setFThreshold(e.target.value)}
                  className="w-14 rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none" /></label>
                <label className="text-[#76828e]">window <input value={fWindow} onChange={(e) => setFWindow(e.target.value)}
                  className="w-12 rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none" /></label>
              </>
            )}
            {(fKind === "price_above" || fKind === "price_below") && (
              <label className="text-[#76828e]">level <input value={fLevel} onChange={(e) => setFLevel(e.target.value)} placeholder="2000"
                className="w-20 rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none" /></label>
            )}
            {(fKind === "atr_spike" || fKind === "volume_spike") && (
              <label className="text-[#76828e]">×mean <input value={fK} onChange={(e) => setFK(e.target.value)}
                className="w-12 rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none" /></label>
            )}
            {fKind === "corr_flip" && (
              <label className="text-[#76828e]">vs <input value={fOther} onChange={(e) => setFOther(e.target.value)}
                className="w-24 rounded bg-[#1a1f2c] px-1.5 py-1 text-[#9aa6b3] outline-none" /></label>
            )}
            <button onClick={() => void addRule()}
              className="rounded bg-[#1f2632] px-2 py-1 text-[#f4f7fa] hover:bg-[#2a3242]">add</button>
            {msg && <span className="italic text-[#76828e]">{msg}</span>}
          </div>

          {/* fired feed */}
          <div className="rounded bg-[#12161f] p-2">
            <div className="gdc-kicker mb-1 text-[7.5px]">fired alerts — newest first (cooldown-deduped · journaled ALERT_FIRED)</div>
            <div className="max-h-44 space-y-1 overflow-y-auto">
              {fired.map((f) => (
                <div key={f.event_id} className={`flex flex-wrap items-baseline gap-2 rounded px-2 py-1 text-[9.5px] ${f.ack ? "opacity-50" : "bg-[#1a1f2c]"}`}>
                  <span className="text-[#76828e] tabular-nums">{relTime(f.wall_fired_at || f.fired_at)}</span>
                  <span style={{ color: kindColor(f.kind) }}>[{f.kind}]</span>
                  <span className="text-[#9aa6b3]">{f.symbol}</span>
                  <span className="text-[#f4f7fa]">{f.message}</span>
                  <span className="text-[#76828e]">{f.channel === "telegram" ? "· tg" : ""}</span>
                  <span className="ml-auto">
                    {f.ack ? (
                      <span className="text-[8.5px] text-[#3fb950]">acked ✓</span>
                    ) : (
                      <button onClick={() => void ackAlert(f.event_id)}
                        className="rounded bg-[#242b38] px-1.5 py-0.5 text-[8.5px] text-[#76828e] hover:text-[#f4f7fa]">ack</button>
                    )}
                  </span>
                </div>
              ))}
              {fired.length === 0 && (
                <div className="px-2 py-1 text-[10px] italic text-[#76828e]">
                  nothing fired yet — run <span className="gdc-data">gold-desk watch-loop --dry-run</span> or the daemon
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

export const AlertsPanel = memo(AlertsPanelImpl);
