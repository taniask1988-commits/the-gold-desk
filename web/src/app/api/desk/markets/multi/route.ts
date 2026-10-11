import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-1 Build 1 — multi-asset monitor (R4-2: universe passthrough):
 *   GET /api/desk/markets/multi             → 8-instrument snapshot
 *   GET /api/desk/markets/multi?all=1        → 24-instrument universe
 *   GET /api/desk/markets/multi?symbols=A,B  → any subset
 * Python CLI is the single source of truth (cli markets-multi --json).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const cliArgs = ["-m", "gold_desk.cli", "markets-multi", "--json",
                   "--data-root", path.join(HARNESS, "data")];
  if (params.get("all") === "1" || params.get("all") === "true") {
    cliArgs.push("--all");
  } else if (params.get("symbols")) {
    cliArgs.push("--symbols", String(params.get("symbols")));
  }
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 120_000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable multi-asset output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
