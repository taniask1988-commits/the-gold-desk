import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * Multi-market board: GET /api/desk/markets            → full board JSON
 *                     GET /api/desk/markets?symbol=btc → single-symbol detail
 * Python CLI is the single source of truth (cli markets [--symbol X] --json).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const symbol = new URL(req.url).searchParams.get("symbol")?.trim() || "";
  const cliArgs = ["-m", "gold_desk.cli", "markets", "--json"];
  if (symbol) cliArgs.push("--symbol", symbol);
  cliArgs.push("--data-root", path.join(HARNESS, "data"));
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable markets output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
