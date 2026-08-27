"""R3-2 BUILD 3 — Real-Time News NLP Sentiment (beats Refinitiv News IntelliSense).

Refinitiv News IntelliSense is a paywalled LSEG-workspace product offering
per-story sentiment, relevance and novelty. This module reproduces that
contract keyless-first, in pure stdlib, with a free-tier LLM second opinion
for genuinely ambiguous stories:

* `NewsSentimentAnalyzer.score(headline)` → the full IntelliSense-style score:
    - polarity      (-1.0 .. +1.0) — lexicon + phrase scoring, tanh-bounded
    - magnitude     (0.0 .. 1.0)   — fraction of tokens that are sentiment-bearing
    - subjectivity  (0.0 .. 1.0)   — share of opinion/judgment language among
                                      sentiment-bearing terms (TextBlob-style)
    - assets        — the 8 desk instruments detected with confidence tiers
                      (exact symbol 1.0 / name 0.8 / fuzzy 0.5)
    - relevance     — asset-mention density × headline-position weight
                      (asset in the first 5 words = 1.0, later = 0.7)
    - novelty       — 1 − max trigram overlap with the last 24h of scored
                      stories (persisted n-gram cache; 80% overlap → 0.2)
    - terms_fired   — the audit trail: which term fired, where, with what
                      weight / intensifier multiplier / negation flip

* Negation handling: "not", "no", "fails to", "fails", "never", ... flip the
  next scored term within a 3-token window ("Fed does not signal easing"
  scores negative, not positive).
* Intensifier/diminisher multipliers: "sharply"/"dramatically" ×1.5,
  "slightly"/"marginally" ×0.5 applied to the next scored term within the
  same 3-token window.
* LLM fallback: when |polarity| < 0.15 AND magnitude > 0.1 (ambiguous but
  signal-rich) the analyzer asks the Zen free-tier model for a second
  opinion and blends 50/50. Fail-closed: any LLM failure keeps the local
  score and flags `llm_fallback_failed`. The pipeline NEVER blocks on the
  LLM (single attempt, short timeout).

* `score_tape()` — score the current live news tape: keyless Yahoo headline
  RSS fanned out across the 8 desk instruments (reuses markets.news), every
  story scored, newest first.

Law boundary: display/education telemetry for the gauntlet surface, NOT
wired into the orchestrator's decision loop (constitution-gated).
"""
from __future__ import annotations

import email.utils
import json
import math
import re
import time
import urllib.parse  # noqa: F401 — used by callers mirroring this pattern
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..clock import iso, utc_now

