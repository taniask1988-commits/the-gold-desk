"""MARKET GAUNTLET news search (NSE-style) — query → merged headlines.

The Bloomberg NSE function takes a topic or symbol and returns the
news stream for it. Our analog: `search_news(query)` runs TWO passes.

SYMBOL PASS — fuzzy-match the query against the 67-symbol registry
(symbol, name, AND the human alias table — "bitcoin", "gold",
"nifty", "inr/usd" all resolve), fetch the keyless Yahoo headline
RSS for the top matches (≤12 symbols, capped to keep the fan-out
polite), and merge in the existing gold-desk general feed (the proven
GC=F channel).

TOPIC PASS (GAUNTLET-P15, the critic's worst defect: the old search
never matched headline TEXT) — for every non-empty query, ALSO fetch
the keyless Google News RSS search feed
(https://news.google.com/rss/search?q=<query>+when:7d — live-probed
2026-08-25, HTTP 200, 100 items for "inflation") and keep only the
items whose TITLE contains a query term (case-insensitive), capped at
12, source "Google News". If Google News is unreachable, the fallback
title-filters the general-market feeds already in play (gold general
+ ^GSPC + BTC-USD).

Both passes dedupe by title and rank by recency, capped at 20 items.
A query that matches no symbols and no headline text is now an
HONEST zero-match error ("no headlines matched '<q>' in the last
day") instead of the old misleading "feeds unreachable"/general-gold
dump. Fail-soft like every markets module: per-feed failures degrade
individually and never raise.

All fetching rides the caches: markets.news.fetch_symbol_news (300s
per symbol), data.feeds.fetch_news (900s gold channel) and the topic
feed (300s per query), so repeat searches within the TTLs are
instant and offline-safe. Law boundary: display/education telemetry,
NOT wired into the orchestrator's decision loop.
"""
from __future__ import annotations

import datetime as dt
import email.utils
import hashlib
import re
import time
import urllib.parse
from pathlib import Path

from . import news as _feeds
from .news import fetch_symbol_news
from .registry import ALIASES, all_symbols

MAX_SYMBOLS = 12        # per-query symbol fan-out
MAX_ITEMS = 20          # result cap
PER_SYMBOL_LIMIT = 8    # headlines per symbol feed
EPOCH_0 = 0.0

GOOGLE_NEWS_RSS = "https://news.google.com/rss/search"
TOPIC_TTL_S = 300       # topic feed cache (matches the symbol feeds)
TOPIC_MAX_ITEMS = 12    # topic-pass result cap (spec)
TOPIC_FALLBACK_SYMBOLS = ("^GSPC", "BTC-USD")  # google-down fallback

# words too generic to headline-match on (dropped from topic terms;
# 1-2 char words are dropped by the length floor anyway)
_TOPIC_STOPWORDS = frozenset({
    "the", "a", "an", "and", "or", "of", "in", "on", "for", "to",
    "with", "at", "by", "from", "is", "are", "was", "were", "be",
    "been", "this", "that", "these", "those", "as", "it", "its",
    "new", "news", "top", "how", "why", "what", "will", "amid",
})


def _score(query: str, text: str) -> int:
    """Cheap relevance score: exact > prefix > substring > fuzzy
    subsequence (only for queries of 4+ chars — "oil" as a
    subsequence of "Volatility" is noise, not a match). 0 = no match.
    """
    q, t = query.lower(), text.lower()
    if not q or not t:
        return 0
    if q == t:
        return 1000
    if t.startswith(q):
        return 800 - (len(t) - len(q))
    if q in t:
        return 600 - (len(t) - len(q))
    if len(q) >= 4:
        # subsequence: "btcd" matches "BTC-USD"
        i = 0
        for ch in t:
            if i < len(q) and ch == q[i]:
                i += 1
        if i == len(q):
            return 300 - (len(t) - len(q))
    return 0


def match_symbols(query: str, limit: int = MAX_SYMBOLS) -> list[dict]:
    """Registry entries whose symbol/name/sector/aliases fuzzy-match
    `query`, best-first, capped at `limit`. FX-pair-shaped queries
    ("inr/usd", "eurusd") resolve through the registry pair logic so
    reciprocal pairs match too; multi-word queries fall back to
    per-token matching ("aapl nvda" → both). Deterministic (stable
    sort over the registry order breaks score ties)."""
    q = str(query or "").strip()
    if not q:
        return []
    hits = _match_one(q, limit)
    if hits:
        return hits
    # multi-word: union the per-token matches, best-first
    seen: set[str] = set()
    merged: list[dict] = []
    for token in q.split():
        for entry in _match_one(token, limit):
            if entry["symbol"] not in seen:
                seen.add(entry["symbol"])
                merged.append(entry)
    return merged[:limit]


