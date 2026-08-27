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
 * R4-1 — alert rules + fired log:
 *   GET  /api/desk/alerts?limit=25
 *        → {ok, rules: [{id, symbol, kind, params, enabled,
 *           cooldown_minutes, note}], fired: [{event_id, rule_id,
 *           symbol, kind, message, value, threshold, fired_at,
 *           wall_fired_at, channel, ack}], rules_count, fired_count}
 *   POST /api/desk/alerts  body {action: "add", symbol, kind, threshold?,
 *           window?, level?, k?, other?, cooldown?, note?}
 *        → {ok, rule}          (the default rule pack seeds an empty store)
 *   POST /api/desk/alerts  body {action: "ack", event_id}
 *        → {ok, acked}
 *   DELETE /api/desk/alerts?id=RULE_ID
 *        → {ok, removed}
 */
async function runCli(harness: string, py: string, args: string[])
  : Promise<Record<string, unknown>> {
  return new Promise((resolve) => {
    execFile(
      py,
      ["-m", "gold_desk.cli", ...args, "--json",
       "--data-root", path.join(harness, "data")],
      { cwd: harness, env: { ...process.env, PYTHONPATH: path.join(harness, "src") }, timeout: 30_000, maxBuffer: 4 * 1024 * 1024 },
      (err, stdout) => {
        if (err && !stdout) { resolve({ ok: false, error: err.message }); return; }
        try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
        catch { resolve({ ok: false, error: "unparseable alerts output" }); }
      },
    );
  });
}

export async function GET(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  const limit = new URL(req.url).searchParams.get("limit") || "25";
  return NextResponse.json(
    await runCli(HARNESS, PYTHON, ["alerts", "--limit", limit]));
}

export async function POST(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  let body: Record<string, unknown> = {};
  try { body = await req.json(); } catch { /* fall through with {} */ }
  if (body.action === "ack") {
    const event_id = String(body.event_id || "");
    if (!event_id) return NextResponse.json({ ok: false, error: "event_id required" }, { status: 400 });
    return NextResponse.json(await runCli(HARNESS, PYTHON, ["alerts", "--ack", event_id]));
  }
  const symbol = String(body.symbol || "");
  const kind = String(body.kind || "pct_move");
  if (!symbol) return NextResponse.json({ ok: false, error: "symbol required" }, { status: 400 });
  const args = ["alerts-add", "--symbol", symbol, "--kind", kind];
  // R4 exit-critic D8: callers may pass either FLAT fields (threshold,
  // level, ...) or a nested params{} object (the store/CLI shape) — the
  // nested object is flattened onto the same flags; flat wins on conflict.
  const nested = (body.params && typeof body.params === "object") ? body.params as Record<string, unknown> : {};
  const num = (v: unknown) => (v === null || v === undefined || v === "" ? null : String(v));
  for (const [flag, key] of [
    ["--threshold", "threshold"], ["--window", "window"], ["--level", "level"],
    ["--k", "k"], ["--other", "other"], ["--cooldown", "cooldown"],
    ["--note", "note"],
  ] as const) {
    const flat = num(body[key]);
    const fromNested = num(nested[key]);
    const v = flat !== null ? flat : fromNested;
    if (v !== null) args.push(flag, v);
  }
  return NextResponse.json(await runCli(HARNESS, PYTHON, args));
}

export async function DELETE(req: Request) {
  const HARNESS = resolveHarness();
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  if (pyErr) return NextResponse.json({ ok: false, error: pyErr }, { status: 500 });
  const id = new URL(req.url).searchParams.get("id");
  if (!id) return NextResponse.json({ ok: false, error: "id required" }, { status: 400 });
  return NextResponse.json(await runCli(HARNESS, PYTHON, ["alerts-rm", "--id", id]));
}
