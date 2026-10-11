import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



export async function GET() {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      ["-m", "gold_desk.cli", "drivers", "--json", "--data-root", path.join(HARNESS, "data")],
      {
        cwd: HARNESS,
        env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") },
        timeout: 60_000,
        maxBuffer: 4 * 1024 * 1024,
      },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable drivers output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
