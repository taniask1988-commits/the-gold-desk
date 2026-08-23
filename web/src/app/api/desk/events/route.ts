import { NextRequest, NextResponse } from "next/server";
import { readEvents, listDays } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const date = req.nextUrl.searchParams.get("date");
    const limit = Number(req.nextUrl.searchParams.get("limit") ?? 800);
    const days = await listDays();
    const target = date || days[days.length - 1];
    const events = await readEvents(target);
    const sorted = [...events].sort((a, b) =>
      (a.decision_ts ?? a.ts).localeCompare(b.decision_ts ?? b.ts),
    );
    return NextResponse.json({
      ok: true,
      date: target,
      count: sorted.length,
      events: sorted.slice(-limit),
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
