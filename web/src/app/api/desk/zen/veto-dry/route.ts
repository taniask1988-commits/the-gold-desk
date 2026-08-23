import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const maxDuration = 180;

// Harness root resolution (portable):
//   1. GOLD_DESK_ROOT env var
//   2. repo parent of the web app cwd
//   3. sandbox fallback
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
const HARNESS = resolveHarness();

// Python resolution: the harness needs PyYAML. Prefer an explicit override,
// then a venv python (sandbox), then plain python3 from PATH.
function resolvePython(): string {
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    "/home/z/.venv/bin/python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    try {
      if (existsSync(c)) return c;
    } catch {
      /* next */
    }
  }
  return "python3";
}
const PYTHON = resolvePython();

const SCENARIOS = new Set(["clean", "news", "stale"]);
const MODEL_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as {
      scenario?: string;
      model?: string;
    };
    const scenario = SCENARIOS.has(body.scenario ?? "")
      ? (body.scenario as string)
      : "clean";
    const model =
      body.model && MODEL_RE.test(body.model) ? body.model : undefined;

    // The Python harness is the single source of truth for pack building,
    // blindfold scrubbing and the veto contract. The web deck only invokes it.
    const args = [
      "-m", "gold_desk.cli", "veto-bench",
      "--scenario", scenario,
      "--json",
      "--timeout", "150",
    ];
    if (model) args.push("--model", model);

    const result = await new Promise<Record<string, unknown>>((resolve) => {
      const proc = execFile(
        PYTHON,
        args,
        {
          cwd: HARNESS,
          env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") },
          timeout: 170_000,
          maxBuffer: 4 * 1024 * 1024,
        },
        (err, stdout) => {
          if (err && !stdout) {
            resolve({ ok: false, error: `bench failed: ${err.message}` });
            return;
          }
          try {
            const lines = stdout.trim().split("\n");
            resolve(JSON.parse(lines[lines.length - 1]));
          } catch {
            resolve({ ok: false, error: "unparseable bench output" });
          }
        },
      );
      // never leave a dangling child if the client disconnects
      req.signal.addEventListener("abort", () => proc.kill());
    });

    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: (e as Error).message },
      { status: 500 },
    );
  }
}
