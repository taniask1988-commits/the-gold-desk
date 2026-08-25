/** Shared formatting + color math for the Market Gauntlet surface.
 *
 * Palette (locked to the deck system): green #6FA97A / red #B85C5C on
 * charcoal — flat rgba mixing, never gradients. */

export const GREEN = "#6FA97A";
export const RED = "#B85C5C";
/** Brighter variants — change text/sparkline ON TINTED tiles (the bg alpha
 *  scales with |chg|, so strong movers need brighter marks to stay legible). */
export const TILE_GREEN = "#8FC79A";
export const TILE_RED = "#D98484";
export const GREEN_RGB = "111,169,122";
export const RED_RGB = "184,92,92";

/** change_pct → background alpha, clamped at 3% magnitude. */
export function chgAlpha(changePct: number): number {
  return Math.min(0.35, (Math.abs(changePct) / 3) * 0.35);
}

/** Flat tinted background for a tile/card (no gradients, no filters). */
export function chgBg(changePct: number): string {
  if (!Number.isFinite(changePct) || changePct === 0) return "transparent";
  const a = chgAlpha(changePct).toFixed(3);
  return changePct > 0
    ? `rgba(${GREEN_RGB},${a})`
    : `rgba(${RED_RGB},${a})`;
}

export function chgColor(changePct: number): string {
  if (!Number.isFinite(changePct) || changePct === 0) return "#8A93A6";
  return changePct > 0 ? GREEN : RED;
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

/**
 * Price display at full served precision — mirrors the harness CLI's
 * symbol-aware `_fmt_price`: FX ("=X") keeps tenth-of-a-pip (5dp below
 * 10 / 3dp at-or-above), derived reciprocals one digit finer, crypto
 * under a dollar 4dp, everything else thousands-separated 2dp.
 */
export function fmtPrice(
  v: number | null | undefined,
  symbol?: string,
  derived?: boolean,
): string {
  if (v == null || !Number.isFinite(v)) return "—";
  const abs = Math.abs(v);
  if (derived) return abs < 1 ? v.toFixed(6) : v.toFixed(5);
  if (symbol && symbol.toUpperCase().endsWith("=X")) {
    return abs < 10 ? v.toFixed(5) : v.toFixed(3);
  }
  if (abs < 1) return v.toFixed(4);
  return v.toLocaleString("en-US", {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });
}

/** Display symbol: strip Yahoo caret/FX decorations for a clean ticker. */
export function displaySymbol(symbol: string): string {
  return symbol.replace(/\^+/, "").replace(/=X$/, "");
}

/** ISO timestamp → "YYYY-MM-DD HH:MM:SS UTC" (input is already Z-suffixed). */
export function fmtAsOf(iso?: string): string {
  if (!iso) return "—";
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return m ? `${m[1]} ${m[2]} UTC` : iso;
}
