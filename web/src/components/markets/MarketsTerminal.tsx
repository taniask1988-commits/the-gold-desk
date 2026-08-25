"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { MarketsBoard, SymbolDetail } from "./types";
import { MarketHeader } from "./MarketHeader";
import { MarketFooter } from "./MarketFooter";
import { MoversStrip, WatchlistMovers } from "./Movers";
import { SectorGrid } from "./SectorHeatmap";
import { SearchLookup } from "./SearchLookup";

const REFRESH_MS = 30_000;

/** The terminal's second screen — full-page multi-market surface. */
export function MarketsTerminal() {
  const [board, setBoard] = useState<MarketsBoard | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [countdown, setCountdown] = useState(REFRESH_MS / 1000);

  // symbol lookup state
  const [query, setQuery] = useState("");
  const [detail, setDetail] = useState<SymbolDetail | null>(null);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  const nextAtRef = useRef<number>(Date.now() + REFRESH_MS);

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await fetch("/api/desk/markets", { cache: "no-store" });
      const d: MarketsBoard = await r.json();
      if (d.ok) {
        setBoard(d);
        setLinkError(null);
      } else {
        setLinkError(d.error || "markets board unreachable");
      }
    } catch (e) {
      setLinkError(e instanceof Error ? e.message : "network error");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // ONE effect: initial fetch + the 30s auto-refresh countdown. The wall
  // clock deadline lives in a ref so the manual-refresh button can reset it.
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

  const onSearch = useCallback(async () => {
    const s = query.trim();
    if (!s || searching) return;
    setSearching(true);
    setSearchError(null);
    try {
      const r = await fetch(`/api/desk/markets?symbol=${encodeURIComponent(s)}`, {
        cache: "no-store",
      });
      const d: SymbolDetail = await r.json();
      if (d.ok) {
        setDetail(d);
      } else {
        setDetail(null);
        setSearchError(d.error || `symbol not found: ${s}`);
      }
    } catch {
      setSearchError("lookup failed — feed unreachable");
    } finally {
      setSearching(false);
    }
  }, [query, searching]);

  // derived: breadth + sector averages, recomputed only when the board swaps
  const derived = useMemo(() => {
    const sectors = board?.sectors ?? [];
    let up = 0;
    let down = 0;
    let flat = 0;
    let pctSum = 0;
    let pctN = 0;
    const avgs = new Map<string, number | null>();
    for (const s of sectors) {
      let sum = 0;
      let n = 0;
      for (const r of s.rows ?? []) {
        if (typeof r.change_pct === "number" && Number.isFinite(r.change_pct)) {
          if (r.change_pct > 0) up++;
          else if (r.change_pct < 0) down++;
          else flat++;
          sum += r.change_pct;
          n++;
          pctSum += r.change_pct;
          pctN++;
        }
      }
      avgs.set(s.key, n > 0 ? sum / n : null);
    }
    return {
      sectors,
      breadth: { up, down, flat },
      avgs,
      avgPct: pctN > 0 ? pctSum / pctN : null,
      symbolCount: up + down + flat,
    };
  }, [board]);

  if (loading && !board) {
    return (
      <div className="gdc-root flex min-h-screen flex-col items-center justify-center gap-5">
        <div className="gdc-script text-[30px] text-[#f0e6d2]">Market Gauntlet</div>
        <div className="gdc-panel flex w-[280px] flex-col items-center gap-3 px-6 py-5">
          <span className="gdc-spec">Linking market feeds</span>
          <span className="gdc-breathe h-[2px] w-[120px] rounded-full bg-[#c8a04b]" />
          <span className="gdc-data text-[9px] text-[#8a93a6]">67 symbols · 9 sectors · keyless</span>
        </div>
      </div>
    );
  }

  if (linkError && !board) {
    return (
      <div className="gdc-root flex min-h-screen flex-col items-center justify-center gap-4">
        <div className="gdc-panel px-6 py-5 text-center">
          <div className="gdc-script mb-2 text-[22px] text-[#f0e6d2]">Market Gauntlet</div>
          <div className="text-sm text-[#f85149]">MARKET LINK ERROR: {linkError}</div>
          <button
            onClick={manualRefresh}
            className="gdc-chip mt-4 cursor-pointer border-[#c8a04b]/35 text-[#c8a04b] transition-colors hover:bg-[#c8a04b]/[0.12]"
          >
            Retry
          </button>
        </div>
      </div>
    );
  }

  const feedErrors = board?.errors ?? [];

  return (
    <div className="gdc-root flex min-h-screen flex-col">
      <MarketHeader
        breadth={derived.breadth.up + derived.breadth.down > 0 ? derived.breadth : null}
        avgPct={derived.avgPct}
        symbolCount={derived.symbolCount}
      />

      <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-4 px-4 py-4 sm:px-6 sm:py-5">
        {/* lookup row + inline result */}
        <div className="gdc-panel px-3.5 py-3">
          <SearchLookup
            query={query}
            setQuery={setQuery}
            onSearch={onSearch}
            searching={searching}
            detail={detail}
            error={searchError}
            onClose={() => setDetail(null)}
          />
        </div>

        {/* whole-market movers */}
        <MoversStrip movers={board?.market_movers} />

        {/* sector heatmaps — the centerpiece. Indices lead as a full-width
            hero band; small sectors pack side-by-side so no row sits sparse. */}
        <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-2 xl:grid-cols-3">
          {derived.sectors.map((s) => (
            <SectorGrid
              key={s.key}
              sector={s}
              avgPct={derived.avgs.get(s.key) ?? null}
              ist={s.key === "india"}
              hero={s.key === "indices"}
            />
          ))}
          {derived.sectors.length === 0 && (
            <div className="gdc-panel px-4 py-6 text-center text-[11px] uppercase tracking-[0.16em] text-[#8a93a6]">
              no sector data served
            </div>
          )}
        </div>

        {/* registry movers */}
        <WatchlistMovers movers={board?.watchlist_movers} />
      </main>

      <MarketFooter
        asOf={board?.as_of}
        countdown={countdown}
        refreshing={refreshing}
        onRefresh={manualRefresh}
        errorCount={feedErrors.length}
      />
    </div>
  );
}
