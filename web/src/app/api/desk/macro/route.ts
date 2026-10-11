import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 120;



/**
 * GAUNTLET 7 Piece 4 — macro plane.
 * GET /api/desk/macro            → dashboard aggregate
 * GET /api/desk/macro?ids=DGS10  → raw series
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const ids = sp.get("ids")?.trim();
  const cliArgs = ["-m", "gold_desk.cli",
    ids ? "macro-series" : "macro-dashboard"];
  if (ids) cliArgs.push("--ids", ids);
  cliArgs.push("--json", "--data-root", path.join(HARNESS, "data"));
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 16 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable macro output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
