import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R5 — temporal lesson memory (READ-ONLY):
 *   GET /api/desk/lessons?symbol=GC=F&regime=all
 *       → {ok, store_path, action: "list", active: [{lesson_id, text,
 *          symbol, regime, confidence, support, contradict, age_days}],
 *          n_total}
 *
 * The Zep/Graphiti-style belief system: every lesson carries a decayed,
 * Laplace-smoothed confidence and a validity window; contradicted
 * lessons retire and never appear here. Evidence flows in from the
 * CLI (operator/Reflexion wiring), not from the UI.
 */
export async function GET(request: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const url = new URL(request.url);
  const symbol = url.searchParams.get("symbol") || "all";
  const regime = url.searchParams.get("regime") || "all";
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      ["-m", "gold_desk.cli", "lessons", "list",
       "--symbol", symbol, "--regime", regime, "--json",
       "--data-root", path.join(HARNESS, "data")],
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable lessons output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
