"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { MarketsBoard } from "./types";
import { CommandPalette } from "./CommandPalette";
import { EcoCalendar } from "./EcoCalendar";
import { NewsSearch } from "./NewsSearch";
import { AlertsManager, type AlertDraft } from "./AlertsManager";
import { MonitorsManager } from "./MonitorsManager";
import {
  alertTripped,
  alertsStore,
  monitorsStore,
  useAlerts,
  useMonitors,
  type MonitorList,
  type PriceAlert,
} from "./localState";
import { fmtPrice } from "./lib";

/* ------------------------------------------------------------------ api */

export interface CommandApi {
  openPalette: (prefill?: string) => void;
  openEco: () => void;
  openNews: (q: string) => void;
  openAlerts: (draft?: AlertDraft | string) => void;
  openMonitors: () => void;
  alerts: PriceAlert[];
  monitors: MonitorList[];
  updateAlerts: (next: PriceAlert[]) => void;
  updateMonitors: (next: MonitorList[]) => void;
  armedCount: number;
  pushToast: (title: string, body: string) => void;
  boardPrice: (symbol: string) => number | null;
}

const CommandCtx = createContext<CommandApi | null>(null);

/** Access the command layer (palette + ECO/news/alerts/mon modals +
 *  toasts) from any component inside <CommandCenter>. */
export function useCommands(): CommandApi {
  const ctx = useContext(CommandCtx);
  if (!ctx) throw new Error("useCommands requires <CommandCenter>");
  return ctx;
}

/* ---------------------------------------------------------------- toast */

interface Toast {
  id: number;
  title: string;
  body: string;
  leaving?: boolean;
}

const TOAST_MS = 8_000; // auto-dismiss (brief: 8s)

const NO_TRIPS: Array<{ a: PriceAlert; price: number }> = []; // stable empty

/**
 * Toast stack — bottom-right, gold border, solid bg (no blur), CSS
 * transition opacity for the dismiss (no animation loops).
 */
function ToastStack({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="pointer-events-none fixed bottom-4 right-4 z-[110] flex w-[320px] flex-col gap-2">
      {toasts.map((t) => (
        <div
          key={t.id}
          className="pointer-events-auto rounded-sm border border-[#c8a04b]/60 bg-[#0f1219] px-3.5 py-2.5 shadow-lg transition-opacity duration-300"
          style={{ opacity: t.leaving ? 0 : 1 }}
          role="status"
        >
          <div className="gdc-data text-[10px] font-bold uppercase tracking-[0.16em] text-[#e2c074]">
            {t.title}
          </div>
          <div className="mt-1 text-[11px] leading-snug text-[#c6cedb]">
            {t.body}
          </div>
        </div>
      ))}
    </div>
  );
}

/* --------------------------------------------------------------- center */

/**
 * COMMAND CENTER — the host for the Bloomberg function layer
 * (GAUNTLET-P13): wraps the page, owns the palette + all four modals
 * + the toast stack, hydrates alerts/monitors from localStorage, and
 * runs the one-shot alert checks against every board refresh (the
 * page already polls /api/desk/markets every 30s — this consumes that
 * data, no extra network).
 *
 * GAUNTLET-P15 lint fix: alerts/monitors ride localStorage-backed
 * external stores (useSyncExternalStore in localState.ts) instead of
 * useState + hydrate-in-effect, and the one-shot trip marking is a
 * pure derivation (which armed alerts does the latest board cross)
 * whose EFFECT only performs side effects — store write + toasts, no
 * setState cascade.
 */
