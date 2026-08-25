"use client";

import { memo } from "react";
import type { DetailBar } from "../types";
import { fmtPrice, GREEN, RED } from "../lib";

/* 5-day candlestick chart — the drill-down hero. Static SVG (no
 * animation, no filters): candles with wicks over a hairline price
 * grid, right-edge price labels, and a gold last-price tag line. When
 * the 5d window returns < 20 bars (sparse feeds — ^NSEI, ags) it
 * degrades to an area line off the closes instead of drawing
 * degenerate candles.
 *
 * P10 defect 3 (news above the 982px fold): the chart renders from TWO
 * aspect profiles of the SAME drawing code —
 *   ≥ xl (1280px+): wide flat profile, viewBox 1000x211 → a ~300px-tall
 *     full-bleed chart at the 1512 reference viewport (TradingView-like
 *     proportions), instead of the 512px the 1000x360 aspect produced.
 *   < xl: the original 1000x360 viewBox (mobile/tablet unchanged).
 * A hard CSS height cap on the 1000x360 viewBox was rejected: SVG
 * letterboxes uniform scaling, which left ~20% dead dark margin on each
 * side of the plot (VLM-verified on the first attempt). Both profiles
 * scale uniformly, so text never distorts; the body renderer is
 * parameterized by (H, PAD_T, PAD_B) and shared. */

const W = 1000;
const PAD_L = 12;
const PAD_R = 96;
const PLOT_W = W - PAD_L - PAD_R;
/* wide flat profile (xl+): ~300px rendered at the 1512 viewport */
const FLAT_H = 211;
const FLAT_PAD_T = 8;
const FLAT_PAD_B = 22;
/* original profile (< xl): unchanged mobile/tablet chart */
const TALL_H = 360;
const TALL_PAD_T = 14;
const TALL_PAD_B = 26;
const GOLD = "#E2C074";
const HAIRLINE = "#1a1f2c";
const DIM = "#8a93a6";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
  "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Axis label at served precision — thousands for indices, pips for
 * sub-10 FX, 4dp under a dollar (crypto). */
function axisFmt(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 10000) return v.toLocaleString("en-US", { maximumFractionDigits: 0 });
  if (abs >= 100) return v.toFixed(1);
  if (abs >= 10) return v.toFixed(2);
  if (abs >= 1) return v.toFixed(3);
  if (abs >= 0.01) return v.toFixed(4);
  return v.toFixed(6);
}

/** 1-2-2.5-5 × 10^k step for ~5 gridlines across the price range
 *  (the plain 1-2-5 ladder rounds a 8,930-point BTC range up to a
 *  5,000 step — only TWO gridlines; the 2.5 rung keeps 4-6). */
function niceStep(range: number): number {
  const raw = range / 5;
  const mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const norm = raw / mag;
  const nice = norm <= 1 ? 1 : norm <= 2 ? 2 : norm <= 2.5 ? 2.5 : norm <= 5 ? 5 : 10;
  return nice * mag;
}

function fmtTime(ts: number): string {
  const d = new Date(ts);
  const hh = String(d.getUTCHours()).padStart(2, "0");
  return `${String(d.getUTCDate()).padStart(2, "0")} ${MONTHS[d.getUTCMonth()]} ${hh}:00`;
}

