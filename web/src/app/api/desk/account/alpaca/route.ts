import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-1 Build 2 — Alpaca paper account summary:
 *   GET /api/desk/account/alpaca → {ok, account, positions, orders, as_of}
 * Fail-closed when creds missing (CONSTITUTION_BLOCKED +
 * ALPACA_CREDS_MISSING). Python CLI is the single source of truth
 * (cli account-alpaca --json).
 */
export async function GET() {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const cliArgs = ["-m", "gold_desk.cli", "account-alpaca", "--json",
                   "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 15_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable alpaca output" }); }
      },
    );
  });
  // 1 (CONSTITUTION_BLOCKED) is the fail-closed path — keep 200 OK so the
  // UI can render the blocked badge instead of a transport error
  return NextResponse.json(result);
}
