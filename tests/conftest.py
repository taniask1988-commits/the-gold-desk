"""Shared fixtures. The FAKE CONTRACT lives ONLY here — never in the
canonical constitution file (plan Doc 5). Values chosen for easy hand math:

    contract_size 100 oz/lot -> a 1.00 price move per lot = $100
    lot_step 0.01, min 0.01, max 20, tick 0.01/$1.00
    risk_pct 0.0025 (0.25%) on equity
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.constitution import Constitution  # noqa: E402
from gold_desk.data.model import Bar, CalendarEvent, Quote  # noqa: E402
from gold_desk.filters import AccountState, MarketState  # noqa: E402
from gold_desk.setup.engine import SetupCandidate  # noqa: E402

REPO = Path(__file__).resolve().parents[1]


def fake_raw(**over) -> dict:
    raw = {
        "schema": "trading_constitution.v1",
        "identity": {"name": "Gold Decision Harness", "version": 1,
                     "instrument": "XAUUSD", "timeframe": "H1",
                     "mode": "proposal_only", "phase": 1},
        "firm": {"enabled": False, "name": "PERSONAL", "account_size": 10000.0,
                 "news_trading_rule": "no_entries_in_blackout",
                 "weekend_holding_rule": "no_weekend"},
        "broker": {"name_or_feed": "FAKE", "symbol": "XAUUSD",
                   "contract_size": 100, "quote_digits": 2, "tick_size": 0.01,
                   "tick_value": 1.0, "lot_step": 0.01, "min_lot": 0.01,
                   "max_lot": 20.0, "typical_london_open_spread": 0.30,
                   "min_spread_assumption": 0.25, "commission_per_lot_rt": 7.0,
                   "session_timezone": "UTC", "sunday_open_policy": "skip"},
        "costs": {"slippage_buffer": 0.10,
                  "assume_spread_at_least_london_typical": True,
                  "unfillable_if_spread_gt": 0.60},
        "internal_limits": {
            "risk_pct_per_trade": 0.0025, "sizing_equity_basis": "equity",
            "max_trades_per_day": 2, "consecutive_loss_standdown": 2,
            "overnight_positions": False,
            "allowed_sessions": ["london", "london_ny_overlap"],
            "news_blackout_minutes_before": 30, "news_blackout_minutes_after": 30,
            "high_impact_only": True, "max_spread": 0.45,
            "min_stop_distance_spread_mult": 3.0,
            "min_stop_distance_atr_mult": 0.5, "min_rr": 1.5,
            "daily_loss_internal_pct": 0.01, "max_dd_internal_pct": 0.04,
            "max_bar_lag_minutes": 5, "outlier_return_abs_pct": 2.0,
        },
        "execution": {"boundary": "telegram_manual_paste", "cbot_template": False,
                      "approval": "human_required", "ticket_expiry_minutes": 10,
                      "late_approval": "dead_ticket", "chase_fill": False},
        "fail_closed": {"llm_timeout": "no_ticket", "llm_invalid_json": "veto",
                        "news_unavailable": "no_trade", "stale_data": "no_trade",
                        "spread_widened": "reject", "retry_into_fill": False},
        "llm": {"in_v1_live_loop": "phase2_only", "tools": [],
                "identity": "context_veto"},
        "journal": {"every_bar_reason_code": True, "memory_retrieval": False},
        "promotion": {"requires_simulator_pass": True, "llm_cannot_promote": True},
    }
    for dotted, value in over.items():
        node = raw
        parts = dotted.split(".")
        for p in parts[:-1]:
            node = node[p]
        node[parts[-1]] = value
    return raw


def make_constitution(**over) -> Constitution:
    raw = fake_raw(**over)
    canon = json.dumps(raw, sort_keys=True, separators=(",", ":"))
    return Constitution(
        raw=raw,
        file_path=Path("FAKE_IN_MEMORY.yaml"),
        file_hash="0" * 64,
        content_hash=hashlib.sha256(canon.encode()).hexdigest(),
    )


def bar_at(day: datetime, hour: int, o=2400.0, h=None, l=None, c=2401.0) -> Bar:
    open_dt = day.replace(hour=hour, minute=0, second=0, microsecond=0,
                          tzinfo=timezone.utc)
    high = h if h is not None else max(o, c) + 1.0
    low = l if l is not None else min(o, c) - 1.0
    from gold_desk.clock import iso
    return Bar(ts_open=iso(open_dt), ts_close=iso(open_dt + timedelta(hours=1)),
               open=o, high=high, low=low, close=c, volume=100.0)


MONDAY = datetime(2026, 6, 1, tzinfo=timezone.utc)  # 2026-06-01 is a Monday


def good_quote(spread=0.30, price=2400.0) -> Quote:
    return Quote(ts="2026-06-01T08:00:00Z", bid=round(price - spread / 2, 2),
                 ask=round(price + spread / 2, 2))


def good_account(**over) -> AccountState:
    state = AccountState(equity=10000.0, balance=10000.0, daily_pnl=0.0,
                         open_positions=[], trades_today=0,
                         consecutive_losses=0, high_water=10000.0)
    for k, v in over.items():
        setattr(state, k, v)
    return state


def good_market(**over) -> MarketState:
    state = MarketState(spread=0.30, bar_close_ts="2026-06-01T08:00:00Z",
                        now_ts="2026-06-01T08:00:00Z", session="london",
                        news_blackout_active=False, news_available=True)
    for k, v in over.items():
        setattr(state, k, v)
    return state


def good_candidate(**over) -> SetupCandidate:
    cand = SetupCandidate(
        setup_id="GUESS_london_range_breakout", setup_version="0.1.0",
        decision_ts="2026-06-01T08:00:00Z", side="buy", entry_type="market",
        entry=2410.0, stop=2404.0, target=2422.0,
        time_stop_ts="2026-06-01T14:00:00Z", expiry_ts="2026-06-01T08:10:00Z",
        invalidation="H1 close back inside range", stop_distance=6.0,
        features_used={"atr14": 4.0}, data_hash="d" * 64, spec_hash="s" * 64,
    )
    for k, v in over.items():
        setattr(cand, k, v)
    return cand


@pytest.fixture()
def constitution() -> Constitution:
    return make_constitution()


@pytest.fixture()
def tmp_data(tmp_path):
    (tmp_path / "events").mkdir(parents=True)
    (tmp_path / "tickets").mkdir(parents=True)
    return tmp_path


# ------------------------------------------------------- scipy-free harness
def pytest_addoption(parser):
    """R3-3 gap-fix: `pytest --no-scipy` installs a meta-path blocker that
    makes ANY import of scipy/numpy raise ModuleNotFoundError — used by
    tests/test_scipy_free.py to prove the stdlib-pure risk/portfolio/
    attribution tests still RUN (not skip wholesale) in a stdlib-only
    deploy. Only the ~7 tests that genuinely reference scipy/numpy as
    test oracles skip."""
    parser.addoption("--no-scipy", action="store_true", default=False,
                     help="block scipy/numpy imports (simulate stdlib-only "
                          "deploy)")


class _ScipyBlocker:
    """Meta-path import blocker for scipy.* / numpy.* — `import scipy`
    raises ModuleNotFoundError exactly as in a stdlib-only deploy."""

    def find_spec(self, fullname, path=None, target=None):  # pragma: no cover
        if fullname.split(".")[0] in ("scipy", "numpy"):
            raise ModuleNotFoundError(f"No module named {fullname!r} "
                                      f"(blocked by --no-scipy)")
        return None


def pytest_configure(config):
    if config.getoption("--no-scipy"):
        import sys
        for mod in list(sys.modules):
            if mod.split(".")[0] in ("scipy", "numpy"):
                del sys.modules[mod]
        sys.meta_path.insert(0, _ScipyBlocker())


# ===========================================================================
# R6-0 — DEVICE-PORTABLE SYNC ROOTS
#
# FUNDAMENTAL: a frozen test matrix is an installer's verification
# function — its verdict must be a function of the REPO CONTENTS, never
# of the machine it runs on. The 3-way sync guards (R2 memo/evidence,
# R4-D5 web routes) compare the repo against EXTERNAL deployment roots
# (the download mirror and the live :3000 runtime tree) that exist ONLY
# on the build machine. On any other device those roots cannot exist,
# so the guard's correct verdict there is "not applicable" (SKIP), not
# "failed". Where a root DOES exist the guard stays a HARD failure —
# drift between repo and a present deployment copy must never pass.
#
# Env overrides (GOLD_DESK_MIRROR_ROOT / GOLD_DESK_RUNTIME_WEB_ROOT)
# exist so the skip path itself is testable on any machine — see
# tests/test_portability.py, which re-runs the guards in a subprocess
# with absent roots and asserts they all SKIP (the fresh-device
# contract the installer depends on).
# ===========================================================================
DEFAULT_MIRROR_ROOT = "/home/z/my-project/download/gold_desk_v1"
DEFAULT_RUNTIME_WEB_ROOT = "/home/z/my-project/src/app"


@pytest.fixture(scope="session")
def mirror_root() -> Path:
    """The download-mirror deployment root, if this device has one."""
    import os
    root = Path(os.environ.get("GOLD_DESK_MIRROR_ROOT",
                               DEFAULT_MIRROR_ROOT))
    if not root.is_dir():
        pytest.skip(f"mirror root not present on this device "
                    f"({root}) — sync guard is a build-machine check")
    return root


@pytest.fixture(scope="session")
def runtime_web_root() -> Path:
    """The live Next.js src/app tree, if this device has one."""
    import os
    root = Path(os.environ.get("GOLD_DESK_RUNTIME_WEB_ROOT",
                               DEFAULT_RUNTIME_WEB_ROOT))
    if not root.is_dir():
        pytest.skip(f"runtime web root not present on this device "
                    f"({root}) — sync guard is a build-machine check")
    return root
