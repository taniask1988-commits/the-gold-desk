import { NextResponse } from "next/server";
import { readTickets, readAllEvents, DeskEvent } from "@/lib/desk/journal";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const [tickets, events] = await Promise.all([readTickets(), readAllEvents()]);
    const byId = new Map<string, DeskEvent[]>();
    for (const e of events) {
      const tid = (e.payload as { ticket_id?: string } | undefined)?.ticket_id;
      if (tid) {
        if (!byId.has(tid)) byId.set(tid, []);
        byId.get(tid)!.push(e);
      }
    }
    const rich = tickets.map((t) => {
      const evs = (byId.get(t.ticket_id) ?? []).sort((a, b) =>
        a.ts.localeCompare(b.ts),
      );
      const outcome = evs.find(
        (e) =>
          e.kind === "Fill" &&
          (e.payload as { status?: string }).status !== "paper-position-opened",
      );
      const rr = Math.abs(t.target - t.entry) / Math.max(1e-9, Math.abs(t.entry - t.stop));
      return {
        ...t,
        lifecycle: evs.map((e) => ({
          ts: e.ts,
          kind: e.kind,
          reason: (e as { reason_code?: string | null }).reason_code ?? null,
          channel: (e.payload as { channel?: string }).channel ?? null,
        })),
        pnl: outcome ? (outcome.payload as { resolution?: { pnl?: number } }).resolution?.pnl ?? null : null,
        exitReason:
          outcome
            ? (outcome.payload as { resolution?: { reason?: string } }).resolution?.reason ?? null
            : null,
        rr: Math.round(rr * 100) / 100,
        // H2: use the risk_money persisted at gate time (ticket.risk_money),
        // not a recompute with the ×100 magic number — the live constitution
        // may have a non-100 point value per lot.
        riskMoney: typeof t.risk_money === "number" && t.risk_money > 0
          ? Math.round(t.risk_money * 100) / 100
          : Math.round(t.lots * Math.abs(t.entry - t.stop) * 100 * 100) / 100,
      };
    });
    return NextResponse.json({ ok: true, tickets: rich });
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
