"use client";

import { useCallback, useEffect, useState } from "react";

interface NewsItem {
  title: string;
  link: string;
  published: string;
  source: string;
}

function ago(published: string): string {
  const t = Date.parse(published);
  if (Number.isNaN(t)) return published.slice(0, 16);
  const h = Math.floor((Date.now() - t) / 3600_000);
  if (h < 1) return "just now";
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

export function NewsPanel() {
  const [items, setItems] = useState<NewsItem[]>([]);
  const [ok, setOk] = useState<boolean | null>(null);

  const load = useCallback(async () => {
    try {
      const r = await fetch("/api/desk/news").then((x) => x.json());
      if (r.ok) {
        setItems(r.items as NewsItem[]);
        setOk(true);
      } else setOk(false);
    } catch {
      setOk(false);
    }
  }, []);

  useEffect(() => {
    const kick = setTimeout(() => void load(), 0);
    const t = setInterval(() => void load(), 10 * 60_000); // refresh 10min
    return () => { clearTimeout(kick); clearInterval(t); };
  }, [load]);

  return (
    <div className="gdc-panel overflow-hidden">
      <div className="gdc-sheen" aria-hidden style={{ "--sheen-delay": "5.5s", "--sheen-dur": "12s" } as React.CSSProperties} />
      <div className="flex flex-wrap items-baseline gap-3 border-b border-white/[0.08] px-5 py-2.5">
        <div className="flex items-baseline gap-3">
          <span className="gdc-display text-[17px] italic text-[#f4f7fa]">The tape</span>
          <span className="gdc-kicker">live gold headlines · yahoo finance rss · free</span>
        </div>
        <span className="ml-auto flex items-center gap-2 text-[8.5px] font-semibold uppercase tracking-[0.18em] text-[#76828e]">
          {ok === true && <span className="gdc-live-dot h-1.5 w-1.5 rounded-full bg-[#3fb950]" />}
          {ok === null ? "loading…" : ok ? "live" : "feed unreachable"}
        </span>
      </div>
      <div className="gdc-scroll max-h-[300px] divide-y divide-white/[0.05] overflow-y-auto">
        {items.map((n, i) => (
          <a
            key={i}
            href={n.link}
            target="_blank"
            rel="noopener noreferrer"
            className="group flex items-baseline gap-3 px-5 py-2.5 transition-colors hover:bg-white/[0.035]"
          >
            <span className="gdc-data w-14 shrink-0 text-[9px] text-[#76828e]">{ago(n.published)}</span>
            <span className="flex-1 text-[11.5px] leading-snug text-[#dfe5eb] transition-colors group-hover:text-white">
              {n.title}
              <span className="gdc-data ml-2 text-[8.5px] text-[#76828e]">{n.source}</span>
            </span>
            <span className="shrink-0 text-[10px] text-[#76828e] opacity-0 transition-opacity group-hover:opacity-100">↗</span>
          </a>
        ))}
        {ok === false && (
          <div className="px-5 py-6 text-center text-[11px] italic text-[#76828e]">
            the RSS feed is unreachable right now — the desk keeps running without it
          </div>
        )}
        {ok === null && items.length === 0 && (
          <div className="px-5 py-6 text-center text-[11px] italic text-[#76828e]">loading the tape…</div>
        )}
      </div>
    </div>
  );
}
