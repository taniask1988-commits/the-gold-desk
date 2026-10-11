import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



const RANGE_KEYS = ["1mo", "3mo", "6mo", "1y", "2y"] as const;

/**
 * R3-2 Build 4 — GUESS setup backtest vs keyless GC=F 1h bars:
 *   GET /api/desk/backtest?bars=1y&seed=7
 *       → {ok, symbol, range, setup_id, n_bars, first_bar, last_bar,
 *          equity_start, equity_end, total_return, sharpe, sortino,
 *          max_drawdown, calmar, buy_hold_return, n_trades, hit_rate,
 *          profit_factor, avg_win, avg_loss, n_days,
 *          equity_curve: number[] (LAST 100 POINTS for payload size),
 *          equity_curve_full_length, trades[], equity_curve_sha256}
 * The CLI returns the full bar-by-bar curve + journal; this route keeps the
 * stats/trades verbatim, truncates the curve to its last 100 points and
 * drops the raw journal text (the sha256 pins reproducibility).
 * Deterministic: seed-pinned, no wall-clock in the result.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const barsParam = params.get("bars") || "1y";
  const bars = (RANGE_KEYS as readonly string[]).includes(barsParam) ? barsParam : "1y";
  const seed = parseInt(params.get("seed") || "7", 10);
  const cliArgs = ["-m", "gold_desk.cli", "backtest", "--bars", bars,
                   "--seed", String(Number.isFinite(seed) ? seed : 7),
                   "--json", "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 120_000, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable backtest output" }); }
      },
    );
  });
  // payload hygiene: last 100 equity points, no journal text
  if (result && Array.isArray(result.equity_curve)) {
    const curve = result.equity_curve as number[];
    result.equity_curve_full_length = curve.length;
    result.equity_curve = curve.slice(-100);
  }
  if (result && typeof result.journal === "string") {
    delete result.journal;
  }
  return NextResponse.json(result);
}
