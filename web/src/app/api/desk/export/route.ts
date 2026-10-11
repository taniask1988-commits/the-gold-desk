import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 120;



/**
 * GAUNTLET 7 Piece 7 — canonical CSV export.
 * GET /api/desk/export?kind=journal|screener|pair|macro|options
 *
 * Byte-identical to the rendered tables by construction: the CSV and
 * the table JSON come from the SAME CLI command (export-csv / screen /
 * pair / macro-dashboard / options-analytics), and the CSV builder is
 * the single Python `rows_to_csv` (round-trip pinned by tests).
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const sp = new URL(req.url).searchParams;
  const kind = (sp.get("kind") || "").trim().toLowerCase();
  const kindArgs: Record<string, string[]> = {
    journal: ["--limit", sp.get("limit") || "500"],
    screener: [
      "--where", sp.get("where") || "rsi < 70",
      "--universe", sp.get("universe") || "registry",
      ...(sp.get("technical") === "1" ? ["--technical-only"] : []),
      "--limit", sp.get("limit") || "50",
    ],
    pair: [
      "--a", sp.get("a") || "GC=F",
      "--b", sp.get("b") || "SLV",
      "--window", sp.get("window") || "60",
      "--mode", sp.get("mode") || "ratio",
    ],
    macro: [],
    options: ["--symbol", sp.get("symbol") || "GLD"],
  };
  const extra = kindArgs[kind];
  if (!extra) {
    return NextResponse.json(
      { ok: false, error: `unknown export kind: ${kind}` },
      { status: 400 }
    );
  }
  const cliArgs = [
    "-m", "gold_desk.cli", "export-csv", kind, ...extra,
    "--data-root", path.join(HARNESS, "data"),
  ];
  const out = await new Promise<{ err: Error | null; stdout: string; stderr: string }>(
    (resolve) => {
      execFile(
        PYTHON, cliArgs,
        { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 16 * 1024 * 1024 },
        (err, stdout, stderr) => resolve({ err, stdout, stderr })
      );
    }
  );
  if (out.err && !out.stdout) {
    return NextResponse.json(
      { ok: false, error: (out.stderr || String(out.err)).slice(0, 300) },
      { status: 500 }
    );
  }
  return new Response(out.stdout, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": `attachment; filename="gold-desk-${kind}.csv"`,
      "Cache-Control": "no-store",
    },
  });
}
