"use client";

import { memo } from "react";
import Link from "next/link";
import type { MarketRow, SectorBlock } from "./types";
import { Sparkline } from "./Sparkline";
import { chgBg, displaySymbol, fmtPrice, fmtPct, symbolHref, TILE_GREEN, TILE_RED } from "./lib";

/** One heatmap tile — now the drill-down entry point (piece 3): the
 *  whole tile is a Link to /markets/<symbol>. Flat rgba background
 *  scaled by |change_pct| (clamped at ~3%). Hero tiles (the full-width
 *  indices band) run one size larger for focal hierarchy. The compact
 *  label strips .NS/-USD/=X decorations; the full symbol rides in the
 *  title attr. */
function TileImpl({ row, hero = false }: { row: MarketRow; hero?: boolean }) {
  const up = row.change_pct > 0;
  const flat = row.change_pct === 0 || !Number.isFinite(row.change_pct);
  const sym = displaySymbol(row.symbol);
  return (
    <Link
      href={symbolHref(row.symbol)}
      className="flex min-w-0 cursor-pointer flex-col gap-1 overflow-hidden rounded-md border border-[#1a1f2c] px-2.5 pb-1.5 pt-2 transition-colors duration-200 hover:border-[#c8a04b]/55"
      style={{ backgroundColor: chgBg(row.change_pct) }}
      title={`${row.symbol} · ${row.name} · ${row.currency} · spark ${row.points_source ?? "1d"} — click for full detail`}
      aria-label={`${row.symbol} ${row.name} — open detail page`}
    >
      <div className="flex items-baseline justify-between gap-1.5">
        <span className="gdc-data min-w-0 truncate text-[11px] font-semibold tracking-tight text-[#e8ecf4]">
          {sym}
        </span>
        <span
          className="gdc-data shrink-0 text-[11px] font-semibold tabular-nums"
          style={{
            color: flat ? "#8a93a6" : row.change_pct > 0 ? TILE_GREEN : TILE_RED,
          }}
        >
          {fmtPct(row.change_pct)}
        </span>
      </div>
      <div className="truncate text-[8.5px] leading-tight text-[#8a93a6]" title={row.name}>
        {row.name}
      </div>
      <div className="mt-0.5 flex items-baseline gap-1">
        <span
          className={`gdc-data font-semibold leading-none tabular-nums text-[#e8ecf4] ${
            hero ? "text-[16px]" : "text-[14.5px]"
          }`}
        >
          {fmtPrice(row.price, row.symbol)}
        </span>
        {row.currency && row.currency !== "USD" && (
          <span className="text-[7.5px] font-semibold uppercase tracking-[0.14em] text-[#76828e]">
            {row.currency}
          </span>
        )}
        {row.points_source === "5d" && (
          <span className="ml-auto self-center rounded-sm border border-[#1a1f2c] px-1 text-[7px] font-semibold uppercase tracking-[0.12em] text-[#76828e]">
            5d
          </span>
        )}
      </div>
      <Sparkline
        points={row.points}
        color={flat ? "#8a93a6" : up ? TILE_GREEN : TILE_RED}
        height={hero ? 32 : 26}
      />
    </Link>
  );
}

export const MemoTile = memo(TileImpl);

/** One sector: header row (label + average change chip) + tile grid.
 *
 * Grid packing (round-3 critic defect 1): CSS grid with
 * repeat(auto-fill, minmax(145px, 1fr)) — auto-FILL (not auto-fit)
 * keeps every column track occupied-or-reserved, so a last-row orphan
 * sits in a track exactly as wide as its siblings instead of
 * stretching to the 210px flex max. Single-tile sectors (volatility)
 * center their lone tile at max 210px instead of stretching it. */
function SectorGridImpl({
  sector,
  avgPct,
  ist = false,
  hero = false,
}: {
  sector: SectorBlock;
  avgPct: number | null;
  ist?: boolean;
  hero?: boolean;
}) {
  if (!sector.rows || sector.rows.length === 0) return null;
  const single = sector.rows.length === 1;
  return (
    <section
      className={`gdc-panel px-3.5 pb-3.5 pt-3 ${hero ? "md:col-span-2 xl:col-span-3" : ""}`}
      aria-label={sector.label}
    >
      <div className="mb-2.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          {sector.label}
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        {ist && (
          <span className="gdc-chip border-[#c8a04b]/30 px-2 py-0 text-[8px] text-[#c8a04b]">
            NSE · BSE · IST
          </span>
        )}
        <span className="gdc-data text-[9px] tabular-nums text-[#8a93a6]">
          {sector.rows.length}
        </span>
        {avgPct != null && (
          <span
            className="gdc-data rounded-sm border px-1.5 py-[1px] text-[10px] font-semibold tabular-nums"
            style={{
              color: avgPct > 0 ? "#6FA97A" : avgPct < 0 ? "#B85C5C" : "#8a93a6",
              borderColor:
                avgPct > 0 ? "rgba(111,169,122,0.35)" : avgPct < 0 ? "rgba(184,92,92,0.35)" : "#1a1f2c",
            }}
          >
            AVG {fmtPct(avgPct)}
          </span>
        )}
      </div>
      {single ? (
        // a lone tile centers instead of stretching across dead space
        <div className="flex justify-center">
          <div className="w-full max-w-[210px]">
            {sector.rows.map((row) => (
              <MemoTile key={row.symbol} row={row} hero={hero} />
            ))}
          </div>
        </div>
      ) : (
        <div className="grid grid-cols-[repeat(auto-fill,minmax(145px,1fr))] gap-1.5">
          {sector.rows.map((row) => (
            <MemoTile key={row.symbol} row={row} hero={hero} />
          ))}
        </div>
      )}
    </section>
  );
}

export const SectorGrid = memo(SectorGridImpl);