function CandleChartImpl({
  bars,
  symbol,
  derived = false,
}: {
  bars: DetailBar[];
  symbol?: string;
  derived?: boolean;
}) {
  if (!bars || bars.length < 2) {
    return (
      <div className="flex h-[220px] items-center justify-center text-[10px] uppercase tracking-[0.18em] text-[#8a93a6]">
        no intraday bars served — feed degraded (fail-soft)
      </div>
    );
  }

  const n = bars.length;
  const area = n < 20; // sparse feed → area line instead of candles

  // price extent: candle wicks (or closes in area mode), 4% headroom
  let lo = Infinity;
  let hi = -Infinity;
  for (const b of bars) {
    lo = Math.min(lo, area ? b.c : b.l);
    hi = Math.max(hi, area ? b.c : b.h);
  }
  const padV = (hi - lo) * 0.04 || Math.abs(hi) * 0.001 || 1;
  const min = lo - padV;
  const max = hi + padV;
  const range = max - min;

  const x = (i: number) => PAD_L + ((i + 0.5) * PLOT_W) / n;

  const last = bars[n - 1];
  const up5 = last.c >= bars[0].c;
  const lineColor = up5 ? GREEN : RED;

  // time labels: ~5 evenly spaced bars
  const timeIdx: number[] = [];
  for (let k = 0; k < 5; k++) {
    timeIdx.push(Math.round((k * (n - 1)) / 4));
  }

  /** The chart body, parameterized by the viewBox height profile —
   * every y-mapping (gridlines, candles, area line, last-price tag)
   * derives from (H, PAD_T, PAD_B); x-mapping is profile-independent. */
  const body = (H: number, PAD_T: number, PAD_B: number) => {
    const PLOT_H = H - PAD_T - PAD_B;
    const y = (v: number) => PAD_T + (1 - (v - min) / range) * PLOT_H;

    // gridlines: nice steps from ceil(min) to max
    const step = niceStep(range);
    const ticks: number[] = [];
    for (let t = Math.ceil(min / step) * step; t <= max; t += step) ticks.push(t);

    const lastY = y(last.c);

    return (
      <>
        {/* horizontal price gridlines + right-edge labels. A label
            within ~22px of the last-price tag is suppressed so the gold
            tag never overlaps an axis value (VLM-caught defect). */}
        {ticks.map((t) => {
          const ty = y(t);
          const nearTag = Math.abs(ty - lastY) < 22;
          return (
            <g key={t}>
              <line
                x1={PAD_L}
                x2={W - PAD_R}
                y1={ty}
                y2={ty}
                stroke={HAIRLINE}
                strokeWidth={1}
              />
              {!nearTag && (
                <text
                  x={W - PAD_R + 8}
                  y={ty + 3.5}
                  fontSize={10}
                  fill={DIM}
                  className="gdc-data"
                >
                  {axisFmt(t)}
                </text>
              )}
            </g>
          );
        })}

        {/* time axis labels */}
        {timeIdx.map((i) => (
          <text
            key={i}
            x={Math.min(Math.max(x(i), PAD_L + 26), W - PAD_R - 26)}
            y={H - 8}
            fontSize={9}
            fill={DIM}
            textAnchor="middle"
            className="gdc-data"
          >
            {fmtTime(bars[i].ts)}
          </text>
        ))}

        {area
          ? /* sparse-feed fallback: area line off the closes */ (() => {
                const pts = bars.map((b, i) => `${x(i).toFixed(1)},${y(b.c).toFixed(1)}`);
                const base = (PAD_T + PLOT_H).toFixed(1);
                return (
                  <g>
                    <polygon
                      points={`${x(0).toFixed(1)},${base} ${pts.join(" ")} ${x(n - 1).toFixed(1)},${base}`}
                      fill={up5 ? "rgba(111,169,122,0.12)" : "rgba(184,92,92,0.12)"}
                    />
                    <polyline
                      points={pts.join(" ")}
                      fill="none"
                      stroke={lineColor}
                      strokeWidth={1.5}
                      strokeLinejoin="round"
                      strokeLinecap="round"
                    />
                  </g>
                );
              })()
          : /* candles: wick + body, flat colors (green/red), no gradients */
            bars.map((b, i) => {
              const up = b.c >= b.o;
              const cx = x(i);
              const bw = Math.max(1.4, (PLOT_W / n) * 0.62);
              const yO = y(b.o);
              const yC = y(b.c);
              const top = Math.min(yO, yC);
              const bodyH = Math.max(1, Math.abs(yC - yO));
              return (
                <g key={b.ts}>
                  <line
                    x1={cx}
                    x2={cx}
                    y1={y(b.h)}
                    y2={y(b.l)}
                    stroke={up ? GREEN : RED}
                    strokeWidth={1}
                  />
                  <rect
                    x={cx - bw / 2}
                    y={top}
                    width={bw}
                    height={bodyH}
                    fill={up ? "rgba(111,169,122,0.85)" : "rgba(184,92,92,0.85)"}
                  />
                </g>
              );
            })}

        {/* last-price tag line — the gold accent */}
        <line
          x1={PAD_L}
          x2={W - PAD_R}
          y1={lastY}
          y2={lastY}
          stroke={GOLD}
          strokeWidth={1}
          strokeDasharray="4 4"
          opacity={0.85}
        />
        <rect
          x={W - PAD_R + 3}
          y={lastY - 9}
          width={PAD_R - 9}
          height={18}
          rx={3}
          fill="#141821"
          stroke="rgba(200,160,75,0.55)"
          strokeWidth={1}
        />
        <text
          x={W - PAD_R + 9}
          y={lastY + 3.5}
          fontSize={10.5}
          fontWeight={600}
          fill={GOLD}
          className="gdc-data"
        >
          {symbol ? fmtPrice(last.c, symbol, derived) : axisFmt(last.c)}
        </text>
      </>
    );
  };

  const label = `5-day ${area ? "area" : "candlestick"} chart, ${n} bars`;
  return (
    <>
      {/* xl+: wide flat profile — full-bleed ~300px at the 1512
          reference viewport (P10 defect 3) */}
      <svg
        viewBox={`0 0 ${W} ${FLAT_H}`}
        className="hidden w-full xl:block"
        role="img"
        aria-label={label}
      >
        {body(FLAT_H, FLAT_PAD_T, FLAT_PAD_B)}
      </svg>
      {/* < xl: the original 1000x360 profile (mobile/tablet unchanged) */}
      <svg
        viewBox={`0 0 ${W} ${TALL_H}`}
        className="block w-full xl:hidden"
        role="img"
        aria-label={label}
      >
        {body(TALL_H, TALL_PAD_T, TALL_PAD_B)}
      </svg>
    </>
  );
}

export const CandleChart = memo(CandleChartImpl);
