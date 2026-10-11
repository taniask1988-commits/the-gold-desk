import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * Multi-analyst desk: GET /api/desk/desk?symbol=X
 * Python CLI is the single source of truth (cli desk SYMBOL --json):
 * 5 personas judge the symbol in parallel + a PM consensus — 6 LLM
 * calls over the keyless Zen provider, so this route allows up to 170s
 * (the button shows a spinner while it runs; per-persona failures
 * abstain inside the engine, context failures return ok:false).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const symbol = new URL(req.url).searchParams.get("symbol")?.trim() || "";
  if (!symbol) {
    return NextResponse.json({ ok: false, error: "missing ?symbol=" }, { status: 400 });
  }
  const cliArgs = [
    "-m", "gold_desk.cli", "desk", symbol, "--json",
    "--data-root", path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 250_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable desk output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
