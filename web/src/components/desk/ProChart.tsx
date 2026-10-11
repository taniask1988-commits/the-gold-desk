"use client";

/**
 * GAUNTLET 7 Piece 1 — ProChart: TradingView lightweight-charts wrapper.
 *
 * Candles + volume + indicator overlays (SMA/EMA/BB lines, RSI in its own
 * pane) fed by /api/desk/chart (cli markets-chart — keyless Yahoo v8/chart,
 * fail-soft + stale-serve). Timeframe switching 15m/1h/1d.
 *
 * Perf doctrine (globals.css): canvas rendering, no backdrop-filter, no
 * width/height animations — lightweight-charts is a 60fps canvas engine.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  createChart,
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";

export type ChartBar = { t: number; o: number; h: number; l: number; c: number; v: number };
export type IndicatorLine = { name: string; kind: "line" | "band"; points: { t: number; v?: number; hi?: number; lo?: number }[] };

type ChartPayload = {
  ok?: boolean;
  symbol?: string;
  timeframe?: string;
  bars?: ChartBar[];
  indicator_series?: IndicatorLine[];
  errors?: string[];
  stale_error?: boolean;
  stale_reason?: string;
  data_delay?: string;
  meta?: { currency?: string; exchange?: string; symbol?: string };
};

const TIMEFRAMES = ["15m", "1h", "1d"] as const;
type Timeframe = (typeof TIMEFRAMES)[number];

const GOLD = "#c8a04b";
const INK = "#f4f7fa";
const DIM = "#76828e";
const LINE = "#1a1f2c";
const SURFACE = "#0f1219";
const GREEN = "#3fb950";
const RED = "#f85149";
const CYAN = "#39c5cf";

const INDICATOR_CHIPS = ["sma20", "sma50", "bb20", "rsi14"] as const;

export function ProChart({
  symbol,
  height = 420,
  defaultTimeframe = "1h",
  compact = false,
}: {
  symbol: string;
  height?: number;
  defaultTimeframe?: Timeframe;
  compact?: boolean;
}) {
  const boxRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const overlayRefs = useRef<ISeriesApi<"Line">[]>([]);
  const rsiPaneRef = useRef<ISeriesApi<"Line"> | null>(null);

  const [tf, setTf] = useState<Timeframe>(defaultTimeframe);
  const [active, setActive] = useState<string[]>(["sma20", "sma50", "rsi14"]);
  const [data, setData] = useState<ChartPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ---- fetch (refetch on symbol / timeframe / indicator change)
  useEffect(() => {
    let dead = false;
    setLoading(true);
    setError(null);
    const params = new URLSearchParams({ symbol, timeframe: tf, indicators: active.join(",") });
    fetch(`/api/desk/chart?${params.toString()}`)
      .then((r) => r.json())
      .then((j: ChartPayload) => { if (!dead) setData(j); })
      .catch((e) => { if (!dead) setError(String(e)); })
      .finally(() => { if (!dead) setLoading(false); });
    return () => { dead = true; };
  }, [symbol, tf, active.join(",")]);

  // ---- build / update chart
  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    if (!chartRef.current) {
      const chart = createChart(box, {
        width: box.clientWidth,
        height,
        layout: {
          background: { color: SURFACE },
          textColor: DIM,
          fontFamily: "var(--font-geist-mono), monospace",
          fontSize: 10,
          panes: { separatorColor: LINE, separatorHoverColor: GOLD },
        },
        grid: { vertLines: { color: LINE }, horzLines: { color: LINE } },
        rightPriceScale: { borderColor: LINE },
        timeScale: { borderColor: LINE, timeVisible: true, secondsVisible: false },
        crosshair: {
          vertLine: { color: GOLD, labelBackgroundColor: GOLD },
          horzLine: { color: GOLD, labelBackgroundColor: GOLD },
        },
      });
      chartRef.current = chart;
      candleRef.current = chart.addSeries(CandlestickSeries, {
        upColor: GREEN, downColor: RED, borderVisible: false,
        wickUpColor: GREEN, wickDownColor: RED,
      });
      volRef.current = chart.addSeries(HistogramSeries, {
        priceFormat: { type: "volume" }, priceScaleId: "vol",
        color: "#1f2937",
      });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });

      const ro = new ResizeObserver(() => {
        if (boxRef.current && chartRef.current)
          chartRef.current.applyOptions({ width: boxRef.current.clientWidth });
      });
      ro.observe(box);
      return () => { ro.disconnect(); };
    }
  }, [height]);

  // ---- push data into series
  useEffect(() => {
    const chart = chartRef.current;
    const candle = candleRef.current;
    const vol = volRef.current;
    if (!chart || !candle || !vol) return;
    if (!data?.ok || !data.bars?.length) return;

    const bars = data.bars;
    candle.setData(bars.map((b) => ({
      time: (b.t / 1000) as UTCTimestamp,
      open: b.o, high: b.h, low: b.l, close: b.c,
    })));
    vol.setData(bars.map((b) => ({
      time: (b.t / 1000) as UTCTimestamp,
      value: b.v || 0,
      color: b.c >= b.o ? "rgba(63,185,80,0.35)" : "rgba(248,81,73,0.35)",
    })));

    // overlays: drop old line series, rebuild from the payload
    for (const s of overlayRefs.current) { chart.removeSeries(s); }
    overlayRefs.current = [];
    if (rsiPaneRef.current) { chart.removeSeries(rsiPaneRef.current); rsiPaneRef.current = null; }

    for (const ser of data.indicator_series || []) {
      if (ser.kind === "line" && ser.name.startsWith("RSI")) {
        const s = chart.addSeries(LineSeries, {
          color: CYAN, lineWidth: 1, priceLineVisible: false, lastValueVisible: false,
        }, 1); // pane 1 — RSI oscillator under the candles
        s.setData(ser.points.filter((p) => typeof p.v === "number").map((p) => ({ time: (p.t / 1000) as UTCTimestamp, value: p.v as number })));
        rsiPaneRef.current = s;
        continue;
      }
      if (ser.kind === "band") {
        for (const [key, color] of [["hi", "rgba(200,160,75,0.7)"], ["lo", "rgba(200,160,75,0.7)"]] as const) {
          const s = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, lineStyle: 2 });
          s.setData(ser.points.filter((p) => typeof p[key] === "number").map((p) => ({ time: (p.t / 1000) as UTCTimestamp, value: p[key] as number })));
          overlayRefs.current.push(s);
        }
        continue;
      }
      const colors: Record<string, string> = { SMA20: GOLD, SMA50: "#8b6fb8", EMA20: CYAN, EMA50: "#d97757" };
      const s = chart.addSeries(LineSeries, {
        color: colors[ser.name] || GOLD, lineWidth: 1,
        priceLineVisible: false, lastValueVisible: false,
      });
      s.setData(ser.points.filter((p) => typeof p.v === "number").map((p) => ({ time: (p.t / 1000) as UTCTimestamp, value: p.v as number })));
      overlayRefs.current.push(s);
    }
    chart.timeScale().fitContent();
  }, [data]);

  const last = useMemo(() => data?.bars?.[data.bars.length - 1] ?? null, [data]);
  const delayLabel = data?.data_delay || "";
  const delayBadge = Boolean(delayLabel) && delayLabel !== "real-time";

  return (
    <div className="gdc-panel" style={{ padding: 0, overflow: "hidden" }}>
      {/* toolbar */}
      <div className="flex items-center gap-2 px-3 py-2 border-b" style={{ borderColor: LINE }}>
        <span className="gdc-kicker" style={{ color: GOLD }}>PRO CHART</span>
        <span className="gdc-data text-[11px]" style={{ color: INK }}>
          {data?.meta?.symbol || symbol}
          {last ? ` · ${last.c.toLocaleString()}` : ""}
        </span>
        <div className="flex gap-1 ml-auto">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className="gdc-chip text-[10px] px-2 py-0.5"
              style={{
                border: `1px solid ${tf === t ? GOLD : LINE}`,
                color: tf === t ? GOLD : DIM,
                background: "transparent",
                cursor: "pointer",
              }}
              title={`switch to ${t} bars`}
            >
              {t.toUpperCase()}
            </button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-1.5 px-3 py-1.5 border-b" style={{ borderColor: LINE }}>
        {INDICATOR_CHIPS.map((ind) => {
          const on = active.includes(ind);
          return (
            <button
              key={ind}
              onClick={() => setActive((a) => on ? a.filter((x) => x !== ind) : [...a, ind])}
              className="gdc-chip text-[10px] px-2 py-0.5 uppercase"
              style={{
                border: `1px solid ${on ? GOLD : LINE}`,
                color: on ? GOLD : DIM,
                background: "transparent",
                cursor: "pointer",
              }}
              title={`${on ? "remove" : "add"} ${ind.toUpperCase()} overlay`}
            >
              {ind.toUpperCase()}
            </button>
          );
        })}
        {delayBadge && (
          <span className="gdc-chip text-[9px] px-2 py-0.5 ml-auto" style={{ border: `1px solid ${LINE}`, color: DIM }}
            title={`data is not real-time: ${delayLabel} (honest label)`}>
            {delayLabel.toUpperCase()}
          </span>
        )}
        {data?.stale_error && (
          <span className="gdc-chip text-[9px] px-2 py-0.5 ml-auto" style={{ border: "1px solid #f85149", color: RED }}
            title={data.stale_reason}>
            STALE
          </span>
        )}
      </div>

      {/* chart body */}
      <div style={{ position: "relative", height }}>
        <div ref={boxRef} style={{ height: "100%" }} />
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center gdc-data text-[11px]" style={{ color: DIM, background: "rgba(11,14,20,0.55)" }}>
            LOADING BARS…
          </div>
        )}
        {!loading && error && (
          <div className="absolute inset-0 flex items-center justify-center gdc-data text-[11px] text-center px-6" style={{ color: RED, background: "rgba(11,14,20,0.7)" }}>
            chart feed failed: {error}
          </div>
        )}
        {!loading && !error && !data?.ok && (
          <div className="absolute inset-0 flex items-center justify-center gdc-data text-[11px] text-center px-6" style={{ color: DIM, background: "rgba(11,14,20,0.7)" }}>
            {(data?.errors || ["no bars served (fail-soft)"]).join(" · ")}
          </div>
        )}
      </div>
      {!compact && (
        <div className="flex items-center gap-3 px-3 py-1.5 border-t" style={{ borderColor: LINE }}>
          <span className="gdc-kicker" style={{ color: DIM }}>SOURCE</span>
          <span className="gdc-data text-[10px]" style={{ color: DIM }}>
            yahoo v8/chart · keyless · {data?.bars?.length ?? 0} bars · {tf}
          </span>
        </div>
      )}
    </div>
  );
}
