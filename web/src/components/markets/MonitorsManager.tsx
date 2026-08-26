"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { TerminalModal } from "./Modal";
import { Sparkline } from "./Sparkline";
import { REGISTRY } from "./registry";
import {
  MAX_LISTS,
  MAX_LIST_SYMBOLS,
  readActiveMonitor,
  writeActiveMonitor,
  type MonitorList,
} from "./localState";
import { chgColor, fmtPct, fmtPrice, symbolHref } from "./lib";
import type { MarketsBoard } from "./types";

/* --------------------------------------------------------- board lookup */

/** symbol → board row (price/chg/sparkline), or null when the symbol
 *  isn't served by the board (ad-hoc monitor entries show "—"). */
function useBoardIndex(board: MarketsBoard | null) {
  return useMemo(() => {
    const map = new Map<
      string,
      { price: number; changePct: number; points: number[]; name: string }
    >();
    for (const sec of board?.sectors ?? []) {
      for (const r of sec.rows ?? []) {
        if (typeof r.price === "number" && Number.isFinite(r.price)) {
          map.set(r.symbol, {
            price: r.price,
            changePct: typeof r.change_pct === "number" ? r.change_pct : 0,
            points: Array.isArray(r.points) ? r.points : [],
            name: r.name,
          });
        }
      }
    }
    return map;
  }, [board]);
}

/* ------------------------------------------------------------ the strip */

/**
 * MON — the MONITORS strip (Bloomberg monitor-list analog, piece 5):
 * one tab per localStorage watchlist, the active list rendering as a
 * compact quote table (symbol · price · chg% · sparkline) fed by the
 * same board the /markets surface already polls. Symbols the board
 * doesn't serve show "—". Rows link to the drill-down; ✕ removes.
 */
function MonitorsStripImpl({
  board,
  monitors,
  updateMonitors,
  onOpenManager,
}: {
  board: MarketsBoard | null;
  monitors: MonitorList[];
  updateMonitors: (next: MonitorList[]) => void;
  onOpenManager: () => void;
}) {
  const [activeId, setActiveId] = useState<string>("");

  // active tab persisted in localStorage (client-only read)
  useEffect(() => {
    setActiveId(readActiveMonitor(monitors));
    // re-validate if the active list disappears
  }, [monitors]);

  const active = monitors.find((m) => m.id === activeId) ?? monitors[0];
  const index = useBoardIndex(board);

  const setActive = useCallback((id: string) => {
    setActiveId(id);
    writeActiveMonitor(id);
  }, []);

  const removeSymbol = useCallback(
    (sym: string) => {
      if (!active) return;
      updateMonitors(
        monitors.map((m) =>
          m.id === active.id
            ? { ...m, symbols: m.symbols.filter((s) => s !== sym) }
            : m,
        ),
      );
    },
    [monitors, active, updateMonitors],
  );

  if (!active) return null;

  return (
    <section className="gdc-panel px-3.5 pb-3 pt-3" aria-label="Monitor lists">
      <div className="mb-2 flex flex-wrap items-center gap-x-2 gap-y-1.5">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          Monitors — MON
        </h2>
        {/* tabs: one per list */}
        <div className="flex flex-wrap items-center gap-1.5">
          {monitors.map((m) => (
            <button
              key={m.id}
              onClick={() => setActive(m.id)}
              className={`gdc-data cursor-pointer rounded-sm border px-2 py-[3px] text-[9.5px] font-semibold uppercase tracking-[0.1em] transition-colors ${
                m.id === active.id
                  ? "border-[#c8a04b]/55 bg-[#c8a04b]/[0.1] text-[#e2c074]"
                  : "border-[#1a1f2c] text-[#8a93a6] hover:text-[#c6cedb]"
              }`}
              aria-pressed={m.id === active.id}
              title={`${m.name}: ${m.symbols.length} symbols`}
            >
              {m.name}
              <span className="ml-1 opacity-60">{m.symbols.length}</span>
            </button>
          ))}
        </div>
        <span className="h-px min-w-4 flex-1 bg-[#1a1f2c]" />
        <button
          onClick={onOpenManager}
          className="gdc-chip cursor-pointer border-[#c8a04b]/35 text-[9.5px] text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
          aria-label="Open the monitor list manager"
        >
          ⚙ MANAGE
        </button>
      </div>

      {/* compact quote table for the active list */}
      <div className="grid grid-cols-1 gap-x-6 lg:grid-cols-2">
        {active.symbols.map((sym) => {
          const row = index.get(sym);
          return (
            <div
              key={sym}
              className="group flex items-center gap-2.5 rounded-sm border-b border-[#141821] px-1 py-[5px] last:border-b-0"
            >
              <Link
                href={symbolHref(sym)}
                className="gdc-data w-[86px] shrink-0 truncate text-[11px] font-semibold text-[#e8ecf4] transition-colors hover:text-[#c8a04b]"
                title={row ? `${sym} · ${row.name}` : sym}
              >
                {sym}
              </Link>
              <span className="gdc-data w-[86px] shrink-0 truncate text-[9px] text-[#8a93a6]">
                {row ? row.name : "—"}
              </span>
              <span className="gdc-data w-[74px] shrink-0 text-right text-[10.5px] tabular-nums text-[#aab4bf]">
                {row ? fmtPrice(row.price, sym) : "—"}
              </span>
              <span
                className="gdc-data w-[54px] shrink-0 text-right text-[10.5px] font-semibold tabular-nums"
                style={{ color: row ? chgColor(row.changePct) : "#5a6272" }}
              >
                {row ? fmtPct(row.changePct) : "—"}
              </span>
              <span className="hidden min-w-0 flex-1 sm:block">
                {row ? (
                  <Sparkline points={row.points} color={chgColor(row.changePct)} height={18} />
                ) : (
                  <svg viewBox="0 0 100 28" height={18} width="100%" aria-hidden />
                )}
              </span>
              <button
                onClick={() => removeSymbol(sym)}
                className="shrink-0 cursor-pointer rounded-sm px-1 py-[1px] text-[9px] text-transparent transition-colors hover:bg-[#B85C5C]/10 hover:text-[#d98484] group-hover:text-[#6f7987]"
                aria-label={`Remove ${sym} from ${active.name}`}
                title={`remove ${sym}`}
              >
                ✕
              </button>
            </div>
          );
        })}
        {active.symbols.length === 0 && (
          <div className="py-3 text-[10px] uppercase tracking-[0.14em] text-[#8a93a6]">
            empty list — add symbols via ⚙ MANAGE or "+ MONITOR" on any
            drill-down
          </div>
        )}
      </div>
    </section>
  );
}

