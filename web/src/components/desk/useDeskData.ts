"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface DeskEventDTO {
  ts: string;
  event_id: string;
  kind: string;
  decision_ts: string | null;
  reason_code?: string | null;
  payload: Record<string, unknown> & {
    bar?: { ts_open: string; ts_close: string; o: number; h: number; l: number; c: number };
    code?: string;
    detail?: string;
    demo?: boolean;
    [k: string]: unknown;
  };
}

export interface OverviewDTO {
  ok: boolean;
  span: { from: string | null; to: string | null; days: number };
  totalEvents: number;
  barsProcessed: number;
  ticketsIssued: number;
  fills: number;
  blackouts: number;
  demo: boolean;
  constitutionHash: string | null;
  histogram: Record<string, number>;
  account: {
    balance: number;
    equity: number;
    highWater: number;
    dailyPnl: number;
    closedTrades: number;
    wins: number;
    losses: number;
    equityCurve: Array<{ ts: string; equity: number }>;
  } | null;
  days: string[];
  featuredDay: string | null;
  // M4: real constitution summary from /api/desk/overview → cli constitution --json.
  // null while loading or when the harness is unreachable.
  constitution: {
    blockedCount: number;
    phase: number;
    tradeCapable: boolean;
    contentHash: string | null;
    summaryLine: string;
  } | null;
}

export interface BarDTO {
  ts_open: string;
  ts_close: string;
  o: number;
  h: number;
  l: number;
  c: number;
  change: number;
}

export interface TicketDTO {
  ticket_id: string;
  status: string;
  decision_ts: string;
  expiry_ts: string;
  side: string;
  entry: number;
  stop: number;
  target: number;
  lots: number;
  risk_pct: number;
  setup_id: string;
  veto: string;
  rr: number;
  riskMoney: number;
  pnl: number | null;
  exitReason: string | null;
  lifecycle: Array<{ ts: string; kind: string; reason: string | null; channel: string | null }>;
}

export interface DriverDTO {
  id: string;
  tier: number;
  name: string;
  unit: string;
  value: number;
  delta: number;
  stance: "TAILWIND" | "HEADWIND" | "NEUTRAL";
  why: string;
  display: string;
  formatted: string;
  history: number[];
  live?: boolean;
  source?: string;
}

export interface DriverValuesDTO {
  ok: boolean;
  live: Record<string, { value: number; unit: string; source: string; display_k?: number }>;
  unavailable: string[];
}

export interface ReplayDTO {
  ok: boolean;
  date: string;
  bars: Array<{
    decisionTs: string;
    o: number; h: number; l: number; c: number;
    code: string | null;
    story: Array<{ kind: string; code: string | null; detail: string | null }>;
  }>;
  histogram: Record<string, number>;
  tickets: Array<Record<string, unknown>>;
}

const json = (url: string) => fetch(url).then((r) => r.json());

