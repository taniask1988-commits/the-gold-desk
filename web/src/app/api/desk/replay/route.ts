import { NextRequest, NextResponse } from "next/server";
import { readEvents, listDays, DeskEvent } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

interface ReplayBar {
  decisionTs: string;
  o: number; h: number; l: number; c: number;
  code: string | null;
  story: Array<{ kind: string; code: string | null; detail: string | null }>;
}

export async function GET(req: NextRequest) {
  try {
    const days = await listDays();
    const date = req.nextUrl.searchParams.get("date") || days[days.length - 1];
    const events = (await readEvents(date)).sort((a, b) =>
      (a.decision_ts ?? a.ts).localeCompare(b.decision_ts ?? b.ts),
    );
    const bars: ReplayBar[] = [];
    for (const e of events) {
      if (e.kind === "BarReceived" && e.payload?.bar) {
        bars.push({
          decisionTs: e.decision_ts ?? e.ts,
          ...e.payload.bar,
          code: null,
          story: [],
        });
      }
    }
    for (const e of events) {
      const rc = (e as { reason_code?: string | null }).reason_code;
      const dts = e.decision_ts;
      if (!rc || !dts) continue;
      const bar = bars.find((b) => b.decisionTs === dts);
      if (bar) {
        bar.code = rc;
        const p = e.payload as Record<string, unknown>;
        bar.story.push({
          kind: e.kind,
          code: rc,
          detail:
            (p.detail as string) ||
            (p.code as string) ||
            (p.action as string) ||
            null,
        });
      }
    }
    const tickets = events
      .filter((e) => e.kind === "TicketEvent")
      .map((e) => e.payload as Record<string, unknown>);
    const hist: Record<string, number> = {};
    for (const b of bars) if (b.code) hist[b.code] = (hist[b.code] ?? 0) + 1;
    return NextResponse.json({
      ok: true,
      date,
      bars,
      histogram: hist,
      tickets,
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
