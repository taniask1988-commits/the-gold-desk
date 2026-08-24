import { NextRequest } from "next/server";
import { spawn, type ChildProcessWithoutNullStreams } from "child_process";
import { existsSync } from "fs";
import path from "path";

export const dynamic = "force-dynamic";
export const maxDuration = 180;
// We drive python stdout straight to the client; never let the platform
// buffer or transform the response body.
export const fetchCache = "force-no-store";
export const revalidate = 0;

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
  const candidates = [
    process.env.GOLD_DESK_PYTHON,
    "/home/z/.venv/bin/python3",
  ].filter(Boolean) as string[];
  for (const c of candidates) if (existsSync(c)) return c;
  return "python3";
}

interface ChatMessage { role: "user" | "assistant"; content: string; }

export async function POST(req: NextRequest) {
  let body: { messages?: ChatMessage[]; model?: string };
  try {
    body = (await req.json().catch(() => ({}))) as typeof body;
  } catch {
    body = {};
  }
  const messages: ChatMessage[] = Array.isArray(body.messages)
    ? body.messages
        .filter((m) => m && typeof m.content === "string" && m.content.trim())
        .slice(-20)
        .map((m) => ({
          role: m.role === "assistant" ? "assistant" : "user",
          content: m.content.slice(0, 4000),
        }))
    : [];
  if (
    messages.length === 0 ||
    messages[messages.length - 1].role !== "user"
  ) {
    return new Response(
      JSON.stringify({ ok: false, error: "send at least one user message" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }
  const model =
    typeof body.model === "string" &&
    /^[a-z0-9][a-z0-9._-]{0,63}$/i.test(body.model)
      ? body.model
      : undefined;

  const HARNESS = resolveHarness();
  const args = [
    "-u",  // unbuffered stdout — critical for streaming tokens to the client
    "-m", "gold_desk.cli", "chat",
    "--json", "--stdin", "--stream",
    "--data-root", path.join(HARNESS, "data"),
  ];
  if (model) args.push("--model", model);

  const proc: ChildProcessWithoutNullStreams = spawn(
    resolvePython(),
    args,
    {
      cwd: HARNESS,
      env: {
        ...process.env,
        PYTHONPATH: path.join(HARNESS, "src"),
        PYTHONUNBUFFERED: "1",
        // keep flakey free-tier endpoints honest
        OPENCODE_ZEN_BASE_URL: process.env.OPENCODE_ZEN_BASE_URL ?? "",
      },
      stdio: ["pipe", "pipe", "pipe"],
    },
  );

  // write the transcript to python stdin (small, immediate)
  try {
    proc.stdin.write(JSON.stringify({ messages }));
    proc.stdin.end();
  } catch {
    /* if stdin breaks, the downstream pipeline surfaces the error */
  }

  // Build the streaming response: NDJSON lines, one chat event per line.
  // Each line is a single JSON object from the python CLI:
  //   {"type":"start","model":..,"grounded":bool}
  //   {"type":"reasoning","delta":str}     (optional, many)
  //   {"type":"content","delta":str}        (many)
  //   {"type":"done","model":..,"latency_ms":int,"grounded":bool}
  //   {"type":"error","error":str}          (terminal)
  const encoder = new TextEncoder();
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      const cleanup = () => {
        try { proc.kill(); } catch { /* noop */ }
        try { controller.close(); } catch { /* noop */ }
      };
      // abort from the client (popup closed, ESC, etc.) → kill python
      const onAbort = () => { cleanup(); };
      req.signal.addEventListener("abort", onAbort);

      let stdoutBuf = "";
      let stderrBuf = "";
      let sawTerminal = false;

      proc.stdout.on("data", (chunk: Buffer) => {
        if (sawTerminal) return;
        stdoutBuf += chunk.toString("utf-8");
        // emit each complete line as a single NDJSON record
        let nlIdx: number;
        while ((nlIdx = stdoutBuf.indexOf("\n")) >= 0) {
          const line = stdoutBuf.slice(0, nlIdx);
          stdoutBuf = stdoutBuf.slice(nlIdx + 1);
          if (!line.trim()) continue;
          try {
            // validate JSON before forwarding — never leak raw python errors
            JSON.parse(line);
            controller.enqueue(encoder.encode(line + "\n"));
            if (line.includes('"type":"done"') ||
                line.includes('"type":"error"')) {
              sawTerminal = true;
            }
          } catch {
            /* skip non-JSON lines (defensive) */
          }
        }
      });
      proc.stderr.on("data", (chunk: Buffer) => {
        stderrBuf += chunk.toString("utf-8");
        if (stderrBuf.length > 16 * 1024) stderrBuf = stderrBuf.slice(-8 * 1024);
      });
      proc.on("error", () => {
        if (!sawTerminal) {
          try {
            controller.enqueue(encoder.encode(
              JSON.stringify({
                type: "error",
                error: "spawn failed: python unreachable",
              }) + "\n",
            ));
          } catch { /* noop */ }
        }
        req.signal.removeEventListener("abort", onAbort);
        cleanup();
      });
      proc.on("close", (code: number | null) => {
        req.signal.removeEventListener("abort", onAbort);
        if (sawTerminal) {
          // already emitted done/error — just close
          try { controller.close(); } catch { /* noop */ }
          return;
        }
        // python exited without emitting a terminal event — synthesize one
        try {
          const errTail = stderrBuf.trim().split("\n").slice(-1)[0] || "";
          controller.enqueue(encoder.encode(
            JSON.stringify({
              type: "error",
              error: code === 0
                ? "stream ended unexpectedly"
                : `chat failed (exit ${code})${errTail ? ": " + errTail : ""}`,
            }) + "\n",
          ));
          try { controller.close(); } catch { /* noop */ }
        } catch { /* noop */ }
      });
    },
    cancel(reason) {
      try { proc.kill(); } catch { /* noop */ }
      // swallow — controller is already closing
      void reason;
    },
  });

  return new Response(stream, {
    status: 200,
    headers: {
      "Content-Type": "application/x-ndstream; charset=utf-8",
      "Cache-Control": "no-store, no-transform",
      "X-Accel-Buffering": "no",  // disable proxy buffering (nginx)
      "Connection": "keep-alive",
    },
  });
}
