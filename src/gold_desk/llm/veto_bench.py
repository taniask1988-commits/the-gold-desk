"""OFFLINE veto research bench. Never the live loop (Law L10).

Builds a REAL blind context pack through the actual pipeline code (synthetic
bars -> SetupEngine -> build_pack with the blindfold scrubber), then runs the
§8.3 veto completion against an OpenCode Zen free model. Scenarios:

  clean : normal London breakout, no news for hours      (expect ENDORSE-ish)
  news  : high-impact CPI landing 12 minutes after entry (expect VETO)
  stale : calendar/news timestamps AFTER the decision ts (expect VETO — the
          pack looks inconsistent, which is exactly what the veto is for)

Results append to data/veto_bench.jsonl (separate from the live journal).
This is model-behaviour research, not promotion: nothing here can issue a
ticket, size a trade, or change any setup's status (L12, §12.5).
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..constitution import load_constitution
from ..context_pack import build_pack
from ..data.bars import SyntheticBarSource, SyntheticConfig
from ..data.model import wrap
from ..setup.engine import SetupEngine
from ..data.model import CalendarEvent, NewsItem
from .veto_llm import run_veto
from .zen_client import LLMUnavailable
from .zen_sync import load_catalog, sync_catalog

REPO_ROOT = Path(__file__).resolve().parents[3]
BENCH_DATE = datetime(2026, 6, 10, 9, 0, tzinfo=timezone.utc)  # a demo ticket day


def _find_signal(bars):
    """Return (candidate_bars, decision_ts, candidate) using the real engine."""
    engine = SetupEngine()
    # scan London mornings for a day that fires the GUESS setup
    for i in range(200, len(bars)):
        window = bars[max(0, i - 60): i]
        decision = bars[i - 1].close_dt
        cand = engine.evaluate(window, decision)
        if cand is not None:
            return window, decision, cand
    return None, None, None


def build_bench_pack(scenario: str = "clean") -> dict | None:
    source = SyntheticBarSource(SyntheticConfig(seed=42),
                                start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                                days=12)
    bars = source.bars_up_to(BENCH_DATE + timedelta(days=2), 10**9)
    window, decision, cand = _find_signal(bars)
    if cand is None:
        return None

    if scenario == "news":
        ev_ts = decision + timedelta(minutes=12)
        calendar = [CalendarEvent(ts=ev_ts.isoformat().replace("+00:00", "Z"),
                                  currency="USD", impact="high",
                                  title="CPI y/y")]
        news = [NewsItem(ts=(ev_ts + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"), headline="CPI comes in hot above forecasts",
            source="BENCH-WIRE")]
    elif scenario == "stale":
        future = decision + timedelta(hours=3)
        calendar = [CalendarEvent(ts=future.isoformat().replace("+00:00", "Z"),
                                  currency="USD", impact="high",
                                  title="FOMC statement")]
        news = [NewsItem(ts=(future + timedelta(minutes=1)).isoformat().replace(
            "+00:00", "Z"), headline="Fed signals surprise hike",
            source="BENCH-WIRE")]
    else:  # clean
        old = decision - timedelta(hours=6)
        calendar = [CalendarEvent(ts=old.isoformat().replace("+00:00", "Z"),
                                  currency="EUR", impact="medium",
                                  title="German factory orders")]
        news = [NewsItem(ts=(old + timedelta(minutes=2)).isoformat().replace(
            "+00:00", "Z"), headline="Eurozone data in line, muted reaction",
            source="BENCH-WIRE")]

    bars_obs = [wrap("bar", b.close_dt, {
        "ts_open": b.ts_open, "ts_close": b.ts_close,
        "o": b.open, "h": b.high, "l": b.low, "c": b.close,
    }) for b in window[-30:]]
    feats_obs = [wrap("feature", decision, dict(cand.features_used))]
    cal_obs = [wrap("calendar",
                    datetime.fromisoformat(e.ts.replace("Z", "+00:00")),
                    {"ts": e.ts, "currency": e.currency, "impact": e.impact,
                     "title": e.title}) for e in calendar]
    news_obs = [wrap("news",
                     datetime.fromisoformat(n.ts.replace("Z", "+00:00")),
                     {"ts": n.ts, "headline": n.headline,
                      "source": n.source}) for n in news]

    demo_constitution = load_constitution(
        REPO_ROOT / "trading_constitution.yaml",
        overlay_path=REPO_ROOT / "config" / "demo.yaml",
    )
    return build_pack(demo_constitution, cand, bars_obs, feats_obs,
                      cal_obs, news_obs).to_dict()


EXPECTED = {"clean": "ENDORSE", "news": "VETO", "stale": "VETO"}


def run_bench(model: str | None = None, scenario: str = "clean",
              data_root: Path | None = None, timeout: float = 120.0,
              as_json: bool = False) -> bool:
    data_root = data_root or (REPO_ROOT / "data")
    catalog = load_catalog(data_root) or sync_catalog(data_root)
    model = model or catalog.get("default")
    if not model:
        msg = "no free model available (catalog empty and sync failed)"
        print(json.dumps({"ok": False, "error": msg}) if as_json else msg)
        return False

    pack = build_bench_pack(scenario)
    if pack is None:
        msg = "scenario produced no candidate pack — engine too quiet"
        print(json.dumps({"ok": False, "error": msg}) if as_json else msg)
        return False

    if not as_json:
        print(f"VETO RESEARCH BENCH — offline, never the live loop")
        print(f"scenario : {scenario} (human expectation: {EXPECTED[scenario]})")
        print(f"model    : {model}  (OpenCode Zen, keyless, free)")
        print(f"pack     : {len(pack.get('bars', []))} bars, "
              f"candidate={pack['candidate']['side']} "
              f"@ {pack['candidate']['entry']}")
    try:
        result = run_veto(pack, model, timeout=timeout, max_tokens=2500)
    except LLMUnavailable as e:
        if not as_json:
            print(f"LLM_UNAVAILABLE: {e}  (bench records the failure and stops)")
        result = {"decision": "VETO", "reason": f"LLM_UNAVAILABLE: {e}",
                  "model": model, "latency_ms": None, "error": True}

    result.update({
        "schema": "veto_bench.v1",
        "ts": datetime.now(timezone.utc).isoformat(),
        "scenario": scenario,
        "expected": EXPECTED[scenario],
        "match": result.get("decision") == EXPECTED[scenario],
    })
    bench_path = data_root / "veto_bench.jsonl"
    data_root.mkdir(parents=True, exist_ok=True)
    with bench_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, sort_keys=True) + "\n")

    if as_json:
        print(json.dumps({"ok": True, **result}, sort_keys=True))
        return True

    latency = result.get("latency_ms")
    print(f"decision : {result['decision']}  "
          f"(expected {EXPECTED[scenario]} -> "
          f"{'MATCH' if result['match'] else 'DIVERGE'})")
    print(f"reason   : {result['reason'][:200]}")
    if latency is not None:
        print(f"latency  : {latency} ms")
    print(f"bench log: {bench_path}")
    return True