# ------------------------------------------------------------------ lexicon
# term -> (weight, subjective). Weight in [-1, 1]; subjective terms express
# opinion/judgment (feed the subjectivity ratio), objective terms state
# directional facts (feed polarity only). ~210 entries incl. variants —
# a deliberately compact financial lexicon, no stemming magic: exact token
# match after a light tokenizer, so "golden" never fires "gold".
LEXICON: dict[str, tuple[float, bool]] = {
    # ---- strong positive, objective
    "surge": (0.8, False), "surges": (0.8, False), "surged": (0.8, False),
    "surging": (0.8, False),
    "soar": (0.8, False), "soars": (0.8, False), "soared": (0.8, False),
    "soaring": (0.8, False),
    "dive": (-0.7, False), "dives": (-0.7, False), "dived": (-0.7, False),
    "diving": (-0.7, False),
    "skyrocket": (0.9, False), "skyrockets": (0.9, False),
    # ---- moderate positive, objective
    "rally": (0.7, False), "rallies": (0.7, False), "rallied": (0.7, False),
    "rallying": (0.7, False),
    "jump": (0.5, False), "jumps": (0.5, False), "jumped": (0.5, False),
    "jumping": (0.5, False),
    "spike": (0.7, False), "spikes": (0.7, False), "spiked": (0.7, False),
    "spiking": (0.7, False),
    "gain": (0.4, False), "gains": (0.4, False), "gained": (0.4, False),
    "gaining": (0.4, False),
    "rise": (0.4, False), "rises": (0.4, False), "rose": (0.4, False),
    "risen": (0.4, False), "rising": (0.4, False),
    "climb": (0.4, False), "climbs": (0.4, False), "climbed": (0.4, False),
    "climbing": (0.4, False),
    "advance": (0.4, False), "advances": (0.4, False), "advanced": (0.4, False),
    "higher": (0.4, False), "highs": (0.3, False),
    "up": (0.2, False),
    "beat": (0.5, False), "beats": (0.5, False), "beaten": (0.5, False),
    "topped": (0.4, False), "tops": (0.4, False),
    "exceeds": (0.5, False), "exceeded": (0.5, False),
    "surpass": (0.5, False), "surpasses": (0.5, False),
    "upgrade": (0.6, False), "upgraded": (0.6, False), "upgrades": (0.6, False),
    "outperform": (0.5, False), "outperforms": (0.5, False),
    "outperformed": (0.5, False),
    "rebound": (0.5, False), "rebounds": (0.5, False), "rebounded": (0.5, False),
    "recover": (0.4, False), "recovery": (0.4, False), "recovers": (0.4, False),
    "growth": (0.4, False), "expansion": (0.3, False), "expands": (0.3, False),
    "boost": (0.4, False), "boosts": (0.4, False), "boosted": (0.4, False),
    "support": (0.3, False), "supports": (0.3, False),
    "inflows": (0.4, False), "accelerate": (0.3, False),
    "accelerates": (0.3, False),
    "firmer": (0.3, False), "momentum": (0.3, False),
    "breakout": (0.5, False), "breakouts": (0.5, False),
    "dovish": (0.5, False), "easing": (0.5, False), "stimulus": (0.4, False),
    "accumulation": (0.3, False),
    "buy": (0.3, False), "buying": (0.3, False),
    # ---- positive, subjective (opinion/judgment)
    "strong": (0.5, True), "strongest": (0.6, True), "robust": (0.5, True),
    "boom": (0.6, True), "booming": (0.6, True),
    "optimism": (0.6, True), "confident": (0.5, True),
    "bullish": (0.6, True), "bulls": (0.5, True), "upbeat": (0.5, True),
    "amazing": (0.7, True), "stunning": (0.6, True),
    "spectacular": (0.6, True), "rosy": (0.5, True),
    "undervalued": (0.5, True), "cheap": (0.3, True),
    "euphoria": (0.6, True),
    # ---- strong negative, objective
    "plunge": (-0.8, False), "plunges": (-0.8, False), "plunged": (-0.8, False),
    "plunging": (-0.8, False),
    "plummet": (-0.8, False), "plummets": (-0.8, False),
    "plummeted": (-0.8, False), "plummeting": (-0.8, False),
    "crash": (-0.9, False), "crashes": (-0.9, False), "crashed": (-0.9, False),
    "crashing": (-0.9, False),
    "collapse": (-0.8, False), "collapses": (-0.8, False),
    "collapsed": (-0.8, False),
    "selloff": (-0.7, False),
    "tumble": (-0.7, False), "tumbles": (-0.7, False), "tumbled": (-0.7, False),
    "tumbling": (-0.7, False),
    "steal": (-0.6, False), "steals": (-0.6, False), "stole": (-0.6, False),
    "stolen": (-0.6, False), "theft": (-0.7, False),
    "sink": (-0.6, False), "sinks": (-0.6, False), "sank": (-0.6, False),
    "sunk": (-0.6, False), "sinking": (-0.6, False),
    # ---- moderate negative, objective
    "slump": (-0.6, False), "slumps": (-0.6, False), "slumped": (-0.6, False),
    "slide": (-0.5, False), "slides": (-0.5, False), "slid": (-0.5, False),
    "sliding": (-0.5, False),
    "drop": (-0.5, False), "drops": (-0.5, False), "dropped": (-0.5, False),
    "dropping": (-0.5, False),
    "fall": (-0.5, False), "falls": (-0.5, False), "fell": (-0.5, False),
    "fallen": (-0.5, False), "falling": (-0.5, False),
    "decline": (-0.5, False), "declines": (-0.5, False),
    "declined": (-0.5, False), "declining": (-0.5, False),
    "lose": (-0.4, False), "loses": (-0.4, False), "lost": (-0.4, False),
    "losing": (-0.4, False),
    "lower": (-0.4, False), "lows": (-0.3, False),
    "down": (-0.2, False),
    "miss": (-0.5, False), "misses": (-0.5, False), "missed": (-0.4, False),
    "downgrade": (-0.6, False), "downgraded": (-0.6, False),
    "downgrades": (-0.6, False),
    "underperform": (-0.5, False), "underperforms": (-0.5, False),
    "underperformed": (-0.5, False),
    "retreat": (-0.4, False), "retreats": (-0.4, False),
    "reversal": (-0.3, False), "reverses": (-0.3, False),
    "recession": (-0.6, False), "contraction": (-0.5, False),
    "crisis": (-0.7, False), "default": (-0.6, False), "defaults": (-0.6, False),
    "bankruptcy": (-0.8, False), "bankrupt": (-0.8, False),
    "hack": (-0.8, False), "hacked": (-0.8, False), "hacks": (-0.8, False),
    "hackers": (-0.8, False), "hacker": (-0.7, False), "heist": (-0.8, False),
    "exploit": (-0.7, False), "exploits": (-0.7, False),
    "exploited": (-0.7, False),
    "breach": (-0.7, False), "breached": (-0.7, False),
    "breaches": (-0.7, False),
    "fraud": (-0.8, False), "scandal": (-0.7, False),
    "probe": (-0.4, False), "probes": (-0.4, False), "lawsuit": (-0.4, False),
    "hawkish": (-0.5, False), "tightening": (-0.5, False),
    "outflows": (-0.4, False),
    "liquidation": (-0.6, False), "liquidations": (-0.6, False),
    "halt": (-0.4, False), "halts": (-0.4, False), "halted": (-0.5, False),
    "suspended": (-0.5, False), "sanctions": (-0.5, False),
    "curbs": (-0.4, False), "curb": (-0.4, False),
    "ban": (-0.4, False), "bans": (-0.4, False), "banned": (-0.4, False),
    "slowdown": (-0.5, False), "slows": (-0.4, False), "stall": (-0.3, False),
    "stalls": (-0.3, False),
    "layoffs": (-0.5, False), "unemployment": (-0.4, False),
    "deficit": (-0.3, False),
    "war": (-0.5, False), "invasion": (-0.6, False), "conflict": (-0.4, False),
    "sell": (-0.3, False), "selling": (-0.4, False),
    "dump": (-0.6, False), "dumps": (-0.6, False),
    "slammed": (-0.6, False), "slams": (-0.6, False),
    "hammered": (-0.6, False), "routed": (-0.6, False),
    # ---- negative, subjective
    "weak": (-0.5, True), "weaker": (-0.5, True), "weakness": (-0.5, True),
    "weakest": (-0.6, True),
    "fears": (-0.5, True), "fear": (-0.5, True), "worried": (-0.5, True),
    "worries": (-0.5, True), "anxiety": (-0.5, True),
    "panic": (-0.7, True), "panics": (-0.7, True),
    "capitulation": (-0.6, True),
    "gloom": (-0.6, True), "doom": (-0.6, True), "grim": (-0.6, True),
    "bleak": (-0.6, True),
    "bearish": (-0.6, True), "bears": (-0.5, True),
    "bloodbath": (-0.9, True), "massacre": (-0.8, True),
    "brutal": (-0.6, True), "ugly": (-0.5, True), "nasty": (-0.5, True),
    "painful": (-0.5, True),
    "bubble": (-0.5, True), "overvalued": (-0.5, True),
    "expensive": (-0.3, True),
    "uncertainty": (-0.4, True), "uncertain": (-0.4, True),
    "volatile": (-0.2, True), "volatility": (-0.2, True),
    "choppy": (-0.2, True), "mixed": (-0.1, True),
    "dramatic": (-0.4, True),
}

