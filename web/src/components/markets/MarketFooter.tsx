"use client";

import { memo } from "react";
import { fmtAsOf } from "./lib";

function MarketFooterImpl({
  asOf,
  countdown,
  refreshing,
  onRefresh,
  errorCount,
}: {
  asOf: string | undefined;
  countdown: number;
  refreshing: boolean;
  onRefresh: () => void;
  errorCount: number;
}) {
  return (
    <footer className="mt-auto border-t border-[#1a1f2c] bg-[#0f1219]">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-7">
        {/* round-3 critic defect 3: microtext was near-invisible — the
            as-of / source / countdown / error notes now render at
            9.5px in #8a93a6 (dim), inline so they can't be overridden. */}
        <span
          className="gdc-spec-tight"
          style={{ fontSize: "9.5px", letterSpacing: "0.16em", color: "#8a93a6" }}
        >
          As of <span className="gdc-data text-[#aab4bf]">{fmtAsOf(asOf)}</span>
        </span>
        <span
          className="gdc-spec-tight"
          style={{ fontSize: "9.5px", letterSpacing: "0.16em", color: "#8a93a6" }}
        >
          Source · keyless free feeds — Yahoo v8/chart + predefined screener
        </span>
        <span
          className="gdc-spec-tight"
          style={{ fontSize: "9.5px", letterSpacing: "0.16em" }}
        >
          {errorCount > 0 ? (
            <span className="text-[#d29922]">⚠ {errorCount} feed error{errorCount > 1 ? "s" : ""} (fail-soft)</span>
          ) : (
            <span className="text-[#6fa97a]">0 feed errors</span>
          )}
        </span>
        <div className="ml-auto flex items-center gap-2.5">
          <span className="gdc-data text-[9.5px] tabular-nums text-[#8a93a6]" aria-live="off">
            next refresh in {countdown}s
          </span>
          <button
            onClick={onRefresh}
            disabled={refreshing}
            className="gdc-chip cursor-pointer border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-wait disabled:opacity-50"
          >
            {refreshing ? "Refreshing…" : "Refresh now"}
          </button>
        </div>
      </div>
    </footer>
  );
}

export const MarketFooter = memo(MarketFooterImpl);
