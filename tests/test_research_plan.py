"""Research plan sanitization — regression for the off-topic BTC report.

Observed in the wild (run 01M0VFAG7AZMWRX7SRP3EG41J0): the planner model
copied the PLAN_PROMPT's example strings verbatim — queries "search
query 1"/"search query 2" — so the fan-out searched for pages about
*queries* (Wikipedia "Web query", Slack blog, Elasticsearch forum) and
the BTC report could only say "no evidence about BTC".

These tests pin the three-layer defense:
  1. _sanitize_plan drops placeholder / asset-irrelevant queries
     (fallback to guaranteed-relevant defaults)
  2. _any_relevant detects when gathered evidence never mentions the asset
  3. PLAN_PROMPT itself no longer contains copyable placeholder strings
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.agent.research import (  # noqa: E402
    PLAN_PROMPT,
    _any_relevant,
    _asset_aliases,
    _default_queries,
    _is_placeholder,
    _query_relevant,
    _sanitize_plan,
)

# ------------------------------------------------------------------ layer 1


def test_placeholder_queries_are_rejected():
    """The exact strings from the wild run must not survive sanitization."""
    plan = {
        "questions": [
            {"question": "What is BTC doing?",
             "queries": ["search query 1", "search query 2"]},
        ],
        "must_check": ["current price"],
    }
    out = _sanitize_plan(plan, "BTC")
    queries = [q for question in out["questions"] for q in question["queries"]]
    assert queries, "sanitized plan must always contain queries"
    for q in queries:
        assert not _is_placeholder(q), f"placeholder survived: {q!r}"
        assert _query_relevant(q, _asset_aliases("BTC")), (
            f"fallback query not asset-relevant: {q!r}")


def test_mixed_placeholder_and_real_queries_keep_only_real():
    plan = {
        "questions": [
            {"question": "BTC state",
             "queries": ["search query 1", "bitcoin ETF flows this week"]},
        ],
        "must_check": ["btc price"],
    }
    out = _sanitize_plan(plan, "BTC")
    queries = [q for question in out["questions"] for q in question["queries"]]
    assert queries == ["bitcoin ETF flows this week"]


def test_alias_matching_btc_bitcoin_gold():
    btc = _asset_aliases("BTC")
    assert "bitcoin" in btc and "btc" in btc
    gold = _asset_aliases("XAUUSD")
    assert "gold" in gold and "xauusd" in gold
    assert _query_relevant("bitcoin ETF flows", btc)
    assert _query_relevant("gold price outlook", gold)
    assert not _query_relevant("how search queries shape content", btc)
    assert not _query_relevant("elasticsearch query examples", btc)


def test_must_check_placeholders_replaced():
    plan = {"questions": [], "must_check": ["search query 1"]}
    out = _sanitize_plan(plan, "BTC")
    assert out["must_check"] == ["current BTC price"]
    assert out["questions"][0]["queries"] == _default_queries("BTC")[:2]


def test_default_queries_mention_the_asset():
    for asset in ("BTC", "ETH", "SOL", "XAUUSD", "DOGE"):
        aliases = _asset_aliases(asset)
        for q in _default_queries(asset):
            assert _query_relevant(q, aliases), (
                f"default query for {asset} not relevant: {q!r}")


# ------------------------------------------------------------------ layer 2


def test_any_relevant_detects_offtopic_evidence():
    """The exact off-topic evidence set from the wild run → not relevant."""
    off_topic_sources = [
        {"title": "Web query", "url": "https://en.wikipedia.org/wiki/Web_query"},
        {"title": "how-search-queries-shape-better-content",
         "url": "https://slack.com/blog/productivity/how-search-queries-shape"},
        {"title": "match-query-with-only-2-word-search-in-search-query",
         "url": "https://discuss.elastic.co/t/match-query/365526"},
    ]
    off_topic_extracts = [
        "[1] https://en.wikipedia.org/wiki/Web_query\n"
        "```UNTRUSTED_WEB_CONTENT\nA web search query is a query that a user "
        "enters into a web search engine...\n```",
        "[2] slack blog\n```UNTRUSTED_WEB_CONTENT\nsearch queries improve "
        "content and smarter search...\n```",
    ]
    assert not _any_relevant(off_topic_extracts, off_topic_sources, "BTC")

    on_topic = [
        {"title": "Bitcoin price today", "url": "https://example.com/btc"},
    ]
    assert _any_relevant(
        ["[1] url\n```UNTRUSTED_WEB_CONTENT\nBitcoin traded at $79,600..."
         "\n```"], on_topic, "BTC")
    # gold alias works too
    assert _any_relevant(
        ["[1] url\n```UNTRUSTED_WEB_CONTENT\ngold spot 4660 USD...\n```"],
        on_topic, "XAUUSD")


def test_empty_evidence_is_not_relevant():
    assert not _any_relevant([], [], "BTC")


# ------------------------------------------------------------------ layer 3


def test_plan_prompt_has_no_copyable_placeholder_queries():
    """The prompt must not contain example query strings a lazy model can
    copy verbatim ('search query 1' etc.). Real examples that name the
    asset are fine."""
    lowered = PLAN_PROMPT.lower()
    for banned in ("search query 1", "search query 2", "query 1",
                   "example query"):
        assert banned not in lowered, (
            f"PLAN_PROMPT still contains copyable placeholder: {banned!r}")
    # the prompt does instruct asset-mentioning queries
    assert "must contain the asset name" in lowered


# --------------------------------------------------- end-to-end (stubbed)


def test_research_rescues_offtopic_gather(monkeypatch, tmp_path):
    """Full research() flow with a placeholder plan + off-topic first
    search: the relevance guard must trigger a rescue pass with default
    queries (stubbed second search returns on-topic hits)."""
    import gold_desk.agent.research as rz

    calls = {"searches": []}

    def fake_plan(asset, depth, model, fallbacks=None):
        return {"questions": [{"question": "q",
                               "queries": ["search query 1"]}],
                "must_check": ["price"]}

    def fake_search(query, max_results=4):
        calls["searches"].append(query)
        if "bitcoin" in query.lower() or "btc" in query.lower():
            return {"ok": True, "query": query, "results": [
                {"title": "Bitcoin price today", "url": "https://x.io/btc",
                 "snippet": "btc 79600"}]}
        return {"ok": True, "query": query, "results": [
            {"title": "Web query", "url": "https://en.wikipedia.org/wiki/Web_query",
             "snippet": "queries"}]}

    def fake_fetch(url):
        if "btc" in url:
            return {"ok": True, "url": url, "tier": "T0", "status": 200,
                    "text": "Bitcoin BTC trades at 79,600 USD, up 3%.",
                    "title": "Bitcoin price", "fetched_ts": "now"}
        return {"ok": True, "url": url, "tier": "T0", "status": 200,
                "text": "A web search query is user-entered text...",
                "title": "Web query", "fetched_ts": "now"}

    monkeypatch.setattr(rz, "_plan", fake_plan)
    monkeypatch.setattr(rz, "web_search_raw", fake_search)
    monkeypatch.setattr(rz, "fetch_page_raw", fake_fetch)
    monkeypatch.setattr(rz, "_verify", lambda *a, **k: {"claims": []})
    monkeypatch.setattr(rz, "_synthesize",
                        lambda *a, **k: "## Summary\nBTC at 79600 [1].\n")

    out = rz.research("BTC", data_root=tmp_path, depth=1)
    assert out.get("ok"), f"research failed: {out.get('detail')}"
    # the placeholder query was searched (harmless), then the rescue pass
    # fired with asset-mentioning default queries
    assert any("bitcoin" in q.lower() for q in calls["searches"]), (
        f"rescue pass never fired; searches={calls['searches']}")
    # and the final report cites an on-topic source
    srcs = out.get("sources") or []
    assert any("btc" in (s.get("url") or "") for s in srcs), (
        f"no on-topic source in report; sources={srcs}")
    # source titles prefer the clean search-result title over on-page junk
    on_topic = [s for s in srcs if "btc" in (s.get("url") or "")]
    assert on_topic and on_topic[0]["title"] == "Bitcoin price today", (
        f"search title not preferred: {on_topic}")
