"""MARKET GAUNTLET news search (NSE-style) tests — offline.

Every feed seam is monkeypatched: the gold general feed
(data.feeds.fetch_news) and the per-symbol RSS (markets.news
fetch_symbol_news) get canned items; nothing touches the network.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.markets import news_search as ns  # noqa: E402


@pytest.fixture(autouse=True)
def _offline_feeds(monkeypatch):
    """All feed seams default to an unreachable state; individual
    tests re-patch with canned payloads. The topic feed's dead default
    rides the REAL HTTP seam (markets.news._http_get raising) so the
    google-RSS parse test can swap in a canned body without fighting
    this patch."""
    def _dead(*a, **k):
        raise RuntimeError("offline test")
    monkeypatch.setattr("gold_desk.data.feeds.fetch_news", _dead)
    monkeypatch.setattr(ns, "fetch_symbol_news",
                        lambda sym, data_root="data", limit=8:
                        {"ok": False, "error": "offline", "items": []})
    monkeypatch.setattr(ns._feeds, "_http_get", _dead)


def _canned(symbols):
    """Canned per-symbol feed: 2 items per symbol with distinct
    recency (newer for earlier symbols)."""
    def fake(sym, data_root="data", limit=8):
        if sym not in symbols:
            return {"ok": True, "symbol": sym, "items": []}
        i = symbols.index(sym)
        return {"ok": True, "symbol": sym, "items": [
            {"title": f"{sym} headline old {i}",
             "link": f"https://example.com/{sym}-old",
             "published": f"Mon, 24 Aug 2026 1{i}:00:00 +0000",
             "source": f"Yahoo Finance · {sym}"},
            {"title": f"{sym} headline new {i}",
             "link": f"https://example.com/{sym}-new",
             "published": f"Tue, 25 Aug 2026 2{i}:00:00 +0000",
             "source": f"Yahoo Finance · {sym}"},
        ]}
    return fake


# --------------------------------------------------------------- match
def test_match_symbols_aliases_and_names():
    assert [e["symbol"] for e in ns.match_symbols("bitcoin")] == \
        ["BTC-USD"]
    assert [e["symbol"] for e in ns.match_symbols("btc")] == ["BTC-USD"]
    golds = [e["symbol"] for e in ns.match_symbols("gold")]
    assert "GC=F" in golds and "GLD" in golds


def test_match_symbols_sector_queries():
    syms = {e["symbol"] for e in ns.match_symbols("crypto")}
    assert {"BTC-USD", "ETH-USD"} <= syms


def test_match_symbols_fx_pair_reciprocal():
    # "inr/usd" isn't a direct registry pair — it resolves through
    # the reciprocal USDINR=X
    assert [e["symbol"] for e in ns.match_symbols("inr/usd")] == \
        ["USDINR=X"]
    assert [e["symbol"] for e in ns.match_symbols("eurusd")] == \
        ["EURUSD=X"]


def test_match_symbols_multiword_falls_back_to_tokens():
    syms = [e["symbol"] for e in ns.match_symbols("aapl nvda")]
    assert syms == ["AAPL", "NVDA"]


def test_match_symbols_no_match_and_empty():
    assert ns.match_symbols("xyzzy") == []
    assert ns.match_symbols("") == []
    assert ns.match_symbols("   ") == []


def test_match_symbols_caps_at_twelve():
    # a broad query ("us" sector) hits many rows but never more than 12
    assert len(ns.match_symbols("us")) <= 12


# -------------------------------------------------------------- search
def test_search_merges_and_ranks_by_recency(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "fetch_symbol_news",
                        _canned(["BTC-USD", "ETH-USD"]))
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "gold feed mid",
             "link": "https://example.com/g",
             "published": "Tue, 25 Aug 2026 15:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})
    out = ns.search_news("bitcoin ethereum", tmp_path)
    assert out["ok"] is True
    # multi-token match: both symbols (bitcoin→BTC-USD is an alias
    # pass; ethereum→ETH-USD)
    assert set(out["matched"]) >= {"BTC-USD", "ETH-USD"}
    # recency ranking: ETH new (21:00) > BTC new (20:00) > gold
    # (15:00) > ETH old (11:00) > BTC old (10:00)
    titles = [it["title"] for it in out["items"]]
    assert titles[0].startswith("ETH-USD headline new")
    assert titles[1].startswith("BTC-USD headline new")
    assert "gold feed mid" in titles[2]
    assert titles[-1].startswith("BTC-USD headline old")
    # every item carries the passthrough fields
    for it in out["items"]:
        assert it["source"] and it["link"] and it["published_ts"] > 0


def test_search_dedupes_identical_titles(monkeypatch, tmp_path):
    # the gold channel serves "&amp;" (unescaped), the markets parser
    # serves "&" — the same headline must appear once
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "Bitcoin &amp; gold rally",
             "link": "https://a", "published": "Tue, 25 Aug 2026 10:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})

    def fake(sym, data_root="data", limit=8):
        if sym != "BTC-USD":
            return {"ok": True, "symbol": sym, "items": []}
        return {"ok": True, "symbol": sym, "items": [
            {"title": "Bitcoin & gold rally",
             "link": "https://b", "published": "Tue, 25 Aug 2026 10:00:00 +0000",
             "source": "Yahoo Finance · BTC-USD"}]}
    monkeypatch.setattr(ns, "fetch_symbol_news", fake)
    out = ns.search_news("bitcoin", tmp_path)
    rally = [it for it in out["items"] if "rally" in it["title"]]
    assert len(rally) == 1


def test_search_unmatched_topic_zero_match_is_honest_error(monkeypatch,
                                                           tmp_path):
    # GAUNTLET-P15: a free-text query with no registry match and no
    # headline-text match must NOT dump the general gold stream nor
    # claim "feeds unreachable" — it reports the honest zero match.
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "general headline",
             "link": "https://example.com",
             "published": "Tue, 25 Aug 2026 12:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})
    monkeypatch.setattr(ns, "fetch_topic_news",
                        lambda query, data_root="data", limit=12:
                        {"ok": True, "query": query, "items": [
                            {"title": "something else entirely",
                             "link": "https://g",
                             "published": "Tue, 25 Aug 2026 13:00:00 +0000",
                             "source": "Google News"}]})
    out = ns.search_news("xyzzy-unmatched", tmp_path)
    assert out["ok"] is False
    assert out["items"] == []
    assert out["matched"] == []
    assert out["topic"] is False
    assert "no headlines matched 'xyzzy-unmatched'" in out["error"]
    assert "unreachable" not in out["error"]


def test_search_empty_query_still_serves_general(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "general headline",
             "link": "https://example.com",
             "published": "Tue, 25 Aug 2026 12:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})
    out = ns.search_news("", tmp_path)
    assert out["ok"] is True
    assert out["matched"] == []
    assert out["topic"] is False
    assert [it["title"] for it in out["items"]] == ["general headline"]


def test_search_caps_at_twenty(monkeypatch, tmp_path):
    syms = [e["symbol"] for e in ns.match_symbols("crypto")]

    def fake(sym, data_root="data", limit=8):
        return {"ok": True, "symbol": sym, "items": [
            {"title": f"{sym} item {i}",
             "link": "https://x", "published": "Tue, 25 Aug 2026 12:00:00 +0000",
             "source": f"Yahoo Finance · {sym}"} for i in range(10)]}
    monkeypatch.setattr(ns, "fetch_symbol_news", fake)
    out = ns.search_news("crypto", tmp_path)
    assert len(out["items"]) == ns.MAX_ITEMS == 20


def test_search_all_feeds_down_fail_soft(monkeypatch, tmp_path):
    # default fixture state: general + symbol + topic feeds all offline
    out = ns.search_news("bitcoin", tmp_path)
    assert out["ok"] is False
    assert out["items"] == []
    assert out["matched"] == ["BTC-USD"]
    assert out["error"] == "news feeds unreachable"


def test_search_unparseable_pubdate_sinks_not_crashes(monkeypatch,
                                                      tmp_path):
    def fake(sym, data_root="data", limit=8):
        if sym != "BTC-USD":
            return {"ok": True, "symbol": sym, "items": []}
        return {"ok": True, "symbol": sym, "items": [
            {"title": "bad date item", "link": "https://x",
             "published": "not a date at all",
             "source": "Yahoo Finance · BTC-USD"},
            {"title": "good date item", "link": "https://y",
             "published": "Tue, 25 Aug 2026 12:00:00 +0000",
             "source": "Yahoo Finance · BTC-USD"}]}
    monkeypatch.setattr(ns, "fetch_symbol_news", fake)
    out = ns.search_news("bitcoin", tmp_path)
    titles = [it["title"] for it in out["items"]]
    # ranked item first, undated item last — no exception raised
    assert titles[0] == "good date item"
    assert titles[-1] == "bad date item"
    assert out["items"][-1]["published_ts"] == 0


# ------------------------------------------------------------ topic pass
def _google_canned(items):
    """Canned Google News topic feed."""
    return lambda query, data_root="data", limit=12: \
        {"ok": True, "query": query, "items": items}


def test_topic_pass_matches_headline_text(monkeypatch, tmp_path):
    # GAUNTLET-P15 headline defect: "news inflation" previously
    # matched nothing (registry-only). Now the topic pass returns
    # title-matched items, topic=True, source Google News.
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "gold inches higher as dollar softens",
             "link": "https://example.com/g",
             "published": "Tue, 25 Aug 2026 09:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})
    monkeypatch.setattr(ns, "fetch_topic_news", _google_canned([
        {"title": "Inflation cools faster than expected",
         "link": "https://news.google.com/a",
         "published": "Tue, 25 Aug 2026 14:00:00 +0000",
         "source": "Google News"},
        {"title": "The three minds leading the Fed's rethink",
         "link": "https://news.google.com/b",
         "published": "Tue, 25 Aug 2026 15:00:00 +0000",
         "source": "Google News"},
        {"title": "Uneventful session on the bond desk",
         "link": "https://news.google.com/c",
         "published": "Tue, 25 Aug 2026 16:00:00 +0000",
         "source": "Google News"},
    ]))
    out = ns.search_news("inflation", tmp_path)
    assert out["ok"] is True
    assert out["matched"] == []           # inflation is not a registry hit
    assert out["topic"] is True
    titles = [it["title"] for it in out["items"]]
    # ONLY the title-matched headline: the non-matching Google item
    # and the general gold stream are both held out
    assert titles == ["Inflation cools faster than expected"]
    assert all(it["source"] == "Google News" for it in out["items"])
    assert out["items"][0]["link"].startswith("https://news.google.com/")


def test_topic_pass_dedupes_across_passes(monkeypatch, tmp_path):
    # same headline served by Yahoo (symbol pass) and Google (topic
    # pass) — the merged result carries it once
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": []})

    def fake_sym(sym, data_root="data", limit=8):
        if sym != "BTC-USD":
            return {"ok": True, "symbol": sym, "items": []}
        return {"ok": True, "symbol": sym, "items": [
            {"title": "Bitcoin surges past resistance",
             "link": "https://yahoo.com/b",
             "published": "Tue, 25 Aug 2026 10:00:00 +0000",
             "source": "Yahoo Finance · BTC-USD"}]}
    monkeypatch.setattr(ns, "fetch_symbol_news", fake_sym)
    monkeypatch.setattr(ns, "fetch_topic_news", _google_canned([
        {"title": "Bitcoin surges past resistance",
         "link": "https://news.google.com/b",
         "published": "Tue, 25 Aug 2026 10:00:00 +0000",
         "source": "Google News"},
        {"title": "Bitcoin ETF inflows accelerate",
         "link": "https://news.google.com/e",
         "published": "Tue, 25 Aug 2026 11:00:00 +0000",
         "source": "Google News"},
    ]))
    out = ns.search_news("bitcoin", tmp_path)
    assert out["ok"] is True
    assert out["matched"] == ["BTC-USD"]
    assert out["topic"] is True           # topic pass contributed a NEW item
    rally = [it for it in out["items"] if "surges" in it["title"]]
    assert len(rally) == 1                 # deduped across the two passes
    assert [it["title"] for it in out["items"]] == [
        "Bitcoin ETF inflows accelerate",   # 11:00 newest
        "Bitcoin surges past resistance",   # 10:00 deduped
    ]


def test_topic_pass_capped_at_twelve(monkeypatch, tmp_path):
    monkeypatch.setattr(ns, "fetch_topic_news", _google_canned([
        {"title": f"inflation report number {i}",
         "link": "https://g", "published": "Tue, 25 Aug 2026 12:00:00 +0000",
         "source": "Google News"} for i in range(30)]))
    out = ns.search_news("inflation", tmp_path)
    assert out["ok"] is True
    assert len(out["items"]) == ns.TOPIC_MAX_ITEMS == 12


def test_topic_pass_google_down_falls_back_to_general_feeds(
        monkeypatch, tmp_path):
    # google unreachable → title-filter the general-market feeds
    # (gold general + ^GSPC + BTC-USD); matches serve, ok=True
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "quiet day for bullion",
             "link": "https://g1", "published": "Tue, 25 Aug 2026 10:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})

    def fake_sym(sym, data_root="data", limit=8):
        items = []
        if sym == "^GSPC":
            items = [{"title": "Stocks waver as inflation data looms",
                      "link": "https://g2",
                      "published": "Tue, 25 Aug 2026 11:00:00 +0000",
                      "source": "Yahoo Finance · ^GSPC"}]
        return {"ok": True, "symbol": sym, "items": items}
    monkeypatch.setattr(ns, "fetch_symbol_news", fake_sym)
    # topic feed stays dead (default fixture) → fallback path
    out = ns.search_news("inflation", tmp_path)
    assert out["ok"] is True
    assert out["matched"] == []
    assert out["topic"] is True
    assert [it["title"] for it in out["items"]] == \
        ["Stocks waver as inflation data looms"]


def test_topic_pass_google_down_zero_matches_honest_error(
        monkeypatch, tmp_path):
    # google down AND no title matches anywhere → honest zero-match,
    # not "feeds unreachable" (feeds DID respond)
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": [
            {"title": "general headline",
             "link": "https://g", "published": "Tue, 25 Aug 2026 10:00:00 +0000",
             "source": "Yahoo Finance · Gold"}]})
    out = ns.search_news("xyzzy", tmp_path)   # topic feed dead (fixture)
    assert out["ok"] is False
    assert "no headlines matched 'xyzzy'" in out["error"]


def test_topic_pass_symbol_queries_unchanged_when_topic_dead(
        monkeypatch, tmp_path):
    # with the topic feed unreachable, registry-matched queries behave
    # exactly as pre-P15 (symbol feeds + general stream, topic=False)
    monkeypatch.setattr(ns, "fetch_symbol_news", _canned(["BTC-USD"]))
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": []})
    out = ns.search_news("bitcoin", tmp_path)
    assert out["ok"] is True
    assert out["matched"] == ["BTC-USD"]
    assert out["topic"] is False
    assert sorted(it["title"] for it in out["items"]) == \
        ["BTC-USD headline new 0", "BTC-USD headline old 0"]


def test_fetch_topic_news_parses_google_rss(monkeypatch, tmp_path):
    # the real HTTP seam with a canned Google News RSS body: parse →
    # items, cache → second call served from disk without HTTP
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Inflation - Google News</title>
<item><title>Fed officials warned rate hikes may be needed if
inflation stays high</title><link>https://news.google.com/rss/articles/xyz</link><guid>xyz</guid><pubDate>Thu, 20 Aug 2026 23:47:52 GMT</pubDate></item>
<item><title>Japan headline inflation rate hits highest this year
&amp;amp; energy prices bite</title><link>https://news.google.com/rss/articles/abc</link><guid>abc</guid><pubDate>Fri, 21 Aug 2026 14:42:00 GMT</pubDate></item>
</channel></rss>"""
    calls = {"n": 0}

    def fake_get(url, timeout=8.0):
        calls["n"] += 1
        assert "news.google.com/rss/search" in url
        assert "q=inflation" in url
        assert "when:7d" in url
        return xml
    monkeypatch.setattr(ns._feeds, "_http_get", fake_get)
    out = ns.fetch_topic_news("inflation", tmp_path)
    assert out["ok"] is True
    assert len(out["items"]) == 2
    assert out["items"][0]["title"].startswith("Fed officials warned")
    assert out["items"][0]["source"] == "Google News"
    assert out["items"][1]["title"].endswith("energy prices bite")  # &amp; unescaped
    assert out["items"][0]["published"] == "Thu, 20 Aug 2026 23:47:52 GMT"
    # cache: second call never touches HTTP
    again = ns.fetch_topic_news("inflation", tmp_path)
    assert again["cache_hit"] is True
    assert calls["n"] == 1


