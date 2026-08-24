import { NextRequest, NextResponse } from "next/server";
import { execFile, execFileSync } from "child_process";
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

/**
 * Python resolution — see README "GOLD_DESK_PYTHON" section.
 * Order: env override → sandbox venv → repo-local venv → bare python3.
 * Each candidate is probed for the `yaml` module before use.
 */
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
const { py: PYTHON, err: PY_ERR } = resolvePython(HARNESS);

const SCENARIOS = new Set(["clean", "news", "stale"]);
const MODEL_RE = /^[a-z0-9][a-z0-9._-]{0,63}$/i;

export async function POST(req: NextRequest) {
  if (PY_ERR) {
    return NextResponse.json({ ok: false, error: PY_ERR }, { status: 500 });
  }
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
