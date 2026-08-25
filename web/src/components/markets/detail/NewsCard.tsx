"use client";

import { memo } from "react";
import type { DetailNews, DetailNewsItem } from "../types";

/** "Mon, 24 Aug 2026 21:03:00 +0000" → "Aug 24 · 21:03 UTC". */
function fmtNewsTime(published?: string): string {
  if (!published) return "";
  const m = /(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+(\d{2}:\d{2})/.exec(published);
  if (!m) return published.slice(0, 22);
  return `${m[2]} ${m[1]} · ${m[4]} UTC`;
}

function NewsItemRow({ item }: { item: DetailNewsItem }) {
  const time = fmtNewsTime(item.published);
  return (
    <li className="border-b border-[#1a1f2c] last:border-b-0">
      <a
        href={item.link || undefined}
        target="_blank"
        rel="noopener noreferrer"
        className="group flex flex-wrap items-baseline gap-x-3 gap-y-0.5 px-1 py-2 transition-colors hover:bg-white/[0.03]"
      >
        <span className="gdc-data shrink-0 text-[9px] uppercase tracking-[0.08em] text-[#8a93a6]">
          {time || "—"}
        </span>
        <span className="min-w-0 flex-1 text-[11.5px] leading-snug text-[#c6cedb] transition-colors group-hover:text-[#e8ecf4]">
          {item.title}
        </span>
        <span className="shrink-0 text-[9px] text-[#c8a04b] opacity-0 transition-opacity group-hover:opacity-100">
          ↗
        </span>
      </a>
    </li>
  );
}

const MemoNewsItemRow = memo(NewsItemRow);

/** Per-symbol headlines card (piece 3 / build 3): keyless Yahoo RSS,
 * max 8 items, fail-soft — hidden entirely when the feed serves no
 * headlines (NSE-listed symbols, minor FX pairs). */
function NewsCardImpl({
  symbol,
  news,
}: {
  symbol: string;
  news?: DetailNews;
}) {
  const items = news?.items ?? [];
  if (items.length === 0) return null;
  return (
    <section className="gdc-panel px-3.5 pb-2 pt-3" aria-label="Symbol news">
      <div className="mb-1 flex flex-wrap items-center gap-x-3 gap-y-1">
        <h2 className="gdc-spec" style={{ fontSize: "10px", color: "#c8a04b" }}>
          News — {symbol}
        </h2>
        <span className="h-px flex-1 bg-[#1a1f2c]" />
        <span className="text-[9px] uppercase tracking-[0.18em] text-[#8a93a6]">
          Yahoo RSS · {items.length} shown · fail-soft
        </span>
      </div>
      <ul>
        {items.map((it, i) => (
          <MemoNewsItemRow key={`${i}-${it.title}`} item={it} />
        ))}
      </ul>
    </section>
  );
}

export const NewsCard = memo(NewsCardImpl);
