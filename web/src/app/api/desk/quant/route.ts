import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R2-2 quant indicator battery (numpy-free, deterministic):
 *   GET /api/desk/quant?symbol=AAPL → RSI14, MACD, BBands, ATR/ATR%,
 *   realized_vol_20d + vol regime, SMA{20,50,200}, EMA{12,26},
 *   ADX14, Stoch, CCI20, OBV.
 * Python CLI is the single source of truth (cli markets-quant --json).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const symbol = new URL(req.url).searchParams.get("symbol")?.trim() || "";
  if (!symbol) {
    return NextResponse.json({ ok: false, error: "symbol required" }, { status: 400 });
  }
  const cliArgs = [
    "-m", "gold_desk.cli", "markets-quant", symbol, "--json",
    "--data-root", path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable quant output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
