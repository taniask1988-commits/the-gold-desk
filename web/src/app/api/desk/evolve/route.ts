import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R5 — self-evolving desk status (READ-ONLY):
 *   GET /api/desk/evolve
 *       → {ok, archive_path, n_individuals, n_generations, statuses,
 *          birth_ops, last_verdict, last_verdict_reason,
 *          last_champion, last_incumbent, lineage_tail}
 *
 * Evolution runs are OPERATOR ACTIONS (`python -m gold_desk.cli
 * evolve-run`): the web layer never triggers a run — the engine never
 * writes the live spec, and neither does the UI. This route reports
 * the archive the runs leave behind (the AlphaEvolve population DB,
 * rendered inspectable).
 */
export async function GET() {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      ["-m", "gold_desk.cli", "evolve-status", "--json",
       "--data-root", path.join(HARNESS, "data")],
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable evolve status output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
