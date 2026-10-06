"""GAUNTLET 7 Piece 3 — the screener: technical + fundamental filters
composed over the desk's own data plane.

Universe: the 67-symbol registry (default) or the S&P 500 (Wikipedia
constituents, aggressively cached, optional). Fields:

  technical (from board.fetch_daily_bars + features.quant):
    price, rsi, sma20, sma50, sma200, atr_pct, change_30d, realized_vol,
    above_sma200 (bool via price > sma200)
  fundamental (from markets.institutional.fetch_fundamentals, SEC XBRL
    PIT / Yahoo fallback; None for non-filers — honest N/A):
    revenue_growth   (latest quarter YoY, %)
    net_income_margin (latest quarter, %)
    fcf_margin        (latest quarter, %)
    quarters          (n_quarters the fundamental feed carries)

Filter DSL — `--where "rsi < 30 and revenue_growth > 10"`:
  parsed with Python's ast module under a strict whitelist (Compare,
  BoolOp(And/Or), UnaryOp(Not), Name, Constant); calls, attributes,
  subscripts, lambdas and everything else are rejected before any
  evaluation. Unknown field names evaluate to a failing row (and are
  reported), never an exception.

Law boundary: read-only observation layer — never wired into the live
decision loop.
"""
from __future__ import annotations

import ast
import concurrent.futures as ex
from pathlib import Path

from ..features.quant import compute_indicators
from .board import fetch_daily_bars
from .institutional import fetch_fundamentals
from .registry import UNIVERSE, all_symbols, normalize

TECH_FIELDS = [
    "price", "rsi", "sma20", "sma50", "sma200", "atr_pct", "change_30d",
    "realized_vol", "above_sma200",
]
FUND_FIELDS = [
    "revenue_growth", "net_income_margin", "fcf_margin", "quarters",
]
ALL_FIELDS = TECH_FIELDS + FUND_FIELDS


# ------------------------------------------------------------ the DSL
class _SafeEval(ast.NodeVisitor):
    """Evaluates a whitelisted boolean AST against a field dict.

    Missing/None fields make every comparison False (the row simply
    doesn't qualify) — never an exception, never a fabricated value.
    """

    def __init__(self, fields: dict):
        self.fields = fields
        self.missing: set[str] = set()

    # pylint: disable=invalid-name
    def visit_Expression(self, node: ast.Expression):
        return self.visit(node.body)

    def visit_BoolOp(self, node: ast.BoolOp):
        vals = [bool(self.visit(v)) for v in node.values]
        if isinstance(node.op, ast.And):
            return all(vals)
        return any(vals)

    def visit_UnaryOp(self, node: ast.UnaryOp):
        if isinstance(node.op, ast.Not):
            return not bool(self.visit(node.operand))
        if isinstance(node.op, ast.USub) and \
                isinstance(node.operand, ast.Constant) and \
                isinstance(node.operand.value, (int, float)):
            return -float(node.operand.value)
        raise ValueError("only `not` and negative numbers are allowed")

    def visit_Compare(self, node: ast.Compare):
        left = self._value(node.left)
        for op, comparator in zip(node.ops, node.comparators):
            right = self._value(comparator)
            if left is None or right is None:
                return False
            if isinstance(op, ast.Gt) and not left > right:
                return False
            if isinstance(op, ast.GtE) and not left >= right:
                return False
            if isinstance(op, ast.Lt) and not left < right:
                return False
            if isinstance(op, ast.LtE) and not left <= right:
                return False
            if isinstance(op, ast.Eq) and not left == right:
                return False
            if isinstance(op, ast.NotEq) and not left != right:
                return False
            if not isinstance(op, (ast.Gt, ast.GtE, ast.Lt, ast.LtE,
                                   ast.Eq, ast.NotEq)):
                raise ValueError(f"operator not allowed: {type(op).__name__}")
            left = right
        return True

    def visit_Name(self, node: ast.Name):
        if node.id not in ALL_FIELDS:
            raise ValueError(f"unknown field: {node.id}")
        v = self.fields.get(node.id)
        if v is None:
            self.missing.add(node.id)
            return False
        return bool(v)

    def visit_Constant(self, node: ast.Constant):
        if not isinstance(node.value, (int, float, bool)):
            raise ValueError("only numeric literals are allowed")
        return bool(node.value)

    def _value(self, node: ast.AST):
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) \
                and isinstance(node.operand, ast.Constant):
            return -float(node.operand.value)
        if isinstance(node, ast.Constant):
            if not isinstance(node.value, (int, float, bool)):
                raise ValueError("only numeric literals are allowed")
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in ALL_FIELDS:
                raise ValueError(f"unknown field: {node.id}")
            v = self.fields.get(node.id)
            if v is None:
                self.missing.add(node.id)
            return v
        raise ValueError(f"expression element not allowed: "
                         f"{type(node).__name__}")

    def generic_visit(self, node: ast.AST):
        raise ValueError(f"expression element not allowed: "
                         f"{type(node).__name__}")


