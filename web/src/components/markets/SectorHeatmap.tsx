"use client";

import { memo } from "react";
import type { MarketRow, SectorBlock } from "./types";
import { Sparkline } from "./Sparkline";
import { chgBg, displaySymbol, fmtPrice, fmtPct, TILE_GREEN, TILE_RED } from "./lib";

/** One heatmap tile: symbol, price at full precision, change_pct, sparkline.
 *  Flat rgba background scaled by |change_pct| (clamped at ~3%). Hero tiles
 *  (the full-width indices band) run one size larger for focal hierarchy. */
function TileImpl({ row, hero = false }: { row: MarketRow; hero?: boolean }) {
  const up = row.change_pct > 0;
  const flat = row.change_pct === 0 || !Number.isFinite(row.change_pct);
  const sym = displaySymbol(row.symbol);
  return (
    <div
      className="flex min-w-[110px] flex-[1_1_110px] max-w-[210px] flex-col gap-1 overflow-hidden rounded-md border border-[#1a1f2c] px-2.5 pb-1.5 pt-2 transition-colors duration-200 hover:border-[#2a3247]"
      style={{ backgroundColor: chgBg(row.change_pct) }}
      title={`${row.name} · ${row.currency} · spark ${row.points_source ?? "1d"}`}
    >
      <div className="flex items-baseline justify-between gap-1.5">
        <span className="gdc-data truncate text-[11px] font-semibold tracking-tight text-[#e8ecf4]">
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
    </div>
  );
}

export const MemoTile = memo(TileImpl);

/** One sector: header row (label + average change chip) + tile grid. */
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
        <span className="gdc-data text-[9px] tabular-nums text-[#76828e]">
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
      <div className="flex flex-wrap gap-1.5">
        {sector.rows.map((row) => (
          <MemoTile key={row.symbol} row={row} hero={hero} />
        ))}
      </div>
    </section>
  );
}

export const SectorGrid = memo(SectorGridImpl);
