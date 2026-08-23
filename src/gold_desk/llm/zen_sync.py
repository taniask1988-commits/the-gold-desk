"""zen-sync ported from the owner's Hermes setup (OPENCODE_HERMES_SETUP.md §2).

Two public data sources:

  Zen /v1/models    https://opencode.ai/zen/v1/models   served model IDs (truth)
  models.dev        https://models.dev/api.json         per-model metadata

Selection rules (§2.3):
  - keep only models Zen CURRENTLY SERVES
  - keep only FREE models (cost.input == 0 and cost.output == 0)
  - agentic filter: tool_call == True (drops TTS/embeddings/noise)
  - deprecated-but-served models are KEPT (flag) but never the default

Change detection (§2.2):
  - Zen ID list changed  -> full rebuild from models.dev
  - unchanged, cache <6h -> replay cache (no 4MB fetch)
  - unchanged, cache >6h -> full rebuild (metadata drift)
  - Zen unreachable      -> replay cache (any age) or BUNDLED_FALLBACK

Default resolution (§2.4): hardcoded preference order, skipping deprecated;
when Zen removes the current default the next sync falls through — no manual
config edits, exactly like the Hermes deployment.

Catalog file: data/zen-catalog.json (atomic write), schema zen_catalog.v1.
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

ZEN_BASE_URL = os.environ.get("OPENCODE_ZEN_BASE_URL", "https://opencode.ai/zen/v1")
MODELS_DEV_URL = "https://models.dev/api.json"
CATALOG_SCHEMA = "zen_catalog.v1"
CACHE_TTL_S = 6 * 3600
USER_AGENT = "opencode/1.18.18 (gold-desk)"

# §2.4 — owner's preference order (skip deprecated; fall through on removal)
DEFAULT_PREFERENCE = [
    "x-preview-f-free",                  # 1M ctx, reasoning
    "muse-spark-1.2-contributor-free",   # 1M ctx, reasoning xhigh
    "hy3-free",                          # established reasoning model
    "mimo-v2.5-free",
    "nemotron-3-ultra-free",             # 1M ctx
    "nemotron-3.5-lightning-free",
    "big-pickle",
    "glm-5-free",
    "kimi-k2.5-free",
    "grok-code",
    "deepseek-v4-flash-free",            # deprecated — last resort
    "laguna-s-2.1-free",                 # deprecated — last resort
]

# §2.5 — bundled static fallback (boot never breaks with zero network).
# Metadata snapshot verified live on 2026-08-23.
BUNDLED_FALLBACK = {
    "schema": CATALOG_SCHEMA,
    "default": "x-preview-f-free",
    "synced_ts": None,
    "source": "bundled-fallback",
    "models": {
        "x-preview-f-free": {"context_window": 1000000, "supports_tools": True,
                             "supports_reasoning": True, "supports_vision": False,
                             "deprecated": False},
        "muse-spark-1.2-contributor-free": {"context_window": 1048576,
                                            "supports_tools": True,
                                            "supports_reasoning": True,
                                            "supports_vision": False,
                                            "deprecated": False},
        "hy3-free": {"context_window": 190000, "supports_tools": True,
                     "supports_reasoning": True, "supports_vision": False,
                     "deprecated": False},
        "mimo-v2.5-free": {"context_window": 200000, "supports_tools": True,
                           "supports_reasoning": True, "supports_vision": False,
                           "deprecated": False},
        "nemotron-3-ultra-free": {"context_window": 1000000,
                                  "supports_tools": True,
                                  "supports_reasoning": True,
                                  "supports_vision": False,
                                  "deprecated": False},
        "nemotron-3.5-lightning-free": {"context_window": 262144,
                                        "supports_tools": True,
                                        "supports_reasoning": True,
                                        "supports_vision": False,
                                        "deprecated": False},
        "big-pickle": {"context_window": 200000, "supports_tools": True,
                       "supports_reasoning": True, "supports_vision": False,
                       "deprecated": False},
        "deepseek-v4-flash-free": {"context_window": 200000,
                                   "supports_tools": True,
                                   "supports_reasoning": True,
                                   "supports_vision": False,
                                   "deprecated": True},
        "laguna-s-2.1-free": {"context_window": 256000, "supports_tools": True,
                              "supports_reasoning": True,
                              "supports_vision": False,
                              "deprecated": True},
    },
}


class ZenSyncError(RuntimeError):
    pass


def _http_json(url: str, timeout: float = 30.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_zen_ids(base_url: str = ZEN_BASE_URL) -> set[str]:
    data = _http_json(f"{base_url.rstrip('/')}/models", timeout=20)
    return {m["id"] for m in data.get("data", []) if m.get("id")}


def fetch_models_dev_opencode() -> dict:
    data = _http_json(MODELS_DEV_URL, timeout=60)
    return (data.get("opencode") or {}).get("models") or {}


def select_free_models(zen_ids: set[str], dev_models: dict) -> dict:
    """The §2.3 rules as a pure function (unit-tested, no network)."""
    out: dict[str, dict] = {}
    for mid, meta in dev_models.items():
        if mid not in zen_ids:
            continue  # not currently served -> drop
        cost = meta.get("cost") or {}
        if cost.get("input") != 0 or cost.get("output") != 0:
            continue  # not free -> drop
        if meta.get("tool_call") is not True:
            continue  # not agentic -> drop
        limit = meta.get("limit") or {}
        out[mid] = {
            "context_window": limit.get("context"),
            "output_limit": limit.get("output"),
            "supports_tools": True,
            "supports_reasoning": bool(meta.get("reasoning")),
            "supports_vision": bool(meta.get("vision")),
            "deprecated": bool(meta.get("deprecated")),
        }
    return out


def resolve_default(models: dict) -> str | None:
    """§2.4 preference order, skipping deprecated; then any live model."""
    for pref in DEFAULT_PREFERENCE:
        m = models.get(pref)
        if m and not m.get("deprecated"):
            return pref
    live = sorted(mid for mid, m in models.items() if not m.get("deprecated"))
    return live[0] if live else (sorted(models)[0] if models else None)


def _atomic_write(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True))
    tmp.replace(path)


def load_catalog(data_root: str | Path) -> dict | None:
    path = Path(data_root) / "zen-catalog.json"
    if not path.exists():
        return None
    try:
        cat = json.loads(path.read_text())
        if cat.get("schema") == CATALOG_SCHEMA and cat.get("models"):
            return cat
    except json.JSONDecodeError:
        pass
    return None


def sync_catalog(data_root: str | Path, force: bool = False) -> dict:
    """Run the §2.2 decision table. Never raises — always returns a catalog."""
    root = Path(data_root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "zen-catalog.json"
    cached = load_catalog(root)

    try:
        zen_ids = fetch_zen_ids()
    except Exception:
        # Zen unreachable: replay cache (any age) or bundled fallback
        if cached:
            cached["source"] = "cache-replay-network-down"
            return cached
        return dict(BUNDLED_FALLBACK)

    if cached and not force:
        # §2.2 ID-diff fast path: same Zen list + fresh cache -> replay
        same_ids = set(cached.get("zen_ids") or []) == zen_ids
        fresh = (time.time() - (cached.get("synced_ts") or 0)) < CACHE_TTL_S
        if same_ids and fresh and cached.get("models"):
            cached["source"] = "cache-replay"
            return cached

    # full rebuild from models.dev
    try:
        dev_models = fetch_models_dev_opencode()
        models = select_free_models(zen_ids, dev_models)
        if not models:
            raise ZenSyncError("zero free models after selection")
    except Exception:
        if cached:
            cached["source"] = "cache-replay-rebuild-failed"
            return cached
        return dict(BUNDLED_FALLBACK)

    catalog = {
        "schema": CATALOG_SCHEMA,
        "default": resolve_default(models),
        "synced_ts": time.time(),
        "source": "live-sync",
        "zen_served": len(zen_ids),
        "zen_ids": sorted(zen_ids),
        "models": models,
    }
    _atomic_write(path, catalog)
    return catalog


def _ids_match(cached: dict, zen_ids: set[str]) -> bool:
    """Kept for API compatibility: true when the cached Zen list is current."""
    return set(cached.get("zen_ids") or []) == zen_ids
