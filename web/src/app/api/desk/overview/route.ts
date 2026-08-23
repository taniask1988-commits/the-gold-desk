import { NextResponse } from "next/server";
import {
  readAllEvents,
  readTickets,
  readAccount,
  listDays,
  reasonHistogram,
} from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [events, tickets, account, days] = await Promise.all([
      readAllEvents(),
      readTickets(),
      readAccount(),
      listDays(),
    ]);
    const bars = events.filter((e) => e.kind === "BarReceived").length;
    const last = events[events.length - 1];
    const demo = events.some((e) => e.payload?.demo === true);
    const ticketDays = Array.from(
      new Set(
        events
          .filter((e) => e.kind === "TicketEvent")
          .map((e) => (e.decision_ts ?? e.ts).slice(0, 10)),
      ),
    ).sort();
    const featuredDay = ticketDays.length ? ticketDays[ticketDays.length - 1] : days[days.length - 1] ?? null;
    const blackouts = events.filter(
      (e) => (e as { reason_code?: string }).reason_code === "NEWS_BLACKOUT",
    ).length;
    return NextResponse.json({
      ok: true,
      span: { from: days[0] ?? null, to: days[days.length - 1] ?? null, days: days.length },
      totalEvents: events.length,
      barsProcessed: bars,
      ticketsIssued: tickets.length,
      fills: tickets.filter((t) => t.status === "FILL").length,
      blackouts,
      demo,
      constitutionHash: last?.constitution_hash?.slice(0, 16) ?? null,
      histogram: reasonHistogram(events),
      featuredDay,
      account: account
        ? {
            balance: account.balance,
            equity: account.equity,
            highWater: account.high_water,
            dailyPnl: account.daily_pnl,
            closedTrades: account.closed_trades?.length ?? 0,
            wins: (account.closed_trades ?? []).filter((t) => t.pnl > 0).length,
            losses: (account.closed_trades ?? []).filter((t) => t.pnl <= 0).length,
            equityCurve: (account.closed_trades ?? []).reduce<
              Array<{ ts: string; equity: number }>
            >((acc, t) => {
              const prev = acc.length ? acc[acc.length - 1].equity : 10000;
              acc.push({ ts: t.closed_ts, equity: prev + t.pnl });
              return acc;
            }, []),
          }
        : null,
      days,
    });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
