import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-1 Build 1 — cross-asset correlation matrix:
 *   GET /api/desk/markets/correlation?window=30d&method=pearson
 *       → {ok, degraded, window, method, symbols, matrix, n_points, errors}
 * Python CLI is the single source of truth (cli markets-multi-corr
 * --json --window N --method {pearson|spearman}).
 * D3: `errors` lists per-symbol failures ({"symbol", "reason":
 * "daily_closes_fetch_failed"}) and insufficient-overlap pairs
 * ({"symbol", "pair", "reason": "insufficient_common_dates"}) — those
 * cells are null; `degraded` is true whenever any symbol/pair failed.
 */
function parseWindow(s: string | null): number | null {
  if (!s) return null;
  const m = s.match(/^(\d+)(d|D)?$/);
  if (!m) return null;
  return parseInt(m[1], 10);
}

export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const windowP = parseWindow(params.get("window"));
  const method = params.get("method") === "spearman" ? "spearman" : "pearson";
  const cliArgs = ["-m", "gold_desk.cli", "markets-multi-corr", "--json",
                   "--method", method];
  if (windowP) cliArgs.push("--window", String(windowP));
  cliArgs.push("--data-root", path.join(HARNESS, "data"));
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable correlation output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
