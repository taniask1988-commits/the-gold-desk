"use client";

import { useMemo, useState } from "react";
import { useDeskData } from "./useDeskData";
import { HeaderBar } from "./HeaderBar";
import { TickerStrip } from "./TickerStrip";
import { PriceChart } from "./PriceChart";
import { DriverBoard } from "./DriverBoard";
import { FeedTerminal } from "./FeedTerminal";
import { ReasonHistogram } from "./ReasonHistogram";
import { TicketBoard } from "./TicketBoard";
import { ReplayPanel } from "./ReplayPanel";
import { ConstitutionPanel, LawsMarquee } from "./ConstitutionPanel";
import { ZenPanel } from "./ZenPanel";
import { NewsPanel } from "./NewsPanel";
import AgentPanel from "./AgentPanel";

export function CommandDeck() {
  const { overview, bars, tickets, drivers, driverValues, composite, replay, dayEvents, day, setDay, error } =
    useDeskData();
  const [live, setLive] = useState(true);
  const [speed, setSpeed] = useState(4);
  const [scope, setScope] = useState<"DAY" | "ALL">("DAY");

  // live price = close of the newest fully-revealed bar in the wire
  const livePrice = useMemo(() => {
    if (!live || dayEvents.length === 0) return null;
    for (let i = dayEvents.length - 1; i >= 0; i--) {
      const bar = dayEvents[i].payload?.bar;
      if (bar) return bar.c;
    }
    return null;
  }, [dayEvents, live]);

  if (error) {
    return (
      <div className="gdc-root flex min-h-screen items-center justify-center">
        <div className="gdc-panel p-6 text-sm text-[#f85149]">DESK LINK ERROR: {error}</div>
      </div>
    );
  }

  return (
    <div className="gdc-root flex min-h-screen flex-col">
      <div className="flex min-h-screen flex-col">
        <HeaderBar
          phase={1}
          demo={overview?.demo ?? false}
          hash={overview?.constitutionHash ?? null}
          composite={composite}
          spanDays={overview?.span.days ?? 0}
        />
        <TickerStrip bars={bars} overview={overview} livePrice={livePrice} />

        <main className="mx-auto w-full max-w-[1600px] flex-1 space-y-5 px-4 py-5 sm:px-6">
          {/* Row 1: price + histogram */}
          <div className="grid gap-4 lg:grid-cols-[1.6fr_1fr]">
            <PriceChart bars={bars} replayBars={replay?.bars} />
            <ReasonHistogram
              dayHist={replay?.histogram ?? {}}
              allHist={overview?.histogram ?? {}}
              scope={scope}
              setScope={setScope}
            />
          </div>

          {/* Row 2: drivers + wire */}
          <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
            <DriverBoard drivers={drivers} driverValues={driverValues} />
            <FeedTerminal
              key={day}
              events={dayEvents}
              day={day}
              live={live}
              setLive={setLive}
              speed={speed}
              setSpeed={setSpeed}
            />
          </div>

          {/* Row 2.5: the tape (live news) */}
          <NewsPanel />

          {/* Row 3: tickets + replay */}
          <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
            <TicketBoard tickets={tickets} />
            <ReplayPanel replay={replay} days={overview?.days ?? []} day={day} setDay={setDay} />
          </div>

          {/* Row 3.5: OpenCode Zen free models + veto research bench */}
          <ZenPanel />

          {/* Row 3.6: research sidecar — agent reports + audit trail */}
          <AgentPanel />

          {/* Row 4: constitution */}
          <ConstitutionPanel
            hash={overview?.constitutionHash ?? null}
            blockedCount={overview?.constitution?.blockedCount ?? null}
            phase={overview?.constitution?.phase ?? null}
          />
        </main>

        <footer className="mt-auto">
          <LawsMarquee />
          <div className="gdc-spec-tight border-t border-[#1a1f2c] bg-[#0f1219] px-4 py-3 text-center">
            GOLD DESK COMMAND v1 · READ-ONLY TELEMETRY OVER AN APPEND-ONLY JOURNAL ·
            USUALLY DO NOTHING · EVERYTHING IS JOURNALED · NOTHING IS PROMOTED BY NARRATIVE
          </div>
        </footer>
      </div>
    </div>
  );
}
