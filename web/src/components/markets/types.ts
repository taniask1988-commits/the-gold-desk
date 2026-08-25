/** DTOs for GET /api/desk/markets (board + single-symbol detail). */

export interface MarketRow {
  symbol: string;
  name: string;
  sector: string;
  price: number;
  prev_close: number;
  change: number;
  change_pct: number;
  currency: string;
  points: number[];
  points_source?: "1d" | "5d";
  ts: number;
}

export interface MoverQuote {
  symbol: string;
  name: string;
  sector?: string;
  price?: number;
  change_pct: number;
}

export interface SectorBlock {
  key: string;
  label: string;
  rows: MarketRow[];
}

export interface MarketsBoard {
  ok: boolean;
  as_of?: string;
  error?: string;
  sectors?: SectorBlock[];
  market_movers?: { gainers: MoverQuote[]; losers: MoverQuote[] };
  watchlist_movers?: { gainers: MoverQuote[]; losers: MoverQuote[] };
  errors?: string[];
}

export interface DetailBar {
  ts: number;
  o: number;
  h: number;
  l: number;
  c: number;
}

export interface SymbolDetail {
  ok: boolean;
  symbol?: string;
  name?: string;
  sector?: string;
  currency?: string;
  price?: number;
  prev_close?: number;
  change?: number;
  change_pct?: number;
  range_5d_change_pct?: number;
  bars?: DetailBar[];
  derived?: boolean;
  derived_from?: string;
  error?: string;
}
