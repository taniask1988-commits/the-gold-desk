"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import type { MarketsBoard, SymbolDetail } from "../types";
import { MarketFooter } from "../MarketFooter";
import { CandleChart } from "./CandleChart";
import { NewsCard } from "./NewsCard";
import { SectorStrip } from "./SectorStrip";
import {
  chgColor,
  derivedPairLabel,
  fmtPct,
  fmtPrice,
  sectorLabel,
} from "../lib";

const REFRESH_MS = 30_000;

/** One stat cell in the stats row. */
function Stat({
  label,
  value,
  sub,
}: {
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <div className="flex min-w-[150px] flex-1 flex-col gap-1 border-l border-[#1a1f2c] pl-4 first:border-l-0 first:pl-0 sm:min-w-0">
      <span className="text-[8.5px] font-semibold uppercase tracking-[0.18em] text-[#8a93a6]">
        {label}
      </span>
      <span className="gdc-data text-[14px] font-semibold tabular-nums leading-none text-[#e8ecf4]">
        {value}
      </span>
      {sub && <span className="text-[9px] text-[#8a93a6]">{sub}</span>}
    </div>
  );
}

/** The drill-down page (piece 3) — one click from any heatmap tile,
 *  mover card, or lookup result lands here: hero quote, 5d candlestick
 *  chart, day/5d ranges, per-symbol news, research hand-off to The
 *  Desk, and the sector strip of sibling tiles. 30s auto-refresh,
 *  fail-soft throughout. */
