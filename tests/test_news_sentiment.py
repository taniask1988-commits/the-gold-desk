"""R3-2 Build 3 — tests for markets/news_sentiment.py.

Covers the charter contract:
* 10 hand-crafted headlines with KNOWN polarity (5 positive / 5 negative)
* negation flips ("Fed does not signal easing" → negative)
* intensifier/diminisher multipliers ("SHARPLY higher" > "slightly higher")
* asset detection for all 8 desk instruments + the 3 confidence tiers
* relevance: asset in the first 5 words vs a late mention
* novelty: identical story twice → < 0.3; distinct story → > 0.7; the
  80%-trigram-overlap → 0.2 decay point; 24h window expiry
* LLM fallback: mocked zen second opinion (blended), failure → local
  score + llm_fallback_failed flag, never called when not ambiguous
* subjectivity vs objectivity
* tape scoring via an injected fetcher (no network)
* CLI wiring (cmd_news_sentiment json + pretty paths)

No test in this file touches the network: every analyzer is built with
an explicit tmp data_root (or data_root=None) and llm_enabled=False or
a mocked llm_complete.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from gold_desk.markets import news_sentiment as ns
from gold_desk.markets.news_sentiment import (
    ASSET_ORDER,
    NewsSentimentAnalyzer,
    detect_assets,
    score_tape,
)


@pytest.fixture
def analyzer(tmp_path) -> NewsSentimentAnalyzer:
    """Fresh, offline analyzer: in-memory novelty (data_root=None), LLM off."""
    return NewsSentimentAnalyzer(data_root=None, llm_enabled=False)


# ------------------------------------------------------ known-polarity set
POSITIVE_HEADLINES = [
    "Gold surges to record high as Fed signals dovish pivot",
    "S&P 500 rallies after blowout earnings beat estimates",
    "Bitcoin soars as institutional inflows accelerate",
    "Oil rebounds as supply cuts boost prices",
    "Treasury yields rise on robust economic growth",
]

NEGATIVE_HEADLINES = [
    "Stocks plunge as selloff deepens amid recession fears",
    "Bitcoin crashes after exchange hack sparks panic selling",
    "Gold slides as hawkish Fed signals rate hikes",
    "Oil slumps under pressure as demand slowdown deepens",
    "Euro tumbles after downgrade and growth miss",
]


@pytest.mark.parametrize("headline", POSITIVE_HEADLINES,
                         ids=[h.split()[0].lower() + "-pos" for h in POSITIVE_HEADLINES])
def test_known_positive_headlines(analyzer, headline):
    out = analyzer.score(headline)
    assert out["ok"] is True
    assert out["polarity"] > 0.2, out
    assert out["label"] == "positive"


@pytest.mark.parametrize("headline", NEGATIVE_HEADLINES,
                         ids=[h.split()[0].lower() + "-neg" for h in NEGATIVE_HEADLINES])
def test_known_negative_headlines(analyzer, headline):
    out = analyzer.score(headline)
    assert out["ok"] is True
    assert out["polarity"] < -0.2, out
    assert out["label"] == "negative"


def test_polarity_bounded_and_symmetric_gauge():
    """Extreme both ways clamp inside [-1, 1] and carry the right sign."""
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False)
    up = a.score("surges soars skyrocket surge rallies surge soars")["polarity"]
    down = a.score("crashes plummets crash collapses crashes plummets crash")["polarity"]
    assert 0.9 < up <= 1.0
    assert -1.0 <= down < -0.9


# ------------------------------------------------------ R3-3 critic gap-fix
def test_regression_oil_spikes_after_opec_cut(analyzer):
    """R3-2 critic live probe: 'Oil spikes after OPEC cut' scored 0.000
    (the spike stem was absent from the lexicon). Must now be positive
    > 0.2 with the spike term fired."""
    out = analyzer.score("Oil spikes after OPEC cut")
    assert out["ok"] is True
    assert out["polarity"] > 0.2, out
    assert out["label"] == "positive"
    assert "spikes" in {t["term"] for t in out["terms_fired"]}


def test_regression_hackers_steal_bitcoin(analyzer):
    """R3-2 critic live probe: 'Hackers steal $130M in Bitcoin' scored
    0.000 (hackers/steal/theft stems absent). Must now be negative
    < -0.2 with both security terms fired."""
    out = analyzer.score("Hackers steal $130M in Bitcoin")
    assert out["ok"] is True
    assert out["polarity"] < -0.2, out
    assert out["label"] == "negative"
    fired = {t["term"] for t in out["terms_fired"]}
    assert "hackers" in fired and "steal" in fired
    # companion stems from the same gap-fix batch
    assert analyzer.score(
        "Exchange exploit: $100M crypto theft in security breach"
    )["polarity"] < -0.2
    assert analyzer.score(
        "Shares dive after guidance cut forecast revision"
    )["polarity"] < -0.2


# ------------------------------------------------------ negation
def test_negation_flips_positive_to_negative(analyzer):
    """Charter: 'Fed does not signal easing' → negative, NOT positive."""
    out = analyzer.score("Fed does not signal easing")
    assert out["polarity"] < 0.0
    fired = {t["term"]: t for t in out["terms_fired"]}
    assert fired["easing"]["negated"] is True
    assert fired["easing"]["contribution"] < 0


def test_negation_window_is_three_tokens(analyzer):
    """A negator more than 3 tokens before the scored term does NOT flip."""
    far = analyzer.score("no plan was ever going to survive the rally")
    near = analyzer.score("no plan to rally")
    # 'no ... rally' at distance 9 → un-negated positive fire
    far_fired = {t["term"]: t for t in far["terms_fired"]}
    assert far_fired["rally"]["negated"] is False
    # distance exactly 3 tokens ('no'@0 → 'rally'@3) → flipped
    near_fired = {t["term"]: t for t in near["terms_fired"]}
    assert near_fired["rally"]["negated"] is True
    assert near_fired["rally"]["contribution"] < 0


def test_negator_variants_and_fails_to(analyzer):
    for h in ("Fed fails to signal easing",
              "Fed doesnt signal easing",
              "Fed never signals easing"):
        out = analyzer.score(h)
        assert out["polarity"] < 0.0, h


# ------------------------------------------------------ intensifiers
def test_intensifier_beats_diminisher(analyzer):
    sharp = analyzer.score("Gold moves sharply higher")
    slight = analyzer.score("Gold moves slightly higher")
    assert sharp["polarity"] > slight["polarity"] > 0.0
    fired = {t["term"]: t for t in sharp["terms_fired"]}
    assert fired["higher"]["multiplier"] == 1.5
    assert fired["higher"]["intensifier"] == "sharply"
    fired_s = {t["term"]: t for t in slight["terms_fired"]}
    assert fired_s["higher"]["multiplier"] == 0.5


def test_intensifier_out_of_window_does_not_apply(analyzer):
    """'sharply' 6 tokens before 'higher' → no multiplier."""
    out = analyzer.score("sharply and then quite a lot later the price is higher")
    fired = {t["term"]: t for t in out["terms_fired"]}
    assert fired["higher"]["multiplier"] == 1.0


# ------------------------------------------------------ assets
EIGHT_ASSET_HEADLINES = [
    ("GC=F", "GC=F breaks out of its weekly range"),
    ("ES=F", "S&P 500 e-mini edges higher into the close"),
    ("^TNX", "10y Treasury yield jumps after strong auction"),
    ("DX-Y.NYB", "DXY climbs as the dollar firms worldwide"),
    ("BTC-USD", "Bitcoin rallies to a fresh monthly high"),
    ("^VIX", "VIX spikes as volatility returns to markets"),
    ("CL=F", "WTI crude oil slips below key support"),
    ("EURUSD=X", "EURUSD drops as the euro loses ground"),
]


@pytest.mark.parametrize("symbol,headline", EIGHT_ASSET_HEADLINES)
def test_all_eight_instruments_detected(analyzer, symbol, headline):
    out = analyzer.score(headline)
    syms = [a["symbol"] for a in out["assets"]]
    assert symbol in syms


def test_symbol_name_fuzzy_confidence_tiers():
    tiers = detect_assets("GC=F and gold and bullion all mentioned")
    g = {a["symbol"]: a for a in tiers}["GC=F"]
    assert g["confidence"] == 1.0 and g["tier"] == "symbols"

    named = detect_assets("gold prices are rising this morning")
    n = {a["symbol"]: a for a in named}["GC=F"]
    assert n["confidence"] == 0.8 and n["tier"] == "names"

    fuzzy = detect_assets("bullion demand rises in Asia")
    f = {a["symbol"]: a for a in fuzzy}["GC=F"]
    assert f["confidence"] == 0.5 and f["tier"] == "fuzzy"


def test_highest_tier_wins_when_several_match():
    """'Gold (GC=F)' → symbol tier 1.0 beats the name mention 0.8;
    mentions count EVERY tier's occurrences; position = earliest mention."""
    both = detect_assets("Gold futures ticker GC=F rally")
    g = {a["symbol"]: a for a in both}["GC=F"]
    assert g["confidence"] == 1.0
    assert g["tier"] == "symbols"
    assert g["mentions"] == 2               # 'gold' (name) + 'gc=f' (symbol)
    assert g["position"] == 0               # earliest mention, any tier


