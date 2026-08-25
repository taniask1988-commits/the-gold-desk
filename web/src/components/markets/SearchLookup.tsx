"use client";

import { memo } from "react";
import Link from "next/link";
import type { SymbolDetail } from "./types";
import { Sparkline } from "./Sparkline";
import { chgColor, fmtPct, fmtPrice, symbolHref } from "./lib";

/** Compact detail card for /api/desk/markets?symbol=X — name, price,
 *  1d/5d change, derived badge, sparkline off the 5d bars. Piece 3:
 *  the card body is a Link to the full drill-down page (the ✕ close
 *  button sits outside the link). */
function DetailCardImpl({
  detail,
  onClose,
}: {
  detail: SymbolDetail;
  onClose: () => void;
}) {
  const sym = detail.symbol ?? "?";
  const closes = (detail.bars ?? []).map((b) => b.c);
  const d5 = detail.range_5d_change_pct;
  return (
    <div className="gdc-panel mt-2 flex items-stretch gap-1 px-4 py-3">
      <Link
        href={symbolHref(sym)}
        className="flex min-w-0 flex-1 cursor-pointer flex-wrap items-center gap-x-7 gap-y-3 transition-colors duration-200 hover:opacity-[0.92]"
        title={`${sym} · ${detail.name ?? ""} — click for the full detail page (chart, news, peers)`}
        aria-label={`Open full detail page for ${sym}`}
      >
      <div className="flex min-w-[190px] flex-col gap-1">
        <div className="flex items-center gap-2">
          <span className="gdc-data text-[15px] font-semibold tracking-tight text-[#e8ecf4]">
            {sym}
          </span>
          {detail.sector && (
            <span className="rounded-sm border border-[#1a1f2c] px-1.5 py-[1px] text-[7.5px] font-semibold uppercase tracking-[0.14em] text-[#8a93a6]">
              {detail.sector}
            </span>
          )}
          {detail.derived && (
            <span
              className="rounded-sm border border-[#c8a04b]/40 px-1.5 py-[1px] text-[7.5px] font-semibold uppercase tracking-[0.14em] text-[#e2c074]"
              title={`derived from ${detail.derived_from ?? "?"} — price = 1/price`}
            >
              derived ← {detail.derived_from ?? "?"}
            </span>
          )}
        </div>
        <span className="truncate text-[10px] text-[#8a93a6]">{detail.name ?? ""}</span>
      </div>

      <div className="flex items-baseline gap-2">
        <span className="gdc-display-num text-[30px] leading-none text-[#f4f7fa]">
          {fmtPrice(detail.price, sym, detail.derived)}
        </span>
        {detail.currency && (
          <span className="text-[9px] font-semibold uppercase tracking-[0.16em] text-[#76828e]">
            {detail.currency}
          </span>
        )}
      </div>

      <div className="flex flex-col gap-0.5">
        <span className="text-[8px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">1d</span>
        <span
          className="gdc-data text-[13px] font-semibold tabular-nums"
          style={{ color: chgColor(detail.change_pct ?? 0) }}
        >
          {fmtPct(detail.change_pct)}
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-[8px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">
          5d · bar-derived
        </span>
        <span
          className="gdc-data text-[13px] font-semibold tabular-nums"
          style={{ color: chgColor(d5 ?? 0) }}
        >
          {fmtPct(d5)}
        </span>
      </div>
      <div className="flex flex-col gap-0.5">
        <span className="text-[8px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">
          prev close
        </span>
        <span className="gdc-data text-[12px] tabular-nums text-[#aab4bf]">
          {fmtPrice(detail.prev_close, sym, detail.derived)}
        </span>
      </div>

      <div className="flex min-w-[150px] flex-1 flex-col gap-0.5">
        <span className="text-[8px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">
          5d bars · {closes.length}
        </span>
        <Sparkline
          points={closes}
          color={(detail.change_pct ?? 0) >= 0 ? "#6FA97A" : "#B85C5C"}
          height={30}
        />
      </div>

      <span
        className="gdc-data hidden shrink-0 self-center rounded-sm border border-[#c8a04b]/35 px-2 py-[3px] text-[9px] font-semibold uppercase tracking-[0.14em] text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12] sm:inline"
      >
        drill ▸
      </span>
      </Link>

      <button
        onClick={onClose}
        aria-label="Close lookup result"
        className="gdc-chip cursor-pointer self-center px-2 text-[#76828e] transition-colors hover:text-[#e8ecf4]"
      >
        ✕
      </button>
    </div>
  );
}

const DetailCard = memo(DetailCardImpl);

function SearchLookupImpl({
  query,
  setQuery,
  onSearch,
  searching,
  detail,
  error,
  onClose,
}: {
  query: string;
  setQuery: (v: string) => void;
  onSearch: () => void;
  searching: boolean;
  detail: SymbolDetail | null;
  error: string | null;
  onClose: () => void;
}) {
  return (
    <div>
      <div className="flex flex-wrap items-center gap-3">
        <span className="gdc-spec shrink-0" style={{ fontSize: "10px", color: "#c8a04b" }}>
          Symbol Lookup
        </span>
        <div className="relative flex min-w-[220px] flex-1 items-center sm:max-w-md">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSearch();
            }}
            placeholder="Search — btc, eur/usd, xauusd, inr/usd…"
            spellCheck={false}
            autoComplete="off"
            aria-label="Symbol lookup"
            className="gdc-data w-full rounded-md border border-[#1a1f2c] bg-[#0b0e14] py-1.5 pl-3 pr-3 text-[11px] text-[#e8ecf4] placeholder-[#76828e] outline-none transition-colors focus:border-[#c8a04b]/60"
          />
        </div>
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          {searching ? "querying…" : "enter to search · click any tile or result for the full page"}
        </span>
      </div>
      {error && (
        <div className="mt-2 text-[10px] text-[#d29922]">⚠ {error}</div>
      )}
      {detail && <DetailCard detail={detail} onClose={onClose} />}
    </div>
  );
}

export const SearchLookup = memo(SearchLookupImpl);
