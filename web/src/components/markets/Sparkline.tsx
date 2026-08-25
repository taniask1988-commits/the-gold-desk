"use client";

import { memo } from "react";

/**
 * Tiny inline sparkline — viewBox 0 0 100 28, polyline normalized to the
 * row's min/max. Static SVG, no animation, stroke by change sign.
 */
function SparklineImpl({
  points,
  color,
  height = 28,
}: {
  points: number[];
  color: string;
  height?: number;
}) {
  if (!points || points.length < 2) {
    return <svg viewBox="0 0 100 28" height={height} width="100%" aria-hidden />;
  }
  let min = Infinity;
  let max = -Infinity;
  for (const p of points) {
    if (p < min) min = p;
    if (p > max) max = p;
  }
  const range = max - min || 1;
  const step = 100 / (points.length - 1);
  const y = (v: number) => 26 - ((v - min) / range) * 24; // 2px headroom top/bottom
  const str = points.map((v, i) => `${(i * step).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  return (
    <svg
      viewBox="0 0 100 28"
      height={height}
      width="100%"
      preserveAspectRatio="none"
      aria-hidden
      className="block"
    >
      <polyline
        points={str}
        fill="none"
        stroke={color}
        strokeWidth={1.25}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export const Sparkline = memo(SparklineImpl);