# multi-word phrases matched FIRST (longest span wins) and consumed so their
# tokens never double-count as singles: "record high" ≠ record + high.
PHRASES: dict[str, tuple[float, bool]] = {
    "record high": (0.9, False),
    "all time high": (0.9, False),          # "all-time high" tokenizes to this
    "under pressure": (-0.5, False),
    "beats estimates": (0.6, False),
    "misses estimates": (-0.6, False),
    "blowout earnings": (0.7, False),
    "rate cut": (0.3, False),
    "rate cuts": (0.3, False),
    "rate hike": (-0.3, False),
    "rate hikes": (-0.3, False),
    "risk off": (-0.6, False),              # "risk-off" tokenizes to this
    "risk on": (0.4, False),
    "safe haven": (0.3, False),
    "flight to safety": (0.4, False),
    "bull run": (0.7, False),
    "bull market": (0.6, False),
    "bear market": (-0.7, False),
    "dead cat": (-0.2, False),              # dead-cat bounce (skepticism)
    "sell off": (-0.7, False),              # "sell-off" tokenizes to this
    "panic selling": (-0.8, False),
    "profit taking": (-0.3, False),
    "short squeeze": (0.6, False),
    "flash crash": (-0.9, False),
    "melt up": (0.6, False),                # "melt-up" tokenizes to this
    "fails to": (-0.1, False),              # negator phrase, tiny weight
    # ---- R3-3 critic gap-fix: estimate/forecast guidance phrases
    "top estimate": (0.5, False),
    "tops estimate": (0.5, False),
    "tops estimates": (0.5, False),
    "top estimates": (0.5, False),
    "topped estimates": (0.5, False),
    "raise forecast": (0.5, False),
    "raises forecast": (0.5, False),
    "raised forecast": (0.5, False),
    "raise forecasts": (0.5, False),
    "raises forecasts": (0.5, False),
    "raise guidance": (0.5, False),
    "raises guidance": (0.5, False),
    "raised guidance": (0.5, False),
    "cut forecast": (-0.5, False),
    "cuts forecast": (-0.5, False),
    "cut forecasts": (-0.5, False),
    "cuts forecasts": (-0.5, False),
    "cut guidance": (-0.5, False),
    "cuts guidance": (-0.5, False),
}

