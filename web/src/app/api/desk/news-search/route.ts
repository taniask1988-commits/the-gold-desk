import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



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
