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
