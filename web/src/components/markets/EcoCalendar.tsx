"use client";

import { memo, useCallback, useEffect, useMemo, useState } from "react";
import { TerminalModal } from "./Modal";

/* -------------------------------------------------------------- types */

interface EcoEvent {
  ts: number;
  country: string;
  impact: "high" | "medium" | "low" | string;
  title: string;
  forecast?: string;
  previous?: string;
}

interface EcoPayload {
  ok: boolean;
  source?: "live" | "static" | string;
  as_of?: string;
  week_start?: string;
  events?: EcoEvent[];
  note?: string;
  error?: string;
}

/* -------------------------------------------------------------- bits */

const IMPACT_COLOR: Record<string, string> = {
  high: "#B85C5C",
  medium: "#E2C074",
  low: "#8A93A6",
};

function ImpactDot({ impact }: { impact: string }) {
  return (
    <span
      className="inline-block h-[7px] w-[7px] shrink-0 rounded-full"
      style={{ background: IMPACT_COLOR[impact] ?? IMPACT_COLOR.low }}
      title={`impact: ${impact}`}
      aria-label={`impact ${impact}`}
    />
  );
}

const DAY_FMT = new Intl.DateTimeFormat("en-GB", {
  weekday: "long",
  day: "2-digit",
  month: "short",
  timeZone: "UTC",
});
const TIME_FMT = new Intl.DateTimeFormat("en-GB", {
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
  timeZone: "UTC",
});

/** One event row: dot · time · country chip · title · fcst/prev. */
const EcoRow = memo(function EcoRow({
  ev,
  past,
}: {
  ev: EcoEvent;
  past: boolean;
}) {
  const t = new Date(ev.ts);
  return (
    <li
      className="flex items-center gap-2.5 border-b border-[#141821] px-1 py-[7px] last:border-b-0"
      style={{ opacity: past ? 0.45 : 1 }}
      title={`${ev.country} · ${ev.title}${
        ev.forecast ? ` · forecast ${ev.forecast}` : ""
      }${ev.previous ? ` · previous ${ev.previous}` : ""}`}
    >
      <ImpactDot impact={ev.impact} />
      <span className="gdc-data w-[44px] shrink-0 text-[10.5px] tabular-nums text-[#aab4bf]">
        {TIME_FMT.format(t)}
      </span>
      <span className="gdc-data w-[38px] shrink-0 rounded-sm border border-[#1a1f2c] px-1 py-[1px] text-center text-[9px] font-semibold tracking-[0.08em] text-[#e2c074]">
        {ev.country || "??"}
      </span>
      <span className="min-w-0 flex-1 truncate text-[11.5px] leading-snug text-[#c6cedb]">
        {ev.title}
      </span>
      {ev.forecast ? (
        <span className="gdc-data hidden shrink-0 text-[10px] tabular-nums text-[#6fa97a] sm:inline">
          fcst {ev.forecast}
        </span>
      ) : null}
      {ev.previous ? (
        <span className="gdc-data hidden shrink-0 text-[10px] tabular-nums text-[#8a93a6] sm:inline">
          prev {ev.previous}
        </span>
      ) : null}
    </li>
  );
});

/* -------------------------------------------------------------- modal */

/**
 * ECO — the economic calendar modal (Bloomberg ECO analog, piece 5).
 * This week's releases grouped by day, impact-colored dots (high red /
 * medium amber / low dim), country codes as ISO chips, past events
 * dimmed. Data from GET /api/desk/eco (live ForexFactory mirror with
 * a static date-math fallback — the modal works either way, the
 * source badge says which).
 */
function EcoCalendarImpl({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [data, setData] = useState<EcoPayload | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch("/api/desk/eco", { cache: "no-store" });
      setData((await r.json()) as EcoPayload);
    } catch {
      setData({ ok: false, error: "network error" });
    } finally {
      setLoading(false);
    }
  }, []);

  // fetch every time the modal opens (30min python-side cache keeps
  // this cheap; the modal itself stays fresh within a session)
  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  const now = Date.now();
  // events grouped by UTC day, days in order — memoized per payload
  const days = useMemo(() => {
    const events = data?.events ?? [];
    const map = new Map<string, EcoEvent[]>();
    for (const ev of events) {
      if (typeof ev.ts !== "number") continue;
      const key = new Date(ev.ts).toISOString().slice(0, 10);
      const list = map.get(key);
      if (list) list.push(ev);
      else map.set(key, [ev]);
    }
    return [...map.entries()];
  }, [data]);

  const isStatic = data?.source === "static";

  return (
    <TerminalModal
      open={open}
      onClose={onClose}
      title="Economic Calendar — This Week"
      subtitle={data?.week_start ? `week of ${data.week_start}` : "ECO"}
      label="Economic calendar"
      width="max-w-[760px]"
      badge={
        isStatic ? (
          <span
            className="rounded-sm border border-[#d29922]/40 px-1.5 py-[2px] text-[8.5px] font-semibold uppercase tracking-[0.12em] text-[#d29922]"
            title={data?.note}
          >
            static schedule (live feed unreachable)
          </span>
        ) : data?.source === "live" ? (
          <span className="rounded-sm border border-[#6fa97a]/40 px-1.5 py-[2px] text-[8.5px] font-semibold uppercase tracking-[0.12em] text-[#6fa97a]">
            live feed
          </span>
        ) : null
      }
    >
      {loading && !data && (
        <div className="flex items-center justify-center gap-3 px-2 py-8">
          <span className="gdc-spec">Loading calendar</span>
          <span className="gdc-breathe h-[2px] w-[90px] rounded-full bg-[#c8a04b]" />
        </div>
      )}
      {data && !data.ok && (
        <div className="rounded-sm border border-[#B85C5C]/30 bg-[#B85C5C]/[0.06] px-3 py-2 text-[11px] text-[#D98484]">
          ⚠ {data.error || "calendar unreachable"}
        </div>
      )}
      {data?.ok && days.length === 0 && (
        <div className="px-2 py-6 text-center text-[11px] uppercase tracking-[0.14em] text-[#8a93a6]">
          no events served this week
        </div>
      )}
      {data?.ok &&
        days.map(([day, events]) => (
          <div key={day} className="mb-3">
            <div className="mb-1 flex items-center gap-2.5">
              <span className="gdc-data text-[10px] font-semibold uppercase tracking-[0.16em] text-[#c8a04b]">
                {DAY_FMT.format(new Date(day + "T00:00:00Z"))}
              </span>
              <span className="h-px flex-1 bg-[#1a1f2c]" />
              <span className="gdc-data text-[9px] tabular-nums text-[#6f7987]">
                {events.length} events
              </span>
            </div>
            <ul>
              {events.map((ev, i) => (
                <EcoRow key={`${ev.ts}-${i}`} ev={ev} past={ev.ts < now} />
              ))}
            </ul>
          </div>
        ))}
      {data?.ok && (
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-[#1a1f2c] pt-2 text-[9px] uppercase tracking-[0.14em] text-[#6f7987]">
          <span className="flex items-center gap-1.5">
            <ImpactDot impact="high" /> high
          </span>
          <span className="flex items-center gap-1.5">
            <ImpactDot impact="medium" /> medium
          </span>
          <span className="flex items-center gap-1.5">
            <ImpactDot impact="low" /> low
          </span>
          <span className="ml-auto">times UTC · keyless feeds</span>
        </div>
      )}
    </TerminalModal>
  );
}

export const EcoCalendar = memo(EcoCalendarImpl);