def test_no_false_symbol_boundary_hits():
    """'golden' must not fire gold; 'european' must not fire eur."""
    assert detect_assets("the golden era of european travel") == []


def test_multi_asset_headline_detects_both():
    out = detect_assets("Gold rallies while bitcoin slides after hack")
    syms = {a["symbol"] for a in out}
    assert "GC=F" in syms and "BTC-USD" in syms


# ------------------------------------------------------ relevance
def test_relevance_early_beats_late_position(analyzer):
    early = analyzer.score("Gold rallies after the Fed meeting ends")
    late = analyzer.score("The Fed meeting ends and gold rallies")
    assert early["assets"][0]["position"] < 5
    assert late["assets"][0]["position"] >= 5
    assert early["assets"][0]["relevance"] > late["assets"][0]["relevance"]
    # 1.0 vs 0.7 position weight (relevance is rounded to 4dp)
    rel_e = early["assets"][0]["relevance"]
    rel_l = late["assets"][0]["relevance"]
    assert abs(rel_e / rel_l - 1.0 / 0.7) < 1e-3


def test_relevance_zero_without_asset(analyzer):
    out = analyzer.score("Corporate earnings season starts this week")
    assert out["relevance"] == 0.0 and out["assets"] == []


# ------------------------------------------------------ novelty
def test_identical_story_second_score_is_stale(tmp_path):
    a = NewsSentimentAnalyzer(data_root=tmp_path, llm_enabled=False)
    h = "Gold surges to record high as Fed signals dovish pivot"
    first = a.score(h)
    second = a.score(h)
    assert first["novelty"] > 0.7
    assert second["novelty"] < 0.3


