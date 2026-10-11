"""GAUNTLET 8 Piece 3 — data integrity & honesty locks.

Covers the critic's ledger, mechanically, no network:

* D-005  journal_util stamps the REAL constitution content hash (same
  loader as demo/validate), never the "agent-sidecar-v1" marker; an
  existing data-root hash file is preserved; a missing YAML degrades to
  an honest not-a-digest marker.
* D-006  macro_dashboard reads live DXY/VIX from the driver payload's
  {"live": {"D2", "D10"}} (the old dv["drivers"]["dxy"/"vix"] probe
  always missed).
* D-007  negative caching: a dead fetch is tombstoned and not re-attempted
  within the window; fetch_series with no key makes no API attempt and a
  second call within the negative window makes NO network call at all.
* D-004  the sp500 Wikipedia parse tolerates markup shapes (ticker as
  anchor text AND legacy title attribute), fails CLEAN on an alien page,
  and cmd_screen fail-closes ANY exception into {ok: false} — no
  traceback ever reaches stdout.
* D-009  fetch_daily_bars_ex distinguishes "feed failed" from "no bars";
  transient failures are retried (bounded), persistent ones tombstoned,
  and a stale cache still serves.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.agent import journal_util  # noqa: E402
from gold_desk.constitution import load_constitution  # noqa: E402
from gold_desk.markets import board  # noqa: E402
from gold_desk.markets import screener  # noqa: E402
from gold_desk.macro import fred  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


# ------------------------------------------------------------------ D-005
class TestConstitutionHashHonesty:
    def test_sidecar_stamps_real_constitution_hash(self, tmp_path):
        """A fresh data root's sidecar journal carries the REAL content
        hash — the same value a demo/validate run journals."""
        expected = load_constitution(
            REPO / "trading_constitution.yaml").content_hash
        jr = journal_util.default_journal(tmp_path)
        assert jr.constitution_hash == expected
        assert jr.constitution_hash != "agent-sidecar-v1"
        # and it is a hex digest, not a marker
        assert len(jr.constitution_hash) == 64
        int(jr.constitution_hash, 16)  # raises if not hex

    def test_existing_hash_file_is_preserved(self, tmp_path):
        """A data root with its own provenance keeps it (demo-overlay
        roots legitimately differ from the canonical YAML)."""
        (tmp_path / "hashes").mkdir()
        (tmp_path / "hashes" / "constitution.sha256").write_text(
            "aa" * 32 + "\n")
        jr = journal_util.default_journal(tmp_path)
        assert jr.constitution_hash == "aa" * 32

    def test_missing_yaml_degrades_to_honest_marker(self, tmp_path,
                                                    monkeypatch):
        """No constitution file → a marker that is visibly NOT a digest —
        never a fabricated hash (fail-closed honesty)."""
        monkeypatch.setattr(journal_util, "CONSTITUTION_YAML",
                            tmp_path / "absent.yaml")
        h = journal_util.real_constitution_hash()
        assert h == journal_util.NO_CONSTITUTION_HASH
        assert "agent-sidecar" not in h
        with pytest.raises(ValueError):
            int(h, 16)  # NOT hex — cannot masquerade as a digest

    def test_no_marker_string_anywhere_in_module(self):
        """The poisoned literal is gone from the source (grep-level)."""
        src = (REPO / "src" / "gold_desk" / "agent" /
               "journal_util.py").read_text()
        assert "agent-sidecar-v1" not in src


# ------------------------------------------------------------------ D-006
class TestMacroLiveDrivers:
    def test_dashboard_reads_live_D2_D10(self, tmp_path, monkeypatch):
        """DXY/VIX come from dv['live']['D2']/['D10'] — the keys that
        actually exist in the driver payload."""
        calls = {}

        def fake_dv(data_root):
            calls["dv"] = True
            return {"ok": True, "live": {
                "D2": {"value": 103.2, "unit": "idx",
                       "source": "Yahoo Finance (DX-Y.NYB)"},
                "D10": {"value": 14.1, "unit": "idx",
                        "source": "Yahoo Finance (^VIX)"},
            }, "unavailable": []}

        def fake_series(sid, data_root=None):
            return {"ok": False, "errors": ["offline"], "series_id": sid}

        def fake_gvr(data_root):
            return {"ok": False, "errors": ["offline"]}

        import gold_desk.data.driver_feeds as df
        monkeypatch.setattr(df, "fetch_driver_values", fake_dv)
        monkeypatch.setattr(fred, "fetch_series", fake_series)
        monkeypatch.setattr(fred, "gold_vs_real", fake_gvr)
        out = fred.macro_dashboard(tmp_path)
        assert out["live_drivers"].get("dxy", {}).get("value") == 103.2
        assert out["live_drivers"].get("vix", {}).get("value") == 14.1
        assert "error" not in out["live_drivers"]

    def test_dashboard_fail_soft_when_drivers_dead(self, tmp_path,
                                                   monkeypatch):
        import gold_desk.data.driver_feeds as df

        def boom(data_root):
            raise RuntimeError("network dead")

        monkeypatch.setattr(df, "fetch_driver_values", boom)
        monkeypatch.setattr(fred, "fetch_series",
                            lambda sid, data_root=None:
                            {"ok": False, "errors": ["offline"]})
        monkeypatch.setattr(fred, "gold_vs_real",
                            lambda data_root: {"ok": False})
        out = fred.macro_dashboard(tmp_path)
        assert "error" in out["live_drivers"]


# ------------------------------------------------------------------ D-007
class TestNegativeCaching:
    def test_tombstone_stops_refetch_within_window(self, tmp_path):
        """A failing fetch is attempted ONCE; within the negative window
        the tombstone answers instantly (call counter stays at 1)."""
        calls = {"n": 0}

        def dead_fetch():
            calls["n"] += 1
            raise RuntimeError("endpoint dead")

        a = fred._cached_fetch(tmp_path, "tomb", 3600, dead_fetch,
                               neg_ttl=600)
        assert a["ok"] is False and a["negative"] is True
        b = fred._cached_fetch(tmp_path, "tomb", 3600, dead_fetch,
                               neg_ttl=600)
        assert b["ok"] is False and b["cached"] is True
        assert calls["n"] == 1, "dead endpoint was re-polled"

    def test_tombstone_expires_and_retries(self, tmp_path):
        """Past the negative window the endpoint is retried (and a
        recovery is picked up)."""
        calls = {"n": 0}

        def flaky_fetch():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("transient")
            return {"ok": True, "value": 42}

        fred._cached_fetch(tmp_path, "flaky", 3600, flaky_fetch,
                           neg_ttl=600)
        # force the tombstone to expire
        p = fred._cache_path(tmp_path, "flaky")
        blob = json.loads(p.read_text())
        blob["fetched_at"] = time.time() - 601
        p.write_text(json.dumps(blob))
        out = fred._cached_fetch(tmp_path, "flaky", 3600, flaky_fetch,
                                 neg_ttl=600)
        assert out["ok"] is True and out["value"] == 42

    def test_fetch_series_no_key_makes_no_api_attempt(self, tmp_path,
                                                      monkeypatch):
        """D-007 fail-fast: keyless → only the CSV path is attempted, and
        a second call inside the negative window makes NO network call."""
        urls: list[str] = []

        def fake_http_get(url, timeout=8.0):
            urls.append(url)
            raise RuntimeError("offline (simulated)")

        monkeypatch.delenv("FRED_API_KEY", raising=False)
        monkeypatch.setattr(fred, "_http_get", fake_http_get)
        a = fred.fetch_series("DGS10", tmp_path)
        assert a["ok"] is False
        assert len(urls) == 1, f"expected only the CSV attempt: {urls}"
        assert "api.stlouisfed.org" not in urls[0]
        b = fred.fetch_series("DGS10", tmp_path)
        assert b["ok"] is False and len(urls) == 1, (
            "second call hit the network inside the negative window")

    def test_fetch_series_with_key_tries_api_first(self, tmp_path,
                                                   monkeypatch):
        urls: list[str] = []

        def fake_http_get(url, timeout=8.0):
            urls.append(url)
            raise RuntimeError("offline (simulated)")

        monkeypatch.setenv("FRED_API_KEY", "test-key")
        monkeypatch.setattr(fred, "_http_get", fake_http_get)
        fred.fetch_series("DGS10", tmp_path)
        assert urls and urls[0].startswith("https://api.stlouisfed.org")


# ------------------------------------------------------------------ D-004
class TestScreenerFailClosed:
    def test_parse_accepts_ticker_as_anchor_text(self, tmp_path):
        """Current Wikipedia markup: the ticker is the anchor TEXT."""
        html = "".join(
            f'<td><a href="/wiki/{t}" title="Company {i} Inc.">{t}</a></td>'
            f"<td>Consumer</td><td>1988</td>"
            for i, t in enumerate(["AAPL", "MSFT", "BRK.B", "NVDA"]))
        monkey = screener._SP500_PATTERNS
        assert monkey  # patterns registered
        import re
        hits = set()
        for pat in monkey:
            hits |= set(re.findall(pat, html))
        assert {"AAPL", "MSFT", "BRK.B", "NVDA"} <= hits

    def test_parse_accepts_legacy_title_attribute(self):
        import re
        html = ('<td><a href="/wiki/X" title="ABC">Acme</a></td>'
                '<td><a href="/wiki/Y" title="XYZ">Beta</a></td>')
        hits = set(re.findall(screener._SP500_PATTERNS[1], html))
        assert {"ABC", "XYZ"} <= hits

    def test_parse_real_wikipedia_fixture(self, tmp_path):
        """D-004 regression against REAL served markup (fixture captured
        live 2026-10-11: Parsoid table, ticker = anchor TEXT, attributed
        td/tr tags). The pre-fix regex scored 0 on exactly this shape."""
        fixture = (REPO / "tests" / "fixtures" /
                   "sp500_sample.html").read_text()
        import re
        hits: set[str] = set()
        for pat in screener._SP500_PATTERNS:
            hits |= set(re.findall(pat, fixture))
        assert len(hits) == 12, f"fixture parse got {len(hits)}: {hits}"
        assert {"A", "ABBV", "ABNB"} <= hits

    def test_sp500_alien_markup_raises_clean(self, tmp_path, monkeypatch):
        """A genuinely alien page fails the sanity gate with a CLEAN
        error (no half-symbols, no index noise)."""
        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return b"<html><body>give up</body></html>"

        import urllib.request as ur
        monkeypatch.setattr(ur, "urlopen", lambda req, timeout=15: FakeResp())
        with pytest.raises(RuntimeError) as ei:
            screener._sp500_symbols_raw(tmp_path, min_count=400)
        assert "wikipedia parse yielded" in str(ei.value)

    def test_cmd_screen_fail_closed_no_traceback(self, tmp_path, capsys,
                                                 monkeypatch):
        """D-004: whatever explodes inside screen(), the CLI prints a
        clean {ok: false, errors: [...]} — never a traceback."""
        from gold_desk.cli import main

        def boom(*a, **k):
            raise RuntimeError("simulated catastrophic failure")

        monkeypatch.setattr(screener, "screen", boom)
        rc = main(["screen", "--where", "rsi<30", "--universe", "registry",
                   "--json", "--data-root", str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "Traceback" not in out
        payload = json.loads(out.strip().splitlines()[-1])
        assert payload["ok"] is False
        assert "simulated catastrophic failure" in payload["errors"][0]

    def test_cmd_screen_non_json_fail_closed(self, tmp_path, capsys,
                                             monkeypatch):
        from gold_desk.cli import main
        monkeypatch.setattr(screener, "screen",
                            lambda *a, **k: (_ for _ in ()).throw(
                                OSError("network down")))
        rc = main(["screen", "--where", "rsi<30", "--data-root",
                   str(tmp_path)])
        out = capsys.readouterr().out
        assert rc == 1
        assert "Traceback" not in out and "network down" in out


# ------------------------------------------------------------------ D-009
class TestDailyBarsVerdict:
    def test_ex_distinguishes_fetch_failed_from_no_bars(self, tmp_path,
                                                        monkeypatch):
        """A transport failure is ok:False (+negative tombstone); a feed
        that answers with zero bars is ok:True with bars=[] — no retry
        on the deterministic case."""
        calls = {"chart": 0}

        def empty_chart(symbol, range_, interval):
            calls["chart"] += 1
            return {}   # no timestamp array → zero bars

        monkeypatch.setattr(board, "_fetch_chart", empty_chart)
        monkeypatch.setattr(board, "DAILY_BAR_RETRY_PAUSE_S", 0)
        out = board.fetch_daily_bars_ex("NOPE=X", "1y", tmp_path)
        assert out["ok"] is True and out["bars"] == []
        assert calls["chart"] == 1, "no-bars must NOT be retried"

        calls2 = {"chart": 0}

        def dead_chart(symbol, range_, interval):
            calls2["chart"] += 1
            raise RuntimeError("connection reset")

        monkeypatch.setattr(board, "_fetch_chart", dead_chart)
        monkeypatch.setattr(board, "DAILY_BAR_ATTEMPTS", 2)
        out2 = board.fetch_daily_bars_ex("GC=F", "1y", tmp_path)
        assert out2["ok"] is False and out2.get("negative") is True
        assert calls2["chart"] == 2, "transient failure must be retried"
        # and the tombstone stops further polling
        board.fetch_daily_bars_ex("GC=F", "1y", tmp_path)
        assert calls2["chart"] == 2, "tombstone did not stop the re-poll"

    def test_stale_cache_still_serves(self, tmp_path, monkeypatch):
        """A transient failure with a warm cache serves the STALE bars
        (stale_error set) instead of dropping to []."""
        stale = [{"ts": 1_700_000_000 * 1000, "o": 1.0, "h": 1.0,
                  "l": 1.0, "c": 1.0, "v": 0.0}]
        board._cached_fetch(tmp_path, "daily_GC=F", 1800,
                            lambda: {"ok": True, "bars": stale})
        # poison the clock so the cache is out of TTL
        p = board._cache_path(tmp_path, "daily_GC=F")
        blob = json.loads(p.read_text())
        blob["fetched_at"] = time.time() - 10_000
        p.write_text(json.dumps(blob))

        def dead(symbol, range_, interval):
            raise RuntimeError("reset")

        monkeypatch.setattr(board, "_fetch_chart", dead)
        monkeypatch.setattr(board, "DAILY_BAR_ATTEMPTS", 1)
        out = board.fetch_daily_bars_ex("GC=F", "1y", tmp_path)
        assert out["ok"] is True and out["stale_error"]
        assert board.fetch_daily_bars("GC=F", "1y", tmp_path) == stale

    def test_fetch_daily_bars_wrapper_returns_bars(self, tmp_path,
                                                   monkeypatch):
        bars = [{"ts": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}]
        monkeypatch.setattr(board, "_fetch_chart",
                            lambda s, r, i: {"timestamp": [1.0],
                                             "quote": [[{"close": [1.0]}]]})
        # simpler: bypass the chart shape — stub _ex directly
        monkeypatch.setattr(board, "fetch_daily_bars_ex",
                            lambda s, r, dr: {"ok": True, "bars": bars})
        assert board.fetch_daily_bars("GC=F", "1y", tmp_path) == bars
        monkeypatch.setattr(board, "fetch_daily_bars_ex",
                            lambda s, r, dr: {"ok": False, "negative": True})
        assert board.fetch_daily_bars("GC=F", "1y", tmp_path) == []
