"use client";

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

/* ----------------------------- types ----------------------------- */

interface Msg {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency?: number;
  grounded?: boolean;
  reasoning?: string;       // full reasoning transcript (collapsible)
}

interface SpotTick {
  ok: boolean;
  price?: number;
  source?: string;
  prev_close?: number;
  asof?: string;
}

interface DriverValueEntry {
  value: number;
  unit: string;
  source: string;
  display_k?: number;
}

interface DriverValues {
  ok: boolean;
  live?: Record<string, DriverValueEntry>;
  unavailable?: string[];
  fetched_at?: number;
  kind?: string;
}

interface NewsResp {
  ok: boolean;
  items?: { title: string; published: string; link: string; source?: string }[];
}

interface Overview {
  ok: boolean;
  span?: { days: number; from: string; to: string };
  days?: string[];
  histogram?: Record<string, number>;
  demo?: boolean;
  constitutionHash?: string;
  closedTradeCount?: number;
  balance?: number;
}

/** Live in-flight stream state. Cleared when a terminal event arrives. */
interface StreamState {
  phase: "reasoning" | "replying";
  model: string | null;
  grounded: boolean;
  reasoning: string;
  reply: string;
  startedAt: number;
  tokenCount: number;
}

/** Events emitted by /api/desk/chat as NDJSON lines. */
type ChatEvent =
  | { type: "start"; model: string; grounded: boolean }
  | { type: "reasoning"; delta: string }
  | { type: "content"; delta: string }
  | { type: "done"; model: string; latency_ms: number; grounded: boolean }
  | { type: "error"; error: string };

/* ----------------------------- constants ----------------------------- */

const SUGGESTIONS = [
  "What moves gold this week?",
  "Read my driver board like I'm new",
  "Why did the desk issue so few tickets?",
  "How do pros trade the London open?",
  "Explain real yields vs nominal yields",
  "What's CFTC managed-money positioning telling us?",
];

const DRIVER_META: Record<string, { label: string; decimals: number }> = {
  D1:  { label: "10y Real Yield",  decimals: 2 },
  D2:  { label: "DXY Dollar Idx", decimals: 2 },
  D3:  { label: "1-Mo Bill Yld",  decimals: 2 },
  D4:  { label: "10y Breakeven",  decimals: 2 },
  D5:  { label: "CFTC MM Net",    decimals: 0 },
  D9:  { label: "Hours → NFP",   decimals: 1 },
  D10: { label: "VIX",           decimals: 1 },
  D11: { label: "Session Liq",   decimals: 1 },
};

/* ----------------------------- component ----------------------------- */