# negators flip the polarity of the NEXT scored term within a 3-token window
NEGATORS: frozenset[str] = frozenset({
    "not", "no", "never", "nor", "fails", "fail", "failed", "failing",
    "without", "cannot", "cant", "wont", "doesnt", "isnt", "arent",
    "dont", "didnt", "refuses", "denies", "lacks", "unlikely",
})

# intensifier / diminisher multipliers applied forward within 3 tokens
INTENSIFIERS: dict[str, float] = {
    "sharply": 1.5, "dramatically": 1.5, "massively": 1.5, "strongly": 1.5,
    "slightly": 0.5, "marginally": 0.5, "mildly": 0.5, "modestly": 0.5,
    "somewhat": 0.5,
}

MODIFIER_WINDOW = 3          # tokens between modifier and the scored term
LABEL_THRESHOLD = 0.15       # |polarity| below this → "neutral"
LLM_POLARITY_THRESHOLD = 0.15  # ambiguity gate (see module docstring)
LLM_MAGNITUDE_FLOOR = 0.10
NOVELTY_WINDOW_S = 24 * 3600  # 24h n-gram cache
NOVELTY_CACHE_MAX = 500       # bound the persisted cache
BLEND_WEIGHT_LLM = 0.5        # 50/50 local/LLM blend

# ------------------------------------------------------------------ assets
# the 8 desk instruments (same canonical symbols as multi_asset.INSTRUMENTS).
# confidence tiers: exact symbol 1.0 / name 0.8 / fuzzy mention 0.5.
ASSET_TABLE: dict[str, dict] = {
    "GC=F": {
        "name": "Gold",
        "symbols": ["gc=f", "gc=x", "xauusd", "xau/usd"],
        "names": ["gold"],
        "fuzzy": ["bullion", "comex", "yellow metal", "precious metal", "xau"],
    },
    "ES=F": {
        "name": "S&P 500 E-mini",
        "symbols": ["es=f", "spx", "^gspc", "gspc"],
        "names": ["s&p 500", "s&p", "sp 500", "sp500", "e-mini", "emini"],
        "fuzzy": ["stocks", "equities", "wall street", "stock market"],
    },
    "^TNX": {
        "name": "US 10Y Yield",
        "symbols": ["^tnx", "tnx"],
        "names": ["10y", "10-year", "10 year", "treasury", "treasuries",
                  "yield", "yields"],
        "fuzzy": ["rates", "bonds", "bond market"],
    },
    "DX-Y.NYB": {
        "name": "US Dollar Index",
        "symbols": ["dx-y.nyb", "dxy"],
        "names": ["dollar index", "us dollar", "dollar"],
        "fuzzy": ["greenback", "buck", "usd"],
    },
    "BTC-USD": {
        "name": "Bitcoin",
        "symbols": ["btc-usd", "btcusd", "btc=x", "btc"],
        "names": ["bitcoin"],
        "fuzzy": ["crypto", "cryptocurrency", "digital asset"],
    },
    "^VIX": {
        "name": "VIX",
        "symbols": ["^vix", "vix"],
        "names": ["volatility", "fear gauge", "fear index"],
        "fuzzy": ["vol", "cboe"],
    },
    "CL=F": {
        "name": "WTI Crude",
        "symbols": ["cl=f", "wti"],
        "names": ["crude oil", "crude", "oil", "brent"],
        "fuzzy": ["energy", "petroleum", "opec"],
    },
    "EURUSD=X": {
        "name": "EUR/USD",
        "symbols": ["eurusd=x", "eurusd", "eur/usd"],
        "names": ["euro", "single currency", "common currency"],
        "fuzzy": ["eur", "ecb", "eurozone"],
    },
}

