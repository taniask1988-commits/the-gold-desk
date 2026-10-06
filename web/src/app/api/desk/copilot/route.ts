import { NextResponse } from "next/server";
import { execFile } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const maxDuration = 120;

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
    return { py: c, err: null };
  }
  return { py: "", err: "no python candidate found (set GOLD_DESK_PYTHON)" };
}

/**
 * GAUNTLET 7 Piece 6 — receipts-backed AI copilot.
 * POST /api/desk/copilot  { question: string }
 * Executes the desk's own READ-ONLY CLI subcommands and returns an
 * evidence-gated answer where every number cites a receipt.
 */
export async function POST(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) {
    return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  }
  let question = "";
  try {
    const body = (await req.json()) as { question?: string };
    question = (body.question || "").trim();
  } catch {
    return NextResponse.json(
      { ok: false, error: "invalid JSON body" },
      { status: 400 }
    );
  }
  if (!question) {
    return NextResponse.json(
      { ok: false, error: "question is required" },
      { status: 400 }
    );
  }
  if (question.length > 500) question = question.slice(0, 500);

  const cliArgs = [
    "-m",
    "gold_desk.cli",
    "copilot",
    question,
    "--json",
    "--data-root",
    path.join(HARNESS, "data"),
  ];
  const result = await new Promise<Record<string, unknown>>((resolve) => {
    execFile(
      PYTHON,
      cliArgs,
      { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") }, timeout: 110_000, maxBuffer: 12 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && !stdout) {
          resolve({ ok: false, error: String(err).slice(0, 300) });
          return;
        }
        try {
          const parsed = JSON.parse(stdout);
          resolve(parsed);
        } catch {
          resolve({
            ok: false,
            error: (stderr || "unparseable copilot output").slice(0, 300),
          });
        }
      }
    );
  });
  return NextResponse.json(result);
}