export function MarketDetail({ symbol }: { symbol: string }) {
  const [detail, setDetail] = useState<SymbolDetail | null>(null);
  const [board, setBoard] = useState<MarketsBoard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000);

  const nextAtRef = useRef<number>(Date.now() + REFRESH_MS);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const [dr, br] = await Promise.all([
        fetch(`/api/desk/markets?symbol=${encodeURIComponent(symbol)}`, {
          cache: "no-store",
        })
          .then((r) => r.json())
          .catch(() => null),
        fetch("/api/desk/markets", { cache: "no-store" })
          .then((r) => r.json())
          .catch(() => null),
      ]);
      if (dr && dr.ok) {
        setDetail(dr as SymbolDetail);
        setError(null);
      } else {
        setError((dr && dr.error) || `symbol not found: ${symbol}`);
      }
      if (br && br.ok) setBoard(br as MarketsBoard);
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [symbol]);

  // ONE effect: initial fetch + the 30s auto-refresh countdown (same
  // wall-clock-deadline-in-a-ref pattern as the board surface).
  useEffect(() => {
    void load();
    const t = setInterval(() => {
      const remain = Math.max(0, Math.ceil((nextAtRef.current - Date.now()) / 1000));
      setCountdown(remain);
      if (remain === 0) {
        nextAtRef.current = Date.now() + REFRESH_MS;
        void load();
      }
    }, 1000);
    return () => clearInterval(t);
  }, [load]);

  const manualRefresh = useCallback(() => {
    nextAtRef.current = Date.now() + REFRESH_MS;
    setCountdown(REFRESH_MS / 1000);
    void load();
  }, [load]);

  // ranges off the 5d bars: DAY = last 24h of bars, 5D = whole window
  const ranges = useMemo(() => {
    const bars = detail?.bars ?? [];
    if (bars.length === 0) return { day: null as [number, number] | null, week: null };
    let dLo = Infinity;
    let dHi = -Infinity;
    let wLo = Infinity;
    let wHi = -Infinity;
    const cutoff = bars[bars.length - 1].ts - 24 * 60 * 60 * 1000;
    let dayN = 0;
    for (const b of bars) {
      wLo = Math.min(wLo, b.l);
      wHi = Math.max(wHi, b.h);
      if (b.ts >= cutoff) {
        dLo = Math.min(dLo, b.l);
        dHi = Math.max(dHi, b.h);
        dayN++;
      }
    }
    return {
      day: dayN >= 2 ? ([dLo, dHi] as [number, number]) : null,
      week: [wLo, wHi] as [number, number],
    };
  }, [detail]);

  if (loading && !detail && !error) {
    return (
      <div className="gdc-root flex min-h-screen flex-col items-center justify-center gap-5">
        <div className="gdc-script text-[30px] text-[#f0e6d2]">Market Gauntlet</div>
        <div className="gdc-panel flex w-[300px] flex-col items-center gap-3 px-6 py-5">
          <span className="gdc-spec">Loading {symbol}</span>
          <span className="gdc-breathe h-[2px] w-[120px] rounded-full bg-[#c8a04b]" />
          <span className="gdc-data text-[9px] text-[#8a93a6]">
            quote · 5d bars · headlines · sector strip
          </span>
        </div>
      </div>
    );
  }

  const sym = detail?.symbol ?? symbol;
  const derived = detail?.derived ?? false;
  const derivedFrom = detail?.derived_from;
  // display symbol: derived pairs show the RECIPROCAL side the user
  // asked for; everything else shows the raw canonical Yahoo symbol
  const symDisplay = derived && derivedFrom ? derivedPairLabel(derivedFrom) : sym;

  return (
    <div className="gdc-root flex min-h-screen flex-col">
      {/* slim top bar: back to the board + breadcrumb */}
      <header className="sticky top-0 z-50 border-b border-[#1a1f2c] bg-[#08090d]">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2 sm:px-7">
          <Link
            href="/markets"
            className="gdc-chip border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
            aria-label="Back to the markets board"
          >
            <span aria-hidden>◂</span> Markets
          </Link>
          <span className="gdc-data truncate text-[11px] text-[#8a93a6]">
            {/* P10 defect 2: breadcrumb shows the DECODED human symbol
             * ("/markets/^NSEI"), never the URL-encoded form — the
             * encoded form only ever lives in fetch/href URLs */}
            /markets/{symDisplay}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <span className="gdc-chip gdc-data text-[#aab4bf]">
              <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
              Live Detail
            </span>
            <Link
              href="/"
              className="gdc-chip border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
            >
              <span aria-hidden>◂</span> Deck
            </Link>
          </div>
        </div>
      </header>

      {/* P10 defect 3: space-y-3 + py-4 (was space-y-4/py-5) — news
          must begin above the fold on a 982px viewport */}
      <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-3 px-4 py-3 sm:px-6 sm:py-4">
        {error && !detail ? (
          <div className="gdc-panel px-6 py-10 text-center">
            <div className="gdc-script mb-2 text-[22px] text-[#f0e6d2]">
              Symbol not found
            </div>
            <div className="text-[12px] text-[#d29922]">⚠ {error}</div>
            <div className="mt-2 text-[11px] text-[#8a93a6]">
              try any Yahoo symbol or an alias — btc, gold, nifty,
              reliance, eur/usd, inr/usd, vix, 10y, or any ticker (TOP,
              ETSY…)
            </div>
            <div className="mt-4 flex items-center justify-center gap-3">
              <button
                onClick={manualRefresh}
                className="gdc-chip cursor-pointer border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
              >
                Retry
              </button>
              <Link
                href="/markets"
                className="gdc-chip cursor-pointer text-[#aab4bf] transition-colors hover:text-[#e8ecf4]"
              >
                ◂ Back to the board
              </Link>
            </div>
          </div>
        ) : (
          detail && (
            <>
              {/* hero: identity + huge price + change chips (py-3,
                  was py-4 — P10 fold tightening; the 48px price stays) */}
              <section className="gdc-panel px-4 py-3 sm:px-6" aria-label="Quote header">
                <div className="flex flex-wrap items-start justify-between gap-x-10 gap-y-3">
                  <div className="flex min-w-[240px] flex-col gap-1">
                    <div className="flex flex-wrap items-center gap-2.5">
                      <h1 className="gdc-data text-[26px] font-semibold leading-none tracking-tight text-[#e8ecf4]">
                        {symDisplay}
                      </h1>
                      {detail.sector && (
                        <span className="rounded-sm border border-[#c8a04b]/30 px-1.5 py-[2px] text-[8px] font-semibold uppercase tracking-[0.16em] text-[#c8a04b]">
                          {/* ad-hoc (non-registry) symbols read "Market" */}
                          {sectorLabel(detail.sector)}
                        </span>
                      )}
                      {derived && derivedFrom && (
                        <span
                          className="rounded-sm border border-[#c8a04b]/40 px-1.5 py-[2px] text-[8px] font-semibold uppercase tracking-[0.14em] text-[#e2c074]"
                          title={`derived from ${derivedFrom} — price = 1/price`}
                        >
                          derived ← {derivedFrom}
                        </span>
                      )}
                      {detail.currency && (
                        <span className="rounded-sm border border-[#1a1f2c] px-1.5 py-[2px] text-[8px] font-semibold uppercase tracking-[0.16em] text-[#8a93a6]">
                          {detail.currency}
                          {derived ? " (reciprocal)" : ""}
                        </span>
                      )}
                    </div>
                    <span className="max-w-[520px] truncate text-[12px] text-[#8a93a6]" title={detail.name}>
                      {detail.name ?? ""}
                    </span>
                  </div>

                  <div className="flex flex-col items-end gap-2">
                    <div className="flex items-baseline gap-2">
                      <span className="gdc-display-num text-[42px] leading-none text-[#f4f7fa] sm:text-[48px]">
                        {fmtPrice(detail.price, sym, derived)}
                      </span>
                    </div>
                    <div className="flex flex-wrap items-center justify-end gap-2">
                      <span
                        className="gdc-chip gdc-data border-[#1a1f2c] text-[12px] font-semibold tabular-nums"
                        style={{
                          color: chgColor(detail.change_pct ?? 0),
                          borderColor:
                            (detail.change_pct ?? 0) > 0
                              ? "rgba(111,169,122,0.35)"
                              : (detail.change_pct ?? 0) < 0
                                ? "rgba(184,92,92,0.35)"
                                : "#1a1f2c",
                        }}
                        title="daily change (vs previous session close)"
                      >
                        1d {fmtPct(detail.change_pct)}
                        {typeof detail.change === "number" && (
                          <span className="text-[10px] opacity-80">
                            {" "}
                            ({fmtPrice(detail.change, sym, derived)})
                          </span>
                        )}
                      </span>
                      <span
                        className="gdc-chip gdc-data border-[#1a1f2c] text-[12px] font-semibold tabular-nums"
                        style={{
                          color: chgColor(detail.range_5d_change_pct ?? 0),
                          borderColor:
                            (detail.range_5d_change_pct ?? 0) > 0
                              ? "rgba(111,169,122,0.35)"
                              : (detail.range_5d_change_pct ?? 0) < 0
                                ? "rgba(184,92,92,0.35)"
                                : "#1a1f2c",
                        }}
                        title="bar-derived: first-bar close → last close over the served 5d bars"
                      >
                        5d {fmtPct(detail.range_5d_change_pct)}
                      </span>
                    </div>
                  </div>
                </div>
              </section>

              {/* chart — the hero (rendered height capped at 300px,
                  P10 defect 3; mobile keeps its natural size) */}
              <section className="gdc-panel px-3.5 pb-2.5 pt-2.5 sm:px-5" aria-label="Price chart">
                <div className="mb-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                  <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
                    5D · 30m bars
                  </h2>
                  <span className="h-px flex-1 bg-[#1a1f2c]" />
                  <span className="gdc-data text-[9px] tabular-nums text-[#8a93a6]">
                    {(detail.bars ?? []).length} bars
                    {(detail.bars ?? []).length > 0 &&
                      (detail.bars ?? []).length < 20 &&
                      " · sparse feed → area mode"}
                  </span>
                </div>
                <CandleChart bars={detail.bars ?? []} symbol={sym} derived={derived} />
              </section>

              {/* stats row (py-3, was py-4 — P10 fold tightening) */}
              <section className="gdc-panel flex flex-wrap gap-y-3 px-4 py-3 sm:px-6" aria-label="Key stats">
                <Stat
                  label="Prev Close (1d)"
                  value={fmtPrice(detail.prev_close, sym, derived) ?? "—"}
                  sub="previous session close, same chain as the board"
                />
                <Stat
                  label="Day Range"
                  value={
                    ranges.day
                      ? `${fmtPrice(ranges.day[0], sym, derived)} – ${fmtPrice(ranges.day[1], sym, derived)}`
                      : "—"
                  }
                  sub="min low – max high, last 24h of bars"
                />
                <Stat
                  label="5D Range"
                  value={
                    ranges.week
                      ? `${fmtPrice(ranges.week[0], sym, derived)} – ${fmtPrice(ranges.week[1], sym, derived)}`
                      : "—"
                  }
                  sub="min low – max high over the served 5d bars"
                />
                <Stat
                  label="Currency"
                  value={detail.currency ?? "—"}
                  sub={derived ? `reciprocal of ${derivedFrom}` : "as quoted by the feed"}
                />
              </section>

              {/* research hand-off → The Desk agent chat (py-3, was
                  py-4 — P10 fold tightening) */}
              <section className="gdc-panel flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3 sm:px-6" aria-label="Research">
                <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
                  Research
                </h2>
                <Link
                  href={`/chat?q=${encodeURIComponent(`research ${sym}`)}`}
                  className="gdc-chip cursor-pointer border-[#c8a04b]/45 px-4 py-1.5 text-[11px] font-semibold text-[#e2c074] transition-colors hover:bg-[#c8a04b]/[0.12]"
                  aria-label={`Hand ${symDisplay} to The Desk research agent`}
                >
                  Research {symDisplay} ▸
                </Link>
                <span className="text-[9.5px] text-[#8a93a6]">
                  opens The Desk agent chat with the query prefilled — never auto-sent
                </span>
              </section>

              {/* per-symbol news (hidden when the feed serves none) */}
              <NewsCard symbol={sym} news={detail.news} />

              {/* sibling tiles from the same sector */}
              <SectorStrip board={board} sectorKey={detail.sector} currentSymbol={sym} />
            </>
          )
        )}
      </main>

      <MarketFooter
        asOf={board?.as_of}
        countdown={countdown}
        refreshing={refreshing}
        onRefresh={manualRefresh}
        errorCount={error && !detail ? 1 : 0}
      />
    </div>
  );
}