def test_distinct_story_stays_novel(analyzer):
    analyzer.score("Gold surges to record high as Fed signals dovish pivot")
    fresh = analyzer.score("Banana futures collapse in Taiwan overnight")
    assert fresh["novelty"] > 0.7


def test_novelty_decay_at_80pct_overlap():
    """Charter point: a new story with ~80% trigram overlap → novelty ≈ 0.2."""
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False)
    a.score("Gold surges higher as the Fed signals a dovish pivot soon")
    # same body, 1 of 5-ish trigrams changed → overlap ≈ 0.8
    near = a.score("Gold surges higher as the Fed signals a dovish pivot today")
    assert 0.0 <= near["novelty"] <= 0.4


def test_novelty_cache_survives_restart(tmp_path):
    """Persisted n-gram cache: a NEW analyzer instance sees the prior story."""
    h = "Silver is not one of our instruments but this is a stale story"
    a1 = NewsSentimentAnalyzer(data_root=tmp_path, llm_enabled=False)
    assert a1.score(h)["novelty"] > 0.7
    a2 = NewsSentimentAnalyzer(data_root=tmp_path, llm_enabled=False)
    assert a2.score(h)["novelty"] < 0.3


def test_novelty_24h_window_expires():
    """A story older than 24h leaves the cache (injected clock)."""
    t0 = 1_000_000.0
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False,
                              clock=lambda: t0)
    a.score("An old story about gold that nobody remembers now")
    # 25h later the cache entry is evicted → the same story is novel again
    b = NewsSentimentAnalyzer(data_root=None, llm_enabled=False,
                              clock=lambda: t0 + 25 * 3600)
    assert b.score("An old story about gold that nobody remembers now")["novelty"] > 0.7


