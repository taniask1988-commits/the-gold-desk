import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-2 Build 4 — risk report:
 *   GET /api/desk/risk
 *       → {ok, portfolio, n_observations, mean, stdev,
 *          var: {parametric|historical|monte_carlo × 95|99},
 *          expected_shortfall: {historical_95, historical_99},
 *          beta?: {beta, alpha, correlation, r_squared, n},
 *          stress: {scenarios: [gfc_2008, covid_2020, rate_shock_2022]}}
 * Default portfolio: 40% SPY / 30% GC=F / 15% BTC-USD / 15% cash from 1y
 * keyless Yahoo daily bars, DATE-ALIGNED across calendars; benchmark = SPY.
 * Pass ?returns=[...] to score an explicit JSON series offline (URL-encoded
 * JSON list of floats; optional &benchmark=[...] and &positions=[...]).
 * R4-3: ?replay=1 adds stress_replay — the REAL historical daily-return
 * paths (2008-H2 / 2020-Mar / 2022) applied to the book (cumulative /
 * worst day / MaxDD + equity path); ?replay=1&fast=1 serves the static
 * vectors without the network.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const cliArgs = ["-m", "gold_desk.cli", "risk", "--json"];
  const returns = params.get("returns");
  if (returns) {
    cliArgs.push("--returns", returns);
    const bench = params.get("benchmark");
    if (bench) cliArgs.push("--benchmark-returns", bench);
    const positions = params.get("positions");
    if (positions) cliArgs.push("--positions", positions);
  }
  if (params.get("replay") === "1") {
    cliArgs.push("--stress-replay");
    if (params.get("fast") === "1") cliArgs.push("--fast");
  }
  cliArgs.push("--data-root", path.join(HARNESS, "data"));
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 90_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable risk output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
