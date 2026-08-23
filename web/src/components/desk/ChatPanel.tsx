"use client";

import { useCallback, useEffect, useRef, useState } from "react";

interface Msg {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency?: number;
}

const SUGGESTIONS = [
  "What moves gold this week?",
  "Explain my driver board like I'm new",
  "Why did the desk issue so few tickets?",
  "How do pros trade the London open?",
];

export function ChatPanel({
  open, onClose,
}: {
  open: boolean;
  onClose: () => void;
}) {
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "assistant",
      content:
        "I'm The Desk — 20 years on gold desks (COMEX, London spot, XAUUSD). Ask me anything about gold, macro, or what your harness is telling you. I'm grounded with the live spot price, the latest gold headlines, and your journal — and I'll never invent numbers.\n\nEducation and research, not financial advice.",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current && open) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, open, busy]);

  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;
      setInput("");
      setError(null);
      setBusy(true);
      const next: Msg[] = [...messages, { role: "user", content: question }];
      setMessages(next);
      try {
        const r = await fetch("/api/desk/chat", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ messages: next }),
        }).then((x) => x.json());
        if (r.ok) {
          setMessages((m) => [
            ...m,
            { role: "assistant", content: r.reply, model: r.model, latency: r.latency_ms },
          ]);
        } else {
          setError(String(r.error || "the model is unreachable"));
        }
      } catch (e) {
        setError((e as Error).message);
      } finally {
        setBusy(false);
      }
    },
    [messages, busy],
  );

  return (
    <>
      {/* scrim */}
      <div
        onClick={onClose}
        className={`fixed inset-0 z-40 bg-black/45 backdrop-blur-sm transition-opacity duration-300 ${
          open ? "opacity-100" : "pointer-events-none opacity-0"
        }`}
        aria-hidden
      />
      {/* drawer */}
      <aside
        className={`gdc-panel fixed bottom-3 right-3 top-3 z-50 flex w-[min(440px,calc(100vw-1.5rem))] flex-col overflow-hidden transition-all duration-400 ${
          open ? "translate-x-0 opacity-100" : "pointer-events-none translate-x-[110%] opacity-0"
        }`}
        role="dialog"
        aria-label="Chat with The Desk"
      >
        <div className="gdc-sheen" aria-hidden />
        {/* header */}
        <div className="flex items-center gap-3 border-b border-white/[0.08] px-5 py-3.5">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#e8b440]/5 shadow-[0_0_24px_rgba(232,180,64,0.28)]">
            <span className="gdc-display text-[15px] font-semibold text-[#e8b440]">Au</span>
          </div>
          <div className="leading-tight">
            <div className="gdc-display text-[16px] italic text-[#f4f7fa]">The Desk</div>
            <div className="text-[8.5px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">
              20-yr gold veteran · free zen models · grounded
            </div>
          </div>
          <button
            onClick={onClose}
            className="gdc-chip ml-auto cursor-pointer text-[#9aa5b0] hover:border-white/[0.2]"
            aria-label="Close chat"
          >
            ✕
          </button>
        </div>

        {/* messages */}
        <div ref={scrollRef} className="gdc-scroll flex-1 space-y-4 overflow-y-auto px-4 py-4">
          {messages.map((m, i) => (
            <div key={i} className={m.role === "user" ? "flex justify-end" : "flex justify-start"}>
              <div
                className={`max-w-[88%] whitespace-pre-wrap rounded-2xl px-4 py-3 text-[12px] leading-relaxed ${
                  m.role === "user"
                    ? "rounded-br-md border border-[#e8b440]/30 bg-[#e8b440]/[0.10] text-[#f4f7fa]"
                    : "rounded-bl-md border border-white/[0.09] bg-white/[0.045] text-[#e9edf2] backdrop-blur-sm"
                }`}
              >
                {m.content}
                {m.role === "assistant" && m.model && (
                  <div className="gdc-data mt-2 text-[8.5px] text-[#76828e]">
                    {m.model} · {((m.latency ?? 0) / 1000).toFixed(1)}s · grounded w/ live data
                  </div>
                )}
              </div>
            </div>
          ))}
          {busy && (
            <div className="flex justify-start">
              <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-white/[0.09] bg-white/[0.045] px-4 py-3 backdrop-blur-sm">
                <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#e8b440]" />
                <span className="text-[11px] italic text-[#9aa5b0]">
                  the desk is thinking… (free model, can take ~30-90s)
                </span>
              </div>
            </div>
          )}
          {error && (
            <div className="rounded-xl border border-[#f85149]/30 bg-[#f85149]/[0.08] px-4 py-2.5 text-[11px] text-[#f85149]">
              {error} — fail-closed by design; try again shortly.
            </div>
          )}
        </div>

        {/* suggestions */}
        {messages.length <= 1 && !busy && (
          <div className="flex flex-wrap gap-1.5 px-4 pb-2">
            {SUGGESTIONS.map((s) => (
              <button
                key={s}
                onClick={() => void send(s)}
                className="rounded-full border border-white/[0.09] bg-white/[0.035] px-3 py-1.5 text-[10px] text-[#9aa5b0] backdrop-blur-sm transition-all hover:border-[#e8b440]/35 hover:text-[#e8b440]"
              >
                {s}
              </button>
            ))}
          </div>
        )}

        {/* input */}
        <form
          onSubmit={(e) => { e.preventDefault(); void send(input); }}
          className="border-t border-white/[0.08] p-3"
        >
          <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send(input);
                }
              }}
              rows={1}
              placeholder="Ask the desk…"
              className="gdc-scroll max-h-28 min-h-[42px] flex-1 resize-none rounded-xl border border-white/[0.12] bg-white/[0.05] px-3.5 py-2.5 text-[12px] text-[#f4f7fa] placeholder:text-[#76828e] backdrop-blur-md outline-none transition-colors focus:border-[#e8b440]/40"
            />
            <button
              type="submit"
              disabled={busy || !input.trim()}
              className="flex h-[42px] w-[42px] shrink-0 cursor-pointer items-center justify-center rounded-xl border border-[#e8b440]/35 bg-[#e8b440]/[0.10] text-[#e8b440] transition-all hover:bg-[#e8b440]/[0.18] disabled:opacity-30"
              aria-label="Send"
            >
              ➤
            </button>
          </div>
          <div className="mt-1.5 text-center text-[8px] uppercase tracking-[0.16em] text-[#76828e]">
            education · not advice · cannot trade · never invents prices
          </div>
        </form>
      </aside>
    </>
  );
}
