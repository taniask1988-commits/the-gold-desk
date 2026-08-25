import type { Metadata } from "next";
import { MarketDetail } from "@/components/markets/detail/MarketDetail";

/**
 * Asset drill-down (piece 3): ONE CLICK from any /markets heatmap tile,
 * mover card, or lookup result lands here.
 *
 * Route shape: catch-all [...symbol] joined back with "/" — a single
 * dynamic segment can't hold "inr/usd" (the slash is a real path
 * separator), while the catch-all receives it as two segments and
 * re-joins it. Segments arrive URL-encoded and are decoded per segment
 * by joinSymbol: /markets/%5ENSEI → "^NSEI", /markets/GC%3DF → "GC=F",
 * /markets/inr/usd → "inr/usd".
 */
type Params = { symbol?: string[] };

/** Catch-all segments arrive URL-ENCODED from Next (verified live:
 * /markets/%5ENSEI hands params ["%5ENSEI"], not ["^NSEI"]), so each
 * segment is decoded before joining. Malformed sequences pass through
 * untouched and simply fail soft downstream. */
function joinSymbol(segs?: string[]): string {
  return (segs ?? [])
    .map((s) => {
      try {
        return decodeURIComponent(s);
      } catch {
        return s;
      }
    })
    .join("/")
    .slice(0, 32);
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { symbol: segs } = await params;
  const symbol = joinSymbol(segs) || "Symbol";
  return {
    title: `${symbol} · Market Gauntlet`,
    description:
      `Asset drill-down for ${symbol} — 5-day 30m candlestick chart, prev close, ` +
      "day and 5-day ranges, keyless Yahoo RSS headlines, sector peer strip, and a " +
      "one-click research hand-off. Fail-soft free feeds.",
  };
}

export default async function SymbolPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { symbol: segs } = await params;
  // rejoin + decode the catch-all; length-capped (the python side
  // fails soft on anything unresolvable — the page renders a
  // not-found state instead of raising)
  const symbol = joinSymbol(segs);
  return <MarketDetail symbol={symbol} />;
}
