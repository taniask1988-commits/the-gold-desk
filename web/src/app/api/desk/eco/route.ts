import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * Economic calendar (ECO, GAUNTLET-P13):
 *   GET /api/desk/eco → this week's releases by country/impact.
 * Python CLI is the single source of truth (cli markets-eco --json):
 * ForexFactory mirror with a 30min cache and a static date-math
 * fallback, so this route always serves a calendar.
 */
export async function GET() {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const cliArgs = [
    "-m",
    "gold_desk.cli",
    "markets-eco",
    "--json",
    "--data-root",
    path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable eco output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
