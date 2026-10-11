import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-3 Build 5b — P&L attribution:
 *   GET /api/desk/pnl/attribution?source=journal|ledger
 *       → {ok, source, n_trades, n_wins, n_losses, win_rate,
 *          total_pnl, gross_profit, gross_loss, profit_factor,
 *          by_asset: [{symbol, pnl, pct_of_total, n_trades, win_rate}],
 *          by_setup: [{setup, pnl, n_trades, win_rate}],
 *          by_hour: 24 × {hour, session: Asia|London|NY, pnl, n_trades},
 *          reconstruction?: {matched, n_entry_fills, n_exit_fills,
 *                            open_or_unmatched, unmatched_exits}}
 * source=journal (default) reconstructs closed trades from the desk's
 * data/events/*.jsonl (TicketEvent + Fill events joined by ticket_id);
 * source=ledger scores the deterministic synthetic demo ledger when no
 * ledger file is configured. Conservation is structural: Σ by_asset ==
 * Σ by_setup == Σ by_hour == total.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const sourceParam = params.get("source") || "journal";
  const source = sourceParam === "ledger" ? "ledger" : "journal";
  const cliArgs = ["-m", "gold_desk.cli", "pnl", "--source", source,
                   "--json", "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 60_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable pnl output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