ASSET_ORDER = ["GC=F", "ES=F", "^TNX", "DX-Y.NYB",
               "BTC-USD", "^VIX", "CL=F", "EURUSD=X"]

_SYMBOL_CONFIDENCE = {"symbols": 1.0, "names": 0.8, "fuzzy": 0.5}
_RELEVANCE_SCALE = 3.0      # a 1-in-3-word mention density saturates relevance
_HEADLINE_POSITION_WORDS = 5   # asset in the first 5 words → weight 1.0
_LATE_POSITION_WEIGHT = 0.7    # later mention → 0.7


# ------------------------------------------------------------------ helpers
def _tokenize(text: str) -> list[str]:
    """Lowercase word tokens. Apostrophes are deleted BEFORE the split so
    "doesn't" → "doesnt" (a NEGATORS member); everything non-alphanumeric
    splits ("risk-off" → [risk, off], "all-time" → [all, time])."""
    cleaned = re.sub(r"['’`]", "", (text or "").lower())
    return [t for t in re.split(r"[^a-z0-9]+", cleaned) if t]


def _normalize_for_assets(text: str) -> str:
    """Lowercased, whitespace-collapsed text (punctuation KEPT — symbol
    terms like 'GC=F' and 's&p 500' need it) padded for boundary lookarounds."""
    return " " + re.sub(r"\s+", " ", (text or "").lower().strip()) + " "


def _term_pattern(term: str) -> re.Pattern:
    # boundary = not adjacent to an alphanumeric char, so "gold" never
    # matches "golden"/"goldman", "eur" never matches "europe".
    return re.compile(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])")


def _word_position(norm_text: str, char_index: int) -> int:
    """0-based word index of a char position in the normalized text."""
    return len(norm_text[:char_index].split())


def detect_assets(text: str) -> list[dict]:
    """Detect the 8 desk instruments in a headline.

    Returns one entry per matched instrument (ASSET_ORDER sequence), each:
        {symbol, name, confidence, tier, matched_term, position, mentions}
    `confidence` is the tier value (symbol 1.0 / name 0.8 / fuzzy 0.5) —
    when several tiers match, the HIGHEST tier wins and the matched term
    is the earliest hit inside that tier. `mentions` counts occurrences of
    ALL the instrument's terms (any tier), and `position` is the word index
    of the EARLIEST mention of any term (the headline-position weight
    follows the first time the instrument comes up, not the strongest
    alias).
    """
    norm = _normalize_for_assets(text)
    if norm.strip() == "":
        return []
    out: list[dict] = []
    for symbol in ASSET_ORDER:
        spec = ASSET_TABLE[symbol]
        best: dict | None = None
        mentions = 0
        earliest_any: int | None = None
        for tier in ("symbols", "names", "fuzzy"):
            conf = _SYMBOL_CONFIDENCE[tier]
            earliest: tuple[str, int] | None = None
            for term in spec.get(tier, []):
                pat = _term_pattern(term)
                m = pat.search(norm)
                if not m:
                    continue
                mentions += len(pat.findall(norm))
                pos = _word_position(norm, m.start())
                if earliest_any is None or pos < earliest_any:
                    earliest_any = pos
                if earliest is None or pos < earliest[1]:
                    earliest = (term, pos)
            if earliest is not None and (best is None
                                         or conf > best["confidence"]):
                best = {"confidence": conf, "tier": tier,
                        "matched_term": earliest[0], "position": earliest[1]}
        if best is not None:
            if mentions == 0:            # defensive: tier hit implies ≥1
                mentions = 1
            if earliest_any is None:     # defensive: same implication
                earliest_any = best["position"]
            out.append({"symbol": symbol, "name": spec["name"],
                        "confidence": best["confidence"],
                        "tier": best["tier"],
                        "matched_term": best["matched_term"],
                        "position": earliest_any,
                        "mentions": mentions})
    return out


def _relevance(assets: list[dict], n_words: int) -> None:
    """In-place relevance per asset: mention density × position weight.

    density   = mentions / max(1, n_words)  (share of the headline devoted
                to the instrument), scaled so a 1-in-3-word density
                saturates at 1.0
    position  = 1.0 when the first mention sits in the first 5 words,
                else 0.7
    relevance = min(1.0, density × 3) × position — clamped to [0, 1].
    """
    for a in assets:
        density = a["mentions"] / max(1, n_words)
        position_weight = (1.0 if a["position"] < _HEADLINE_POSITION_WORDS
                           else _LATE_POSITION_WEIGHT)
        a["relevance"] = round(min(1.0, density * _RELEVANCE_SCALE)
                               * position_weight, 4)


