"use client";

import { useCallback, useRef, useState } from "react";

/**
 * GAUNTLET 7 Piece 6 — COPILOT PANEL (receipts-backed).
 *
 * Natural-language question → the desk's own read-only CLI subcommands
 * → an answer where EVERY number carries a [R#] receipt citation, then
 * mechanically evidence-gated (untraceable numbers are flagged and the
 * answer is marked UNPUBLISHED — the R2-5 zero-fabrication discipline).
 *
 * This exceeds the plain /chat AGENT MODE structurally: multi-command
 * execution + per-number citations + a mechanical gate. Read-only by
 * construction (the Python allowlist is AST-pinned by tests).
 */

interface CopilotReceipt {
  receipt_id: string;
  command: string;
  argv: string[];
  ts: string;
  ok: boolean;
  data: Record<string, unknown>;
  error: string | null;
  duration_ms: number;
}

interface CopilotOut {
  ok: boolean;
  question: string;
  plan?: string[];
  answer_md?: string;
  receipts?: CopilotReceipt[];
  evidence?: { checked: number; untraceable: { value: string; context: string }[]; ok: boolean };
  published?: boolean;
  mode?: string;
  elapsed_ms?: number;
  error?: string;
}

const SUGGESTIONS = [
  "gold's RSI + CFTC positioning + desk view",
  "gold vs silver spread z-score",
  "macro: rates, dollar, VIX",
  "GLD options max pain + put/call",
  "latest gold headlines",
];

/** minimal markdown-ish renderer: ### headings, **bold**, bullets */
function renderAnswer(md: string) {
  return md.split("\n").map((line, i) => {
    const body = line.startsWith("- ") ? line.slice(2) : line;
    const segs = body.split(/(\*\*[^*]+\*\*)/g).map((seg, j) =>
      seg.startsWith("**") && seg.endsWith("**") ? (
        <strong key={j} className="text-[#e8d5a3]">
          {seg.slice(2, -2)}
        </strong>
      ) : (
        <span key={j}>{seg}</span>
      )
    );
    if (line.startsWith("### ")) {
      return (
        <div key={i} className="mt-3 font-mono text-[11px] uppercase tracking-[0.14em] text-[#c8a04b]">
          {line.slice(4)}
        </div>
      );
    }
    if (line.startsWith("- ")) {
      // strip ONLY the leading "- " from the first text segment —
      // keep every label (the old slice(1) dropped them)
      return (
        <div key={i} className="flex gap-2 pl-1 text-[12px] leading-relaxed text-neutral-300">
          <span className="text-[#c8a04b]/60">·</span>
          <span>{segs}</span>
        </div>
      );
    }
    if (!line.trim()) return <div key={i} className="h-2" />;
    return (
      <div key={i} className="text-[12px] leading-relaxed text-neutral-300">
        {segs}
      </div>
    );
  });
}