# ------------------------------------------------------ LLM fallback
def _ambiguous_headline() -> str:
    """"mixed" fires a single −0.1 subjective term: |polarity| = tanh(0.1)
    ≈ 0.0997 < 0.15 while magnitude 0.5 > 0.1 — the ambiguous gate."""
    return "Gold outlook mixed ahead of Fed"


def test_llm_fallback_called_and_blended():
    calls = []

    def mock_llm(headline: str) -> dict:
        calls.append(headline)
        return {"polarity": 0.8, "confidence": 0.9, "note": "clearly bullish"}

    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=True,
                              llm_complete=mock_llm)
    out = a.score(_ambiguous_headline())
    assert len(calls) == 1                       # called exactly once
    assert out["llm_fallback_used"] is True
    assert out["llm_fallback_failed"] is False
    assert out["llm_polarity"] == 0.8
    local = a._local_score(_ambiguous_headline())["polarity"]
    expected = 0.5 * local + 0.5 * 0.8
    assert abs(out["polarity"] - expected) < 1e-4   # 4dp rounding
    assert out["label"] == "positive"            # blend crossed the threshold


def test_llm_failure_fail_closed_keeps_local_score():
    def boom(_headline):
        raise RuntimeError("zen unreachable")

    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=True,
                              llm_complete=boom)
    out = a.score(_ambiguous_headline())
    local = a._local_score(_ambiguous_headline())["polarity"]
    assert out["llm_fallback_failed"] is True
    assert out["llm_fallback_used"] is False
    assert out["polarity"] == round(local, 4)    # local score kept verbatim


def test_llm_not_called_when_unambiguous():
    calls = []
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=True,
                              llm_complete=lambda h: calls.append(h) or {})
    a.score("Gold surges to record high")        # strongly positive
    a.score("Gold closed at 2000 flat")          # no signal at all
    assert calls == []


def test_llm_disabled_never_called():
    calls = []
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False,
                              llm_complete=lambda h: calls.append(h) or {})
    a.score(_ambiguous_headline())
    assert calls == []


def test_llm_polarity_clamped():
    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=True,
                              llm_complete=lambda h: {"polarity": 5.0})
    out = a.score(_ambiguous_headline())
    assert out["llm_polarity"] == 1.0
    assert out["polarity"] <= 1.0


# ------------------------------------------------------ subjectivity
def test_subjective_vs_objective(analyzer):
    subjective = analyzer.score("Gold is amazing")
    objective = analyzer.score("Gold closed at 2000")
    assert subjective["subjectivity"] == 1.0     # 'amazing' is subjective
    assert objective["subjectivity"] == 0.0      # no sentiment terms at all
    assert subjective["magnitude"] > objective["magnitude"]


def test_subjectivity_mixed_terms():
    """'Gold surges but feels weak' → both a subjective and an objective term."""
    out = NewsSentimentAnalyzer(data_root=None, llm_enabled=False).score(
        "Gold surges but sentiment feels weak")
    assert 0.0 < out["subjectivity"] < 1.0


