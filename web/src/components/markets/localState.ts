"use client";

/** MARKET GAUNTLET browser-side persisted state (GAUNTLET-P13):
 *  price alerts + monitor lists in localStorage — no accounts, no
 *  server, no infra. All writes are defensive (JSON parse guards,
 *  shape checks, cap enforcement) so a corrupted or hand-edited key
 *  degrades to the seed, never a crash.
 *
 * GAUNTLET-P15: alerts/monitors are exposed as tiny localStorage-backed
 *  EXTERNAL STORES (useSyncExternalStore) — SSR renders the stable
 *  empty snapshot, the client hydrates from localStorage without the
 *  old setState-in-effect cascade, and every write persists + notifies
 *  subscribers. */

import { useSyncExternalStore } from "react";

/* ------------------------------------------------------------------ alerts */

export interface PriceAlert {
  symbol: string;
  above?: number;
  below?: number;
  created: number; // epoch ms
  firedAt?: number; // set once, one-shot
  firedPrice?: number;
}

const ALERTS_KEY = "mg.alerts";
export const MAX_ALERTS = 20;

function parseAlerts(raw: string): PriceAlert[] {
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed)) return [];
  const out: PriceAlert[] = [];
  for (const a of parsed) {
    if (!a || typeof a.symbol !== "string" || !a.symbol) continue;
    const alert: PriceAlert = {
      symbol: a.symbol.slice(0, 24),
      created: typeof a.created === "number" ? a.created : Date.now(),
    };
    if (typeof a.above === "number" && Number.isFinite(a.above))
      alert.above = a.above;
    if (typeof a.below === "number" && Number.isFinite(a.below))
      alert.below = a.below;
    if (typeof a.firedAt === "number") alert.firedAt = a.firedAt;
    if (typeof a.firedPrice === "number")
      alert.firedPrice = a.firedPrice;
    if (alert.above != null || alert.below != null) out.push(alert);
  }
  return out.slice(0, MAX_ALERTS);
}

/** Check one alert against a price. Returns the trip side or null. */
export function alertTripped(
  a: PriceAlert,
  price: number,
): "above" | "below" | null {
  if (a.firedAt) return null; // one-shot: already fired
  if (a.above != null && price >= a.above) return "above";
  if (a.below != null && price <= a.below) return "below";
  return null;
}

/* -------------------------------------------------------- external stores */

type Listener = () => void;

/** Tiny localStorage-backed external store (the useSyncExternalStore
 *  pattern): the snapshot is parsed once on the first client read and
 *  cached in memory, every write persists + notifies subscribers, and
 *  getServerSnapshot returns the stable module-level EMPTY so SSR and
 *  the client hydration pass never mismatch. Replaces the old
 *  hydrate-with-setState-in-effect. */
function makeLocalStore<T>(
  key: string,
  empty: T,
  parse: (raw: string) => T,
  serialize: (value: T) => string,
) {
  let snapshot = empty;
  let loaded = false;
  let listeners: Listener[] = [];
  const read = (): T => {
    if (!loaded && typeof window !== "undefined") {
      loaded = true;
      try {
        const raw = window.localStorage.getItem(key);
        if (raw) snapshot = parse(raw);
      } catch {
        /* corrupted key — keep the seed */
      }
    }
    return snapshot;
  };
  return {
    subscribe(cb: Listener): () => void {
      listeners.push(cb);
      return () => {
        listeners = listeners.filter((l) => l !== cb);
      };
    },
    getSnapshot: read,
    getServerSnapshot: (): T => empty,
    set(next: T): void {
      snapshot = next;
      loaded = true;
      try {
        if (typeof window !== "undefined") {
          window.localStorage.setItem(key, serialize(next));
        }
      } catch {
        /* private mode / quota — values live in memory for this tab */
      }
      for (const l of listeners) l();
    },
  };
}

const EMPTY_ALERTS: PriceAlert[] = [];

/** The alerts store: read via useAlerts(), write via .set(next). */
export const alertsStore = makeLocalStore<PriceAlert[]>(
  ALERTS_KEY,
  EMPTY_ALERTS,
  parseAlerts,
  (a) => JSON.stringify(a.slice(0, MAX_ALERTS)),
);

export function useAlerts(): PriceAlert[] {
  return useSyncExternalStore(
    alertsStore.subscribe,
    alertsStore.getSnapshot,
    alertsStore.getServerSnapshot,
  );
}

/* ---------------------------------------------------------------- monitors */

export interface MonitorList {
  id: string;
  name: string;
  symbols: string[];
}

const MONITORS_KEY = "mg.monitors";
const ACTIVE_KEY = "mg.monitors.active";
export const MAX_LISTS = 5;
export const MAX_LIST_SYMBOLS = 30;

export const DEFAULT_MONITOR: MonitorList = {
  id: "mw",
  name: "MY WATCH",
  symbols: ["BTC-USD", "GC=F", "^NSEI", "EURUSD=X", "SPY"],
};

function parseMonitors(raw: string): MonitorList[] {
  const parsed = JSON.parse(raw);
  if (!Array.isArray(parsed) || parsed.length === 0)
    return [DEFAULT_MONITOR];
  const out: MonitorList[] = [];
  for (const m of parsed) {
    if (!m || typeof m.name !== "string" || !m.name) continue;
    if (!Array.isArray(m.symbols)) continue;
    const symbols = m.symbols
      .filter((s: unknown) => typeof s === "string" && s)
      .map((s: string) => s.slice(0, 24))
      .slice(0, MAX_LIST_SYMBOLS);
    out.push({
      id: typeof m.id === "string" ? m.id : `m${out.length}`,
      name: m.name.slice(0, 24),
      symbols,
    });
    if (out.length >= MAX_LISTS) break;
  }
  return out.length > 0 ? out : [DEFAULT_MONITOR];
}

const EMPTY_MONITORS: MonitorList[] = [DEFAULT_MONITOR];

/** The monitors store: read via useMonitors(), write via .set(next). */
export const monitorsStore = makeLocalStore<MonitorList[]>(
  MONITORS_KEY,
  EMPTY_MONITORS,
  parseMonitors,
  (m) => JSON.stringify(m.slice(0, MAX_LISTS)),
);

export function useMonitors(): MonitorList[] {
  return useSyncExternalStore(
    monitorsStore.subscribe,
    monitorsStore.getSnapshot,
    monitorsStore.getServerSnapshot,
  );
}

export function readActiveMonitor(lists: MonitorList[]): string {
  if (typeof window === "undefined") return lists[0]?.id ?? "";
  try {
    const id = window.localStorage.getItem(ACTIVE_KEY) || "";
    if (lists.some((l) => l.id === id)) return id;
  } catch {
    /* fall through */
  }
  return lists[0]?.id ?? "";
}

export function writeActiveMonitor(id: string): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ACTIVE_KEY, id);
  } catch {
    /* non-fatal */
  }
}