export function CopilotPanel() {
  const [q, setQ] = useState("");
  const [out, setOut] = useState<CopilotOut | null>(null);
  const [busy, setBusy] = useState(false);
  const [openReceipt, setOpenReceipt] = useState<string | null>(null);
  const abort = useRef<AbortController | null>(null);

  const ask = useCallback(async (question: string) => {
    if (!question.trim() || busy) return;
    setBusy(true);
    setOut(null);
    abort.current?.abort();
    abort.current = new AbortController();
    try {
      const r = await fetch("/api/desk/copilot", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question }),
        signal: abort.current.signal,
      });
      const j = (await r.json()) as CopilotOut;
      setOut(j);
    } catch (e) {
      setOut({ ok: false, question, error: `copilot unreachable: ${e}` });
    } finally {
      setBusy(false);
    }
  }, [busy]);

  const gate = out?.evidence;

  return (
    <section
      id="copilot"
      className="gdc-panel px-4 py-4 sm:px-6"
      aria-label="Receipts-backed copilot"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] uppercase tracking-[0.22em] text-[#c8a04b]">
          Copilot — receipts-backed
        </h2>
        <span className="font-mono text-[10px] text-neutral-500">
          read-only · every number cited · evidence-gated
        </span>
      </div>

      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void ask(q);
        }}
      >
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="ask the desk — e.g. gold's RSI + CFTC positioning + desk view"
          className="min-w-0 flex-1 rounded-none border border-[#c8a04b]/25 bg-black/30 px-3 py-2 font-mono text-[12px] text-neutral-200 placeholder:text-neutral-600 focus:border-[#c8a04b]/60 focus:outline-none"
          aria-label="copilot question"
        />
        <button
          type="submit"
          disabled={busy || !q.trim()}
          className="border border-[#c8a04b]/40 bg-[#c8a04b]/10 px-4 py-2 font-mono text-[11px] uppercase tracking-[0.18em] text-[#c8a04b] transition hover:bg-[#c8a04b]/20 disabled:opacity-40"
        >
          {busy ? "running…" : "ask"}
        </button>
      </form>

      {!out && !busy && (
        <div className="mt-3 flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              onClick={() => {
                setQ(s);
                void ask(s);
              }}
              className="border border-neutral-800 px-2 py-1 font-mono text-[10px] text-neutral-400 transition hover:border-[#c8a04b]/40 hover:text-[#c8a04b]"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {busy && (
        <div className="mt-4 animate-pulse font-mono text-[11px] text-neutral-500">
          executing read-only desk commands…
        </div>
      )}

      {out?.error && (
        <div className="mt-3 border border-red-900/50 bg-red-950/20 px-3 py-2 font-mono text-[11px] text-red-400">
          {out.error}
        </div>
      )}

      {out && !out.error && (
        <div className="mt-4 grid gap-4 lg:grid-cols-[1.5fr_1fr]">
          <div>
            {out.published === false && gate && (
              <div className="mb-3 border border-red-900/50 bg-red-950/20 px-3 py-2 font-mono text-[11px] text-red-400">
                EVIDENCE GATE: UNPUBLISHED — {gate.untraceable.length}{" "}
                untraceable number(s)
              </div>
            )}
            {out.published === true && gate && (
              <div className="mb-3 font-mono text-[10px] text-emerald-500/80">
                evidence gate: {gate.checked} number(s) checked · 0
                untraceable · published
              </div>
            )}
            <div>{renderAnswer(out.answer_md || "")}</div>
            {out.elapsed_ms != null && (
              <div className="mt-3 font-mono text-[10px] text-neutral-600">
                plan: {(out.plan || []).join(" → ")} · {out.elapsed_ms}ms ·
                mode {out.mode || "n/a"}
              </div>
            )}
          </div>

          <div className="min-w-0">
            <div className="font-mono text-[10px] uppercase tracking-[0.18em] text-neutral-500">
              receipts ({(out.receipts || []).length})
            </div>
            <div className="mt-2 space-y-1">
              {(out.receipts || []).map((r) => (
                <div key={r.receipt_id} className="border border-neutral-800/80">
                  <button
                    onClick={() =>
                      setOpenReceipt(openReceipt === r.receipt_id ? null : r.receipt_id)
                    }
                    className="flex w-full items-center justify-between gap-2 px-2 py-1.5 text-left font-mono text-[11px] hover:bg-white/[0.02]"
                  >
                    <span className={r.ok ? "text-neutral-300" : "text-red-400"}>
                      [R{r.receipt_id}] {r.command}
                    </span>
                    <span className="text-neutral-600">
                      {r.ok ? `${r.duration_ms}ms` : "failed"}
                    </span>
                  </button>
                  {openReceipt === r.receipt_id && (
                    <pre className="max-h-56 overflow-auto border-t border-neutral-800/80 bg-black/40 px-2 py-1.5 font-mono text-[10px] leading-relaxed text-neutral-400">
                      {JSON.stringify(r, null, 2)}
                    </pre>
                  )}
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
