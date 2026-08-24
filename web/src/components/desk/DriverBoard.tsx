"use client";

import { useEffect, useRef, useState } from "react";
import type { DriverDTO, DriverValuesDTO } from "./useDeskData";
import { DRIVERS, stanceFor } from "@/lib/desk/drivers";

function DriverSpark({ data, stance }: { data: number[]; stance: string }) {
  const w = 88, h = 22;
  if (data.length < 2) return <svg width={w} height={h} />;
  const min = Math.min(...data), max = Math.max(...data), range = max - min || 1;
  const color = stance === "TAILWIND" ? "#3fb950" : stance === "HEADWIND" ? "#f85149" : "#8b949e";
  const pts = data.map((v, i) => `${(i / (data.length - 1)) * w},${h - ((v - min) / range) * (h - 4) - 2}`);
  return (
    <svg width={w} height={h}>
      <polyline points={pts.join(" ")} fill="none" stroke={color} strokeWidth="1.2" opacity="0.85" />
    </svg>
  );
}

const TIER_META: Record<number, { label: string; note: string }> = {
  1: { label: "TIER 1 · MACRO REGIME", note: "moves gold for weeks" },
  2: { label: "TIER 2 · POSITIONING & FLOWS", note: "confirms or diverges" },
  3: { label: "TIER 3 · EVENT RISK", note: "gates the timing" },
  4: { label: "TIER 4 · MICROSTRUCTURE", note: "this hour, tradeable or not" },
};

/**
 * M5: the delta column shows the change in the LIVE series, not noise vs the
 * simulated baseline. The previous implementation computed
 * `overrideVal - d.value` (live minus SIMULATED), which produced a constant
 * offset that looked like signal but was meaningless noise.
 *
 * Implementation: the merged-with-deltas array lives in component state. The
 * `prevLiveRef` is only READ inside the effect (never during render) and
 * holds the previous round's live values so the next round can compute
 * `delta = newLive - prevLive` (or 0 on the very first reading). The ref is
 * safe to mutate inside the effect — that's the canonical pattern.
 */
function useMergeReal(drivers: DriverDTO[], values: DriverValuesDTO | null) {
  const [merged, setMerged] = useState<DriverDTO[]>(drivers);
  const prevLiveRef = useRef<Record<string, number>>({});
  useEffect(() => {
    if (!values?.live) { setMerged(drivers); return; }
    const prev = prevLiveRef.current;  // snapshot from last round
    const next = drivers.map((d) => {
      const real = values.live[d.id];
      if (!real || typeof real.value !== "number") return d;
      const def = DRIVERS.find((x) => x.id === d.id);
      const overrideVal =
        d.id === "D5" && typeof real.display_k === "number"
          ? real.display_k
          : real.value;
      const prevVal = prev[d.id];
      const delta = prevVal === undefined ? 0 : overrideVal - prevVal;
      return {
        ...d,
        value: overrideVal,
        stance: def ? stanceFor(def, overrideVal) : d.stance,
        formatted: def ? def.format(overrideVal) : d.formatted,
        delta,
        live: true,
        source: real.source,
      };
    });
    // record this round's live values for the next delta computation
    const nextPrev: Record<string, number> = {};
    for (const [id, v] of Object.entries(values.live)) {
      if (typeof v.value === "number") nextPrev[id] =
        id === "D5" && typeof v.display_k === "number" ? v.display_k : v.value;
    }
    prevLiveRef.current = nextPrev;
    setMerged(next);
  }, [values, drivers]);
  return merged;
}

export function DriverBoard({
  drivers, driverValues,
}: {
  drivers: DriverDTO[];
  driverValues: DriverValuesDTO | null;
}) {
  const tiers = [1, 2, 3, 4];
  const merged = useMergeReal(drivers, driverValues);
  const liveCount = merged.filter((d) => d.live).length;
  return (
    <div className="gdc-panel overflow-hidden">
      <div className="gdc-sheen" aria-hidden style={{ "--sheen-delay": "4.8s", "--sheen-dur": "10.5s" } as React.CSSProperties} />
      <div className="flex items-center justify-between border-b border-white/[0.07] px-4 py-2">
        <div className="flex items-baseline gap-3">
          <span className="gdc-accent text-[20px] text-[#f4f7fa]">Market drivers</span>
          <span className="gdc-spec">what institutions watch</span>
        </div>
        <span
          className={`gdc-chip ${liveCount > 0 ? "border-[#3fb950]/35 text-[#3fb950]" : "border-[#d29922]/30 text-[#d29922]"}`}
        >
          {liveCount > 0 ? (
            <>
              <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
              {liveCount}/{DRIVERS.length} live feeds
            </>
          ) : (
            "SIM VALUES"
          )}
        </span>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {tiers.map((t) => {
          const list = merged.filter((d) => d.tier === t);
          const meta = TIER_META[t];
          return (
            <div key={t} className="rounded-2xl border border-white/[0.05] bg-white/[0.02] p-3 backdrop-blur-sm">
              <div className="mb-2 flex items-baseline justify-between">
                <span className="gdc-accent text-[14.5px] leading-none text-[#e8b440]">{meta.label}</span>
                <span className="gdc-spec-tight">{meta.note}</span>
              </div>
              <div className="space-y-1.5">
                {list.map((d) => {
                  const c =
                    d.stance === "TAILWIND" ? "#3fb950" : d.stance === "HEADWIND" ? "#f85149" : "#8b949e";
                  return (
                    <div
                      key={d.id}
                      className="group flex items-center gap-3 rounded-lg border border-transparent px-2 py-1.5 transition-all hover:border-white/[0.09] hover:bg-white/[0.04]"
                      title={d.why}
                    >
                      <span className="gdc-data w-6 shrink-0 text-[9px] font-semibold text-[#8a95a1]">{d.id}</span>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-baseline justify-between gap-2">
                          <span className="truncate text-[11.5px] font-medium text-[#f4f7fa]">{d.name}</span>
                          <span
                            className="gdc-display-num shrink-0 text-[16px]"
                            style={{ color: c }}
                            title={d.live ? `live · ${d.source}` : "simulated — no free feed"}
                          >
                            {d.formatted}
                          </span>
                        </div>
                        <div className="flex items-center justify-between gap-2">
                          <span className="gdc-spec-tight truncate">{d.display} — {d.why}</span>
                          <span
                            className="gdc-chip shrink-0 px-1.5 py-0 text-[8px]"
                            style={{ color: c, borderColor: c + "40", background: c + "0d" }}
                          >
                            {d.delta >= 0 ? "▲" : "▼"} {Math.abs(d.delta) < 10 ? Math.abs(d.delta).toFixed(2) : Math.abs(d.delta).toFixed(0)}
                            {" · "}
                            {d.stance}
                            {d.live ? " · LIVE" : " · SIM"}
                          </span>
                        </div>
                      </div>
                      <DriverSpark data={d.history} stance={d.stance} />
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <div className="gdc-spec-tight border-t border-white/[0.07] px-4 py-2">
        Taxonomy — docs/MARKET_DRIVERS.md · {DRIVERS.length} drivers · tier-weighted composite in the header ·{" "}
        {liveCount > 0
          ? `${liveCount} live (Treasury, Yahoo, CFTC, computed) · ${DRIVERS.length - liveCount} simulated (no free feed)`
          : "all simulated — feeds unreachable"}
      </div>
    </div>
  );
}
