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
 * NSE-style news search (GAUNTLET-P13):
 *   GET /api/desk/news-search?q=bitcoin → merged Yahoo RSS headlines.
 * Python CLI is the single source of truth (cli markets-news Q --json):
 * query fuzzy-matches the registry (symbol/name/alias/sector), the
 * per-symbol feeds + the gold general feed merge, recency-ranked,
 * capped at 20, all behind the 300s caches.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const q = new URL(req.url).searchParams.get("q")?.trim() || "";
  if (!q) {
    return NextResponse.json({ ok: false, error: "missing q" }, { status: 400 });
  }
  const cliArgs = [
    "-m",
    "gold_desk.cli",
    "markets-news",
    q.slice(0, 64),
    "--json",
    "--data-root",
    path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 45_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable news-search output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
