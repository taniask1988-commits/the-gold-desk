import { NextRequest, NextResponse } from "next/server";
import { simulateDrivers, compositeBias } from "@/lib/desk/drivers";
import { listDays } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET(req: NextRequest) {
  try {
    const tick = Number(req.nextUrl.searchParams.get("tick") ?? 0);
    const days = await listDays();
    const day = req.nextUrl.searchParams.get("day") || days[days.length - 1] || "1970-01-01";
    const drivers = simulateDrivers(day, tick);
    return NextResponse.json({
      ok: true,
      simulated: true,
      day,
      tick,
      composite: compositeBias(drivers),
      drivers,
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