def parse_where(where: str) -> ast.Expression:
    """Parse + validate the filter expression (raises ValueError on any
    non-whitelisted construct — this is a security boundary, not style)."""
    try:
        tree = ast.parse(where or "True", mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"filter syntax error: {exc}") from exc
    # structural audit BEFORE evaluation: reject everything not whitelisted
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BoolOp, ast.UnaryOp,
                             ast.Compare, ast.Constant, ast.Name,
                             ast.And, ast.Or, ast.Not, ast.USub, ast.Gt,
                             ast.GtE, ast.Lt, ast.LtE, ast.Eq, ast.NotEq,
                             ast.Load)):
            continue
        if isinstance(node, ast.expr_context):
            continue
        raise ValueError(f"filter element not allowed: {type(node).__name__}")
    # field-name validation at parse time (fail fast on typos AND on
    # names that would only fail per-row at evaluation)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id not in ALL_FIELDS:
            raise ValueError(f"unknown field: {node.id}")
    return tree


def evaluate_where(where: str, fields: dict) -> tuple[bool, set[str]]:
    """(passed, missing_fields) — errors propagate as ValueError."""
    tree = parse_where(where)
    ev = _SafeEval(fields)
    result = bool(ev.visit(tree))
    return result, ev.missing


# ------------------------------------------------------------ universe
def _sp500_symbols_raw(data_root, min_count: int = 400) -> list[str]:
    """S&P 500 constituents from Wikipedia (aggressively cached 7d)."""
    import json
    import re
    import time
    import urllib.request
    d = Path(data_root)
    (d / "cache").mkdir(parents=True, exist_ok=True)
    cache = d / "cache" / "sp500_constituents.json"
    if cache.exists():
        try:
            blob = json.loads(cache.read_text())
            if time.time() - blob.get("fetched_at", 0) < 7 * 86400:
                return blob["symbols"]
        except (json.JSONDecodeError, OSError, KeyError):
            pass
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) gold-desk/1.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", "replace")
    # first table row cells: ticker in the first column link
    symbols = sorted(set(re.findall(
        r"<td><a[^>]*title=\"([A-Z.]+)\"[^>]*>.{0,40}?</a></td>", html)))
    if len(symbols) < min_count:   # sanity: the real table has ~503 rows
        raise RuntimeError(f"wikipedia parse yielded {len(symbols)} symbols")
    cache.write_text(json.dumps(
        {"fetched_at": time.time(), "symbols": symbols}))
    return symbols


def _sp500_symbols(data_root) -> list[str]:
    return _sp500_symbols_raw(data_root, min_count=400)


