import { NextResponse } from "next/server";
import { execFile, execFileSync } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";

function resolveHarness(): string {
  const candidates = [
    process.env.GOLD_DESK_ROOT,
    path.resolve(process.cwd(), ".."),
    "/home/z/my-project/download/gold_desk_v1",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (existsSync(path.join(c, "src", "gold_desk", "cli.py"))) return c;
  }
  return candidates[candidates.length - 1];
}

function resolvePython(harness: string): { py: string; err: string | null } {
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    "/home/z/.venv/bin/python3",
    path.join(harness, ".venv", "bin", "python3"),
    "python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (c !== "python3" && !existsSync(c)) continue;
    try {
      execFileSync(c, ["-c", "import yaml"], { stdio: "ignore", timeout: 5_000 });
      return { py: c, err: null };
    } catch {
      /* probe failed — try the next candidate */
    }
  }
  return { py: "", err: "no python candidate has PyYAML installed (set GOLD_DESK_PYTHON or run: pip install pyyaml)" };
}

/**
 * R3-2 Build 3 — NLP news sentiment (Refinitiv News IntelliSense contract,
 * keyless): POST /api/desk/news/sentiment with {"headline": "..."} →
 * {ok, polarity, magnitude, subjectivity, label, assets[], relevance,
 * novelty, terms_fired[], llm_fallback_used?, llm_fallback_failed?}.
 *
 * The Python CLI is the single source of truth (cli news-sentiment --json).
 * `llm: false` in the body disables the Zen second opinion (--no-llm);
 * by default it runs fail-closed (any failure keeps the local score and
 * flags llm_fallback_failed — the pipeline never blocks on the LLM).
 */
export async function POST(req: Request) {
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "invalid JSON body" }, { status: 400 });
  }
  const headline = typeof body.headline === "string" ? body.headline.trim() : "";
  if (!headline) {
    return NextResponse.json(
      { ok: false, error: "missing 'headline' (non-empty string)" },
      { status: 400 },
    );
  }
  // argparse cannot take a positional that starts with '-' — reject rather
  // than silently misparse it as a flag.
  if (headline.startsWith("-")) {
    return NextResponse.json(
      { ok: false, error: "headline must not start with '-'" },
      { status: 400 },
    );
  }
  const noLlm = body.llm === false;

  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  const cliArgs = ["-m", "gold_desk.cli", "news-sentiment", headline, "--json"];
  if (noLlm) cliArgs.push("--no-llm");
  cliArgs.push("--data-root", path.join(HARNESS, "data"));
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 45_000, maxBuffer: 2 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable sentiment output" }); }
      },
    );
  });
  return NextResponse.json(result);
}
