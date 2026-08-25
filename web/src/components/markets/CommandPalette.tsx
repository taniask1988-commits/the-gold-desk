"use client";

import { memo, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { REGISTRY, registryNormalize, SECTOR_LABELS } from "./registry";
import { symbolHref } from "./lib";

/* --------------------------------------------------------- fuzzy match */

/** Same tiers as the python scorer: exact > prefix > substring >
 *  subsequence (4+ chars). 0 = no match. */
function fuzzyScore(query: string, text: string): number {
  const q = query.toLowerCase().trim();
  const t = text.toLowerCase();
  if (!q || !t) return 0;
  if (q === t) return 1000;
  if (t.startsWith(q)) return 800 - (t.length - q.length);
  if (t.includes(q)) return 600 - (t.length - q.length);
  if (q.length >= 4) {
    let i = 0;
    for (const ch of t) {
      if (i < q.length && ch === q[i]) i++;
    }
    if (i === q.length) return 300 - (t.length - q.length);
  }
  return 0;
}

/* ------------------------------------------------------------- items */

interface PaletteItem {
  id: string;
  kind: "command" | "symbol" | "search";
  label: string;
  hint?: string;
  score: number;
  run: () => void;
}

/**
 * COMMAND ENTRY — the command palette (Bloomberg <GO> analog, piece
 * 5). ⌘K / Ctrl+K / "/" opens it anywhere on the markets pages; type
 * a mnemonic (eco, mon, news bitcoin, alert gc=f, run desk btc), a
 * symbol (btc → /markets/BTC-USD), or free text (any query becomes a
 * drill-down passthrough). Arrow keys + Enter; mouse works too.
 *
 * Structure (GAUNTLET-P15 lint fix): the outer component is a thin
 * gate that mounts a fresh PaletteBody per open — the query/selection
 * state initializes from the prefill via lazy initial state instead
 * of the old seed-in-effect (react-hooks/set-state-in-effect), and
 * the selection resets in the input's onChange handler rather than a
 * q-keyed effect.
 */
function CommandPaletteImpl({
  open,
  prefill,
  onClose,
  openEco,
  openNews,
  openAlerts,
  openMonitors,
}: {
  open: boolean;
  prefill: string;
  onClose: () => void;
  openEco: () => void;
  openNews: (q: string) => void;
  openAlerts: (symbol?: string) => void;
  openMonitors: () => void;
}) {
  if (!open) return null;
  return (
    <PaletteBody
      prefill={prefill}
      onClose={onClose}
      openEco={openEco}
      openNews={openNews}
      openAlerts={openAlerts}
      openMonitors={openMonitors}
    />
  );
}

function PaletteBody({
  prefill,
  onClose,
  openEco,
  openNews,
  openAlerts,
  openMonitors,
}: {
  prefill: string;
  onClose: () => void;
  openEco: () => void;
  openNews: (q: string) => void;
  openAlerts: (symbol?: string) => void;
  openMonitors: () => void;
}) {
  const router = useRouter();
  // lazy initial state: the body mounts fresh on every palette open,
  // so the prefill seeds the query without an effect
  const [q, setQ] = useState(() => prefill);
  const [sel, setSel] = useState(0);
  const [deskHint, setDeskHint] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  // focus the input once mounted (DOM side effect only)
  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 20);
    return () => clearTimeout(t);
  }, []);

  const goDrillDown = useCallback(
    (sym: string, desk = false) => {
      const target = symbolHref(sym) + (desk ? "?desk=1" : "");
      router.push(target);
      onClose();
    },
    [router, onClose],
  );

  const items = useMemo<PaletteItem[]>(() => {
    const query = q.trim();
    const out: PaletteItem[] = [];

    // ---- prefix commands (news <q> / alert <sym> / run desk <sym> /
    //      search: <sym>) take over the whole query
    const newsM = /^news\s+(.+)$/i.exec(query);
    if (newsM) {
      out.push({
        id: "cmd-news",
        kind: "command",
        label: `NEWS SEARCH — "${newsM[1].trim()}"`,
        hint: "NSE-style headline search",
        score: 2000,
        run: () => {
          openNews(newsM[1].trim());
          onClose();
        },
      });
    }
    const alertM = /^(alert|alerts)\s+(.+)$/i.exec(query);
    if (alertM) {
      const symRaw = alertM[2].trim();
      const sym = registryNormalize(symRaw) ?? symRaw.toUpperCase();
      out.push({
        id: "cmd-alert",
        kind: "command",
        label: `ARM ALERT — ${sym}`,
        hint: "open the alerts panel prefilled",
        score: 2000,
        run: () => {
          openAlerts(sym);
          onClose();
        },
      });
    }
    const deskM = /^run\s+desk\s+(.+)$/i.exec(query);
    if (deskM) {
      const symRaw = deskM[1].trim();
      const sym = registryNormalize(symRaw) ?? symRaw.toUpperCase();
      out.push({
        id: "cmd-desk",
        kind: "command",
        label: `RUN ANALYST DESK — ${sym}`,
        hint: "drill-down + auto-run the 5-persona desk",
        score: 2000,
        run: () => goDrillDown(sym, true),
      });
    }
    const searchM = /^search:?\s*(.+)$/i.exec(query);
    if (searchM) {
      out.push({
        id: "cmd-search",
        kind: "command",
        label: `SEARCH — ${searchM[1].trim()}`,
        hint: "open the drill-down",
        score: 2000,
        run: () => goDrillDown(searchM[1].trim()),
      });
    }

    // ---- plain commands, fuzzy-matched
    const commands: Array<{
      id: string;
      label: string;
      hint: string;
      kw: string;
      run: () => void;
    }> = [
      {
        id: "go-markets",
        label: "GO MARKETS",
        hint: "the multi-market board",
        kw: "go markets board terminal",
        run: () => {
          router.push("/markets");
          onClose();
        },
      },
      {
        id: "go-deck",
        label: "GO DECK",
        hint: "the gold command deck",
        kw: "go deck home gold command",
        run: () => {
          router.push("/");
          onClose();
        },
      },
      {
        id: "go-chat",
        label: "GO CHAT",
        hint: "The Desk research agent",
        kw: "go chat desk agent research hermes",
        run: () => {
          router.push("/chat");
          onClose();
        },
      },
      {
        id: "movers",
        label: "MOVERS",
        hint: "scroll to the market movers",
        kw: "movers gainers losers screener",
        run: () => {
          if (window.location.pathname.startsWith("/markets/")) {
            router.push("/markets#movers");
          } else {
            document
              .getElementById("movers")
              ?.scrollIntoView({ behavior: "smooth", block: "start" });
          }
          onClose();
        },
      },
      {
        id: "eco",
        label: "ECO — ECONOMIC CALENDAR",
        hint: "this week's releases by country / impact",
        kw: "eco calendar economic events releases",
        run: () => {
          openEco();
          onClose();
        },
      },
      {
        id: "news",
        label: "NEWS — SEARCH",
        hint: "news <query> · NSE-style headline search",
        kw: "news nse search headlines top",
        run: () => {
          openNews("");
          onClose();
        },
      },
      {
        id: "alert",
        label: "ALERTS — PRICE ALERTS",
        hint: "alert <symbol> · arm a one-shot price alert",
        kw: "alert alerts price notify trip",
        run: () => {
          openAlerts();
          onClose();
        },
      },
      {
        id: "mon",
        label: "MON — MONITOR LISTS",
        hint: "manage watchlists (max 5 × 30)",
        kw: "mon monitors monitor watchlist lists",
        run: () => {
          openMonitors();
          onClose();
        },
      },
      {
        id: "run-desk",
        label: "RUN ANALYST DESK",
        hint: "run desk <symbol> · 5 personas + PM consensus",
        kw: "run desk analyst personas",
        run: () => {
          // GAUNTLET-P15 wiring fix: plain "run" / "run desk" has no
          // symbol — show the inline hint (it used to open the
          // monitor manager, a copy-paste bug). "run desk <sym>" is
          // the prefix command above; "mon" still opens monitors.
          setDeskHint(true);
        },
      },
    ];
    for (const c of commands) {
      const score = fuzzyScore(query, c.label) || fuzzyScore(query, c.kw);
      if (score > 0) {
        out.push({
          id: `cmd-${c.id}`,
          kind: "command",
          label: c.label,
          hint: c.hint,
          score,
          run: c.run,
        });
      }
    }

    // ---- registry symbols (67) — fuzzy over symbol + name
    if (query) {
      let symbolHits = 0;
      for (const e of REGISTRY) {
        const aliasHit = registryNormalize(query) === e.symbol ? 900 : 0;
        const score =
          aliasHit || fuzzyScore(query, e.symbol) || fuzzyScore(query, e.name);
        if (score > 0 && symbolHits < 12) {
          symbolHits++;
          out.push({
            id: `sym-${e.symbol}`,
            kind: "symbol",
            label: e.symbol,
            hint: `${e.name} · ${SECTOR_LABELS[e.sector] ?? e.sector}`,
            score: score - 50, // commands outrank symbols on ties
            run: () => goDrillDown(e.symbol),
          });
        }
      }
    }

    // ---- relevance order: best score first (stable — definition
    //      order breaks ties), so "alert" ranks ALERTS above the
    //      loose subsequence hit on ECO
    out.sort((a, b) => b.score - a.score);

    // ---- free-text passthrough: any query opens a drill-down
    if (query) {
      out.push({
        id: "freetext",
        kind: "search",
        label: `SEARCH — "${query}"`,
        hint: "open the /markets drill-down (any Yahoo symbol)",
        score: -1, // always last
        run: () => goDrillDown(query),
      });
    }

    return out;
  }, [q, router, onClose, openEco, openNews, openAlerts, openMonitors, goDrillDown]);

  // selection reset on query change lives in the input's onChange
  // (the only place q changes) — not an effect
  const onKey = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setSel((s) => (items.length ? (s + 1) % items.length : 0));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setSel((s) => (items.length ? (s - 1 + items.length) % items.length : 0));
      } else if (e.key === "Enter") {
        e.preventDefault();
        const item = items[sel];
        if (item) item.run();
      }
    },
    [items, sel, onClose],
  );

  // keep the selected row in view (keyboard scrolling)
  useEffect(() => {
    const el = listRef.current?.querySelector<HTMLElement>(
      `[data-idx="${sel}"]`,
    );
    el?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  return (
    <div
      className="fixed inset-0 z-[100] flex items-start justify-center bg-[#08090d]/88 px-4 pt-[12vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
    >
      <div className="gdc-panel w-full max-w-[640px] pb-2.5 pt-3">
        <div className="mb-2 flex items-center gap-2.5 px-4">
          <span className="gdc-data text-[13px] font-semibold text-[#c8a04b]">
            &gt;
          </span>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              setSel(0);          // result set reshapes → reset selection
              setDeskHint(false); // typing dismisses the run-desk hint
            }}
            onKeyDown={onKey}
            placeholder="type a command or symbol — eco · mon · news bitcoin · alert gc=f · run desk btc · BTC ⏎"
            className="gdc-data min-w-0 flex-1 bg-transparent text-[14px] text-[#e8ecf4] outline-none placeholder:text-[#5a6272]"
            aria-label="Command input"
            maxLength={80}
            spellCheck={false}
            autoComplete="off"
          />
          <span className="gdc-chip gdc-data text-[9px] text-[#8a93a6]">
            ⏎ GO
          </span>
        </div>
        {deskHint && (
          <div
            className="mx-2 mb-2 flex items-baseline gap-2 rounded-sm border border-[#c8a04b]/35 bg-[#c8a04b]/[0.07] px-3 py-2"
            role="status"
            aria-label="Run desk usage hint"
          >
            <span className="gdc-data shrink-0 text-[10.5px] font-semibold text-[#e2c074]">
              run desk &lt;symbol&gt;
            </span>
            <span className="text-[10.5px] text-[#c6cedb]">
              — e.g.{" "}
              <span className="gdc-data font-semibold text-[#e2c074]">
                run desk btc
              </span>{" "}
              opens /markets/BTC-USD?desk=1 and auto-runs the 5-persona
              desk. Type a symbol to complete the command.
            </span>
          </div>
        )}
        <div
          ref={listRef}
          className="mx-2 max-h-[52vh] overflow-y-auto"
          role="listbox"
          aria-label="Command results"
        >
          {items.map((it, i) => (
            <button
              key={it.id}
              data-idx={i}
              onClick={it.run}
              onMouseEnter={() => setSel(i)}
              className={`flex w-full cursor-pointer items-center gap-3 rounded-sm px-2.5 py-[7px] text-left transition-colors ${
                i === sel ? "bg-[#c8a04b]/[0.1]" : "hover:bg-white/[0.03]"
              }`}
              role="option"
              aria-selected={i === sel}
            >
              <span
                className="gdc-data w-[54px] shrink-0 text-[8px] font-bold uppercase tracking-[0.14em]"
                style={{
                  color:
                    it.kind === "command"
                      ? "#c8a04b"
                      : it.kind === "search"
                        ? "#8a93a6"
                        : "#6fa97a",
                }}
              >
                {it.kind === "command" ? "CMD" : it.kind === "search" ? "FIND" : "SYM"}
              </span>
              <span className="gdc-data min-w-0 flex-1 truncate text-[12px] font-semibold text-[#e8ecf4]">
                {it.label}
              </span>
              {it.hint && (
                <span className="hidden max-w-[45%] shrink-0 truncate text-[10px] text-[#8a93a6] sm:inline">
                  {it.hint}
                </span>
              )}
            </button>
          ))}
          {items.length === 0 && (
            <div className="px-3 py-5 text-center text-[11px] text-[#8a93a6]">
              type a mnemonic (eco, mon, news, alert) or any symbol — btc,
              gold, nifty, ^GSPC, inr/usd
            </div>
          )}
        </div>
        <div className="mt-1.5 flex items-center gap-3 border-t border-[#1a1f2c] px-4 pt-2 text-[8.5px] uppercase tracking-[0.16em] text-[#6f7987]">
          <span>↑↓ navigate</span>
          <span>⏎ run</span>
          <span>esc close</span>
          <span className="ml-auto">⌘K / Ctrl+K / /</span>
        </div>
      </div>
    </div>
  );
}

export const CommandPalette = memo(CommandPaletteImpl);
