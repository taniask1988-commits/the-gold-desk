"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";

/* ----------------------------- types ----------------------------- */

interface Msg {
  role: "user" | "assistant";
  content: string;
  model?: string;
  latency?: number;
  grounded?: boolean;
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

/* ----------------------------- constants ----------------------------- */

const SUGGESTIONS = [
  "What moves gold this week?",
  "Read my driver board like I'm new",
  "Why did the desk issue so few tickets?",
  "How do pros trade the London open?",
  "Explain real yields vs nominal yields",
  "What's CFTC managed-money positioning telling us?",
];

// Driver code → human label (matches src/gold_desk/data/driver_feeds.py _collect)
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

  // left-rail live data
  const [spot, setSpot] = useState<SpotTick | null>(null);
  const [drivers, setDrivers] = useState<DriverValues | null>(null);
  const [news, setNews] = useState<NewsResp | null>(null);
  const [overview, setOverview] = useState<Overview | null>(null);

  const scrollRef = useRef<HTMLDivElement>(null);

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

  /* -------- autoscroll on new messages -------- */
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [messages, busy]);

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
          setModel(r.model ?? null);
          setLastLatency(r.latency_ms ?? null);
          setMessages((m) => [
            ...m,
            {
              role: "assistant",
              content: r.reply,
              model: r.model,
              latency: r.latency_ms,
              grounded: r.grounded,
            },
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

  /* --------------------------- derived data --------------------------- */

  const spotPrice = spot?.ok && typeof spot.price === "number" ? spot.price : null;
  const spotChange =
    spotPrice && typeof spot?.prev_close === "number" ? spotPrice - spot.prev_close : null;
  const spotChangePct =
    spotPrice && typeof spot?.prev_close === "number" && spot.prev_close > 0
      ? (spotChange! / spot.prev_close) * 100
      : null;

  // live driver entries in the order we want to show them
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
              20-yr gold veteran · free Zen models · grounded w/ live data
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
                {busy && (
                  <div className="flex justify-start">
                    <div className="mr-2.5 mt-1 flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-[#e8b440]/40 bg-gradient-to-b from-[#e8b440]/25 to-[#1a1408]/40">
                      <span className="gdc-display text-[10px] font-semibold text-[#e8b440]">Au</span>
                    </div>
                    <div className="flex items-center gap-2 rounded-2xl rounded-bl-md border border-white/[0.09] bg-white/[0.045] px-4 py-3 backdrop-blur-md">
                      <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#e8b440]" />
                      <span className="text-[11px] italic text-[#9aa5b0]">
                        the desk is thinking… (free model, can take ~10-30s)
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
  // split on lines, then split each line into segments by ** ** and ` ` ` `
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
  // tokenize on **bold** and `code`
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