# ---------------------------------------------------------------- rows
def _technical_fields(symbol: str, data_root) -> dict:
    bars = fetch_daily_bars(symbol, "1y", data_root)
    if not bars:
        return {"_error": "no daily bars"}
    ind = compute_indicators(bars)
    closes = [b["c"] for b in bars if b.get("c")]
    price = closes[-1] if closes else None
    chg_30d = None
    if len(closes) >= 31 and closes[-31]:
        chg_30d = round((closes[-1] / closes[-31] - 1.0) * 100.0, 4)
    sma = ind.get("sma") or {}
    return {
        "price": price,
        "rsi": ind.get("rsi14"),
        "sma20": sma.get("20"),
        "sma50": sma.get("50"),
        "sma200": sma.get("200"),
        "atr_pct": ind.get("atr_pct"),
        "change_30d": chg_30d,
        "realized_vol": ind.get("realized_vol_20d"),
        "above_sma200": bool(price and sma.get("200")
                             and price > sma["200"]),
    }


def _fundamental_fields(symbol: str, data_root) -> dict:
    f = fetch_fundamentals(symbol, data_root)
    if not f.get("ok") or not f.get("periods"):
        return {"revenue_growth": None, "net_income_margin": None,
                "fcf_margin": None, "quarters": 0}
    periods = f["periods"]
    latest = periods[0]
    # YoY revenue growth: same fiscal quarter one year back
    rev_now, rev_prev = latest.get("revenue"), None
    for p in periods[1:]:
        if p.get("fp") == latest.get("fp") and \
                int(p.get("fy") or 0) == int(latest.get("fy") or 0) - 1:
            rev_prev = p.get("revenue")
            break
    growth = None
    if rev_now and rev_prev and rev_prev > 0:
        growth = round((rev_now / rev_prev - 1.0) * 100.0, 4)
    ni = latest.get("net_income")
    margin = round(ni / rev_now * 100.0, 4) if ni is not None and rev_now \
        else None
    fcf = latest.get("fcf_derived") or latest.get("free_cash_flow")
    fcf_m = round(fcf / rev_now * 100.0, 4) if fcf is not None and rev_now \
        else None
    return {"revenue_growth": growth, "net_income_margin": margin,
            "fcf_margin": fcf_m, "quarters": f.get("n_quarters", 0)}


def screen(where: str, universe: str = "registry",
           data_root=None, with_fundamentals: bool = True,
           limit: int = 100, workers: int = 8) -> dict:
    """Run the screen. Fail-soft per symbol; errors land in the row."""
    dr = data_root or "data"
    if universe == "sp500":
        symbols = _sp500_symbols(dr)
    elif universe == "universe":
        symbols = [u["symbol"] for u in UNIVERSE]
    else:
        symbols = [u["symbol"] for u in all_symbols()]   # the full 67
    parse_where(where)          # fail fast on a bad filter

    def _row(sym: str) -> dict:
        fields: dict = {}
        errs: list[str] = []
        try:
            fields.update(_technical_fields(sym, dr))
        except Exception as exc:  # noqa: BLE001 — fail-soft per symbol
            errs.append(f"technical: {type(exc).__name__}")
        if with_fundamentals:
            try:
                fields.update(_fundamental_fields(sym, dr))
            except Exception as exc:  # noqa: BLE001
                errs.append(f"fundamentals: {type(exc).__name__}")
        try:
            passed, missing = evaluate_where(where, fields)
        except ValueError as exc:
            return {"symbol": sym, "passed": False, "error": str(exc),
                    "fields": fields}
        return {"symbol": sym, "passed": passed, "fields": fields,
                "missing_fields": sorted(missing), "errors": errs}

    rows: list[dict] = []
    with ex.ThreadPoolExecutor(max_workers=workers) as pool:
        for row in pool.map(_row, symbols):
            rows.append(row)

    matched = [r for r in rows if r.get("passed")]
    matched.sort(key=lambda r: str(r["symbol"]))
    errors = [r for r in rows if r.get("error") or r.get("errors")]
    return {
        "ok": True,
        "universe": universe,
        "n_symbols": len(symbols),
        "n_matched": len(matched),
        "where": where,
        "rows": matched[:limit],
        "n_errors": len(errors),
        "errors_sample": [
            {"symbol": r["symbol"],
             "error": r.get("error") or (r.get("errors") or ["?"])[0]}
            for r in errors[:8]],
    }