export function CommandCenter({
  board,
  children,
}: {
  board: MarketsBoard | null;
  children: React.ReactNode;
}) {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [prefill, setPrefill] = useState("");
  const [ecoOpen, setEcoOpen] = useState(false);
  const [newsOpen, setNewsOpen] = useState(false);
  const [newsQuery, setNewsQuery] = useState("");
  const [alertsOpen, setAlertsOpen] = useState(false);
  const [alertDraft, setAlertDraft] = useState<AlertDraft>({});
  const [monitorsOpen, setMonitorsOpen] = useState(false);
  // persisted localStorage state — SSR/hydration-safe external stores
  const alerts = useAlerts();
  const monitors = useMonitors();
  const [toasts, setToasts] = useState<Toast[]>([]);
  const toastId = useRef(0);

  /* ---------------------------------------------------------- toasts */

  const pushToast = useCallback((title: string, body: string) => {
    const id = ++toastId.current;
    setToasts((ts) => [...ts.slice(-4), { id, title, body }]);
    // two-stage dismiss: fade out, then unmount (transition, no loop)
    setTimeout(() => {
      setToasts((ts) => ts.map((t) => (t.id === id ? { ...t, leaving: true } : t)));
    }, TOAST_MS - 300);
    setTimeout(() => {
      setToasts((ts) => ts.filter((t) => t.id !== id));
    }, TOAST_MS);
  }, []);

  /* ------------------------------------------------------- open/close */

  const openPalette = useCallback((p = "") => {
    setPrefill(p);
    setPaletteOpen(true);
  }, []);
  const openEco = useCallback(() => setEcoOpen(true), []);
  const openNews = useCallback((q: string) => {
    setNewsQuery(q);
    setNewsOpen(true);
  }, []);
  const openAlerts = useCallback((draft?: AlertDraft | string) => {
    setAlertDraft(
      typeof draft === "string" ? { symbol: draft.toUpperCase() } : draft ?? {},
    );
    setAlertsOpen(true);
  }, []);
  const openMonitors = useCallback(() => setMonitorsOpen(true), []);

  /* -------------------------------------------------- persisted state */

  const updateAlerts = useCallback((next: PriceAlert[]) => {
    alertsStore.set(next);
  }, []);
  const updateMonitors = useCallback((next: MonitorList[]) => {
    monitorsStore.set(next);
  }, []);

  /* -------------------------------------------------- board lookups */

  const priceIndex = useMemo(() => {
    const map = new Map<string, number>();
    for (const sec of board?.sectors ?? []) {
      for (const r of sec.rows ?? []) {
        if (typeof r.price === "number" && Number.isFinite(r.price)) {
          map.set(r.symbol, r.price);
        }
      }
    }
    return map;
  }, [board]);

  const boardPrice = useCallback(
    (symbol: string) => priceIndex.get(symbol.trim().toUpperCase()) ?? null,
    [priceIndex],
  );

  /* -------------------------------------------------- alert checking */

  // one-shot trips, DERIVED: which armed alerts does the latest board
  // cross right now (pure — recomputed when the board or alerts move)
  const tripped = useMemo(() => {
    if (!board) return NO_TRIPS;
    const out: Array<{ a: PriceAlert; price: number }> = [];
    for (const a of alerts) {
      if (a.firedAt) continue;
      const price = priceIndex.get(a.symbol);
      if (typeof price === "number" && alertTripped(a, price)) {
        out.push({ a, price });
      }
    }
    return out;
  }, [board, alerts, priceIndex]);

  // side effects only: mark the trips in the store (persists +
  // notifies the useSyncExternalStore subscribers) and toast each
  // fired alert once — a marked alert leaves `tripped`, so this
  // effect settles instead of cascading
  useEffect(() => {
    if (tripped.length === 0) return;
    alertsStore.set(
      alerts.map((a) => {
        const hit = tripped.find((t) => t.a === a);
        return hit
          ? { ...a, firedAt: Date.now(), firedPrice: hit.price }
          : a;
      }),
    );
    for (const { a, price } of tripped) {
      const side = a.below != null && price <= a.below ? "below" : "above";
      const level = side === "above" ? a.above : a.below;
      pushToast(
        "ALERT FIRED",
        `${a.symbol} traded ${side} ${fmtPrice(level, a.symbol)} — ` +
          `now ${fmtPrice(price, a.symbol)}`,
      );
    }
  }, [tripped, alerts, pushToast]);

  /* ------------------------------------------------------- keyboard */

  // ⌘K / Ctrl+K toggles the palette; "/" opens it when not typing.
  // Skipped while any modal is open (those own their Escape keys).
  const anyModal =
    ecoOpen || newsOpen || alertsOpen || monitorsOpen || paletteOpen;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((o) => {
          if (!o) setPrefill("");
          return !o;
        });
        return;
      }
      if (e.key === "/" && !anyModal) {
        const t = e.target as HTMLElement | null;
        const typing =
          !!t &&
          (t.tagName === "INPUT" ||
            t.tagName === "TEXTAREA" ||
            t.tagName === "SELECT" ||
            t.isContentEditable);
        if (!typing) {
          e.preventDefault();
          setPrefill("");
          setPaletteOpen(true);
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [anyModal]);

  const api = useMemo<CommandApi>(
    () => ({
      openPalette,
      openEco,
      openNews,
      openAlerts,
      openMonitors,
      alerts,
      monitors,
      updateAlerts,
      updateMonitors,
      armedCount: alerts.filter((a) => !a.firedAt).length,
      pushToast,
      boardPrice,
    }),
    [
      openPalette,
      openEco,
      openNews,
      openAlerts,
      openMonitors,
      alerts,
      monitors,
      updateAlerts,
      updateMonitors,
      pushToast,
      boardPrice,
    ],
  );

  return (
    <CommandCtx.Provider value={api}>
      {children}
      <CommandPalette
        open={paletteOpen}
        prefill={prefill}
        onClose={() => setPaletteOpen(false)}
        openEco={openEco}
        openNews={openNews}
        openAlerts={(sym) => openAlerts(sym ? { symbol: sym } : undefined)}
        openMonitors={openMonitors}
      />
      <EcoCalendar open={ecoOpen} onClose={() => setEcoOpen(false)} />
      <NewsSearch
        open={newsOpen}
        onClose={() => setNewsOpen(false)}
        initialQuery={newsQuery}
      />
      <AlertsManager
        open={alertsOpen}
        onClose={() => setAlertsOpen(false)}
        alerts={alerts}
        updateAlerts={updateAlerts}
        draft={alertDraft}
        boardPrice={boardPrice}
        pushToast={pushToast}
      />
      <MonitorsManager
        open={monitorsOpen}
        onClose={() => setMonitorsOpen(false)}
        monitors={monitors}
        updateMonitors={updateMonitors}
      />
      <ToastStack toasts={toasts} />
    </CommandCtx.Provider>
  );
}