export function ChatRoom() {
  const [messages, setMessages] = useState<Msg[]>([
    {
      role: "assistant",
      content:
        "I'm **The Desk** — 20 years on gold desks (COMEX, London spot, XAUUSD). Ask me anything about gold, macro, or what your harness is telling you. I'm grounded with the live spot price, the latest gold headlines, Treasury yields, CFTC positioning, and your journal — and I'll never invent numbers.\n\nEducation and research, not financial advice.",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [lastLatency, setLastLatency] = useState<number | null>(null);
  const [stream, setStream] = useState<StreamState | null>(null);
  /** When reasoning has finished and reply is streaming, collapse it. */
  const [reasoningCollapsed, setReasoningCollapsed] = useState(false);

  // left-rail live data
  const [spot, setSpot] = useState<SpotTick | null>(null);
  const [drivers, setDrivers] = useState<DriverValues | null>(null);
  const [news, setNews] = useState<NewsResp | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);
  const streamRef = useRef<StreamState | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  // keep streamRef in sync so the streaming reader can mutate latest state
  useEffect(() => { streamRef.current = stream; }, [stream]);

  /* -------- polling for left-rail data -------- */
  useEffect(() => {
    let cancelled = false;
    const refresh = async () => {
      try {
        const [s, d, n, o] = await Promise.all([
          fetch("/api/desk/price").then((r) => r.json()).catch(() => null),
          fetch("/api/desk/driver-values").then((r) => r.json()).catch(() => null),
          fetch("/api/desk/news").then((r) => r.json()).catch(() => null),
          fetch("/api/desk/overview").then((r) => r.json()).catch(() => null),
        ]);
        if (cancelled) return;
        if (s) setSpot(s);
        if (d) setDrivers(d);
        if (n) setNews(n);
        if (o) setOverview(o);
      } catch {
        /* soft-fail */
      }
    };
    refresh();
    const t = setInterval(refresh, 30_000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, []);

  /* -------- autoscroll on new messages / streaming tokens -------- */
  useEffect(() => {
    if (scrollRef.current) {
      // Only snap to bottom if user is already near the bottom (don't yank
      // them while they're reading earlier reasoning).
      const el = scrollRef.current;
      const nearBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < 220;
      if (nearBottom) el.scrollTop = el.scrollHeight;
    }
  }, [messages, busy, stream]);

  /**
   * Send a question and stream the answer back. Reads NDJSON events from
   * /api/desk/chat line-by-line, mutating `stream` state for each delta so
   * the reasoning + reply panels paint token-by-token.
   */
  const send = useCallback(
    async (text: string) => {
      const question = text.trim();
      if (!question || busy) return;
      setInput("");
      setError(null);
      setBusy(true);
      setReasoningCollapsed(false);
      const next: Msg[] = [...messages, { role: "user", content: question }];
      setMessages(next);

      const fresh: StreamState = {
        phase: "reasoning",
        model: null,
        grounded: false,
        reasoning: "",
        reply: "",
        startedAt: performance.now(),
        tokenCount: 0,
      };
      setStream(fresh);

      const ac = new AbortController();
      abortRef.current = ac;

      try {
        const res = await fetch("/api/desk/chat", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "Accept": "application/x-ndstream",
          },
          body: JSON.stringify({ messages: next }),
          signal: ac.signal,
        });
        if (!res.ok || !res.body) {
          const j = await res.json().catch(() => ({}));
          throw new Error(
            (j as { error?: string }).error || `chat endpoint ${res.status}`,
          );
        }

        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = "";
        let terminalSeen = false;
        let doneModel: string | null = null;
        let doneLatency: number | null = null;
        let doneGrounded = false;
        let errorText: string | null = null;

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let nlIdx: number;
          while ((nlIdx = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, nlIdx).trim();
            buf = buf.slice(nlIdx + 1);
            if (!line) continue;
            let evt: ChatEvent;
            try { evt = JSON.parse(line) as ChatEvent; }
            catch { continue; }
            switch (evt.type) {
              case "start":
                setStream((s) => s ? {
                  ...s, model: evt.model, grounded: evt.grounded,
                } : s);
                break;
              case "reasoning":
                setStream((s) => s ? {
                  ...s,
                  phase: "reasoning",
                  reasoning: s.reasoning + evt.delta,
                  tokenCount: s.tokenCount + 1,
                } : s);
                break;
              case "content":
                setStream((s) => {
                  if (!s) return s;
                  const next2: StreamState = {
                    ...s,
                    phase: "replying",
                    reply: s.reply + evt.delta,
                    tokenCount: s.tokenCount + 1,
                  };
                  return next2;
                });
                // collapse reasoning when first content token arrives
                setReasoningCollapsed(true);
                break;
              case "done":
                terminalSeen = true;
                doneModel = evt.model;
                doneLatency = evt.latency_ms;
                doneGrounded = evt.grounded;
                break;
              case "error":
                terminalSeen = true;
                errorText = evt.error;
                break;
            }
          }
        }

        if (errorText) {
          setError(errorText);
        } else if (terminalSeen) {
          const finalReply = streamRef.current?.reply?.trim() ?? "";
          if (!finalReply) {
            setError("the model returned an empty reply");
          } else {
            setMessages((m) => [
              ...m,
              {
                role: "assistant",
                content: finalReply,
                model: doneModel ?? undefined,
                latency: doneLatency ?? undefined,
                grounded: doneGrounded || undefined,
                reasoning: streamRef.current?.reasoning?.trim() || undefined,
              },
            ]);
            if (doneModel) setModel(doneModel);
            if (doneLatency !== null) setLastLatency(doneLatency);
          }
        } else {
          setError("stream ended unexpectedly");
        }
      } catch (e) {
        const err = e as Error;
        if (err.name === "AbortError") {
          // user aborted — don't surface an error, just stop
        } else {
          setError(err.message);
        }
      } finally {
        setStream(null);
        setBusy(false);
        abortRef.current = null;
      }
    },
    [messages, busy],
  );

  /* --------------------------- derived data --------------------------- */

  const spotPrice = spot?.ok && typeof spot.price === "number" ? spot.price : null;
  const spotChange =
    spotPrice && typeof spot?.prev_close === "number" ? spotPrice - spot.prev_close : null;
  const spotChangePct =
    spotPrice && typeof spot?.prev_close === "number" && spot.prev_close > 0
      ? (spotChange! / spot.prev_close) * 100
      : null;

  const driverRows = drivers?.ok && drivers.live
    ? (Object.keys(DRIVER_META).map((code) => {
        const meta = DRIVER_META[code];
        const entry = drivers.live?.[code];
        const unavailable = drivers.unavailable?.includes(code);
        return { code, ...meta, entry, unavailable };
      }))
    : [];

  const hoursToNfp = drivers?.ok ? drivers.live?.D9?.value ?? null : null;

  /* ------------------------------- render ------------------------------- */

  return (
    <div className="gdc-root gdc-grain flex h-screen flex-col overflow-hidden">
      {/* aurora field */}
      <div className="gdc-aurora" aria-hidden>
        <div className="band band-a" />
        <div className="band band-b" />
        <div className="band band-c" />
        <div className="orb orb-gold" />
        <div className="orb orb-teal" />
        <div className="orb orb-ember" />
        <div className="gdc-dots" />
        <div className="gdc-noise" />
      </div>

      <div className="relative z-10 flex h-screen flex-col">
        {/* ===== TOP BAR ===== */}
        <header className="gdc-panel-flat flex shrink-0 items-center gap-3 border-b border-white/[0.08] px-5 py-3">
          {/* vault-dial avatar */}
          <div className="relative flex h-11 w-11 items-center justify-center" aria-hidden>
            <div className="absolute inset-0 rounded-full border border-[#e8b440]/45 bg-gradient-to-b from-[#e8b440]/25 to-[#1a1408]/40 shadow-[0_0_28px_rgba(232,180,64,0.35)]" />
            <div className="absolute inset-0 rounded-full" style={{
              background:
                "conic-gradient(from -90deg, rgba(232,180,64,0.18) 0deg, transparent 30deg, rgba(232,180,64,0.35) 90deg, transparent 120deg, rgba(232,180,64,0.18) 180deg, transparent 210deg, rgba(232,180,64,0.35) 270deg, transparent 300deg)",
            }} />
            <span className="gdc-display relative z-10 text-[18px] font-semibold text-[#e8b440] gdc-glow-gold">Au</span>
          </div>

          <div className="leading-tight">
            <div className="gdc-script text-[22px] leading-none text-[#f0e6d2]">The Desk</div>
            <div className="gdc-spec mt-1 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
              20-yr gold veteran · free Zen models · live reasoning stream
            </div>
          </div>

          <div className="ml-auto flex items-center gap-2">
            {model && (
              <span className="gdc-chip gdc-data text-[#aab4bf]">
                <span className="text-[#76828e]">model</span>
                <span className="text-[#f4f7fa]">{model}</span>
              </span>
            )}
            {lastLatency !== null && (
              <span className="gdc-chip gdc-data text-[#aab4bf]">
                <span className="text-[#76828e]">lat</span>
                <span className="text-[#f4f7fa]">{(lastLatency / 1000).toFixed(1)}s</span>
              </span>
            )}
            <button
              onClick={() => {
                if (window.opener) {
                  window.opener.focus();
                  window.close();
                } else {
                  window.location.href = "/";
                }
              }}
              className="gdc-chip cursor-pointer border-[#e8b440]/35 text-[#e8b440] transition-all hover:bg-[#e8b440]/[0.12]"
              aria-label="Back to main deck"
            >
              <span aria-hidden>←</span> main deck
            </button>
          </div>
        </header>

        {/* ===== BODY: 2-column ===== */}
        <div className="flex min-h-0 flex-1">
          {/* ----- LEFT RAIL ----- */}
          <aside className="gdc-scroll hidden w-[340px] shrink-0 overflow-y-auto border-r border-white/[0.08] px-4 py-4 md:block">
            <div className="space-y-4">
              {/* Live Spot */}
              <section className="gdc-panel p-4">
                <div className="gdc-spec mb-2 flex items-center gap-2 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
                  <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />
                  Live Spot · XAUUSD
                </div>
                {spotPrice !== null ? (
                  <>
                    <div className="gdc-display-num text-[28px] font-semibold leading-none text-[#f4f7fa]">
                      ${spotPrice.toFixed(2)}
                    </div>
                    <div className="mt-1.5 flex items-baseline gap-2 text-[10px]">
                      <span
                        className="gdc-display-num font-semibold"
                        style={{
                          color:
                            (spotChange ?? 0) >= 0 ? "#3fb950" : "#f85149",
                        }}
                      >
                        {(spotChange ?? 0) >= 0 ? "+" : ""}
                        {spotChange?.toFixed(2)}
                      </span>
                      <span
                        className="gdc-display-num"
                        style={{
                          color:
                            (spotChange ?? 0) >= 0 ? "#3fb950" : "#f85149",
                        }}
                      >
                        ({spotChangePct! >= 0 ? "+" : ""}
                        {spotChangePct?.toFixed(2)}%)
                      </span>
                      <span className="text-[#76828e]">prev close</span>
                    </div>
                    <div className="mt-2 text-[8.5px] text-[#76828e]">
                      {spot?.source ?? "—"} {spot?.asof ? `· ${spot.asof}` : ""}
                    </div>
                  </>
                ) : (
                  <div className="text-[10px] italic text-[#76828e]">spot feed cooling…</div>
                )}
              </section>

              {/* Macro Drivers */}
              <section className="gdc-panel p-4">
                <div className="gdc-spec mb-2.5 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
                  Macro Drivers · live
                </div>
                {driverRows.length > 0 ? (
                  <div className="space-y-1.5">
                    {driverRows.map((d) => {
                      const v = d.entry?.value;
                      return (
                        <div
                          key={d.code}
                          className="flex items-center justify-between rounded-md border border-white/[0.05] bg-white/[0.022] px-2 py-1.5"
                        >
                          <div className="flex items-center gap-2">
                            <span className="gdc-data text-[8.5px] font-semibold text-[#76828e]">
                              {d.code}
                            </span>
                            <span className="text-[10px] text-[#e9edf2]">{d.label}</span>
                          </div>
                          <div className="flex items-baseline gap-1">
                            {v !== undefined && v !== null ? (
                              <span className="gdc-display-num text-[11px] font-semibold text-[#f4f7fa]">
                                {d.code === "D5" && d.entry?.display_k !== undefined
                                  ? `${d.entry.display_k}k`
                                  : v.toFixed(d.decimals)}
                                <span className="ml-0.5 text-[8px] text-[#76828e]">{d.entry?.unit}</span>
                              </span>
                            ) : (
                              <span className="text-[9px] italic text-[#76828e]">n/a</span>
                            )}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <div className="text-[10px] italic text-[#76828e]">drivers cooling…</div>
                )}
                {hoursToNfp !== null && hoursToNfp !== undefined && (
                  <div className="mt-2 text-[8.5px] text-[#76828e]">
                    {hoursToNfp < 24
                      ? `⚠ NFP in ${hoursToNfp.toFixed(1)}h`
                      : `next NFP in ${(hoursToNfp / 24).toFixed(1)}d`}
                  </div>
                )}
              </section>

              {/* Gold headlines */}
              <section className="gdc-panel p-4">
                <div className="gdc-spec mb-2.5 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
                  Gold Headlines
                </div>
                {news?.ok && news.items && news.items.length > 0 ? (
                  <ul className="space-y-2.5">
                    {news.items.slice(0, 6).map((it, i) => (
                      <li key={i} className="text-[10px] leading-tight">
                        <div className="text-[#e9edf2]">{it.title}</div>
                        <div className="gdc-data mt-0.5 text-[8px] uppercase tracking-[0.1em] text-[#76828e]">
                          {it.source ?? "yahoo"} · {it.published}
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="text-[10px] italic text-[#76828e]">tape cooling…</div>
                )}
              </section>

              {/* Harness journal */}
              <section className="gdc-panel p-4">
                <div className="gdc-spec mb-2.5 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
                  Harness Journal
                </div>
                {overview?.ok ? (
                  <div className="space-y-1.5 text-[10px]">
                    <Row label="phase" value="1 · deterministic" />
                    <Row label="journal days" value={`${overview.span?.days ?? 0}d`} />
                    <Row
                      label="closed trades"
                      value={`${overview.closedTradeCount ?? 0}`}
                    />
                    <Row
                      label="balance"
                      value={`$${(overview.balance ?? 0).toFixed(0)}`}
                    />
                    <Row label="demo feed" value={overview.demo ? "yes" : "no"} />
                    {overview.constitutionHash && (
                      <Row label="constitution" value={`⛓ ${overview.constitutionHash}`} />
                    )}
                  </div>
                ) : (
                  <div className="text-[10px] italic text-[#76828e]">harness cooling…</div>
                )}
                <div className="mt-2 text-[8.5px] text-[#76828e]">
                  Education only · cannot trade · nothing promoted by narrative
                </div>
              </section>
            </div>
          </aside>

          {/* ----- RIGHT RAIL (CHAT) ----- */}
          <main className="flex min-w-0 flex-1 flex-col">
            {/* messages */}
            <div ref={scrollRef} className="gdc-scroll flex-1 overflow-y-auto px-5 py-5">
              <div className="mx-auto max-w-[760px] space-y-4">
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={m.role === "user" ? "flex justify-end" : "flex justify-start"}
                  >
                    {m.role === "assistant" && (
                      <div className="mr-2.5 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#1a1408]/40 shadow-[0_0_16px_rgba(232,180,64,0.25)]">
                        <span className="gdc-display text-[10px] font-semibold text-[#e8b440]">Au</span>
                      </div>
                    )}
                    <div
                      className={`max-w-[88%] whitespace-pre-wrap break-words rounded-2xl px-4 py-3 text-[12.5px] leading-relaxed ${
                        m.role === "user"
                          ? "rounded-br-md border border-[#e8b440]/30 bg-[#e8b440]/[0.10] text-[#f4f7fa]"
                          : "rounded-bl-md border border-white/[0.09] bg-white/[0.045] text-[#e9edf2] backdrop-blur-md"
                      }`}
                    >
                      <MarkdownLite text={m.content} />
                      {/* Per-message collapsible reasoning (after a reply is finalized) */}
                      {m.role === "assistant" && m.reasoning && m.reasoning.length > 0 && (
                        <ReasoningCollapse summary={m.reasoning} />
                      )}
                      {m.role === "assistant" && (m.model || m.latency !== undefined) && (
                        <div className="gdc-data mt-2 border-t border-white/[0.05] pt-1.5 text-[8.5px] text-[#76828e]">
                          {m.model && <span>{m.model}</span>}
                          {m.model && m.latency !== undefined && <span> · </span>}
                          {m.latency !== undefined && (
                            <span>{(m.latency / 1000).toFixed(1)}s</span>
                          )}
                          {m.grounded && <span> · grounded w/ live data</span>}
                        </div>
                      )}
                    </div>
                  </div>
                ))}

                {/* ----- LIVE STREAM PANEL (reasoning + reply) ----- */}
                {stream && (
                  <LiveStreamPanel
                    stream={stream}
                    reasoningCollapsed={reasoningCollapsed}
                    onToggleCollapse={() => setReasoningCollapsed((v) => !v)}
                  />
                )}

                {/* fallback "thinking" badge before stream arrives */}
                {busy && !stream && (
                  <div className="flex justify-start">
                    <div className="mr-2.5 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#1a1408]/40">
                      <span className="gdc-display text-[10px] font-semibold text-[#e8b440]">Au</span>
                    </div>
                    <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-white/[0.09] bg-white/[0.045] px-4 py-3 backdrop-blur-md">
                      <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#e8b440]" />
                      <span className="text-[11px] italic text-[#9aa5b0]">
                        opening secure channel to the model…
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
            </div>

            {/* suggestions */}
            {messages.length <= 1 && !busy && (
              <div className="mx-auto w-full max-w-[760px] px-5 pb-2">
                <div className="gdc-spec mb-1.5 text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
                  Try asking
                </div>
                <div className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
                  {SUGGESTIONS.map((s) => (
                    <button
                      key={s}
                      onClick={() => void send(s)}
                      className="rounded-xl border border-white/[0.09] bg-white/[0.035] px-3 py-2 text-left text-[10.5px] text-[#9aa5b0] backdrop-blur-sm transition-all hover:border-[#e8b440]/35 hover:text-[#e8b440]"
                    >
                      {s}
                    </button>
                  ))}
                </div>
              </div>
            )}

            {/* input */}
            <form
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
              className="mx-auto w-full max-w-[760px] shrink-0 border-t border-white/[0.08] bg-[#08090d]/40 px-5 py-3 backdrop-blur-xl"
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
                  placeholder="Ask the desk… (Shift+Enter for newline)"
                  className="gdc-scroll max-h-32 min-h-[44px] flex-1 resize-none rounded-xl border border-white/[0.12] bg-white/[0.05] px-4 py-3 text-[12.5px] text-[#f4f7fa] placeholder:text-[#76828e] backdrop-blur-md outline-none transition-colors focus:border-[#e8b440]/40"
                />
                <button
                  type="submit"
                  disabled={busy || !input.trim()}
                  className="flex h-[44px] w-[44px] shrink-0 cursor-pointer items-center justify-center rounded-xl border border-[#e8b440]/35 bg-[#e8b440]/[0.10] text-[#e8b440] transition-all hover:bg-[#e8b440]/[0.18] disabled:opacity-30 disabled:cursor-not-allowed"
                  aria-label="Send"
                >
                  ➤
                </button>
              </div>
              <div className="gdc-spec mt-1.5 text-center text-[8px] uppercase tracking-[0.16em] text-[#76828e]">
                education · not advice · cannot trade · never invents prices
              </div>
            </form>
          </main>
        </div>
      </div>
    </div>
  );
}

/* ----------------------------- live stream panel ----------------------------- */

function LiveStreamPanel({
  stream,
  reasoningCollapsed,
  onToggleCollapse,
}: {
  stream: StreamState;
  reasoningCollapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const elapsed = ((performance.now() - stream.startedAt) / 1000).toFixed(1);
  const phaseLabel = stream.phase === "reasoning" ? "REASONING" : "REPLYING";
  const phaseColor =
    stream.phase === "reasoning" ? "#e8b440" : "#3fb950";
  const hasReply = stream.reply.length > 0;

  return (
    <div className="gdc-reasoning-enter flex justify-start">
      <div className="mr-2.5 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#1a1408]/40 shadow-[0_0_16px_rgba(232,180,64,0.25)]">
        <span className="gdc-display text-[10px] font-semibold text-[#e8b440]">Au</span>
      </div>
      <div className="max-w-[88%] flex-1">
        {/* HEADER STRIP — always visible */}
        <div
          className={`relative flex items-center gap-2 rounded-t-2xl border border-b-0 px-4 py-2 ${
            stream.phase === "reasoning"
              ? "border-[#e8b440]/35 bg-[#e8b440]/[0.05] gdc-reasoning-live"
              : "border-[#3fb950]/30 bg-[#3fb950]/[0.04]"
          }`}
        >
          {/* shimmer line across the top */}
          {stream.phase === "reasoning" && (
            <div className="gdc-reasoning-sheen" aria-hidden />
          )}
          {/* phase chip */}
          <span
            className="gdc-data inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 text-[8.5px] font-semibold uppercase tracking-[0.16em]"
            style={{
              color: phaseColor,
              borderColor: phaseColor + "55",
              backgroundColor: phaseColor + "14",
            }}
          >
            {phaseLabel}
          </span>
          {/* three breathing dots while reasoning */}
          {stream.phase === "reasoning" && !hasReply && (
            <span className="flex items-center gap-0.5" aria-hidden>
              <span className="gdc-think-dot-1 h-1 w-1 rounded-full bg-[#e8b440]" />
              <span className="gdc-think-dot-2 h-1 w-1 rounded-full bg-[#e8b440]" />
              <span className="gdc-think-dot-3 h-1 w-1 rounded-full bg-[#e8b440]" />
            </span>
          )}
          {/* model + elapsed */}
          {stream.model && (
            <span className="gdc-data text-[8.5px] text-[#aab4bf]">
              <span className="text-[#76828e]">model </span>
              <span className="text-[#f4f7fa]">{stream.model}</span>
            </span>
          )}
          <span className="gdc-data text-[8.5px] text-[#76828e]">
            {elapsed}s
          </span>
          {stream.tokenCount > 0 && (
            <span className="gdc-data text-[8.5px] text-[#76828e]">
              · {stream.tokenCount} tok
            </span>
          )}
          {/* collapse toggle */}
          {hasReply && (
            <button
              onClick={onToggleCollapse}
              className="gdc-data ml-auto cursor-pointer text-[8.5px] uppercase tracking-[0.14em] text-[#76828e] transition-colors hover:text-[#e8b440]"
            >
              {reasoningCollapsed ? "▾ show reasoning" : "▴ hide reasoning"}
            </button>
          )}
        </div>

        {/* REASONING BODY (collapses when reply starts) */}
        <div
          className={`gdc-reasoning-collapsed relative ${
            reasoningCollapsed ? "is-collapsed" : ""
          } border-l-2 border-l-[#e8b440]/45 bg-[#1a1408]/30 px-4 py-3 ${
            hasReply ? "rounded-b-0 border-b-0" : "rounded-b-2xl border-b border-[#e8b440]/35"
          }`}
        >
          {/* breathing gold rule on the left */}
          <span
            className="gdc-reasoning-rule pointer-events-none absolute left-0 top-3 bottom-3 w-[2px] rounded-full bg-[#e8b440]/60"
            aria-hidden
          />
          <div className="gdc-data whitespace-pre-wrap break-words pl-3 text-[10.5px] leading-relaxed text-[#d4b96a]">
            {stream.reasoning ? (
              <>
                <span className="gdc-token-in">{stream.reasoning}</span>
                {stream.phase === "reasoning" && (
                  <span className="gdc-reasoning-caret" aria-hidden />
                )}
              </>
            ) : (
              <span className="italic text-[#76828e]">
                reasoning… (free model, first tokens can take 5-15s)
              </span>
            )}
          </div>
        </div>

        {/* REPLY BODY (always visible once reply starts) */}
        {hasReply && (
          <div className="whitespace-pre-wrap break-words rounded-b-2xl border border-[#3fb950]/25 bg-white/[0.05] px-4 py-3 text-[12.5px] leading-relaxed text-[#e9edf2] backdrop-blur-md">
            <span className="gdc-token-in">{stream.reply}</span>
            {stream.phase === "replying" && (
              <span className="gdc-reasoning-caret" aria-hidden style={{
                background: "linear-gradient(180deg, #b9f6c0, #3fb950)",
                boxShadow: "0 0 8px rgba(63, 185, 80, 0.65)",
              }} />
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ----------------------------- per-message collapsible reasoning ----------------------------- */

function ReasoningCollapse({ summary }: { summary: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="mt-2.5 border-t border-white/[0.06] pt-2">
      <button
        onClick={() => setOpen((v) => !v)}
        className="gdc-data flex cursor-pointer items-center gap-1.5 text-[8.5px] uppercase tracking-[0.14em] text-[#76828e] transition-colors hover:text-[#e8b440]"
      >
        <span aria-hidden>{open ? "▾" : "▸"}</span>
        reasoning · {summary.length} chars
      </button>
      {open && (
        <div className="mt-2 border-l-2 border-[#e8b440]/35 pl-3">
          <div className="gdc-data max-h-[260px] overflow-y-auto whitespace-pre-wrap break-words text-[10px] leading-relaxed text-[#d4b96a]">
            {summary}
          </div>
        </div>
      )}
    </div>
  );
}

/* ----------------------------- helpers ----------------------------- */

function StatChip({
  label,
  value,
  decimals = 2,
  suffix = "",
}: {
  label: string;
  value: number | null | undefined;
  decimals?: number;
  suffix?: string;
}) {
  return (
    <div className="flex items-center justify-between rounded-md border border-white/[0.06] bg-white/[0.025] px-2 py-1">
      <span className="gdc-data text-[8.5px] uppercase tracking-[0.1em] text-[#76828e]">{label}</span>
      <span className="gdc-display-num text-[10px] text-[#f4f7fa]">
        {value !== null && value !== undefined ? `${value.toFixed(decimals)}${suffix}` : "—"}
      </span>
    </div>
  );
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between">
      <span className="gdc-data text-[#76828e]">{label}</span>
      <span className="gdc-data text-[#f4f7fa]">{value}</span>
    </div>
  );
}

/** Tiny markdown renderer — bold + inline code + line breaks only (no XSS surface). */
function MarkdownLite({ text }: { text: string }) {
  const lines = text.split(/\n/);
  return (
    <>
      {lines.map((line, li) => (
        <span key={li}>
          {renderInline(line)}
          {li < lines.length - 1 && <br />}
        </span>
      ))}
    </>
  );
}

function renderInline(text: string): ReactNode {
  const tokens: ReactNode[] = [];
  const re = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) tokens.push(text.slice(last, m.index));
    const seg = m[0];
    if (seg.startsWith("**")) {
      tokens.push(
        <strong key={k++} className="font-semibold text-[#f4f7fa]">
          {seg.slice(2, -2)}
        </strong>,
      );
    } else if (seg.startsWith("`")) {
      tokens.push(
        <code key={k++} className="gdc-data rounded bg-white/[0.08] px-1 py-0.5 text-[11px] text-[#e8b440]">
          {seg.slice(1, -1)}
        </code>,
      );
    }
    last = m.index + seg.length;
  }
  if (last < text.length) tokens.push(text.slice(last));
  return tokens;
}
