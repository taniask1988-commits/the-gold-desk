import { NextResponse } from "next/server";
import { execFile, execFileSync } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";

function resolveHarness(): string {
  const candidates = [
    process.env.GOLD_DESK_ROOT,
    path.resolve(process.cwd(), ".."),
    "/home/z/my-project/download/gold_desk_v1",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (existsSync(path.join(c, "src", "gold_desk", "cli.py"))) return c;
  }
  return candidates[candidates.length - 1];
}

function resolvePython(harness: string): { py: string; err: string | null } {
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    "/home/z/.venv/bin/python3",
    path.join(harness, ".venv", "bin", "python3"),
    "python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (c !== "python3" && !existsSync(c)) continue;
    try {
      execFileSync(c, ["-c", "import yaml"], { stdio: "ignore", timeout: 5_000 });
      return { py: c, err: null };
    } catch {
      /* probe failed — try the next candidate */
    }
  }
  return { py: "", err: "no python candidate has PyYAML installed (set GOLD_DESK_PYTHON or run: pip install pyyaml)" };
}

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
