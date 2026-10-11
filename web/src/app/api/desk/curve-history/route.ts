import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 120;



/**
 * GAUNTLET 7 Piece 4 — Treasury curve history (tenors × dates matrix,
 * keyless). GET /api/desk/curve-history?years=2
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const years = new URL(req.url).searchParams.get("years")?.trim() || "2";
  const cliArgs = ["-m", "gold_desk.cli", "curve-history",
    "--years", years, "--json",
    "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable curve-history output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
