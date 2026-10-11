import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * GAUNTLET 7 Piece 1 — pro charting data plane.
 * GET /api/desk/chart?symbol=gold&timeframe=1h&indicators=sma20,sma50,rsi14
 * Python CLI is the single source of truth (cli markets-chart --json).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const symbol = sp.get("symbol")?.trim() || "GC=F";
  const timeframe = sp.get("timeframe")?.trim() || "1h";
  const indicators = sp.get("indicators")?.trim() || "";
  const cliArgs = ["-m", "gold_desk.cli", "markets-chart", symbol,
    "--timeframe", timeframe, "--json",
    "--data-root", path.join(HARNESS, "data")];
  if (indicators) cliArgs.push("--indicators", indicators);
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable markets-chart output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
