import { NextResponse } from "next/server";
import { execFile, execFileSync } from "child_process";
import { existsSync, readdirSync, readFileSync } from "fs";
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

interface ResearchSource { n: number; url: string; title: string; fetched_ts: string; }

function parseReport(file: string, researchDir: string) {
  try {
    const raw = readFileSync(path.join(researchDir, file), "utf-8");
    // split front-matter (--- ... ---) from body
    let meta: Record<string, unknown> = {};
    let body = raw;
    if (raw.startsWith("---\n")) {
      const end = raw.indexOf("\n---\n", 4);
      if (end > 0) {
        const fm = raw.slice(4, end);
        body = raw.slice(end + 5);
        let currentKey = "";
        for (const line of fm.split("\n")) {
          const kv = line.match(/^([a-z_]+):\s*(.*)$/);
          if (kv) {
            currentKey = kv[1];
            if (kv[2] === "") {
              meta[currentKey] = "";
            } else {
              let v: unknown = kv[2];
              try { v = JSON.parse(kv[2]); } catch { /* plain string */ }
              meta[currentKey] = v;
            }
          } else if (line.trim().startsWith("- ") && currentKey) {
            // list item under the current key (e.g. sources / risks)
            if (!Array.isArray(meta[currentKey])) meta[currentKey] = [];
            const item = line.trim().slice(2);
            try {
              (meta[currentKey] as unknown[]).push(JSON.parse(item));
            } catch {
              (meta[currentKey] as unknown[]).push(item);
            }
          }
        }
      }
    }
    return {
      file,
      asset: meta.asset ?? "",
      run_id: meta.run_id ?? file.split("-")[0],
      generated_ts: meta.generated_ts ?? "",
      confidence: meta.confidence ?? "medium",
      thesis: meta.thesis ?? "",
      models: meta.models ?? [],
      sources: Array.isArray(meta.sources) ? (meta.sources as ResearchSource[]) : [],
      body,
    };
  } catch {
    return { file, error: "unreadable", body: "" };
  }
}

export async function GET(req: Request) {
  const url = new URL(req.url);
  const file = url.searchParams.get("file");
  const HARNESS = resolveHarness();
  const researchDir = path.join(HARNESS, "data", "research");

  // ---- single report detail
  if (file) {
    if (!/^[A-Za-z0-9._-]+\.md$/.test(file) || !existsSync(path.join(researchDir, file))) {
      return NextResponse.json({ ok: false, error: "not found" }, { status: 404 });
    }
    return NextResponse.json({ ok: true, report: parseReport(file, researchDir) });
  }

  // ---- agent activity: recent agent events from the journal
  const { py: PYTHON, err: pyErr } = resolvePython(HARNESS);
  let agentEvents: unknown[] = [];
  if (!pyErr) {
    const events = await new Promise<Record<string, unknown>>((resolve) => {
      execFile(
        PYTHON,
        ["-m", "gold_desk.cli", "events", "--json", "--limit", "400",
         "--data-root", path.join(HARNESS, "data")],
        { cwd: HARNESS, env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") },
          timeout: 20_000, maxBuffer: 8 * 1024 * 1024 },
        (err, stdout) => {
          if (err && !stdout) { resolve({ ok: false, events: [] }); return; }
          try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
          catch { resolve({ ok: false, events: [] }); }
        },
      );
    });
    const all = (events.events as unknown[]) || [];
    agentEvents = all
      .filter((e) =>
        typeof (e as { kind?: string }).kind === "string" &&
        ((e as { kind: string }).kind.startsWith("Agent")
          || (e as { kind: string }).kind === "ResearchReport"
          || (e as { kind: string }).kind === "BudgetExceeded"
          || (e as { kind: string }).kind === "ResearchSourceFetched"))
      .slice(-60)
      .reverse();
  }

  // ---- report list (newest first by name — ULID prefixes are time-sorted)
  let reports: unknown[] = [];
  try {
    const files = readdirSync(researchDir)
      .filter((f) => f.endsWith(".md"))
      .sort()
      .reverse()
      .slice(0, 50);
    reports = files.map((f) => {
      const r = parseReport(f, researchDir) as { body?: string };
      // list view: clip the body to keep the payload light
      return { ...r, body: (r.body || "").slice(0, 400) };
    });
  } catch {
    reports = [];
  }

  return NextResponse.json({
    ok: true,
    reports,
    agentEvents,
    autonomy: process.env.GOLD_DESK_AUTONOMY || "L1",
    python: pyErr ? null : PYTHON,
  });
}
