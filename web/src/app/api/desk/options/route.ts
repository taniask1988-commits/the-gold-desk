import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * GAUNTLET 7 Piece 2 — options analytics (Greeks, IV surface, max pain,
 * PCR, OI wall) computed keyless in Python. Options math is deterministic;
 * chain data is fail-soft + stale-serve (honest errors[]).
 * GET /api/desk/options?symbol=GLD
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const symbol = sp.get("symbol")?.trim() || "GLD";
  const provider = sp.get("provider")?.trim() || "auto";
  const cliArgs = ["-m", "gold_desk.cli", "options-analytics", symbol,
    "--provider", provider, "--json",
    "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 45_000, maxBuffer: 12 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable options-analytics output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
