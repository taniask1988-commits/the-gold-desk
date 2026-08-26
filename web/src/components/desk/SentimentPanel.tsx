"use client";

import { memo, useCallback, useEffect, useState } from "react";

interface TermFired {
  term: string;
  contribution: number;
  multiplier: number;
  intensifier?: string | null;
  negated: boolean;
  subjective: boolean;
}

interface SentimentAsset {
  symbol: string;
  name: string;
  confidence: number;
  tier: string;
  matched_term: string;
  relevance: number;
  position: number;
  mentions: number;
}

interface SentimentScore {
  ok: boolean;
  headline?: string;
  polarity?: number;
  magnitude?: number;
  subjectivity?: number;
  label?: string;
  assets?: SentimentAsset[];
  relevance?: number;
  novelty?: number;
  terms_fired?: TermFired[];
  llm_fallback_used?: boolean;
  llm_fallback_failed?: boolean;
  llm_polarity?: number;
  n_tokens?: number;
  error?: string;
}

interface TapeStory {
  headline?: string;
  polarity?: number;
  label?: string;
  novelty?: number;
  feed_symbol?: string;
  published?: string;
  assets?: SentimentAsset[];
}

interface TapeResult {
  ok: boolean;
  as_of?: string;
  n_feeds?: number;
  n_feeds_requested?: number;
  n_stories?: number;
  stories?: TapeStory[];
  error?: string;
}

const GAUGE_W = 260;
const GAUGE_H = 26;

function polarityColor(p: number | undefined): string {
  if (p === undefined || p === null) return "#76828e";
  if (p > 0.15) return "#3fb950";
  if (p < -0.15) return "#f85149";
  return "#7ab5e0";
}

/** −1..+1 horizontal gauge with a center mark and a needle at `p`. */
function PolarityGauge({ p }: { p: number }) {
  const x = ((Math.max(-1, Math.min(1, p)) + 1) / 2) * GAUGE_W;
  const col = polarityColor(p);
  return (
    <svg width={GAUGE_W} height={GAUGE_H} className="overflow-visible">
      <rect x={0} y={10} width={GAUGE_W} height={6} rx={3} fill="#1a1f2c" />
      <rect x={0} y={10} width={x} height={6} rx={3} fill={col} opacity={0.55} />
      <line x1={GAUGE_W / 2} y1={6} x2={GAUGE_W / 2} y2={20} stroke="#76828e" strokeWidth={1} />
      <polygon points={`${x - 5},4 ${x + 5},4 ${x},14`} fill={col} />
      <text x={2} y={GAUGE_H - 1} fontSize={8} fill="#76828e">−1</text>
      <text x={GAUGE_W - 10} y={GAUGE_H - 1} fontSize={8} fill="#76828e">+1</text>
    </svg>
  );
}

function MiniBar({ label, v, color = "#7ab5e0" }: { label: string; v: number; color?: string }) {
  const pct = Math.round(Math.max(0, Math.min(1, v)) * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="gdc-kicker w-[74px] shrink-0 text-[7.5px]">{label}</span>
      <div className="h-1.5 flex-1 rounded bg-[#1a1f2c]">
        <div className="h-1.5 rounded" style={{ width: `${pct}%`, background: color }} />
      </div>
      <span className="gdc-data w-8 text-right text-[9px] text-[#9aa6b3]">{v.toFixed(2)}</span>
    </div>
  );
}