export function useDeskData() {
  const [overview, setOverview] = useState<OverviewDTO | null>(null);
  const [bars, setBars] = useState<BarDTO[]>([]);
  const [tickets, setTickets] = useState<TicketDTO[]>([]);
  const [drivers, setDrivers] = useState<DriverDTO[]>([]);
  const [driverValues, setDriverValues] = useState<DriverValuesDTO | null>(null);
  const [composite, setComposite] = useState(50);
  const [replay, setReplay] = useState<ReplayDTO | null>(null);
  const [dayEvents, setDayEvents] = useState<DeskEventDTO[]>([]);
  const [day, setDay] = useState<string>("");
  const [pickedDay, setPickedDay] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const tickRef = useRef(0);

  // core snapshot — setState only inside async .then callbacks
  useEffect(() => {
    let dead = false;
    json("/api/desk/overview")
      .then((ov) => { if (!dead && ov.ok) setOverview(ov as OverviewDTO); })
      .catch((e) => { if (!dead) setError((e as Error).message); });
    json("/api/desk/bars?limit=1500")
      .then((br) => { if (!dead && br.ok) setBars(br.bars as BarDTO[]); })
      .catch(() => {});
    json("/api/desk/tickets")
      .then((tk) => { if (!dead && tk.ok) setTickets(tk.tickets as TicketDTO[]); })
      .catch(() => {});
    return () => { dead = true; };
  }, []);

  const activeDay =
    pickedDay ?? overview?.featuredDay ?? null;

  // day switch — events + replay together
  useEffect(() => {
    if (!activeDay) return;
    let dead = false;
    json(`/api/desk/events?date=${encodeURIComponent(activeDay)}&limit=1000`)
      .then((ev) => { if (!dead && ev.ok) { setDayEvents(ev.events as DeskEventDTO[]); setDay(ev.date as string); } })
      .catch(() => {});
    json(`/api/desk/replay?date=${encodeURIComponent(activeDay)}`)
      .then((rp) => { if (!dead && rp.ok) setReplay(rp as ReplayDTO); })
      .catch(() => {});
    return () => { dead = true; };
  }, [activeDay]);

  // real driver values — free feeds, refreshed every 5 minutes
  useEffect(() => {
    let dead = false;
    const load = () =>
      fetch("/api/desk/driver-values")
        .then((r) => r.json())
        .then((d: DriverValuesDTO) => { if (!dead && d.ok) setDriverValues(d); })
        .catch(() => {});
    const kick = setTimeout(() => void load(), 0);
    const iv = setInterval(() => void load(), 5 * 60_000);
    return () => { dead = true; clearTimeout(kick); clearInterval(iv); };
  }, []);

  // driver drift — deterministic simulation ticks
  useEffect(() => {
    if (!activeDay) return;
    let dead = false;
    const load = () => {
      tickRef.current += 1;
      return json(`/api/desk/drivers?day=${encodeURIComponent(activeDay)}&tick=${tickRef.current}`)
        .then((d) => { if (!dead && d.ok) { setDrivers(d.drivers as DriverDTO[]); setComposite(d.composite as number); } })
        .catch(() => {});
    };
    void load();
    // 5s polling — satisfies the performance budget (no faster than 5s on
    // any front-end data path). The deterministic-sim tick drift looks fine
    // at this cadence and it dramatically cuts React re-renders vs 4.2s.
    const iv = setInterval(() => void load(), 5_000);
    return () => { dead = true; clearInterval(iv); };
  }, [activeDay]);

  const reload = useCallback(() => {
    json("/api/desk/overview").then((ov) => { if (ov.ok) setOverview(ov as OverviewDTO); }).catch(() => {});
    json("/api/desk/bars?limit=1500").then((br) => { if (br.ok) setBars(br.bars as BarDTO[]); }).catch(() => {});
    json("/api/desk/tickets").then((tk) => { if (tk.ok) setTickets(tk.tickets as TicketDTO[]); }).catch(() => {});
  }, []);

  return {
    overview, bars, tickets, drivers, driverValues, composite, replay, dayEvents,
    day, setDay: setPickedDay, error, reload,
  };
}

export const REASON_COLORS: Record<string, string> = {
  FILL: "#3fb950",
  HUMAN_SKIP: "#8b949e",
  TICKET_EXPIRED: "#d29922",
  TICKET_SENT: "#e8b440",
  NO_SETUP: "#6b7681",
  SESSION: "#4d5761",
  SPREAD: "#d29922",
  NEWS_BLACKOUT: "#f85149",
  NEWS_UNAVAILABLE: "#f85149",
  STALE_DATA: "#f85149",
  OUTLIER_PRICE: "#f85149",
  CONSEC_LOSS: "#f85149",
  OPEN_POSITION: "#39c5cf",
  BUDGET: "#f85149",
  MAX_TRADES: "#d29922",
  GATE_REJECT: "#d29922",
  CONSTITUTION_BLOCKED: "#f85149",
  IGNORED_LATE_RESPONSE: "#d29922",
  LLM_VETO: "#f85149",
};

export function sessionOfHour(hourUtc: number): string {
  if (hourUtc < 7) return "ASIA";
  if (hourUtc < 12) return "LONDON";
  if (hourUtc < 16) return "LDN·NY OVERLAP";
  if (hourUtc < 21) return "NEW YORK";
  return "OFF-SESSION";
}