export const MonitorsStrip = memo(MonitorsStripImpl);

/* ------------------------------------------------------- manager modal */

/**
 * MON manager modal — create/rename/delete lists (max 5 × 30 symbols,
 * localStorage-persisted). Seeded with the default "MY WATCH" list.
 */
function MonitorsManagerImpl({
  open,
  onClose,
  monitors,
  updateMonitors,
}: {
  open: boolean;
  onClose: () => void;
  monitors: MonitorList[];
  updateMonitors: (next: MonitorList[]) => void;
}) {
  const [newName, setNewName] = useState("");
  const [addSym, setAddSym] = useState<Record<string, string>>({});

  const listsFull = monitors.length >= MAX_LISTS;

  const rename = useCallback(
    (id: string, name: string) => {
      updateMonitors(
        monitors.map((m) => (m.id === id ? { ...m, name: name.slice(0, 24) } : m)),
      );
    },
    [monitors, updateMonitors],
  );

  const addList = useCallback(() => {
    const name = newName.trim().toUpperCase().slice(0, 24);
    if (!name || listsFull) return;
    const id = `m${Date.now().toString(36)}`;
    updateMonitors([...monitors, { id, name, symbols: [] }]);
    setNewName("");
  }, [newName, listsFull, monitors, updateMonitors]);

  const deleteList = useCallback(
    (id: string) => {
      // keep at least one list
      if (monitors.length <= 1) return;
      updateMonitors(monitors.filter((m) => m.id !== id));
    },
    [monitors, updateMonitors],
  );

  const removeSymbol = useCallback(
    (id: string, sym: string) => {
      updateMonitors(
        monitors.map((m) =>
          m.id === id ? { ...m, symbols: m.symbols.filter((s) => s !== sym) } : m,
        ),
      );
    },
    [monitors, updateMonitors],
  );

  const addSymbol = useCallback(
    (id: string) => {
      const raw = (addSym[id] ?? "").trim().toUpperCase().slice(0, 24);
      if (!raw) return;
      updateMonitors(
        monitors.map((m) => {
          if (m.id !== id) return m;
          if (m.symbols.includes(raw) || m.symbols.length >= MAX_LIST_SYMBOLS)
            return m;
          return { ...m, symbols: [...m.symbols, raw] };
        }),
      );
      setAddSym((s) => ({ ...s, [id]: "" }));
    },
    [addSym, monitors, updateMonitors],
  );

  return (
    <TerminalModal
      open={open}
      onClose={onClose}
      title="Monitor Lists — MON"
      subtitle={`${monitors.length}/${MAX_LISTS} lists · browser-persisted`}
      label="Monitor list manager"
      width="max-w-[720px]"
    >
      <div className="flex flex-col gap-3">
        {monitors.map((m) => (
          <div key={m.id} className="rounded-sm border border-[#1a1f2c] px-3 py-2.5">
            <div className="mb-2 flex flex-wrap items-center gap-2">
              <input
                value={m.name}
                onChange={(e) => rename(m.id, e.target.value)}
                className="gdc-data w-[150px] rounded-sm border border-transparent bg-transparent px-1.5 py-[3px] text-[11px] font-semibold uppercase tracking-[0.1em] text-[#e2c074] outline-none transition-colors hover:border-[#1a1f2c] focus:border-[#c8a04b]/50"
                aria-label={`Rename list ${m.name}`}
                maxLength={24}
              />
              <span className="gdc-data text-[9px] tabular-nums text-[#6f7987]">
                {m.symbols.length}/{MAX_LIST_SYMBOLS}
              </span>
              <span className="h-px flex-1 bg-[#141821]" />
              {monitors.length > 1 && (
                <button
                  onClick={() => deleteList(m.id)}
                  className="gdc-chip cursor-pointer px-2 py-0.5 text-[9.5px] text-[#6f7987] transition-colors hover:text-[#d98484]"
                  aria-label={`Delete list ${m.name}`}
                >
                  DELETE LIST
                </button>
              )}
            </div>
            <div className="mb-2 flex flex-wrap gap-1.5">
              {m.symbols.map((s) => (
                <span
                  key={s}
                  className="gdc-data flex items-center gap-1.5 rounded-sm border border-[#1a1f2c] px-1.5 py-[2px] text-[10px] text-[#c6cedb]"
                >
                  {s}
                  <button
                    onClick={() => removeSymbol(m.id, s)}
                    className="cursor-pointer text-[9px] text-[#6f7987] transition-colors hover:text-[#d98484]"
                    aria-label={`Remove ${s} from ${m.name}`}
                  >
                    ✕
                  </button>
                </span>
              ))}
              {m.symbols.length === 0 && (
                <span className="text-[9.5px] text-[#6f7987]">empty</span>
              )}
            </div>
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                addSymbol(m.id);
              }}
            >
              <input
                value={addSym[m.id] ?? ""}
                onChange={(e) => setAddSym((s) => ({ ...s, [m.id]: e.target.value }))}
                list="mg-monitor-symbols"
                placeholder="add symbol (BTC-USD, ^NSEI, GC=F…)"
                className="gdc-data w-[220px] rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-2 py-1 text-[10.5px] text-[#e8ecf4] outline-none transition-colors placeholder:text-[#5a6272] focus:border-[#c8a04b]/50"
                aria-label={`Add symbol to ${m.name}`}
                maxLength={24}
              />
              <datalist id="mg-monitor-symbols">
                {REGISTRY.map((e) => (
                  <option key={e.symbol} value={e.symbol}>
                    {e.name}
                  </option>
                ))}
              </datalist>
              <button
                type="submit"
                disabled={m.symbols.length >= MAX_LIST_SYMBOLS}
                className="gdc-chip cursor-pointer border-[#c8a04b]/35 text-[9.5px] text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
              >
                + ADD
              </button>
            </form>
          </div>
        ))}

        {/* new list */}
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            addList();
          }}
        >
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder={listsFull ? "list cap reached (5)" : "new list name…"}
            disabled={listsFull}
            className="gdc-data w-[220px] rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-2 py-1.5 text-[11px] uppercase tracking-[0.08em] text-[#e8ecf4] outline-none transition-colors placeholder:normal-case placeholder:tracking-normal placeholder:text-[#5a6272] focus:border-[#c8a04b]/50 disabled:opacity-50"
            aria-label="New monitor list name"
            maxLength={24}
          />
          <button
            type="submit"
            disabled={listsFull || !newName.trim()}
            className="gdc-chip cursor-pointer border-[#c8a04b]/45 px-3 py-1.5 text-[10.5px] font-semibold text-[#e2c074] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-not-allowed disabled:opacity-50"
          >
            + NEW LIST
          </button>
          <span className="text-[9px] text-[#6f7987]">
            max {MAX_LISTS} lists × {MAX_LIST_SYMBOLS} symbols
          </span>
        </form>
      </div>
    </TerminalModal>
  );
}

export const MonitorsManager = memo(MonitorsManagerImpl);
