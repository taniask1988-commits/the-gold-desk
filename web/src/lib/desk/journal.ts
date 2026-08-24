import { existsSync, promises as fs } from "fs";
import path from "path";

// Data root resolution (portable):
//   1. GOLD_DESK_DATA env var
//   2. ../data relative to the web app cwd (repo layout: web/ + harness data/)
//   3. sandbox fallback
function resolveDataRoot(): string {
  const candidates = [
    process.env.GOLD_DESK_DATA,
    path.join(process.cwd(), "..", "data"),
    "/home/z/my-project/download/gold_desk_v1/data",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    try {
      if (existsSync(path.join(c, "events"))) return c;
    } catch {
      /* next candidate */
    }
  }
  return candidates[candidates.length - 1];
}

export const DATA_ROOT = resolveDataRoot();

export interface DeskEvent {
  ts: string;
  event_id: string;
  kind: string;
  decision_ts: string | null;
  symbol: string;
  constitution_hash: string;
  setup_spec_hash?: string | null;
  prompt_hash?: string | null;
  model_id?: string | null;
  data_hash?: string | null;
  reason_code?: string | null;
  payload: Record<string, unknown> & { demo?: boolean; bar?: BarPayload };
}

export interface BarPayload {
  ts_open: string;
  ts_close: string;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface Ticket {
  ticket_id: string;
  status: string;
  decision_ts: string;
  expiry_ts: string;
  symbol: string;
  side: string;
  entry_type: string;
  entry: number;
  stop: number;
  target: number;
  lots: number;
  risk_pct: number;
  risk_money?: number;          // H2: persisted at gate time; readers use this
  time_stop_ts: string;
  invalidation: string;
  setup_id: string;
  setup_version: string;
  veto: string;
  veto_reason: string;
  spread_at_gate: number;
  constitution_hash: string;
  spec_hash: string;
  content_key: string;
}

export interface PaperAccount {
  balance: number;
  equity: number;
  day_key: string;
  daily_pnl: number;
  trades_today: number;
  consecutive_losses: number;
  high_water: number;
  closed_trades: Array<{
    ticket_id: string;
    exit: number;
    reason: string;
    pnl: number;
    closed_ts: string;
  }>;
}

export async function listDays(): Promise<string[]> {
  try {
    const files = await fs.readdir(path.join(DATA_ROOT, "events"));
    return files
      .filter((f) => f.endsWith(".jsonl"))
      .map((f) => f.replace(".jsonl", ""))
      .sort();
  } catch {
    return [];
  }
}

export async function readEvents(date?: string | null): Promise<DeskEvent[]> {
  const days = await listDays();
  if (!days.length) return [];
  const targets = date ? days.filter((d) => d === date) : days;
  const out: DeskEvent[] = [];
  for (const d of targets) {
    try {
      const text = await fs.readFile(
        path.join(DATA_ROOT, "events", `${d}.jsonl`),
        "utf-8",
      );
      for (const line of text.split("\n")) {
        const t = line.trim();
        if (!t) continue;
        try {
          out.push(JSON.parse(t) as DeskEvent);
        } catch {
          /* skip corrupt line */
        }
      }
    } catch {
      /* missing day file */
    }
  }
  return out;
}

export async function readAllEvents(): Promise<DeskEvent[]> {
  return readEvents(null);
}

export async function readTickets(): Promise<Ticket[]> {
  try {
    const files = await fs.readdir(path.join(DATA_ROOT, "tickets"));
    const out: Ticket[] = [];
    for (const f of files.filter((x) => x.endsWith(".json"))) {
      try {
        const t = JSON.parse(
          await fs.readFile(path.join(DATA_ROOT, "tickets", f), "utf-8"),
        ) as Ticket;
        out.push(t);
      } catch {
        /* skip */
      }
    }
    return out.sort((a, b) => a.decision_ts.localeCompare(b.decision_ts));
  } catch {
    return [];
  }
}

export async function readAccount(): Promise<PaperAccount | null> {
  try {
    return JSON.parse(
      await fs.readFile(path.join(DATA_ROOT, "account.json"), "utf-8"),
    ) as PaperAccount;
  } catch {
    return null;
  }
}

export function reasonHistogram(events: DeskEvent[]): Record<string, number> {
  const hist: Record<string, number> = {};
  for (const e of events) {
    const rc = (e as { reason_code?: string | null }).reason_code;
    if (rc && rc !== "__OPEN__") hist[rc] = (hist[rc] || 0) + 1;
  }
  return Object.fromEntries(
    Object.entries(hist).sort((a, b) => b[1] - a[1]),
  );
}

export function barsFromEvents(events: DeskEvent[]): BarPayload[] {
  const bars: BarPayload[] = [];
  for (const e of events) {
    if (e.kind === "BarReceived" && e.payload?.bar) bars.push(e.payload.bar);
  }
  return bars;
}