def _match_one(q: str, limit: int) -> list[dict]:
    """Single-token scoring pass (the function match_symbols used to
    be, factored out so per-token fallback can reuse it)."""
    # alias → symbol reverse map (aliases score like the symbol itself)
    alias_by_symbol: dict[str, list[str]] = {}
    for alias, sym in ALIASES.items():
        alias_by_symbol.setdefault(sym, []).append(alias)
    scored: list[tuple[int, int, dict]] = []
    for order, entry in enumerate(all_symbols()):
        best = max(
            _score(q, entry["symbol"]),
            _score(q, entry["name"]),
            _score(q, entry["sector"]),          # "crypto" / "india" ...
            max((_score(q, a) for a in alias_by_symbol.get(
                entry["symbol"], [])), default=0),
        )
        if best > 0:
            scored.append((-best, order, entry))
    # FX-pair queries: resolve through the pair logic and append the
    # direct/reciprocal registry hit if the symbol scan missed it
    if "/" in q or (len(q) == 6 and q.replace("/", "").isalpha()):
        from .registry import resolve_pair
        pr = resolve_pair(q)
        if pr and pr[2]:
            sym = pr[0]
            if not any(e["symbol"] == sym for _, _, e in scored):
                hit = next(e for e in all_symbols()
                           if e["symbol"] == sym)
                scored.append((-500, -1, hit))
    scored.sort()
    return [e for _, _, e in scored[:limit]]


def _pub_ts(published: str) -> float:
    """RFC-822 pubDate → epoch seconds (0 when unparseable — unranked
    items sink to the bottom, they don't crash the merge)."""
    try:
        t = email.utils.parsedate_to_datetime(str(published or ""))
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.timezone.utc)
        return t.timestamp()
    except (TypeError, ValueError):
        return EPOCH_0


def _title_key(title: str) -> str:
    """Normalized dedupe key (case/punct-insensitive, XML entities
    unescaped — the gold-desk feed channel doesn't unescape "&amp;"
    while the markets parser does, and those are the same headline)."""
    from .news import _unescape
    return re.sub(r"[^a-z0-9]+", " ",
                  _unescape(str(title or "")).lower()).strip()


# ------------------------------------------------------------- topic pass
def _topic_terms(query: str) -> list[str]:
    """Significant query terms for headline matching: lowercased,
    split on non-alphanumerics, stopwords and 1-2 char fragments
    dropped ("oil" survives at 3). Falls back to the longest raw word
    when everything filters out ("news" → match headlines carrying
    "news" rather than matching nothing at all)."""
    q = str(query or "").strip().lower()
    words = [w for w in re.split(r"[^a-z0-9]+", q) if w]
    terms = [w for w in words
             if len(w) >= 3 and w not in _TOPIC_STOPWORDS]
    if not terms and words:
        terms = [max(words, key=len)]
    return terms


def _title_matches(title: str, terms: list[str]) -> bool:
    """True when any query term starts a word in the normalized title
    (case/punct-insensitive; prefix so "rate" matches "rates")."""
    if not terms:
        return False
    hay = _title_key(title)
    if not hay:
        return False
    return any(re.search(rf"\b{re.escape(t)}", hay) for t in terms)


def fetch_topic_news(query: str, data_root: str | Path = "data",
                     limit: int = TOPIC_MAX_ITEMS) -> dict:
    """Google News RSS full-text topic search (the TOPIC PASS feed) —
    keyless, live-probed 2026-08-25 (100 items for "inflation").
    Cached like the symbol feeds (300s per query), parsed with the
    same regex RSS parser, fail-soft, never raises.

    Returns {ok, query, items: [{title, link, published, source}]} —
    UNFILTERED items (search-result relevance order from Google);
    title filtering against the query terms is search_news's job."""
    q = str(query or "").strip()
    if not q:
        return {"ok": False, "error": "no query", "items": []}
    url = (f"{GOOGLE_NEWS_RSS}?q={urllib.parse.quote(q)}"
           "+when:7d&hl=en-US&gl=US&ceid=US:en")

    def _fetch() -> dict:
        xml = _feeds._http_get(url)
        items = _feeds.parse_headline_rss(xml, source="Google News")
        return {"ok": True, "query": q,
                "items": items[:max(1, limit)]}

    name = ("markets_news_topic_"
            + hashlib.sha1(q.lower().encode()).hexdigest()[:12])
    out = _feeds._cached_fetch(data_root, name, TOPIC_TTL_S, _fetch)
    out.setdefault("ok", False)
    out.setdefault("query", q)
    out.setdefault("items", [])
    return out