def _ngrams(tokens: list[str], n: int) -> set[str]:
    if len(tokens) < n:
        return set()
    return {" ".join(tokens[i:i + n]) for i in range(len(tokens) - n + 1)}


def _novelty_ngrams(tokens: list[str]) -> set[str]:
    """Trigram set with bigram → unigram fallback for tiny headlines."""
    for n in (3, 2, 1):
        grams = _ngrams(tokens, n)
        if grams:
            return grams
    return set()


# ------------------------------------------------------------------ analyzer
class NewsSentimentAnalyzer:
    """Lexicon sentiment scorer with asset detection, relevance, novelty
    and an optional (fail-closed, never-blocking) LLM second opinion.

    `llm_complete` — injectable second-opinion callable: takes the headline,
    returns {"polarity": float(-1..1), ...}. Defaults to the Zen free-tier
    client (single attempt, `llm_timeout` seconds). Any exception → the
    local score is kept and `llm_fallback_failed: true` is flagged.

    `clock` — injectable epoch-seconds callable (tests pin the 24h novelty
    window). The novelty cache persists to
    <data_root>/cache/news_sentiment_novelty.json so novelty survives CLI
    invocations; pass `data_root=None` for a purely in-memory cache.
    """

    def __init__(self, data_root: str | Path | None = "data",
                 llm_complete=None, llm_timeout: float = 12.0,
                 llm_enabled: bool = True, clock=None):
        self.data_root = Path(data_root) if data_root is not None else None
        self.llm_complete = llm_complete
        self.llm_timeout = float(llm_timeout)
        self.llm_enabled = bool(llm_enabled)
        self.clock = clock or time.time
        self._cache: list[dict] = []          # [{ts, ngrams: [...]}]
        self._load_cache()

    # ------------------------------------------------------------ novelty
    def _cache_path(self) -> Path | None:
        if self.data_root is None:
            return None
        d = self.data_root / "cache"
        return d / "news_sentiment_novelty.json"

    def _load_cache(self) -> None:
        path = self._cache_path()
        if path is None or not path.exists():
            return
        try:
            raw = json.loads(path.read_text())
            now = self.clock()
            self._cache = [e for e in raw.get("entries", [])
                           if now - float(e.get("ts", 0)) < NOVELTY_WINDOW_S]
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            self._cache = []                  # corrupt cache → fresh start

    def _save_cache(self) -> None:
        path = self._cache_path()
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"entries": self._cache}))
        except OSError:
            pass                              # fail-soft: novelty is best-effort

    def _novelty(self, grams: set[str]) -> float:
        """1 − max overlap: fraction of THIS story's n-grams already seen in
        any prior (24h) story. Identical story → 0.0; fully distinct → 1.0;
        80% trigram overlap → 0.2."""
        if not grams or not self._cache:
            return 1.0
        new = grams
        worst = 0.0
        for entry in self._cache:
            prior = set(entry.get("ngrams") or [])
            if not prior:
                continue
            overlap = len(new & prior) / len(new)
            if overlap > worst:
                worst = overlap
        return 1.0 - worst

    def _remember(self, grams: set[str]) -> None:
        now = self.clock()
        self._cache.append({"ts": now, "ngrams": sorted(grams)})
        self._cache = [e for e in self._cache
                       if now - float(e.get("ts", 0)) < NOVELTY_WINDOW_S]
        if len(self._cache) > NOVELTY_CACHE_MAX:
            self._cache = self._cache[-NOVELTY_CACHE_MAX:]
        self._save_cache()

    # ------------------------------------------------------------ scoring
    def _local_score(self, headline: str) -> dict:
        tokens = _tokenize(headline)
        n = len(tokens)
        consumed = [False] * n
        matches: list[tuple[int, int, str, float, bool]] = []

        # phrase pass — longest spans first, consuming their tokens
        for span in (3, 2):
            for i in range(n - span + 1):
                if any(consumed[i:i + span]):
                    continue
                phrase = " ".join(tokens[i:i + span])
                if phrase in PHRASES:
                    w, subj = PHRASES[phrase]
                    matches.append((i, i + span, phrase, w, subj))
                    for k in range(i, i + span):
                        consumed[k] = True

        # single-token pass
        for i, tok in enumerate(tokens):
            if consumed[i]:
                continue
            hit = LEXICON.get(tok)
            if hit is not None:
                w, subj = hit
                matches.append((i, i + 1, tok, w, subj))
                consumed[i] = True

        negators = {i for i, t in enumerate(tokens) if t in NEGATORS}
        intens = {i: INTENSIFIERS[t] for i, t in enumerate(tokens)
                  if t in INTENSIFIERS}

        fired: list[dict] = []
        total = 0.0
        n_sentiment_tokens = 0
        n_subjective_tokens = 0
        for start, end, term, weight, subj in sorted(matches):
            negated = any(0 < start - ni <= MODIFIER_WINDOW
                          for ni in negators)
            multiplier = 1.0
            intensifier = None
            for ii, mult in intens.items():
                if 0 < start - ii <= MODIFIER_WINDOW:
                    multiplier = mult
                    intensifier = tokens[ii]
            contribution = weight * multiplier * (-1.0 if negated else 1.0)
            total += contribution
            n_sentiment_tokens += end - start
            if subj:
                n_subjective_tokens += end - start
            fired.append({
                "term": term,
                "position": start,
                "weight": weight,
                "multiplier": multiplier,
                "intensifier": intensifier,
                "negated": negated,
                "contribution": round(contribution, 4),
                "subjective": subj,
            })

        return {
            "tokens": tokens,
            "polarity": math.tanh(total),
            "raw_sum": round(total, 4),
            "magnitude": (n_sentiment_tokens / n) if n else 0.0,
            "subjectivity": (n_subjective_tokens / n_sentiment_tokens)
                            if n_sentiment_tokens else 0.0,
            "terms_fired": fired,
        }

    # ------------------------------------------------------------ LLM
    def _default_llm_complete(self, headline: str) -> dict:
        """Zen free-tier second opinion — single attempt, no catalog sync
        (never blocks, never hits the network for model discovery)."""
        from ..llm.zen_client import complete_json
        from ..llm.expert_chat import load_catalog
        catalog = (load_catalog(str(self.data_root)) if self.data_root
                   else {}) or {}
        model = catalog.get("default") or "x-preview-f-free"
        body = complete_json(
            [
                {"role": "system", "content": (
                    "You are a financial-news sentiment engine. Score the "
                    "polarity of the user's market headline from -1.0 (max "
                    "bearish) to +1.0 (max bullish) for the asset(s) it "
                    "mentions. Respond ONLY with compact JSON: "
                    '{"polarity": <float>, "confidence": <float 0..1>, '
                    '"note": "<max 12 words>"}. No prose, no markdown.')},
                {"role": "user", "content": headline},
            ],
            model, timeout=self.llm_timeout, temperature=0.0,
            max_tokens=120, retries=1,
        )
        polarity = body.get("polarity")
        if not isinstance(polarity, (int, float)) or isinstance(polarity, bool):
            raise ValueError("llm polarity missing/not numeric")
        return {
            "polarity": max(-1.0, min(1.0, float(polarity))),
            "confidence": max(0.0, min(1.0, float(body.get("confidence", 0.5)))),
            "note": str(body.get("note", ""))[:120],
            "model": model,
        }

    # ------------------------------------------------------------ public
    def score(self, headline: str) -> dict:
        """Full IntelliSense-style score for one headline. Never raises."""
        headline = (headline or "").strip()
        if not headline:
            return {"ok": False, "error": "empty headline"}

        local = self._local_score(headline)
        tokens = local["tokens"]
        grams = _novelty_ngrams(tokens)
        novelty = self._novelty(grams)

        assets = detect_assets(headline)
        _relevance(assets, len(tokens))

        polarity = local["polarity"]
        out: dict = {
            "ok": True,
            "headline": headline,
            "polarity": round(polarity, 4),
            "magnitude": round(local["magnitude"], 4),
            "subjectivity": round(local["subjectivity"], 4),
            "label": self._label(polarity),
            "assets": assets,
            "relevance": max((a["relevance"] for a in assets), default=0.0),
            "novelty": round(novelty, 4),
            "terms_fired": local["terms_fired"],
            "llm_fallback_used": False,
            "n_tokens": len(tokens),
            "scored_at": iso(utc_now()),
        }

        # ---- LLM second opinion: ambiguous but signal-rich only
        ambiguous = (abs(polarity) < LLM_POLARITY_THRESHOLD
                     and local["magnitude"] > LLM_MAGNITUDE_FLOOR)
        if ambiguous and self.llm_enabled:
            try:
                call = (self.llm_complete or self._default_llm_complete)
                second = call(headline)
                llm_pol = float(second.get("polarity", 0.0))
                llm_pol = max(-1.0, min(1.0, llm_pol))
                blended = ((1.0 - BLEND_WEIGHT_LLM) * polarity
                           + BLEND_WEIGHT_LLM * llm_pol)
                out.update({
                    "polarity": round(blended, 4),
                    "label": self._label(blended),
                    "llm_fallback_used": True,
                    "llm_fallback_failed": False,
                    "llm_polarity": round(llm_pol, 4),
                    "llm_note": str(second.get("note", ""))[:120],
                })
            except Exception:  # noqa: BLE001 — fail-closed, never block
                out["llm_fallback_failed"] = True

        self._remember(grams)                 # novelty cache AFTER scoring
        return out

    @staticmethod
    def _label(polarity: float) -> str:
        if polarity > LABEL_THRESHOLD:
            return "positive"
        if polarity < -LABEL_THRESHOLD:
            return "negative"
        return "neutral"


