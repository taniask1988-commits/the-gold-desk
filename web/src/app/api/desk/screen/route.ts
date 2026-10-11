import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 120;



/**
 * GAUNTLET 7 Piece 3 — screener.
 * GET /api/desk/screen?where=rsi<30+and+revenue_growth>10&universe=registry
 * The `where` DSL is AST-whitelisted in Python (calls/attributes rejected).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const where = sp.get("where")?.trim();
  if (!where) {
    return NextResponse.json(
      { ok: false, error: "missing ?where= filter (e.g. rsi<30 and price>sma200)" },
      { status: 400 });
  }
  const universe = sp.get("universe")?.trim() || "registry";
  const technicalOnly = sp.get("technical_only") === "1";
  const cliArgs = ["-m", "gold_desk.cli", "screen", "--where", where,
    "--universe", universe, "--json",
    "--data-root", path.join(HARNESS, "data")];
  if (technicalOnly) cliArgs.push("--technical-only");
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 12 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable screen output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