# ------------------------------------------------------ lexicon hygiene
def test_lexicon_size_in_charter_range():
    """Compact by institutional standards (Loughran-McDonald ~85k,
    VADER ~7.5k): ~210 distinct stems, ~310 entries with inflection
    variants (surge/surges/surged/surging counted separately) after the
    R3-3 critic gap-fix added spike/steal/hackers/exploit/dive stems and
    the estimate/forecast guidance phrases."""
    total = len(ns.LEXICON) + len(ns.PHRASES)
    assert 150 <= total <= 360


def test_lexicon_weights_bounded():
    for term, (w, _s) in {**ns.LEXICON, **ns.PHRASES}.items():
        assert -1.0 <= w <= 1.0, term
        assert term == term.lower()


def test_tokenizer_handles_punctuation_and_apostrophes():
    toks = ns._tokenize("Doesn't GOLD all-time risk-off!")
    assert toks == ["doesnt", "gold", "all", "time", "risk", "off"]


def test_phrase_not_double_counted_as_singles(analyzer):
    out = analyzer.score("record high")
    terms = [t["term"] for t in out["terms_fired"]]
    assert terms == ["record high"]              # not record + high


def test_empty_and_whitespace_headlines(analyzer):
    assert analyzer.score("")["ok"] is False
    assert analyzer.score("   ")["ok"] is False


def test_score_never_raises_on_junk(analyzer):
    for junk in ("!!!", "123456", "GC=F 0987654321 ???", "🙂📈🚀"):
        out = analyzer.score(junk)
        assert out["ok"] is True


# ------------------------------------------------------ tape
def test_score_tape_with_injected_fetcher():
    def fetch(symbol, data_root):
        return {"ok": True, "symbol": symbol, "items": [
            {"title": f"Gold surges in {symbol} trading",
             "link": "https://x/1", "published": "Mon, 01 Jun 2026 10:00:00 GMT"},
            {"title": f"Gold plunges in {symbol} trading",
             "link": "https://x/2", "published": "Mon, 01 Jun 2026 09:00:00 GMT"},
        ]}

    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False)
    out = score_tape(data_root=None, symbols=["GC=F", "BTC-USD"],
                     analyzer=a, fetcher=fetch, workers=1)
    assert out["ok"] is True
    assert out["n_feeds"] == 2
    assert out["n_stories"] == 4
    # newest first
    assert out["stories"][0]["published"] > out["stories"][-1]["published"]
    pols = [s["polarity"] for s in out["stories"]]
    assert max(pols) > 0.2 and min(pols) < -0.2


def test_score_tape_failsoft_feeds_and_limit():
    def fetch(symbol, data_root):
        if symbol == "^VIX":
            return {"ok": False, "symbol": symbol, "error": "boom", "items": []}
        return {"ok": True, "symbol": symbol, "items": [
            {"title": f"{symbol} story number {i}",
             "link": "l", "published": "Mon, 01 Jun 2026 10:00:00 GMT"}
            for i in range(6)]}

    a = NewsSentimentAnalyzer(data_root=None, llm_enabled=False)
    out = score_tape(data_root=None, symbols=["GC=F", "^VIX"],
                     analyzer=a, fetcher=fetch, limit=4)
    assert out["ok"] is True                    # one live feed is enough
    assert out["n_feeds"] == 1
    assert out["n_feeds_requested"] == 2
    assert out["n_stories"] == 4                # limit respected


def test_score_tape_all_feeds_dead():
    out = score_tape(data_root=None, symbols=["GC=F"],
                     analyzer=NewsSentimentAnalyzer(data_root=None,
                                                    llm_enabled=False),
                     fetcher=lambda s, r: {"ok": False, "items": []},
                     workers=1)
    assert out["ok"] is False
    assert out["n_stories"] == 0