function SentimentPanelImpl() {
  const [headline, setHeadline] = useState("Gold surges as Fed signals dovish pivot");
  const [score, setScore] = useState<SentimentScore | null>(null);
  const [busy, setBusy] = useState(false);
  const [tape, setTape] = useState<TapeResult | null>(null);
  const [tapeBusy, setTapeBusy] = useState(false);

  const scoreIt = useCallback(async (h: string) => {
    const trimmed = h.trim();
    if (!trimmed) return;
    setBusy(true);
    try {
      const r = await fetch("/api/desk/news/sentiment", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ headline: trimmed }),
      }).then((x) => x.json());
      setScore(r as SentimentScore);
    } catch {
      setScore({ ok: false, error: "transport failure" });
    } finally {
      setBusy(false);
    }
  }, []);

  const loadTape = useCallback(async () => {
    setTapeBusy(true);
    try {
      const r = await fetch("/api/desk/news/sentiment/tape?limit=20").then((x) => x.json());
      setTape(r as TapeResult);
    } catch {
      setTape({ ok: false, error: "transport failure" });
    } finally {
      setTapeBusy(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void scoreIt("Gold surges as Fed signals dovish pivot"), 0);
    return () => clearTimeout(kick);
  }, [scoreIt]);

  const pol = score?.polarity ?? 0;

  return (
    <div className="gdc-panel space-y-3 p-4">
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] pb-2">
        <span className="gdc-display text-[17px] italic text-[#f4f7fa]">News sentiment</span>
        <span className="gdc-kicker">
          nlp polarity · magnitude · subjectivity · 8-asset detection · novelty — keyless, llm fallback
        </span>
        <span className="ml-auto text-[8.5px] uppercase tracking-[0.18em] text-[#76828e]">
          {busy ? "scoring…" : score?.ok ? "scored" : score === null ? "loading…" : "error"}
        </span>
      </div>

      {/* headline input */}
      <div className="flex flex-wrap gap-2">
        <input
          value={headline}
          onChange={(e) => setHeadline(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") void scoreIt(headline); }}
          placeholder="paste a market headline…"
          className="gdc-data min-w-[260px] flex-1 rounded border border-[#232a3a] bg-[#12161f] px-2.5 py-1.5 text-[11px] text-[#f4f7fa] outline-none focus:border-[#3d4a5e]"
        />
        <button
          onClick={() => void scoreIt(headline)}
          disabled={busy}
          className="rounded bg-[#1f2632] px-3 py-1.5 text-[10px] uppercase tracking-[0.15em] text-[#f4f7fa] hover:bg-[#273040] disabled:opacity-50"
        >{busy ? "scoring…" : "score"}</button>
        <button
          onClick={() => void loadTape()}
          disabled={tapeBusy}
          className="rounded bg-[#1a1f2c] px-3 py-1.5 text-[10px] uppercase tracking-[0.15em] text-[#9aa6b3] hover:text-[#f4f7fa] disabled:opacity-50"
        >{tapeBusy ? "loading tape…" : "score live tape"}</button>
      </div>

      {score && !score.ok && (
        <div className="text-[10px] italic text-[#f85149]">{score.error || "score failed"}</div>
      )}

      {score?.ok && (
        <div className="grid gap-4 lg:grid-cols-2">
          {/* left: gauge + signal bars */}
          <div className="space-y-2.5">
            <div className="flex items-center gap-3">
              <PolarityGauge p={pol} />
              <span className="gdc-display-num text-[19px]" style={{ color: polarityColor(pol) }}>
                {pol >= 0 ? "+" : ""}{pol.toFixed(3)}
              </span>
              <span
                className="rounded px-1.5 py-0.5 text-[8.5px] uppercase tracking-[0.15em]"
                style={{
                  color: polarityColor(pol),
                  background: "rgba(255,255,255,0.04)",
                }}
              >{score.label}</span>
            </div>
            <MiniBar label="magnitude" v={score.magnitude ?? 0} color="#7ab5e0" />
            <MiniBar label="subjectivity" v={score.subjectivity ?? 0} color="#a78bfa" />
            <MiniBar label="relevance" v={score.relevance ?? 0} color="#3fb950" />
            <MiniBar label="novelty" v={score.novelty ?? 0} color="#d9a343" />
            {score.llm_fallback_used && (
              <div className="text-[9px] text-[#7ab5e0]">
                llm 2nd opinion: blended 50/50 · llm polarity {score.llm_polarity?.toFixed(3)}
              </div>
            )}
            {score.llm_fallback_failed && (
              <div className="text-[9px] italic text-[#d9a343]">
                llm 2nd opinion failed — local score kept (fail-closed)
              </div>
            )}
          </div>

          {/* right: assets + terms fired */}
          <div className="space-y-2.5">
            <div className="gdc-kicker text-[#9aa6b3]">assets detected</div>
            {(score.assets || []).length === 0 ? (
              <div className="text-[10px] italic text-[#76828e]">none of the 8 desk instruments</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {(score.assets || []).map((a) => (
                  <span key={a.symbol} className="gdc-data rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[9px] text-[#9aa6b3]">
                    <span className="text-[#f4f7fa]">{a.symbol}</span>
                    {" "}{a.name} · conf {a.confidence.toFixed(1)} ({a.tier}) · rel {a.relevance.toFixed(2)}
                  </span>
                ))}
              </div>
            )}
            <div className="gdc-kicker text-[#9aa6b3]">terms fired (explanation)</div>
            {(score.terms_fired || []).length === 0 ? (
              <div className="text-[10px] italic text-[#76828e]">no lexicon terms matched</div>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {(score.terms_fired || []).map((t, i) => (
                  <span key={`${t.term}-${i}`} className="gdc-data rounded bg-[#1a1f2c] px-1.5 py-0.5 text-[9px]">
                    <span className="text-[#f4f7fa]">{t.term}</span>
                    <span style={{ color: t.contribution >= 0 ? "#3fb950" : "#f85149" }}>
                      {" "}{t.contribution >= 0 ? "+" : ""}{t.contribution.toFixed(2)}
                    </span>
                    {t.multiplier !== 1 && <span className="text-[#d9a343]"> ×{t.multiplier}</span>}
                    {t.negated && <span className="text-[#f85149]"> neg</span>}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* tape */}
      {tape && (
        <div className="space-y-2 border-t border-white/[0.08] pt-2">
          <div className="flex flex-wrap items-baseline gap-3">
            <span className="gdc-kicker text-[#9aa6b3]">live tape</span>
            <span className="text-[9.5px] text-[#76828e]">
              {tape.ok
                ? `${tape.n_stories} stories · ${tape.n_feeds}/${tape.n_feeds_requested} feeds${tape.as_of ? ` · ${tape.as_of.slice(11, 19)}Z` : ""}`
                : "unreachable"}
            </span>
          </div>
          {tape.stories && tape.stories.length > 0 && (
            <div className="max-h-56 space-y-1 overflow-y-auto pr-1">
              {tape.stories.map((s, i) => (
                <div key={i} className="flex items-baseline gap-2">
                  <span className="gdc-data w-12 shrink-0 text-right text-[9.5px]" style={{ color: polarityColor(s.polarity) }}>
                    {s.polarity !== undefined ? `${s.polarity >= 0 ? "+" : ""}${s.polarity.toFixed(2)}` : "—"}
                  </span>
                  <span className="gdc-data w-[72px] shrink-0 truncate text-[8.5px] text-[#76828e]">{s.feed_symbol}</span>
                  <span className="flex-1 truncate text-[10px] text-[#9aa6b3]" title={s.headline}>{s.headline}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export const SentimentPanel = memo(SentimentPanelImpl);
