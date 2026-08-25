"use client";

import { memo, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { TerminalModal } from "./Modal";
import { REGISTRY } from "./registry";
import { MAX_ALERTS, type PriceAlert } from "./localState";
import { fmtPrice, symbolHref } from "./lib";

/** A half-built alert (palette "alert gc=f" or the drill-down "+ ALERT"
 *  quick-add prefills the form with these). */
export interface AlertDraft {
  symbol?: string;
  above?: number;
  below?: number;
}

const DATE_FMT = new Intl.DateTimeFormat("en-GB", {
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

function condText(a: PriceAlert): string {
  const parts: string[] = [];
  if (a.above != null) parts.push(`ABOVE ${a.above}`);
  if (a.below != null) parts.push(`BELOW ${a.below}`);
  return parts.join("  +  ");
}

/** One alert row: symbol → condition → status → delete. Fired alerts
 *  stay in the list (one-shot semantics) reading "FIRED @ price". */
const AlertRow = memo(function AlertRow({
  a,
  onDelete,
}: {
  a: PriceAlert;
  onDelete: (symbol: string, created: number) => void;
}) {
  const fired = !!a.firedAt;
  return (
    <li className="flex items-center gap-2.5 border-b border-[#141821] px-1 py-[7px] last:border-b-0">
      <Link
        href={symbolHref(a.symbol)}
        className="gdc-data w-[86px] shrink-0 truncate text-[11px] font-semibold text-[#e8ecf4] transition-colors hover:text-[#c8a04b]"
        title={`open ${a.symbol} drill-down`}
      >
        {a.symbol}
      </Link>
      <span className="gdc-data min-w-0 flex-1 truncate text-[10.5px] tabular-nums text-[#aab4bf]">
        {condText(a)}
      </span>
      {fired ? (
        <span
          className="gdc-data shrink-0 rounded-sm border border-[#6fa97a]/40 px-1.5 py-[2px] text-[9px] font-bold uppercase tracking-[0.1em] text-[#6fa97a]"
          title={`fired ${DATE_FMT.format(new Date(a.firedAt!))} UTC`}
        >
          FIRED @ {a.firedPrice != null ? fmtPrice(a.firedPrice, a.symbol) : "—"}
        </span>
      ) : (
        <span className="gdc-data shrink-0 rounded-sm border border-[#c8a04b]/40 px-1.5 py-[2px] text-[9px] font-bold uppercase tracking-[0.1em] text-[#e2c074]">
          ARMED
        </span>
      )}
      <span className="gdc-data hidden w-[88px] shrink-0 text-right text-[9px] tabular-nums text-[#6f7987] sm:inline">
        {DATE_FMT.format(new Date(a.created))}
      </span>
      <button
        onClick={() => onDelete(a.symbol, a.created)}
        className="shrink-0 cursor-pointer rounded-sm px-1.5 py-[2px] text-[10px] text-[#6f7987] transition-colors hover:bg-[#B85C5C]/10 hover:text-[#d98484]"
        aria-label={`Delete ${a.symbol} alert`}
      >
        ✕
      </button>
    </li>
  );
});

/**
 * ALERTS — the price-alert panel (Bloomberg alert analog, piece 5).
 * Alerts persist in localStorage (max 20) and are checked against the
 * /markets board every 30s refresh by the CommandCenter host; a trip
 * fires a bottom-right toast once (one-shot, stays as FIRED @ price).
 * This modal is the manager: list + add form, prefilled by the
 * palette ("alert gc=f") or the drill-down quick-add (+ ALERT).
 *
 * Structure (GAUNTLET-P15 lint fix): the form state lives in an
 * inner component mounted only while the panel is open — it seeds
 * from the draft via lazy initial state (fresh per open) instead of
 * the old reseed-in-effect (react-hooks/set-state-in-effect).
 */
function AlertsManagerImpl({
  open,
  onClose,
  alerts,
  updateAlerts,
  draft,
  boardPrice,
  pushToast,
}: {
  open: boolean;
  onClose: () => void;
  alerts: PriceAlert[];
  updateAlerts: (next: PriceAlert[]) => void;
  draft: AlertDraft;
  boardPrice: (symbol: string) => number | null;
  pushToast: (title: string, body: string) => void;
}) {
  if (!open) return null;
  const armed = alerts.filter((a) => !a.firedAt).length;
  return (
    <TerminalModal
      open
      onClose={onClose}
      title="Price Alerts"
      subtitle={`${armed} armed · ${alerts.length}/${MAX_ALERTS}`}
      label="Price alerts"
      width="max-w-[720px]"
    >
      <AlertsForm
        alerts={alerts}
        updateAlerts={updateAlerts}
        draft={draft}
        boardPrice={boardPrice}
        pushToast={pushToast}
      />
      <p className="mt-2 border-t border-[#1a1f2c] pt-2 text-[9.5px] leading-snug text-[#6f7987]">
        Checked against the live board every 30s. One-shot: a tripped
        alert fires a toast once, then stays listed as FIRED. Persisted
        in this browser only — no accounts, no server.
      </p>
    </TerminalModal>
  );
}

function AlertsForm({
  alerts,
  updateAlerts,
  draft,
  boardPrice,
  pushToast,
}: {
  alerts: PriceAlert[];
  updateAlerts: (next: PriceAlert[]) => void;
  draft: AlertDraft;
  boardPrice: (symbol: string) => number | null;
  pushToast: (title: string, body: string) => void;
}) {
  // lazy initial state: the form mounts fresh on every panel open,
  // so the draft (palette "alert gc=f" / + ALERT quick-add) seeds it
  // without an effect
  const [symbol, setSymbol] = useState(() => draft.symbol ?? "");
  const [above, setAbove] = useState(() =>
    draft.above != null ? String(draft.above) : "",
  );
  const [below, setBelow] = useState(() =>
    draft.below != null ? String(draft.below) : "",
  );

  const full = alerts.length >= MAX_ALERTS;
  const livePrice = useMemo(
    () => boardPrice(symbol.trim().toUpperCase()),
    [symbol, boardPrice],
  );
  const canAdd =
    !full &&
    symbol.trim().length > 0 &&
    (above.trim() !== "" || below.trim() !== "");

  const add = useCallback(() => {
    const sym = symbol.trim().toUpperCase().slice(0, 24);
    if (!sym || full) return;
    const a = Number(above);
    const b = Number(below);
    const alert: PriceAlert = { symbol: sym, created: Date.now() };
    if (above.trim() !== "" && Number.isFinite(a)) alert.above = a;
    if (below.trim() !== "" && Number.isFinite(b)) alert.below = b;
    if (alert.above == null && alert.below == null) return;
    updateAlerts([alert, ...alerts].slice(0, MAX_ALERTS));
    pushToast("ALERT ARMED", `${sym} ${condText(alert)}`);
    setSymbol("");
    setAbove("");
    setBelow("");
  }, [symbol, above, below, full, alerts, updateAlerts, pushToast]);

  const remove = useCallback(
    (sym: string, created: number) => {
      updateAlerts(alerts.filter((a) => !(a.symbol === sym && a.created === created)));
    },
    [alerts, updateAlerts],
  );

  return (
    <>
      {/* add form */}
      <form
        className="mb-3 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          add();
        }}
      >
        <input
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          list="mg-alert-symbols"
          placeholder="symbol (GC=F, BTC-USD…)"
          className="gdc-data w-[150px] rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-2.5 py-1.5 text-[11.5px] text-[#e8ecf4] outline-none transition-colors placeholder:text-[#5a6272] focus:border-[#c8a04b]/50"
          aria-label="Alert symbol"
          maxLength={24}
        />
        <datalist id="mg-alert-symbols">
          {REGISTRY.map((e) => (
            <option key={e.symbol} value={e.symbol}>
              {e.name}
            </option>
          ))}
        </datalist>
        <label className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.14em] text-[#8a93a6]">
          above
          <input
            value={above}
            onChange={(e) => setAbove(e.target.value)}
            inputMode="decimal"
            placeholder="—"
            className="gdc-data w-[92px] rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-2 py-1.5 text-[11.5px] tabular-nums text-[#6fa97a] outline-none transition-colors placeholder:text-[#5a6272] focus:border-[#6fa97a]/50"
            aria-label="Alert above price"
          />
        </label>
        <label className="flex items-center gap-1.5 text-[9px] uppercase tracking-[0.14em] text-[#8a93a6]">
          below
          <input
            value={below}
            onChange={(e) => setBelow(e.target.value)}
            inputMode="decimal"
            placeholder="—"
            className="gdc-data w-[92px] rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-2 py-1.5 text-[11.5px] tabular-nums text-[#d98484] outline-none transition-colors placeholder:text-[#5a6272] focus:border-[#b85c5c]/50"
            aria-label="Alert below price"
          />
        </label>
        <button
          type="submit"
          disabled={!canAdd}
          className="gdc-chip cursor-pointer border-[#c8a04b]/45 px-3.5 py-1.5 text-[10.5px] font-semibold text-[#e2c074] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
        >
          + Arm alert
        </button>
        {livePrice != null && (
          <span className="gdc-data text-[9.5px] tabular-nums text-[#6f7987]">
            board: {fmtPrice(livePrice, symbol.trim().toUpperCase())}
          </span>
        )}
      </form>
      {full && (
        <div className="mb-2 rounded-sm border border-[#d29922]/30 bg-[#d29922]/[0.05] px-3 py-1.5 text-[10.5px] text-[#d29922]">
          alert list full ({MAX_ALERTS}) — delete one to arm another
        </div>
      )}

      {/* list */}
      {alerts.length === 0 ? (
        <div className="px-2 py-6 text-center text-[11px] uppercase tracking-[0.14em] text-[#8a93a6]">
          no alerts — arm one above or from any drill-down (+ ALERT)
        </div>
      ) : (
        <ul className="max-h-[46vh] overflow-y-auto">
          {alerts.map((a) => (
            <AlertRow key={`${a.symbol}-${a.created}`} a={a} onDelete={remove} />
          ))}
        </ul>
      )}
    </>
  );
}

export const AlertsManager = memo(AlertsManagerImpl);