def test_score_tape_default_is_local_only():
    """Bulk tape scoring must never fan out N blocking LLM calls: with an
    ambiguous story on the tape and NO analyzer injected (the default
    path), the default analyzer is local-only — the LLM mock stays cold."""
    calls = []

    def fetch(symbol, data_root):
        return {"ok": True, "symbol": symbol, "items": [
            {"title": "Gold outlook mixed ahead of Fed",   # ambiguous
             "link": "x", "published": "Mon, 01 Jun 2026 10:00:00 GMT"},
        ]}

    # patch the default-LLM path: if llm_enabled were True, the default
    # analyzer would hit _default_llm_complete → the zen import/call. We
    # detect it by monkeypatching the class attribute.
    original = NewsSentimentAnalyzer._default_llm_complete

    def _spy(self, headline):
        calls.append(headline)
        return {"polarity": 0.5}

    NewsSentimentAnalyzer._default_llm_complete = _spy
    try:
        out = score_tape(data_root=None, symbols=["GC=F"], fetcher=fetch,
                         workers=1)
    finally:
        NewsSentimentAnalyzer._default_llm_complete = original
    assert out["ok"] is True
    assert calls == []                      # local-only: no LLM on the tape
    # and the ambiguous story is still scored (locally)
    assert out["n_stories"] == 1
    assert out["stories"][0]["llm_fallback_used"] is False


def test_tape_default_symbols_are_the_eight_desk_instruments():
    assert list(ASSET_ORDER) == ["GC=F", "ES=F", "^TNX", "DX-Y.NYB",
                                 "BTC-USD", "^VIX", "CL=F", "EURUSD=X"]


# ------------------------------------------------------ CLI wiring
def test_cli_news_sentiment_json(capsys, tmp_path):
    from gold_desk.cli import cmd_news_sentiment

    class _Args:
        headline = "Gold surges as Fed signals dovish pivot"
        tape = False
        limit = 20
        no_llm = True
        json = True
        data_root = str(tmp_path)

    rc = cmd_news_sentiment(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["ok"] is True
    assert out["polarity"] > 0.2
    assert any(a["symbol"] == "GC=F" for a in out["assets"])


def test_cli_news_sentiment_pretty(capsys, tmp_path):
    from gold_desk.cli import cmd_news_sentiment

    class _Args:
        headline = "Stocks plunge in a broad selloff"
        tape = False
        limit = 20
        no_llm = True
        json = False
        data_root = str(tmp_path)

    rc = cmd_news_sentiment(_Args())
    text = capsys.readouterr().out
    assert rc == 0
    assert "polarity" in text and "negative" in text
    assert "plunge" in text                     # terms fired listed


def test_cli_news_sentiment_missing_headline(capsys, tmp_path):
    from gold_desk.cli import cmd_news_sentiment

    class _Args:
        headline = None
        tape = False
        limit = 20
        no_llm = True
        json = True
        data_root = str(tmp_path)

    rc = cmd_news_sentiment(_Args())
    out = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert out["ok"] is False


def test_cli_news_sentiment_tape(capsys, tmp_path, monkeypatch):
    from gold_desk.cli import cmd_news_sentiment

    monkeypatch.setattr(
        "gold_desk.markets.news_sentiment.score_tape",
        lambda data_root=None, limit=20, **_kw: {
            "ok": True, "as_of": "2026-06-01T10:00:00Z", "n_feeds": 2,
            "n_feeds_requested": 8, "n_stories": 1, "limit": limit,
            "stories": [{"headline": "Gold surges", "polarity": 0.8,
                         "novelty": 1.0, "feed_symbol": "GC=F",
                         "assets": [{"symbol": "GC=F"}]}],
        })

    class _Args:
        headline = None
        tape = True
        limit = 5
        no_llm = True
        json = False
        data_root = str(tmp_path)

    rc = cmd_news_sentiment(_Args())
    text = capsys.readouterr().out
    assert rc == 0
    assert "TAPE" in text and "Gold surges" in text
