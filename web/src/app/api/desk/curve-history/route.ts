import { NextResponse } from "next/server";
import { execFile, execFileSync } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

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
