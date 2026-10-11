import { NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";



/**
 * R3-2 Build 3 — score the LIVE news tape:
 *   GET /api/desk/news/sentiment/tape?limit=20
 *       → {ok, as_of, n_feeds, n_feeds_requested, n_stories,
 *          stories: [{headline, polarity, magnitude, subjectivity, label,
 *                     assets[], relevance, novelty, feed_symbol, link,
 *                     published}]}
 * Keyless Yahoo headline RSS fanned out across the 8 desk instruments
 * (cli news-sentiment --tape --json). Every story is scored by ONE analyzer
 * so novelty is meaningful across the tape.
 */
export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const params = new URL(req.url).searchParams;
  const parsed = parseInt(params.get("limit") || "20", 10);
  const limit = Number.isFinite(parsed) && parsed > 0 && parsed <= 50 ? parsed : 20;
  const cliArgs = ["-m", "gold_desk.cli", "news-sentiment", "--tape",
                   "--no-llm", "--limit", String(limit), "--json",
                   "--data-root", path.join(HARNESS, "data")];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 60_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable tape output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
