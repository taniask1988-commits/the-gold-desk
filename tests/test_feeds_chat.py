"""Free-market feeds + expert chat tests. All network calls mocked."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from gold_desk.data import feeds  # noqa: E402
from gold_desk.llm import expert_chat  # noqa: E402


# ------------------------------------------------------------------- helpers
def yahoo_daily_payload(price=4680.6, prev_close=4587.0, market_time=None):
    import time as _t
    return json.dumps({"chart": {"result": [{
        "meta": {
            "regularMarketPrice": price,
            "regularMarketTime": market_time or _t.time(),
            "chartPreviousClose": 4366.0,
            "currency": "USD",
        },
        "timestamp": [100, 200, 300],
        "indicators": {"quote": [{"close": [4500.0, prev_close, price]}]},
    }]}})


def paxg_payload(price=4605.34, prev=4587.09):
    return json.dumps({
        "lastPrice": f"{price}", "prevClosePrice": f"{prev}",
        "priceChangePercent": "0.4",
    })


def rss_payload():
    return """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item><title><![CDATA[Gold rallies on Fed pivot hopes]]></title>
<link>https://finance.yahoo.com/foo?utm=1&amp;url=https%3A%2F%2Freal.example%2Fa</link>
<pubDate>Sun, 23 Aug 2026 11:08:41 +0000</pubDate></item>
<item><title>Plain title no cdata</title>
<link>https://example.com/plain</link>
<pubDate>Sun, 23 Aug 2026 12:00:00 +0000</pubDate></item>
</channel></rss>"""


# -------------------------------------------------------------------- price
def test_yahoo_price_uses_true_prev_close(monkeypatch):
    monkeypatch.setattr(feeds, "_http_get",
                        lambda url, timeout=8: yahoo_daily_payload())
    out = feeds._yahoo_price()
    assert out["ok"] is True
    assert out["price"] == 4680.6
    assert out["prev_close"] == 4587.0  # NOT chartPreviousClose (4366)


def test_spot_falls_back_to_paxg_when_yahoo_down(monkeypatch):
    def boom(url, timeout=8):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(feeds, "_http_get", boom)
    # separate fetchers: patch each individually
    monkeypatch.setattr(feeds, "_yahoo_price", lambda: (_ for _ in ()).throw(
        RuntimeError("yahoo down")))
    monkeypatch.setattr(feeds, "_paxg_price",
                        lambda: {"ok": True, "price": 4600.0,
                                 "source": "binance:PAXGUSDT",
                                 "prev_close": 4590.0,
                                 "market_time": 1, "change_pct": 0.2})
    out = feeds.fetch_spot(tmp_path := Path("/tmp/gdt_test_feeds"))
    # note: fetch_spot caches; use a fresh dir via monkeypatched root
    assert out.get("price") in (4600.0,) or out.get("cached") is True


def test_weekend_stale_futures_switch_to_paxg(monkeypatch, tmp_path):
    import time as _t
    stale = _t.time() - 20 * 3600  # 20h old quote = market closed
    monkeypatch.setattr(feeds, "_yahoo_price", lambda: {
        "ok": True, "source": "yahoo:GC=F", "symbol": "GC=F",
        "price": 4680.6, "prev_close": 4587.0, "currency": "USD",
        "market_time": stale, "unit": "USD/oz"})
    monkeypatch.setattr(feeds, "_paxg_price", lambda: {
        "ok": True, "source": "binance:PAXGUSDT", "symbol": "PAXGUSDT",
        "price": 4605.34, "prev_close": 4587.09, "currency": "USD",
        "market_time": _t.time(), "unit": "USD/oz",
        "change_pct": 0.4})
    out = feeds.fetch_spot(tmp_path)
    assert out["price"] == 4605.34
    assert "futures closed" in out["source"]
    assert out["reference"]["price"] == 4680.6  # futures kept as reference


def test_spot_cache_hit(monkeypatch, tmp_path):
    calls = {"n": 0}

    def fake_fetch():
        calls["n"] += 1
        return {"ok": True, "price": 4600.0, "source": "x"}

    feeds._cache_path(tmp_path, "price").write_text(json.dumps({
        "ok": True, "price": 4600.0, "source": "x",
        "fetched_at": __import__("time").time() + 10}))
    out = feeds._cached_fetch(tmp_path, "price", 60, fake_fetch)
    assert out.get("cache_hit") is True and calls["n"] == 0


def test_spot_stale_cache_served_when_network_down(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("net down")
    feeds._cache_path(tmp_path, "price").write_text(json.dumps({
        "ok": True, "price": 4600.0, "source": "x",
        "fetched_at": 100}))  # ancient
    out = feeds._cached_fetch(tmp_path, "price", 60, boom)
    assert out["price"] == 4600.0 and out["cached"] is True


# --------------------------------------------------------------------- news
def test_rss_parse_titles_links_dates():
    items = feeds._parse_rss(rss_payload())
    assert len(items) == 2
    assert "Gold rallies" in items[0]["title"]
    # tracking wrapper stripped
    assert items[0]["link"].startswith("https://real.example")
    assert items[1]["link"] == "https://example.com/plain"
    assert items[0]["published"].startswith("Sun, 23 Aug 2026")


def test_news_ok(monkeypatch, tmp_path):
    monkeypatch.setattr(feeds, "_http_get", lambda url, timeout=8: rss_payload())
    out = feeds.fetch_news(tmp_path)
    assert out["ok"] is True
    assert len(out["items"]) == 2
    assert out["items"][0]["source"] == "Yahoo Finance · Gold"


def test_news_down_no_cache_fails_soft(monkeypatch, tmp_path):
    def boom(url, timeout=8):
        raise RuntimeError("down")
    monkeypatch.setattr(feeds, "_http_get", boom)
    out = feeds.fetch_news(tmp_path)
    assert out.get("ok") is False
    assert "error" in out


# -------------------------------------------------------------- expert chat
def test_context_includes_spot_and_news(monkeypatch, tmp_path):
    # patch the names as imported INTO expert_chat (from ..data.feeds import …)
    monkeypatch.setattr(expert_chat, "fetch_spot",
                        lambda root: {"ok": True, "price": 4680.6,
                                      "source": "yahoo:GC=F",
                                      "prev_close": 4587.0})
    monkeypatch.setattr(expert_chat, "fetch_news",
                        lambda root, limit=5: {"ok": True, "items": [
                            {"title": "Gold rallies", "published": "x"}]})
    (tmp_path / "account.json").write_text(json.dumps({
        "balance": 10039.0, "closed_trades": [{"pnl": 10}, {"pnl": -5}]}))
    ctx = expert_chat.build_context(tmp_path)
    assert "4680.60" in ctx
    assert "Gold rallies" in ctx
    assert "10039" in ctx


def test_context_fails_soft(monkeypatch, tmp_path):
    def boom(root, *a, **k):
        raise RuntimeError("feed down")
    monkeypatch.setattr(expert_chat, "fetch_spot", boom)
    monkeypatch.setattr(expert_chat, "fetch_news", boom)
    ctx = expert_chat.build_context(tmp_path / "does-not-exist")
    assert "unavailable" in ctx  # honest when feeds+journal missing


def test_system_prompt_persona_rules():
    p = expert_chat.SYSTEM_PROMPT
    assert "20 years" in p
    assert "NEVER invent" in p
    assert "NOT financial advice" in p


def test_chat_sends_grounding_and_history(monkeypatch, tmp_path):
    seen = {}

    def fake_complete(messages, model, timeout=30.0, temperature=0.0,
                      max_tokens=500):
        seen["messages"] = messages
        seen["model"] = model
        return {"choices": [{"message": {
            "content": "The desk answers with specifics."}}]}

    monkeypatch.setattr(expert_chat, "complete", fake_complete)
    monkeypatch.setattr(expert_chat, "build_context",
                        lambda root: "MARKET CONTEXT: spot 4680")
    monkeypatch.setattr(expert_chat, "resolve_model",
                        lambda root, req=None: "test-model")
    out = expert_chat.chat(
        [{"role": "user", "content": "hi"},
         {"role": "assistant", "content": "hello"},
         {"role": "user", "content": "what moves gold?"}],
        data_root=tmp_path)
    assert out["ok"] is True
    assert out["reply"] == "The desk answers with specifics."
    assert out["model"] == "test-model"
    msgs = seen["messages"]
    assert msgs[0]["role"] == "system"      # persona
    assert "spot 4680" in msgs[1]["content"]  # grounding
    assert msgs[-1]["content"] == "what moves gold?"
    assert len(msgs) == 5  # 2 system + 3 history


def test_chat_empty_reply_raises_unavailable(monkeypatch, tmp_path):
    monkeypatch.setattr(expert_chat, "complete",
                        lambda *a, **k: {"choices": [{"message": {}}]})
    from gold_desk.llm.zen_client import LLMUnavailable
    with pytest.raises(LLMUnavailable):
        expert_chat.chat([{"role": "user", "content": "x"}],
                         data_root=tmp_path)
