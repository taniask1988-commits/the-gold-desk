import type { Metadata } from "next";
import { MarketsTerminal } from "@/components/markets/MarketsTerminal";

export const metadata: Metadata = {
  title: "Market Gauntlet · Gold Desk Command",
  description:
    "Multi-market terminal surface — 67 symbols across 9 sectors (indices, US + India equities, ETFs, commodities, forex, rates, volatility, crypto), whole-market movers, and sector heatmaps. Free keyless feeds, fail-soft.",
  keywords: [
    "markets",
    "heatmap",
    "stock screener",
    "crypto",
    "forex",
    "commodities",
    "NSE",
    "BSE",
    "India equities",
  ],
};

export default function MarketsPage() {
  return <MarketsTerminal />;
}
