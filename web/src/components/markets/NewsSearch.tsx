"use client";

import { memo, useCallback, useEffect, useRef, useState } from "react";
import { TerminalModal } from "./Modal";
import { displaySymbol } from "./lib";

/* -------------------------------------------------------------- types */

interface NewsItem {
  title: string;
  link?: string;
  published?: string;
  published_ts?: number;
  source?: string;
}

interface NewsPayload {
  ok: boolean;
  query?: string;
  matched?: string[];
  topic?: boolean;
  items?: NewsItem[];
  feeds_ok?: number;
  error?: string;
}

/* -------------------------------------------------------------- bits */

function fmtAgo(published?: string, ts?: number): string {
  // recency string: "12m ago" / "3h ago" / "2d ago" / raw date tail
  const epoch = typeof ts === "number" && ts > 0 ? ts * 1000 : NaN;
  if (Number.isFinite(epoch)) {
    const mins = Math.max(0, Math.round((Date.now() - epoch) / 60000));
    if (mins < 60) return `${mins}m ago`;
    if (mins < 60 * 24) return `${Math.round(mins / 60)}h ago`;
    return `${Math.round(mins / (60 * 24))}d ago`;
  }
  if (!published) return "";
  const tail = published.split(" ").slice(0, 4).join(" "); // "Mon, 25 Aug 2026"
  return tail.replace(/^[A-Za-z]{3},\s*/, "");
}

function NewsRowImpl({ item }: { item: NewsItem }) {
  const when = fmtAgo(item.published, item.published_ts);
  const sym = (item.source ?? "").replace("Yahoo Finance · ", "");
  const body = (
    <>
      <span className="min-w-0 flex-1 truncate text-[11.5px] leading-snug text-[#c6cedb]">
        {item.title}
      </span>
      <span className="gdc-data w-[92px] shrink-0 truncate text-[9.5px] text-[#8a93a6] sm:w-[130px]">
        {sym}
      </span>
      <span className="gdc-data w-[62px] shrink-0 text-right text-[9.5px] tabular-nums text-[#6f7987]">
        {when}
      </span>
    </>
  );
  return item.link ? (
    <a
      href={item.link}
      target="_blank"
      rel="noopener noreferrer"
      className="flex items-center gap-3 border-b border-[#141821] px-1 py-[7px] transition-colors hover:bg-white/[0.03] last:border-b-0"
      title={`${item.title} — ${item.source ?? ""}`}
    >
      {body}
      <span className="shrink-0 text-[10px] text-[#c8a04b]" aria-hidden>
        ↗
      </span>
    </a>
  ) : (
    <li className="flex items-center gap-3 border-b border-[#141821] px-1 py-[7px] last:border-b-0">
      {body}
    </li>
  );
}
const NewsRow = memo(NewsRowImpl);

/* -------------------------------------------------------------- modal */

/**
 * NSE-style news search modal (Bloomberg NSE analog, piece 5): query
 * box → GET /api/desk/news-search?q= — the python side runs TWO
 * passes: fuzzy registry match (symbol/name/alias/sector → Yahoo RSS
 * per-symbol feeds + the general stream) and a TOPIC pass (Google
 * News RSS full-text search, headline-text matched — GAUNTLET-P15:
 * "news inflation" now returns inflation headlines, not an error).
 * Merged, recency-ranked, capped at 20. Links open in a new tab.
 */