# ------------------------------------------------------------------ tape
TAPE_WORKERS = 8
TAPE_DEFAULT_LIMIT = 20


def score_tape(data_root: str | Path = "data", symbols: list[str] | None = None,
               limit: int = TAPE_DEFAULT_LIMIT, analyzer: NewsSentimentAnalyzer | None = None,
               fetcher=None, workers: int = TAPE_WORKERS,
               llm_enabled: bool = False) -> dict:
    """Score the current live news tape: keyless Yahoo headline RSS per
    instrument (reuses markets.news.fetch_symbol_news — TTL-cached,
    fail-soft per symbol), merged, newest-first, every story scored by one
    analyzer so novelty is meaningful across the tape.

    Bulk scoring is LOCAL-ONLY by default (`llm_enabled=False`): a 20-story
    tape must never fan out up to 20 sequential blocking LLM second
    opinions — the LLM fallback belongs on the single-headline surface
    (NewsSentimentAnalyzer.score / the /api/desk/news/sentiment route),
    where one fail-closed call is bounded. Pass `llm_enabled=True` (or an
    analyzer built that way) to override.

    `fetcher(symbol, data_root) -> dict` is injectable for tests.
    Returns {ok, as_of, n_feeds, n_stories, stories: [score + tape fields]}.
    """
    from .news import fetch_symbol_news
    if symbols is None:
        symbols = list(ASSET_ORDER)
    fetch = fetcher or (lambda sym, root: fetch_symbol_news(sym, data_root=root))

    def _one(sym: str) -> dict:
        try:
            return fetch(sym, data_root)
        except Exception as e:  # noqa: BLE001 — fail-soft per feed
            return {"ok": False, "symbol": sym,
                    "error": f"{type(e).__name__}: {e}", "items": []}

    fetched: dict[str, dict] = {}
    if workers > 1 and len(symbols) > 1:
        with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as ex:
            futures = {ex.submit(_one, sym): sym for sym in symbols}
            for fut in as_completed(futures):
                fetched[futures[fut]] = fut.result()
    else:
        for sym in symbols:
            fetched[sym] = _one(sym)

    def _published_key(item: dict) -> float:
        try:
            dt = email.utils.parsedate_to_datetime(item.get("published", ""))
            return dt.timestamp() if dt else 0.0
        except (TypeError, ValueError):
            return 0.0

    stories: list[dict] = []
    n_ok = 0
    for sym in symbols:
        res = fetched.get(sym) or {}
        if not res.get("ok"):
            continue
        n_ok += 1
        for item in (res.get("items") or []):
            stories.append({
                "feed_symbol": sym,
                "title": item.get("title", ""),
                "link": item.get("link", ""),
                "published": item.get("published", ""),
                "published_ts": _published_key(item),
            })
    stories.sort(key=lambda s: -s["published_ts"])

    if analyzer is None:
        analyzer = NewsSentimentAnalyzer(data_root=data_root,
                                         llm_enabled=llm_enabled)
    scored = []
    for s in stories[:max(0, limit)]:
        result = analyzer.score(s["title"])
        result["feed_symbol"] = s["feed_symbol"]
        result["link"] = s["link"]
        result["published"] = s["published"]
        scored.append(result)

    return {
        "ok": n_ok > 0,
        "as_of": iso(utc_now()),
        "n_feeds": n_ok,
        "n_feeds_requested": len(symbols),
        "n_stories": len(scored),
        "limit": limit,
        "stories": scored,
    }
