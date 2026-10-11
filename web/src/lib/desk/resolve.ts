import { execFileSync } from "child_process";
import { existsSync } from "fs";
import path from "path";

/**
 * Shared harness/python resolution — the SINGLE portable copy of the
 * resolver every /api/desk route used to duplicate (39 copies, several
 * carrying sandbox-absolute paths — D-008, GAUNTLET 8).
 *
 * Harness resolution order (portable, never a hardcoded user path):
 *   1. GOLD_DESK_ROOT env var
 *   2. the repo root above the web app (web/ -> repo), proven by the
 *      presence of src/gold_desk/cli.py
 *   3. the server's cwd itself (started from the repo root)
 *   4. walk UP from cwd (≤6 levels) looking for the harness
 * Falls back to the first candidate; the caller surfaces a clean
 * {ok:false} when the harness is unreachable.
 *
 * Python resolution order — see README "GOLD_DESK_PYTHON" section:
 *   env override → repo-local .venv (python3 then python) → bare python3.
 *   Each candidate is probed for the `yaml` module before use.
 */
export function resolveHarness(): string {
  const candidates = [
    process.env.GOLD_DESK_ROOT,
    path.resolve(process.cwd(), ".."),
    process.cwd(),
  ].filter(Boolean) as string[];
  const probe = (c: string) =>
    existsSync(path.join(c, "src", "gold_desk", "cli.py"));
  for (const c of candidates) {
    if (probe(c)) return c;
  }
  let dir = process.cwd();
  for (let i = 0; i < 6; i++) {
    if (probe(dir)) return dir;
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return candidates[0] ?? process.cwd();
}

export function resolvePython(
  harness: string,
): { py: string; err: string | null } {
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    path.join(harness, ".venv", "bin", "python3"),
    path.join(harness, ".venv", "bin", "python"),
    "python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) {
    if (c !== "python3" && !existsSync(c)) continue;
    try {
      execFileSync(c, ["-c", "import yaml"], {
        stdio: "ignore",
        timeout: 5_000,
      });
      return { py: c, err: null };
    } catch {
      /* probe failed — try the next candidate */
    }
  }
  return {
    py: "",
    err: "no python candidate has PyYAML installed (set GOLD_DESK_PYTHON or run: pip install pyyaml)",
  };
}
