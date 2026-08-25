"use client";

import { memo } from "react";
import Link from "next/link";
import type { MoverQuote } from "./types";
import { chgBg, chgColor, fmtPct, fmtPrice, displaySymbol, symbolHref } from "./lib";

/** Compact whole-market mover card — tint intensity scales with
 *  |change_pct|. Click-through (piece 3): the card is a Link to the
 *  symbol's drill-down page. */
function MoverCardImpl({ q }: { q: MoverQuote }) {
  return (
    <Link
      href={symbolHref(q.symbol)}
      className="flex min-w-0 cursor-pointer flex-col gap-0.5 overflow-hidden rounded-md border border-[#1a1f2c] px-2.5 py-1.5 transition-colors duration-200 hover:border-[#c8a04b]/55"
      style={{ backgroundColor: chgBg(q.change_pct) }}
      title={`${q.symbol} · ${q.name} — ${fmtPct(q.change_pct)} — click for full detail`}
      aria-label={`${q.symbol} ${q.name} — open detail page`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="gdc-data min-w-0 truncate text-[10.5px] font-semibold tracking-tight text-[#e8ecf4]">
          {displaySymbol(q.symbol)}
        </span>
        <span
          className="gdc-data shrink-0 text-[11px] font-bold tabular-nums"
          style={{ color: chgColor(q.change_pct) }}
        >
          {fmtPct(q.change_pct)}
        </span>
      </div>
      <div className="truncate text-[8.5px] leading-tight text-[#8a93a6]" title={q.name}>
        {q.name}
      </div>
      <div className="gdc-data text-[11px] tabular-nums text-[#aab4bf]">
        {q.price != null ? fmtPrice(q.price, q.symbol) : "—"}
      </div>
    </Link>
  );
}

export const MoverCard = memo(MoverCardImpl);

function MoverRowImpl({
  label,
  quotes,
  accent,
}: {
  label: string;
  quotes: MoverQuote[];
  accent: string;
}) {
  if (!quotes || quotes.length === 0) return null;
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-2">
        <span className="text-[8.5px] font-semibold uppercase tracking-[0.22em]" style={{ color: accent }}>
          {label}
        </span>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(104px,1fr))] gap-1.5">
        {quotes.map((q) => (
          <MoverCard key={q.symbol} q={q} />
        ))}
      </div>
    </div>
  );
}

const MoverRow = memo(MoverRowImpl);

/** MARKET MOVERS — whole-market gainers + losers from the Yahoo screener. */
function MoversStripImpl({
  movers,
}: {
  movers?: { gainers: MoverQuote[]; losers: MoverQuote[] };
}) {
  if (!movers || (!movers.gainers?.length && !movers.losers?.length)) return null;
  return (
    <section className="gdc-panel px-3.5 pb-3.5 pt-3" aria-label="Market movers">
      <div className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          Market Movers — Whole US Market
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          Yahoo day_gainers / day_losers · 12 per side
        </span>
      </div>
      <div className="flex flex-col gap-3">
        <MoverRow label="Top Gainers ▲" quotes={movers.gainers ?? []} accent="#6FA97A" />
        <MoverRow label="Top Losers ▼" quotes={movers.losers ?? []} accent="#B85C5C" />
      </div>
    </section>
  );
}

export const MoversStrip = memo(MoversStripImpl);

/** WATCHLIST MOVERS — top 5 per side across our own 67-symbol registry. */
function WatchlistRowImpl({
  label,
  quotes,
  accent,
}: {
  label: string;
  quotes: MoverQuote[];
  accent: string;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex items-center gap-2">
        <span className="text-[8.5px] font-semibold uppercase tracking-[0.22em]" style={{ color: accent }}>
          {label}
        </span>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
      </div>
      {quotes.length === 0 && (
        <div className="py-2 text-[9px] uppercase tracking-[0.14em] text-[#8a93a6]">no rows</div>
      )}
      {quotes.map((q, i) => (
        <Link
          key={q.symbol}
          href={symbolHref(q.symbol)}
          title={`${q.symbol} · ${q.name} — click for full detail`}
          className="flex cursor-pointer items-baseline gap-2 rounded-sm px-1.5 py-[3px] transition-colors duration-150 hover:bg-white/[0.04]"
        >
          <span className="gdc-data w-3.5 shrink-0 text-[9px] tabular-nums text-[#8a93a6]">
            {i + 1}
          </span>
          <span className="gdc-data w-[76px] shrink-0 truncate text-[10.5px] font-semibold text-[#e8ecf4]">
            {displaySymbol(q.symbol)}
          </span>
          <span className="min-w-0 flex-1 truncate text-[9px] text-[#8a93a6]" title={q.name}>
            {q.name}
          </span>
          <span className="gdc-data hidden shrink-0 text-[10px] tabular-nums text-[#8a93a6] sm:inline">
            {q.price != null ? fmtPrice(q.price, q.symbol) : ""}
          </span>
          <span
            className="gdc-data w-[56px] shrink-0 text-right text-[10.5px] font-semibold tabular-nums"
            style={{ color: chgColor(q.change_pct) }}
          >
            {fmtPct(q.change_pct)}
          </span>
        </Link>
      ))}
    </div>
  );
}

const WatchlistRow = memo(WatchlistRowImpl);

function WatchlistMoversImpl({
  movers,
}: {
  movers?: { gainers: MoverQuote[]; losers: MoverQuote[] };
}) {
  if (!movers || (!movers.gainers?.length && !movers.losers?.length)) return null;
  return (
    <section className="gdc-panel px-3.5 pb-3.5 pt-3" aria-label="Watchlist movers">
      <div className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          Watchlist Movers — Registry Top 5
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          across the 67-symbol board
        </span>
      </div>
      <div className="grid gap-x-6 gap-y-3 lg:grid-cols-2">
        <WatchlistRow label="Gainers ▲" quotes={movers.gainers ?? []} accent="#6FA97A" />
        <WatchlistRow label="Losers ▼" quotes={movers.losers ?? []} accent="#B85C5C" />
      </div>
    </section>
  );
}

export const WatchlistMovers = memo(WatchlistMoversImpl);
