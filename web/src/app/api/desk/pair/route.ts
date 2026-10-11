import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 120;



/**
 * GAUNTLET 7 Piece 5 — pair / relative-value analytics.
 * GET /api/desk/pair?a=gold&b=silver&window=60&mode=ratio
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const a = sp.get("a")?.trim() || "gold";
  const b = sp.get("b")?.trim() || "silver";
  const window = sp.get("window")?.trim() || "60";
  const mode = sp.get("mode")?.trim() || "ratio";
  const cliArgs = ["-m", "gold_desk.cli", "pair", a, b,
    "--window", window, "--mode", mode, "--json",
    "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable pair output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
