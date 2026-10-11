import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R2-1 institutional aggregator (7-slice, fail-soft per slice):
 *   GET /api/desk/institutional?symbol=AAPL → fundamentals + 13F + curve +
 *   F&G + onchain + global + social, each slice its own ok flag.
 * Python CLI is the single source of truth (cli markets-institutional --json).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const symbol = new URL(req.url).searchParams.get("symbol")?.trim() || "";
  if (!symbol) {
    return NextResponse.json({ ok: false, error: "symbol required" }, { status: 400 });
  }
  const cliArgs = [
    "-m", "gold_desk.cli", "markets-institutional", symbol, "--json",
    "--data-root", path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 45_000, maxBuffer: 6 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable institutional output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
