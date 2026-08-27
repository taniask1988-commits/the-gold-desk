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

const METHODS = ["mv", "rp", "hrp"] as const;

/**
 * R3-3 Build 5a — portfolio construction:
 *   GET /api/desk/portfolio?method=mv|rp|hrp&lookback=90d
 *       → {ok, method, symbols, weights: {sym: w}, portfolio_vol,
 *          risk_contributions: {sym: rc}, diversification_ratio,
 *          expected_returns, volatilities, n_observations, source,
 *          method-specific extras (mv: lambda_risk/max_weight/n_candidates/
 *          seed/objective; rp: iterations/converged/tol; hrp:
 *          quasi_diagonal_order/merges)}
 * Default book: SPY / GC=F / BTC-USD from 1y keyless Yahoo daily bars,
 * DATE-ALIGNED across calendars, tail-truncated to `lookback` return
 * observations (default 90d). MV search is seed-pinned → deterministic.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const methodParam = params.get("method") || "mv";
  const method = (METHODS as readonly string[]).includes(methodParam) ? methodParam : "mv";
  const lookback = params.get("lookback") || "90d";
  const cliArgs = ["-m", "gold_desk.cli", "portfolio", "--method", method,
                   "--lookback", lookback, "--json",
                   "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 90_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable portfolio output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
