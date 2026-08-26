"""R3-2 BUILD 4 — backtest engine: the GUESS London-range-breakout setup
run against historical GC=F 1h bars (keyless Yahoo chart endpoint).

`fetch_hourly_bars(symbol, range_key)` — 1y of hourly OHLCV bars as closed
`Bar` objects (UTC, ISO-stamped), TTL file-cached under
<data_root>/cache/ like every markets module. A `_TEST_BARS` module seam
lets tests pin the chart payload without any network.

`BacktestEngine(bars).run()` — walks every closed bar, asks the existing
`setup.engine.SetupEngine` (the SAME rule the live desk runs: pre-London
range 02:00–07:00 UTC, signal bars opening 08–10 UTC, 1.5·ATR stop, 2R
target, 6-bar time-stop) for a candidate, then resolves the position
mechanically bar by bar — stop before target inside the same bar
(pessimistic, mirrors account.PaperAccountStore), time-stop at bar close.
One position at a time (single-ticket desk discipline), 1% equity risk
per trade, explicit per-unit costs, optional seeded adverse slippage.

Determinism: no wall-clock anywhere in the output. The seed pins the
slippage RNG (and is echoed in the result). The equity journal is JSONL
(bar-by-bar equity) and hashed — `equity_curve_sha256` — so the test
suite pins same-input → byte-identical output.

Metrics: total return, Sharpe (rf=0.05, daily aggregation, 252), Sortino,
max drawdown (positive magnitude), Calmar, hit-rate, profit factor, trade
count, avg win/loss, plus the buy-and-hold comparison over the same bars.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..clock import iso
from ..data.model import Bar
from ..setup.engine import SetupEngine
from ..setup.spec import SetupSpec
from .metrics import calmar_ratio, max_drawdown, sharpe_ratio, sortino_ratio

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/"
HTTP_TIMEOUT = 10.0
BT_BARS_TTL_S = 6 * 3600          # historical bars move slowly
RANGE_KEYS = ("1mo", "3mo", "6mo", "1y", "2y")

DEFAULT_SYMBOL = "GC=F"
DEFAULT_RANGE = "1y"
DEFAULT_SEED = 7
DEFAULT_STARTING_EQUITY = 100_000.0
DEFAULT_RISK_PCT = 0.01           # 1% of equity risked per trade
DEFAULT_COST_PER_UNIT = 0.10      # round-trip cost per unit (price units)
DEFAULT_WINDOW = 40               # trailing bars handed to SetupEngine


def _http_get(url: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _cache_path(data_root: str | Path, name: str) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


# Test seam — set to a canned Yahoo chart JSON body (string) to pin the
# parser path without any network. Never set in production code paths.
_TEST_BARS: str | None = None


def _parse_hourly_chart(symbol: str, body: dict) -> list[Bar]:
    """Yahoo v8/chart result[0] → list[Bar] (closed 1h bars, UTC).

    Null OHLC rows are skipped; the final row is dropped when its close
    time is still in the future (the forming bar must never reach the
    engine — same firewall as indicators.assert_closed).
    """
    results = (body.get("chart") or {}).get("result") or []
    if not results or not results[0]:
        raise RuntimeError(f"no chart result for {symbol}")
    r = results[0]
    ts_arr = r.get("timestamp") or []
    q = ((r.get("indicators") or {}).get("quote") or [{}])[0] or {}
    opens = q.get("open") or []
    highs = q.get("high") or []
    lows = q.get("low") or []
    closes = q.get("close") or []
    vols = q.get("volume") or []

    def _at(arr, idx):
        return arr[idx] if idx < len(arr) else None

    bars: list[Bar] = []
    now_epoch = time.time()
    for i, t in enumerate(ts_arr):
        o, h, l, c = _at(opens, i), _at(highs, i), _at(lows, i), _at(closes, i)
        v = _at(vols, i)
        if None in (o, h, l, c):
            continue
        open_dt = datetime.fromtimestamp(int(t), tz=timezone.utc)
        close_dt = open_dt + timedelta(hours=1)
        if close_dt.timestamp() > now_epoch:
            continue                          # forming bar firewall
        bars.append(Bar(
            ts_open=iso(open_dt), ts_close=iso(close_dt),
            open=float(o), high=float(h), low=float(l), close=float(c),
            volume=float(v or 0.0),
        ))
    if not bars:
        raise RuntimeError(f"no closed hourly bars for {symbol}")
    bars.sort(key=lambda b: b.ts_close)
    return bars


def fetch_hourly_bars(symbol: str = DEFAULT_SYMBOL,
                      range_key: str = DEFAULT_RANGE,
                      data_root: str | Path = "data",
                      timeout: float = HTTP_TIMEOUT) -> list[Bar]:
    """1y (or other supported range) of hourly bars for `symbol`,
    fail-soft with a 6h TTL file cache. Raises on transport/parse failure
    when no cache exists (the CLI surfaces the error)."""
    sym = str(symbol or DEFAULT_SYMBOL).strip() or DEFAULT_SYMBOL
    rk = range_key if range_key in RANGE_KEYS else DEFAULT_RANGE
    url = (f"{YAHOO_CHART_URL}{urllib.parse.quote(sym, safe='')}"
           f"?interval=1h&range={rk}")
    slug = urllib.parse.quote(f"{sym}_{rk}", safe="")

    def _fetch() -> dict:
        raw = _TEST_BARS if _TEST_BARS is not None else _http_get(url, timeout)
        bars = _parse_hourly_chart(sym, json.loads(raw))
        return {"ok": True, "symbol": sym, "range": rk, "interval": "1h",
                "n_bars": len(bars),
                "bars": [b.canonical() for b in bars]}

    path = _cache_path(data_root, f"bt_hourly_{slug}")
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < BT_BARS_TTL_S:
        cached["cache_hit"] = True
    else:
        try:
            fresh = _fetch()
            fresh["fetched_at"] = time.time()
            fresh["cache_hit"] = False
            path.write_text(json.dumps(fresh))
            cached = fresh
        except Exception as e:  # noqa: BLE001 — stale-serve on failure
            if not cached:
                raise
            cached["cache_hit"] = True
            cached["stale_error"] = f"{type(e).__name__}"

    out: list[Bar] = []
    for canon in cached.get("bars") or []:
        ts_open, ts_close, o, h, l, c, v = canon.split("|")
        out.append(Bar(ts_open=ts_open, ts_close=ts_close,
                       open=float(o), high=float(h), low=float(l),
                       close=float(c), volume=float(v)))
    return out


# ------------------------------------------------------------------ engine
class BacktestEngine:
    """Mechanical GUESS-setup backtest over a closed-bar series.

    Parameters
    ----------
    bars          : list[Bar] — closed H1 bars (any symbol; the setup rule
                    is UTC-hour based).
    spec          : SetupSpec (default: the desk's GUESS spec verbatim).
    seed          : pins the slippage RNG (and is echoed in the result).
    risk_pct      : fraction of current equity risked per trade
                    (position units = equity·risk_pct / stop_distance).
    cost_per_unit : round-trip cost per unit in price units, charged on
                    exit.
    slippage_atr_mult : adverse entry/exit slippage drawn per fill as
                    |N(0, mult·ATR14)| from the seeded RNG. 0.0 (default)
                    = pure mechanical fills; any value > 0 makes the seed
                    load-bearing.
    window        : trailing bar count handed to SetupEngine.evaluate.
    """

    def __init__(self, bars: list[Bar], spec: SetupSpec | None = None,
                 seed: int = DEFAULT_SEED,
                 starting_equity: float = DEFAULT_STARTING_EQUITY,
                 risk_pct: float = DEFAULT_RISK_PCT,
                 cost_per_unit: float = DEFAULT_COST_PER_UNIT,
                 slippage_atr_mult: float = 0.0,
                 window: int = DEFAULT_WINDOW):
        self.bars = sorted(bars, key=lambda b: b.ts_close)
        self.spec = spec or SetupSpec()
        self.seed = int(seed)
        self.starting_equity = float(starting_equity)
        self.risk_pct = float(risk_pct)
        self.cost_per_unit = float(cost_per_unit)
        self.slippage_atr_mult = float(slippage_atr_mult)
        self.window = int(window)

    # ------------------------------------------------------------ internals
    def _slip(self, rng: random.Random, atr14: float) -> float:
        if self.slippage_atr_mult <= 0 or atr14 <= 0:
            return 0.0
        return abs(rng.gauss(0.0, self.slippage_atr_mult * atr14))

    def _mark_equity(self, cash: float, pos: dict | None,
                     close: float) -> float:
        if pos is None:
            return cash
        direction = 1 if pos["side"] == "buy" else -1
        floating = direction * (close - pos["entry"]) * pos["units"]
        return cash + floating

    # ------------------------------------------------------------ run
    def run(self, journal_path: str | Path | None = None) -> dict:
        bars = self.bars
        engine = SetupEngine(self.spec)     # fresh per run (one-candidate/day state)
        rng = random.Random(self.seed)
        cash = self.starting_equity
        pos: dict | None = None
        curve: list[float] = []
        journal_lines: list[str] = []
        trades: list[dict] = []
        warmup = max(self.window, 16)

        def _journal(ts: str, equity: float, event: str, extra: dict | None = None):
            line = {"ts": ts, "equity": round(equity, 2), "event": event}
            if extra:
                line.update(extra)
            journal_lines.append(json.dumps(line, sort_keys=True))

        for i, bar in enumerate(bars):
            # ---- 1. resolve an open position on this bar (opened earlier)
            if pos is not None:
                exit_price: float | None = None
                reason = ""
                if pos["side"] == "buy":
                    hit_stop = bar.low <= pos["stop"]
                    hit_target = bar.high >= pos["target"]
                else:
                    hit_stop = bar.high >= pos["stop"]
                    hit_target = bar.low <= pos["target"]
                # pessimistic ordering: stop before target in the same bar
                if hit_stop:
                    exit_price, reason = pos["stop"], "stop"
                elif hit_target:
                    exit_price, reason = pos["target"], "target"
                elif bar.ts_close >= pos["time_stop_ts"]:
                    exit_price, reason = bar.close, "time_stop"
                if exit_price is not None:
                    exit_price += self._slip(rng, pos["atr14"]) * \
                        (1 if pos["side"] == "sell" else -1)
                    direction = 1 if pos["side"] == "buy" else -1
                    pnl = (direction * (exit_price - pos["entry"])
                           * pos["units"]
                           - pos["units"] * self.cost_per_unit)
                    cash += pnl
                    trades.append({
                        "side": pos["side"],
                        "entry_ts": pos["entry_ts"],
                        "exit_ts": bar.ts_close,
                        "entry": round(pos["entry"], 2),
                        "exit": round(exit_price, 2),
                        "stop": round(pos["stop"], 2),
                        "target": round(pos["target"], 2),
                        "units": round(pos["units"], 4),
                        "pnl": round(pnl, 2),
                        "reason": reason,
                        "bars_held": i - pos["entry_index"],
                    })
                    _journal(bar.ts_close, cash, "exit",
                             {"pnl": round(pnl, 2), "reason": reason})
                    pos = None

            # ---- 2. ask the live setup rule for a candidate at this close
            if pos is None and i >= warmup:
                window_bars = bars[max(0, i - self.window + 1): i + 1]
                cand = engine.evaluate(window_bars, bar.close_dt)
                if cand is not None and cand.stop_distance > 0:
                    atr14 = float((cand.features_used or {}).get("atr14", 0.0))
                    direction = 1 if cand.side == "buy" else -1
                    slip = self._slip(rng, atr14)
                    entry = cand.entry + direction * slip
                    units = (self._mark_equity(cash, None, bar.close)
                             * self.risk_pct) / cand.stop_distance
                    pos = {
                        "side": cand.side, "entry": entry,
                        "stop": cand.stop, "target": cand.target,
                        "units": units, "entry_ts": bar.ts_close,
                        "entry_index": i,
                        "time_stop_ts": cand.time_stop_ts,
                        "atr14": atr14,
                    }
                    _journal(bar.ts_close,
                             self._mark_equity(cash, pos, bar.close),
                             "entry", {"side": cand.side,
                                       "entry": round(entry, 2)})

            # ---- 3. mark equity at this bar's close
            equity = self._mark_equity(cash, pos, bar.close)
            curve.append(round(equity, 2))
            _journal(bar.ts_close, equity, "bar")

        # ---- force-close anything still open at the end of the data
        if pos is not None:
            last = bars[-1]
            direction = 1 if pos["side"] == "buy" else -1
            pnl = (direction * (last.close - pos["entry"]) * pos["units"]
                   - pos["units"] * self.cost_per_unit)
            cash += pnl
            trades.append({
                "side": pos["side"], "entry_ts": pos["entry_ts"],
                "exit_ts": last.ts_close,
                "entry": round(pos["entry"], 2), "exit": round(last.close, 2),
                "stop": round(pos["stop"], 2),
                "target": round(pos["target"], 2),
                "units": round(pos["units"], 4), "pnl": round(pnl, 2),
                "reason": "end_of_data",
                "bars_held": len(bars) - 1 - pos["entry_index"],
            })
            curve[-1] = round(cash, 2)
            _journal(last.ts_close, cash, "exit",
                     {"pnl": round(pnl, 2), "reason": "end_of_data"})

        # ---- metrics
        journal_text = "\n".join(journal_lines) + "\n"
        journal_sha = hashlib.sha256(journal_text.encode("utf-8")).hexdigest()

        daily_equity: list[float] = []
        day_key = None
        for bar, eq in zip(bars, curve):
            d = bar.ts_close[:10]
            if d != day_key:
                daily_equity.append(eq)
                day_key = d
            else:
                daily_equity[-1] = eq
        daily_rets: list[float] = []
        prev = self.starting_equity
        for eq in daily_equity:
            if prev > 0:
                daily_rets.append(eq / prev - 1.0)
            prev = eq

        equity_end = curve[-1] if curve else self.starting_equity
        total_return = equity_end / self.starting_equity - 1.0
        mdd = max_drawdown(curve)
        wins = [t["pnl"] for t in trades if t["pnl"] > 0]
        losses = [t["pnl"] for t in trades if t["pnl"] <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        bh_return = (bars[-1].close / bars[0].close - 1.0) if len(bars) >= 2 else None

        result = {
            "ok": True,
            "setup_id": self.spec.setup_id,
            "setup_version": self.spec.setup_version,
            "n_bars": len(bars),
            "first_bar": bars[0].ts_close if bars else None,
            "last_bar": bars[-1].ts_close if bars else None,
            "seed": self.seed,
            "config": {
                "starting_equity": self.starting_equity,
                "risk_pct": self.risk_pct,
                "cost_per_unit": self.cost_per_unit,
                "slippage_atr_mult": self.slippage_atr_mult,
                "window": self.window,
                "spec_hash": self.spec.hash(),
            },
            "equity_start": self.starting_equity,
            "equity_end": round(equity_end, 2),
            "total_return": round(total_return, 6),
            "sharpe": sharpe_ratio(daily_rets),
            "sortino": sortino_ratio(daily_rets),
            "max_drawdown": mdd,
            "calmar": calmar_ratio(total_return, mdd, len(daily_equity)),
            "buy_hold_return": round(bh_return, 6) if bh_return is not None else None,
            "n_trades": len(trades),
            "n_wins": len(wins),
            "n_losses": len(losses),
            "hit_rate": round(len(wins) / len(trades), 4) if trades else None,
            "profit_factor": (round(gross_win / gross_loss, 4)
                              if gross_loss > 0 else (None if not wins else float("inf"))),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "n_days": len(daily_equity),
            "equity_curve": curve,
            "trades": trades,
            "journal": journal_text,
            "equity_curve_sha256": journal_sha,
        }

        if journal_path is not None:
            p = Path(journal_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(journal_text)
        return result
