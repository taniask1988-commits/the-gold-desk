import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R2-2 deterministic verified snapshot (no LLM, no network beyond the
 * bar fetch) — the source-of-truth block the technician persona treats
 * as ground truth for any exact numeric claim. Closes the TradingAgents
 * v0.3.1 market_data_validator.py:1-25 discipline keyless.
 *   GET /api/desk/snapshot?symbol=AAPL → last_close, change %,
 *   ATR14/ATR%, realized_vol_20d, RSI14, MACD hist, BB %b, volume,
 *   regime labels, benchmark beta vs SPY.
 * Python CLI is the single source of truth (cli markets-snapshot --json).
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
    "-m", "gold_desk.cli", "markets-snapshot", symbol, "--json",
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
        catch { resolve({ ok: false, error: "unparseable snapshot output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
