import { NextResponse } from "next/server";
import { execFile } from "child_process";
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
function resolvePython(): string {
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    "/home/z/.venv/bin/python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return "python3";
}

export async function GET() {
  const HARNESS = resolveHarness();
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      resolvePython(),
      ["-m", "gold_desk.cli", "news", "--json", "--limit", "14", "--data-root", path.join(HARNESS, "data")],
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 2 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable price output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
