"use client";

import { useMemo, useState } from "react";
import type { BarDTO } from "./useDeskData";

export function PriceChart({ bars, replayBars }: { bars: BarDTO[]; replayBars?: Array<{ decisionTs: string; c: number; code: string | null }> }) {
  const [hover, setHover] = useState<number | null>(null);
  const W = 1200, H = 260, PAD = 8;

  const view = useMemo(() => {
    const tail = bars.slice(-480); // ~20 trading days
    if (tail.length < 2) return null;
    const min = Math.min(...tail.map((b) => b.l));
    const max = Math.max(...tail.map((b) => b.h));
    const range = max - min || 1;
    const x = (i: number) => PAD + (i / (tail.length - 1)) * (W - PAD * 2);
    const y = (v: number) => PAD + (1 - (v - min) / range) * (H - PAD * 2);
    const line = tail.map((b, i) => `${x(i)},${y(b.c)}`).join(" ");
    const area = `${PAD},${H - PAD} ${line} ${W - PAD},${H - PAD}`;
    // session bands: London 07-12 UTC, overlap 12-16 UTC (per visible range)
    const bands: Array<{ x1: number; x2: number; label: string; color: string }> = [];
    for (let i = 0; i < tail.length; i++) {
      const hr = Number(tail[i].ts_close.slice(11, 13));
      const isLdn = hr === 7 || hr === 8 || hr === 9 || hr === 10 || hr === 11;
      const isOvl = hr === 12 || hr === 13 || hr === 14 || hr === 15;
      const color = isOvl ? "rgba(232,180,64,0.10)" : isLdn ? "rgba(63,185,80,0.05)" : null;
      if (color) {
        const lastBand = bands[bands.length - 1];
        if (lastBand && lastBand.x2 >= x(i) - 2 && lastBand.color === color) {
          lastBand.x2 = x(i);
        } else {
          bands.push({ x1: x(i), x2: x(i), label: isOvl ? "OVERLAP" : "LONDON", color });
        }
      }
    }
    return { tail, min, max, x, y, line, area, bands };
  }, [bars]);

  if (!view) {
    return (
      <div className="gdc-panel flex h-[300px] items-center justify-center text-xs text-[#98a3af]">
        loading price series…
      </div>
    );
  }

  const { tail, min, max, x, y, line, area, bands } = view;
  const hi = hover !== null ? tail[hover] : null;

  return (
    <div className="gdc-panel overflow-hidden">
      <div className="gdc-sheen" aria-hidden style={{ "--sheen-delay": "1.2s", "--sheen-dur": "8.5s" } as React.CSSProperties} />
      <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-white/[0.07] px-5 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="gdc-accent text-[20px] text-[#f4f7fa]">Price, hourly</span>
          <span className="gdc-spec">XAU/USD · last {tail.length} bars</span>
        </div>
        <div className="gdc-spec flex items-center gap-3">
          <span className="flex items-center gap-1.5"><span className="h-2 w-3 bg-[#3fb950]/20" />London 07–12</span>
          <span className="flex items-center gap-1.5"><span className="h-2 w-3 bg-[#e8b440]/15" />LDN·NY 12–16</span>
          <span>{min.toFixed(0)} – {max.toFixed(0)}</span>
        </div>
      </div>
      <div className="relative">
        <svg viewBox={`0 0 ${W} ${H}`} className="block w-full" onMouseLeave={() => setHover(null)}
          onMouseMove={(e) => {
            const rect = (e.target as SVGElement).ownerSVGElement?.getBoundingClientRect();
            if (!rect) return;
            const frac = (e.clientX - rect.left) / rect.width;
            setHover(Math.max(0, Math.min(tail.length - 1, Math.round(frac * (tail.length - 1)))));
          }}>
          {bands.map((b, i) => (
            <rect key={i} x={b.x1} y={0} width={Math.max(1, b.x2 - b.x1)} height={H} fill={b.color} />
          ))}
          {[0.2, 0.4, 0.6, 0.8].map((f) => (
            <line key={f} x1={PAD} x2={W - PAD} y1={H * f} y2={H * f} stroke="#1b222b" strokeWidth="0.5" strokeDasharray="3 5" />
          ))}
          <defs>
            <linearGradient id="gdcArea" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#e8b440" stopOpacity="0.26" />
              <stop offset="100%" stopColor="#e8b440" stopOpacity="0.01" />
            </linearGradient>
          </defs>
          <polygon points={area} fill="url(#gdcArea)" />
          <polyline points={line} fill="none" stroke="#e8b440" strokeWidth="1.5" strokeLinejoin="round" />
          {hover !== null && (
            <g>
              <line x1={x(hover)} x2={x(hover)} y1={PAD} y2={H - PAD} stroke="#39c5cf" strokeWidth="0.8" strokeDasharray="2 3" />
              <circle cx={x(hover)} cy={y(tail[hover].c)} r="3" fill="#39c5cf" />
            </g>
          )}
        </svg>
        {hi && (
          <div className="gdc-data pointer-events-none absolute right-3 top-2 rounded-full border border-white/[0.12] bg-[#0b0e13]/85 px-3.5 py-1.5 text-[10px] backdrop-blur-md">
            <span className="text-[#98a3af]">{hi.ts_close.slice(0, 16).replace("T", " ")}</span>
            <span className="ml-3">O {hi.o.toFixed(2)}</span>
            <span className="ml-2">H {hi.h.toFixed(2)}</span>
            <span className="ml-2">L {hi.l.toFixed(2)}</span>
            <span className="ml-2 text-[#e8b440]">C {hi.c.toFixed(2)}</span>
          </div>
        )}
      </div>
      {replayBars && replayBars.length > 0 && (
        <div className="border-t border-white/[0.07] px-4 py-1.5 text-[9px] tracking-[0.14em] text-[#98a3af]">
          Replay synchronized · {replayBars.length} bars this day
        </div>
      )}
    </div>
  );
}