function NewsSearchImpl({
  open,
  onClose,
  initialQuery,
}: {
  open: boolean;
  onClose: () => void;
  initialQuery: string;
}) {
  const [query, setQuery] = useState("");
  const [data, setData] = useState<NewsPayload | null>(null);
  const [searching, setSearching] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const run = useCallback(async (q: string) => {
    const s = q.trim();
    if (!s) return;
    setSearching(true);
    try {
      const r = await fetch(
        `/api/desk/news-search?q=${encodeURIComponent(s.slice(0, 64))}`,
        { cache: "no-store" },
      );
      setData((await r.json()) as NewsPayload);
    } catch {
      setData({ ok: false, error: "network error" });
    } finally {
      setSearching(false);
    }
  }, []);

  // opening with a prefilled query (palette "news bitcoin") searches
  // immediately; a bare "news" just focuses the box
  useEffect(() => {
    if (!open) return;
    setQuery(initialQuery);
    setData(null);
    if (initialQuery) void run(initialQuery);
    const t = setTimeout(() => inputRef.current?.focus(), 30);
    return () => clearTimeout(t);
  }, [open, initialQuery, run]);

  return (
    <TerminalModal
      open={open}
      onClose={onClose}
      title="News Search"
      subtitle="NSE — topic / symbol"
      label="News search"
      width="max-w-[760px]"
    >
      <form
        className="mb-3 flex items-center gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          void run(query);
        }}
      >
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search news — bitcoin, gold, nifty, aapl nvda, eur/usd…"
          className="gdc-data min-w-0 flex-1 rounded-sm border border-[#1a1f2c] bg-[#0b0e14] px-3 py-2 text-[12px] text-[#e8ecf4] outline-none transition-colors placeholder:text-[#5a6272] focus:border-[#c8a04b]/50"
          aria-label="News search query"
          maxLength={64}
        />
        <button
          type="submit"
          disabled={searching || !query.trim()}
          className="gdc-chip cursor-pointer border-[#c8a04b]/45 px-4 py-1.5 text-[11px] font-semibold text-[#e2c074] transition-colors hover:bg-[#c8a04b]/[0.12] disabled:cursor-wait disabled:opacity-60"
        >
          {searching ? "Searching…" : "Search"}
        </button>
      </form>

      {data && !data.ok && (
        <div className="rounded-sm border border-[#B85C5C]/30 bg-[#B85C5C]/[0.06] px-3 py-2 text-[11px] text-[#D98484]">
          ⚠ {data.error || "news feeds unreachable"}
        </div>
      )}
      {data?.ok && (
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[9px] uppercase tracking-[0.16em] text-[#6f7987]">
            matched:
          </span>
          {(data.matched ?? []).length === 0 && (
            <span className="text-[9.5px] text-[#8a93a6]">
              {data.topic
                ? `topic — headline text for "${data.query ?? ""}"`
                : "none — general stream"}
            </span>
          )}
          {(data.matched ?? []).map((s) => (
            <span
              key={s}
              className="gdc-data rounded-sm border border-[#c8a04b]/30 px-1.5 py-[1px] text-[9px] font-semibold text-[#e2c074]"
              title={s}
            >
              {displaySymbol(s)}
            </span>
          ))}
          {data.topic && (data.matched ?? []).length > 0 && (
            <span
              className="gdc-data rounded-sm border border-[#6fa97a]/35 px-1.5 py-[1px] text-[9px] font-semibold text-[#6fa97a]"
              title={`headlines whose text matches "${data.query ?? ""}" (Google News)`}
            >
              +TOPIC
            </span>
          )}
          <span className="ml-auto gdc-data text-[9px] tabular-nums text-[#6f7987]">
            {(data.items ?? []).length} headlines
          </span>
        </div>
      )}
      {searching && (
        <div className="flex items-center gap-3 px-2 py-6">
          <span className="gdc-spec">Searching feeds</span>
          <span className="gdc-breathe h-[2px] w-[90px] rounded-full bg-[#c8a04b]" />
        </div>
      )}
      {!searching && data?.ok && (data.items ?? []).length === 0 && (
        <div className="px-2 py-6 text-center text-[11px] uppercase tracking-[0.14em] text-[#8a93a6]">
          no headlines served for this query
        </div>
      )}
      {!searching && data?.ok && (data.items ?? []).length > 0 && (
        <ul className="max-h-[52vh] overflow-y-auto">
          {(data.items ?? []).map((it, i) => (
            <NewsRow key={`${it.title}-${i}`} item={it} />
          ))}
        </ul>
      )}
    </TerminalModal>
  );
}

export const NewsSearch = memo(NewsSearchImpl);
