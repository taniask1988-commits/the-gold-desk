"use client";

import { memo } from "react";
import Link from "next/link";
import type { MarketRow, MarketsBoard } from "../types";
import { chgBg, chgColor, displaySymbol, fmtPct, fmtPrice, symbolHref } from "../lib";

/** One peer tile in the horizontal sector strip — a compact linked
 *  card (fixed width, no sparkline) tinted by change_pct. */
function StripTileImpl({ row, current }: { row: MarketRow; current: boolean }) {
  return (
    <Link
      href={symbolHref(row.symbol)}
      aria-current={current ? "page" : undefined}
      className={`flex w-[148px] shrink-0 cursor-pointer flex-col gap-1 overflow-hidden rounded-md border px-2.5 pb-2 pt-1.5 transition-colors duration-200 ${
        current
          ? "border-[#c8a04b]/60"
          : "border-[#1a1f2c] hover:border-[#c8a04b]/55"
      }`}
      style={{ backgroundColor: chgBg(row.change_pct) }}
      title={`${row.symbol} · ${row.name} — click for full detail`}
    >
      <div className="flex items-baseline justify-between gap-1.5">
        <span className="gdc-data min-w-0 truncate text-[11px] font-semibold tracking-tight text-[#e8ecf4]">
          {displaySymbol(row.symbol)}
        </span>
        {current && (
          <span className="shrink-0 text-[7px] font-semibold uppercase tracking-[0.14em] text-[#c8a04b]">
            here
          </span>
        )}
      </div>
      <div className="truncate text-[8.5px] leading-tight text-[#8a93a6]" title={row.name}>
        {row.name}
      </div>
      <div className="mt-0.5 flex items-baseline justify-between gap-1.5">
        <span className="gdc-data text-[12.5px] font-semibold tabular-nums text-[#e8ecf4]">
          {fmtPrice(row.price, row.symbol)}
        </span>
        <span
          className="gdc-data shrink-0 text-[10.5px] font-semibold tabular-nums"
          style={{ color: chgColor(row.change_pct) }}
        >
          {fmtPct(row.change_pct)}
        </span>
      </div>
    </Link>
  );
}

const StripTile = memo(StripTileImpl);

/** SECTOR STRIP — the detail page's sibling context: every tile from
 *  the symbol's sector, horizontally scrollable, each clickable
 *  through to its own detail page. Hidden until the board loads
 *  (fail-soft: the page works without it). */
function SectorStripImpl({
  board,
  sectorKey,
  currentSymbol,
}: {
  board: MarketsBoard | null;
  sectorKey?: string;
  currentSymbol?: string;
}) {
  if (!board || !sectorKey) return null;
  const sec = (board.sectors ?? []).find((s) => s.key === sectorKey);
  if (!sec || !sec.rows || sec.rows.length === 0) return null;
  return (
    <section className="gdc-panel px-3.5 pb-3 pt-3" aria-label="Sector peers">
      <div className="mb-2 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          {sec.label} — Sector Strip
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          {sec.rows.length} tiles · click any to drill
        </span>
      </div>
      <div className="gdc-scroll flex gap-1.5 overflow-x-auto pb-1">
        {sec.rows.map((row) => (
          <StripTile key={row.symbol} row={row} current={row.symbol === currentSymbol} />
        ))}
      </div>
    </section>
  );
}

export const SectorStrip = memo(SectorStripImpl);