def search_news(query: str, data_root: str | Path = "data") -> dict:
    """NSE-style news search — SYMBOL PASS + TOPIC PASS. Returns

        {ok, query, matched: [symbols], topic: bool, items:
         [{title, link, published, published_ts, source}],
          feeds_ok, error?}

    ok=True when a feed responded AND the result is meaningful:
    symbol-matched queries merge the gold general feed + per-symbol
    feeds + the topic pass; free-text queries (no registry match)
    return ONLY headline-text matches from the topic pass (Google
    News RSS; on google-down, title-filtered general-market feeds).
    ok=False with error "news feeds unreachable" when every feed
    failed, or the HONEST "no headlines matched '<q>' in the last
    day" when feeds responded but nothing matched the query text.
    Items: title-deduped, recency-ranked, capped at 20; topic=True
    when the topic pass contributed at least one item. Never raises."""
    q = str(query or "").strip()
    matched = match_symbols(q) if q else []

    items: list[dict] = []
    seen_titles: set[str] = set()
    feeds_ok = 0
    sources: dict[str, int] = {}

    def _merge(feed_items: list[dict]) -> int:
        added = 0
        for it in feed_items:
            key = _title_key(it.get("title", ""))
            if not key or key in seen_titles:
                continue
            seen_titles.add(key)
            src = str(it.get("source") or "Yahoo Finance")
            sources[src] = sources.get(src, 0) + 1
            items.append({
                "title": str(it.get("title", ""))[:200],
                "link": str(it.get("link") or "")[:300],
                "published": str(it.get("published") or ""),
                "published_ts": _pub_ts(it.get("published", "")),
                "source": src,
            })
            added += 1
        return added

    # 1. the general stream — the proven gold-desk feed (GC=F
    #    channel). Held back for free-text queries: dumping the gold
    #    stream into "news inflation" was the defect, not a feature.
    general_items: list[dict] = []
    try:
        from ..data.feeds import fetch_news
        general = fetch_news(data_root, limit=PER_SYMBOL_LIMIT)
        if general.get("ok"):
            feeds_ok += 1
            general_items = list(general.get("items") or [])
    except Exception:  # noqa: BLE001 — display telemetry fails soft
        pass
    if matched or not q:
        _merge(general_items)

    # 2. per-symbol feeds for the registry matches (cached, fail-soft)
    for entry in matched:
        out = fetch_symbol_news(entry["symbol"], data_root,
                                limit=PER_SYMBOL_LIMIT)
        if out.get("ok"):
            feeds_ok += 1
            _merge(out.get("items") or [])

    # 3. TOPIC PASS — headline-text matching for the query itself.
    #    Google News RSS first; on google-down, title-filter the
    #    general-market feeds already in play (gold + ^GSPC + BTC).
    topic_items: list[dict] = []
    if q:
        terms = _topic_terms(q)
        topic = fetch_topic_news(q, data_root)
        if topic.get("ok"):
            feeds_ok += 1
            topic_items = [
                it for it in (topic.get("items") or [])
                if _title_matches(it.get("title", ""), terms)
            ][:TOPIC_MAX_ITEMS]
        else:
            pool = list(general_items)
            for sym in TOPIC_FALLBACK_SYMBOLS:
                out = fetch_symbol_news(sym, data_root,
                                        limit=PER_SYMBOL_LIMIT)
                if out.get("ok"):
                    feeds_ok += 1
                    pool.extend(out.get("items") or [])
            topic_items = [
                it for it in pool
                if _title_matches(it.get("title", ""), terms)
            ][:TOPIC_MAX_ITEMS]
    topic_added = _merge(topic_items)

    # honest outcomes: every feed down ≠ nothing matched
    error: str | None = None
    if feeds_ok == 0:
        error = "news feeds unreachable"
    elif not matched and q and not items:
        error = f"no headlines matched '{q}' in the last day"

    # rank by recency (desc); stable within a timestamp by merge order
    items.sort(key=lambda it: -it["published_ts"])
    return {
        "ok": error is None,
        "query": q,
        "matched": [e["symbol"] for e in matched],
        "topic": topic_added > 0,
        "items": items[:MAX_ITEMS],
        "feeds_ok": feeds_ok,
        **({"error": error} if error else {}),
        "as_of_epoch": time.time(),
    }
