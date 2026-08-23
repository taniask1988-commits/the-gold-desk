import { NextRequest, NextResponse } from "next/server";
import { readAllEvents, barsFromEvents } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const limit = Number(req.nextUrl.searchParams.get("limit") ?? 1200);
    const events = await readAllEvents();
    const bars = barsFromEvents(events).slice(-limit);
    let prevClose: number | null = null;
    const enriched = bars.map((b) => {
      const change = prevClose === null ? 0 : b.c - prevClose;
      prevClose = b.c;
      return { ...b, change: Math.round(change * 100) / 100 };
    });
    const last = enriched[enriched.length - 1] ?? null;
    const dayOpen =
      enriched.find((b) => b.ts_close.slice(0, 10) === (last?.ts_close.slice(0, 10) ?? ""))?.o ??
      last?.o ??
      0;
    return NextResponse.json({
      ok: true,
      count: enriched.length,
      last,
      dayChange: last ? Math.round((last.c - dayOpen) * 100) / 100 : 0,
      bars: enriched,
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