def test_topic_terms_filtering():
    assert ns._topic_terms("inflation") == ["inflation"]
    # stopwords + short fragments drop; multi-word keeps both real terms
    assert ns._topic_terms("the news on oil") == ["oil"]
    assert ns._topic_terms("aapl nvda") == ["aapl", "nvda"]
    # all-stopword query falls back to the longest raw word
    assert ns._topic_terms("news") == ["news"]
    assert ns._topic_terms("") == []


def test_title_matches_prefix_and_case():
    assert ns._title_matches("Inflation cools faster", ["inflation"])
    assert ns._title_matches("Fed debates RATE hikes", ["rate"])
    assert ns._title_matches("rates on hold", ["rate"])      # prefix
    assert not ns._title_matches("boiling point", ["oil"])   # no mid-word
    assert not ns._title_matches("anything", [])


# ------------------------------------------------------------------ CLI
def test_cli_markets_news_json(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ns, "fetch_symbol_news", _canned(["BTC-USD"]))
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": []})
    from gold_desk.cli import main
    rc = main(["markets-news", "bitcoin", "--json",
               "--data-root", str(tmp_path)])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["ok"] is True
    assert out["matched"] == ["BTC-USD"]
    assert len(out["items"]) == 2


def test_cli_markets_news_table(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ns, "fetch_symbol_news", _canned(["BTC-USD"]))
    monkeypatch.setattr(
        "gold_desk.data.feeds.fetch_news",
        lambda data_root="data", limit=12: {"ok": True, "items": []})
    from gold_desk.cli import main
    rc = main(["markets-news", "bitcoin", "now", "--data-root",
               str(tmp_path)])
    text = capsys.readouterr().out
    assert rc == 0
    assert 'NEWS SEARCH — "bitcoin now"' in text
    assert "BTC-USD headline new 0" in text
