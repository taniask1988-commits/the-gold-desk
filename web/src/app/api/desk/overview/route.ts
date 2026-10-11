import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";
import {
  readAllEvents,
  readTickets,
  readAccount,
  listDays,
  reasonHistogram,
} from "@/lib/desk/journal";

export const dynamic = "force-dynamic";



/** Fetch the real constitution summary (BLOCKED count, phase, trade_capable)
 * from the harness via `cli constitution --json`. Returns null if the
 * harness is unreachable — the UI falls back to a loading/unknown state. */
function fetchConstitution(harness: string, py: string): Promise<{
  blockedCount: number;
  phase: number;
  tradeCapable: boolean;
  contentHash: string;
  summaryLine: string;
} | null> {
  return new Promise((resolve) => {
    execFile(
      py,
      ["-m", "gold_desk.cli", "constitution", "--json"],
      { cwd: harness, env: { ...process.env, PYTHONPATH: path.join(harness, "src") }, timeout: 10_000, maxBuffer: 1 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve(null); return; }
        try {
          const d = JSON.parse(stdout.trim().split("\n").pop() || "{}");
          resolve({
            blockedCount: d.blocked_count ?? 0,
            phase: d.phase ?? 1,
            tradeCapable: !!d.trade_capable,
            contentHash: d.content_hash ?? "",
            summaryLine: d.summary_line ?? "",
          });
        } catch {
          resolve(null);
        }
      },
    );
  });
}

export async function GET() {
  try {
    const [events, tickets, account, days] = await Promise.all([
      readAllEvents(),
      readTickets(),
      readAccount(),
      listDays(),
    ]);
    const HARNESS = resolveHarness();
    const { py: PY, err: _pyErr } = resolvePython(HARNESS);
    const constitution = _pyErr ? null : await fetchConstitution(HARNESS, PY);
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
      // D-005 — the constitution hash comes from the CONSTITUTION LOADER
      // (`cli constitution --json`, the same content hash demo/validate
      // journal), NEVER from the last event: the agent sidecar's events
      // used to poison this field with a provenance marker.
      constitutionHash: constitution
        ? constitution.contentHash.slice(0, 16) || null
        : null,
      histogram: reasonHistogram(events),
      featuredDay,
      // M4: real constitution summary — null when the harness is unreachable
      // (the UI shows a loading/fallback state in that case, NOT a hardcoded
      // "30 BLOCKED phase=1").
      constitution: constitution
        ? {
            blockedCount: constitution.blockedCount,
            phase: constitution.phase,
            tradeCapable: constitution.tradeCapable,
            contentHash: constitution.contentHash.slice(0, 16) || null,
            summaryLine: constitution.summaryLine,
          }
        : null,
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
