import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import path from "path";
import { resolveHarness, resolvePython } from "@/lib/desk/resolve";

export const dynamic = "force-dynamic";
export const maxDuration = 180;

const HARNESS = resolveHarness();

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
