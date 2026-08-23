import { NextRequest, NextResponse } from "next/server";
import { execFile } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const maxDuration = 180;

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
function resolvePython(): string {
  const candidates = [process.env.GOLD_DESK_PYTHON, "/home/z/.venv/bin/python3"].filter(Boolean) as string[];
  for (const c of candidates) if (existsSync(c)) return c;
  return "python3";
}

interface ChatMessage { role: "user" | "assistant"; content: string; }

export async function POST(req: NextRequest) {
  try {
    const body = (await req.json().catch(() => ({}))) as { messages?: ChatMessage[]; model?: string };
    const messages = Array.isArray(body.messages)
      ? body.messages
          .filter((m) => m && typeof m.content === "string" && m.content.trim())
          .slice(-20)
          .map((m) => ({ role: m.role === "assistant" ? "assistant" : "user", content: m.content.slice(0, 4000) }))
      : [];
    if (!messages.length || messages[messages.length - 1].role !== "user") {
      return NextResponse.json({ ok: false, error: "send at least one user message" }, { status: 400 });
    }
    const model = typeof body.model === "string" && /^[a-z0-9][a-z0-9._-]{0,63}$/i.test(body.model) ? body.model : undefined;

    const HARNESS = resolveHarness();
    const args = ["-m", "gold_desk.cli", "chat", "--json", "--stdin", "--data-root", path.join(HARNESS, "data")];
    if (model) args.push("--model", model);

    const result = await new Promise<Record<string, unknown>>((resolve) => {
      const proc = execFile(
        resolvePython(),
        args,
        {
          cwd: HARNESS,
          env: { ...process.env, PYTHONPATH: path.join(HARNESS, "src") },
          timeout: 170_000,
          maxBuffer: 4 * 1024 * 1024,
        },
        (err, stdout) => {
          if (err && !stdout) { resolve({ ok: false, error: `chat failed: ${err.message}` }); return; }
          try { resolve(JSON.parse(stdout.trim().split("\n").pop() || "{}")); }
          catch { resolve({ ok: false, error: "unparseable chat output" }); }
        },
      );
      proc.stdin?.end(JSON.stringify({ messages }));
      req.signal.addEventListener("abort", () => proc.kill());
    });
    return NextResponse.json(result);
  } catch (e) {
    return NextResponse.json({ ok: false, error: (e as Error).message }, { status: 500 });
  }
}
