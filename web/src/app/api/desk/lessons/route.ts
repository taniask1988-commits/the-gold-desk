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
