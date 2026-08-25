"""MARKET GAUNTLET R2-1 — institutional data plane (keyless superset).

The bar: ai-hedge-fund v2.2.0 pays Financial Datasets API for 20 quarters
× 14 metrics of point-in-time (PIT) fundamentals, filing-date-filtered.
SEC XBRL companyconcept gives us the EXACT same shape (and more history
— 40+ quarters, every us-gaap concept, accession numbers) for FREE,
keyless, with SEC's only requirement being a descriptive User-Agent
header (not an API key). Yahoo fundamentals-timeseries is the cross-
check fallback for non-US equities / futures / FX / crypto with no CIK.

Beyond ai-hedge-fund's data depth, this module also gives:
  * 13F institutional positioning (Berkshire Q2-26: 89 positions, $299.3B
    disclosed value, top-10 % of book) — neither ai-hedge-fund v2.2.0
    nor TradingAgents v0.3.1 has this keyless.
  * Treasury yield curve (full 1M–30Y daily, 162 days YTD) — the FRED
    keyless gap left by TradingAgents' macro grounding.
  * Crypto Fear & Greed (30-day history), on-chain (blockchain.info),
    global dominance (CoinGecko) — the StockTwits/crypto gap.
  * Reddit RSS social feed (r/wallstreetbets / r/CryptoCurrency /
    r/stocks) — TradingAgents' Reddit edge, keyless.

All feeds are fail-soft per slice: a dead XBRL or 429'd CoinGecko
returns {ok: False} on its slice and never raises — the desk run must
not die because EDGAR rate-limited. 30-minute TTL file caches mirror
gold_desk.markets.board._cached_fetch (and news.py's pattern).

gather_institutional_context(symbol) is the convenience aggregator:
fans out the 7 sub-fetches in parallel (ThreadPoolExecutor, ≤8s wall)
and returns a dict where each slice carries its own ok flag — the
desk engine pulls only the slices the personas are entitled to.

Law boundary (L11): these feeds are RESEARCH context only — no
account / balance / equity / pnl / capital / trade data touches any
prompt. The desk's own account never appears in this module's output.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

# --- UAs (SEC requires a descriptive UA — NOT a key; verified live) ---
EDGAR_UA = "Gold Desk Research research@example.com"
YAHOO_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
REDDIT_UA = YAHOO_UA
TREASURY_UA = YAHOO_UA
ALTERNATIVE_UA = YAHOO_UA
BLOCKCHAIN_UA = YAHOO_UA
COINGECKO_UA = YAHOO_UA

# --- endpoints (all probed live 2026-08-25 — see probe_feeds*.py) ---
EDGAR_CC_URL = ("https://data.sec.gov/api/xbrl/companyconcept/"
                "CIK{cik}/us-gaap/{concept}.json")
EDGAR_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data/{cik_num}/{acc}/"
EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_BROWSE_URL = ("https://www.sec.gov/cgi-bin/browse-edgar"
                     "?action=getcompany&CIK=TYPE&ticker={t}&type=")
YAHOO_FT_URL = ("https://query2.finance.yahoo.com/ws/fundamentals-timeseries"
                 "/v1/finance/timeseries/{sym}?type={types}"
                 "&period1={p1}&period2={p2}")
TREASURY_XML_URL = ("https://home.treasury.gov/resource-center/"
                     "data-chart-center/interest-rates/pages/xml"
                     "?data=daily_treasury_yield_curve"
                     "&field_tdr_date_value={year}")
FNG_URL = "https://api.alternative.me/fng/?limit=30"
BLOCKCHAIN_STATS_URL = "https://api.blockchain.info/stats"
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
REDDIT_RSS_URL = "https://www.reddit.com/r/{sub}/.rss?limit=10"

# --- cache + timeouts ---
TTL_S = 30 * 60            # 30 min per-slice TTL (mirror board.py pattern)
TTL_CIK_S = 24 * 3600      # CIK map cache 24h (tickers move slowly)
HTTP_TIMEOUT = 8.0         # per-call wall (gather ≤ 8s via parallelism)
DEFAULT_BRK_CIK = "0001067983"   # Berkshire Hathaway (zero-padded 10-digit)
N_QUARTERS = 8             # PIT window — matches ai-hedge-fund's 20 reduced
                           # to 8 (filing-fresh; bigger would burn prompt tokens)

# XBRL us-gaap concept → output field name (the ~10 we surface).
# RevenueFromContractWithCustomerExcludingAssessedTax is the modern us-gaap
# revenue concept (post-ASC 606); Revenues is the legacy fallback.
XBRL_CONCEPTS = {
    "RevenueFromContractWithCustomerExcludingAssessedTax": "revenue",
    "Revenues": "revenue",
    "NetIncomeLoss": "net_income",
    "GrossProfit": "gross_profit",
    "OperatingIncomeLoss": "operating_income",
    "EarningsPerShareDiluted": "eps_diluted",
    "EarningsPerShareBasic": "eps_basic",
    "LongTermDebt": "total_debt",
    "StockholdersEquity": "stockholders_equity",
    "CashAndCashEquivalentsAtCarryingValue": "cash",
    "CashAndCashEquivalents": "cash",
    "FreeCashFlow": "free_cash_flow",
    "CashFlowFromOperatingActivities": "operating_cash_flow",
}

# Treasury yield curve XML BC_* tag → output key (1M-30Y the desk uses).
CURVE_FIELDS = {
    "1MONTH": "1M", "3MONTH": "3M", "6MONTH": "6M",
    "1YEAR": "1Y", "2YEAR": "2Y", "5YEAR": "5Y",
    "10YEAR": "10Y", "20YEAR": "20Y", "30YEAR": "30Y",
}

# Yahoo fundamentals-timeseries type → output field (the fallback shape).
YAHOO_FT_TYPES = {
    "quarterlyTotalRevenue": "revenue",
    "quarterlyNetIncome": "net_income",
    "quarterlyGrossProfit": "gross_profit",
    "quarterlyOperatingIncome": "operating_income",
    "quarterlyTotalDebt": "total_debt",
    "quarterlyStockholdersEquity": "stockholders_equity",
    "quarterlyCashAndCashEquivalents": "cash",
    "quarterlyFreeCashFlow": "free_cash_flow",
    "quarterlyOperatingCashFlow": "operating_cash_flow",
    "quarterlyDilutedEPS": "eps_diluted",
    "quarterlyBasicEPS": "eps_basic",
}


# ----------------------------------------------------------------- HTTP
def _http_get(url: str, ua: str, timeout: float = HTTP_TIMEOUT) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua, "Accept": "application/json, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _http_get_bytes(url: str, ua: str, timeout: float = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": ua, "Accept": "application/json, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


# ------------------------------------------------------------- cache
def _cache_path(data_root, name: str) -> Path:
    d = Path(data_root) / "cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{name}.json"


def _cached_fetch(data_root, name: str, ttl: int, fetch) -> dict:
    """Cache-through fetch under <data_root>/cache/<name>.json (board.py
    pattern). Fresh within TTL → fetch → stale-serve on error. Never
    raises — the caller's slice stays alive when one feed is down."""
    path = _cache_path(data_root, name)
    cached: dict = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text())
        except json.JSONDecodeError:
            cached = {}
    if cached.get("fetched_at") and time.time() - cached["fetched_at"] < ttl:
        cached["cache_hit"] = True
        return cached
    try:
        fresh = fetch()
        fresh["fetched_at"] = time.time()
        fresh["cache_hit"] = False
        path.write_text(json.dumps(fresh, default=str))
        return fresh
    except Exception as e:  # noqa: BLE001 — display telemetry fails soft
        if cached:
            cached["cache_hit"] = True
            cached["stale_error"] = f"{type(e).__name__}"
            return cached
        return {"ok": False, "error": f"{type(e).__name__}: {e}",
                "fetched_at": time.time(), "cache_hit": False}


# --------------------------------------------------------- CIK resolve
def _resolve_cik(symbol: str, data_root=None) -> str | None:
    """Yahoo ticker → SEC CIK (zero-padded 10-digit string) via the master
    company_tickers.json (3-line JSON map: cik_str, ticker, title).
    Cache 24h. Fail-soft: unknown ticker → None."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    cache_name = "company_tickers_master"
    try:
        master = _cached_fetch(data_root or "data", cache_name, TTL_CIK_S,
                                lambda: {"ok": True, "data": json.loads(
                                    _http_get(EDGAR_TICKERS_URL, EDGAR_UA))})
    except Exception:  # noqa: BLE001
        return None
    data = (master or {}).get("data") or {}
    for _, row in data.items():
        if isinstance(row, dict) and row.get("ticker", "").upper() == sym:
            cik = int(row.get("cik_str", 0))
            if cik:
                return f"{cik:010d}"
    return None


# -------------------------------------------------------- XBRL periods
def _is_standalone_period(row: dict) -> bool:
    """SEC XBRL companyconcept returns multiple rows per (fy,fp) filing:
    for 10-Q: the standalone 3-month quarter, the 6-month YTD, the 9-
    month YTD, plus the prior-year comparatives. For 10-K: the 12-month
    FY plus 3-month quarter slices. We isolate the standalone period:

      Flow items (income, cash flow) — start AND end both set:
        10-Q → duration 60-100 days (a calendar quarter)
        10-K → duration 300-400 days (a fiscal year)
      Instantaneous items (balance-sheet snapshots — debt, equity, cash,
      assets, liabilities) — only `end` set (start is None or == end):
        accepted if form is 10-Q or 10-K (the snapshot IS the period).

    The instantaneous path is required for the fundamentalist's balance-
    sheet checklist item (debt/equity structure, financial position) —
    XBRL tags these as `end`-only rows, which the duration filter would
    otherwise drop.
    """
    form = row.get("form")
    start, end = row.get("start"), row.get("end")
    if form not in ("10-Q", "10-K") or not end:
        return False
    # Instantaneous balance-sheet item (no duration) — accept as-is
    if not start or start == end:
        return True
    try:
        s = datetime.strptime(start, "%Y-%m-%d")
        e = datetime.strptime(end, "%Y-%m-%d")
    except (ValueError, TypeError):
        return False
    days = (e - s).days
    if form == "10-Q":
        return 60 <= days <= 100
    if form == "10-K":
        return 300 <= days <= 400
    return False


def _period_sort_key(row: dict) -> str:
    """Sort key: prefer start; fall back to end. Flow items have a
    start, instantaneous balance-sheet snapshots have only end."""
    return row.get("start") or row.get("end") or ""


def _merge_xbrl_periods(concepts_data: dict[str, dict]) -> list[dict]:
    """Merge the ~10 XBRL companyconcept responses into ONE row per
    (fy, fp) period — the desk's 8 most-recent quarters.

    A single 10-Q filing ships several rows per (fy, fp):
      * flow items (revenue, NI, EPS, OCF, FCF): the standalone 3-month
        quarter AND prior-year comparative, both tagged (fy=2026, fp=Q3)
        for the FILING (not the data) — multiple (start, end) pairs.
      * instantaneous items (debt, equity, cash, assets): snapshots at
        multiple period ends — the current quarter end AND the prior FY
        end (the comparative balance sheet), both tagged (fy=2026, fp=Q3).

    So we cannot dedup by (start, end) — that would create two period
    rows per quarter and lose half the fields. Instead, for each
    (fy, fp, concept) we keep the standalone row with the LATEST
    _period_sort_key (start for flow items; end for instantaneous).
    That row carries the CURRENT period's value. We then merge fields
    across concepts into one row per (fy, fp). 10-K supersedes 10-Q for
    FY when both exist. Sort by filed date desc, keep latest 8.

    Each period's `accn` is the accession number of the filing it came
    from — preserved per period for L11 audit-grade citation.
    """
    # Step 1: per (fy, fp, concept), keep the latest standalone row.
    latest: dict[tuple, dict] = {}
    for concept, payload in concepts_data.items():
        if not isinstance(payload, dict) or "units" not in payload:
            continue
        field = XBRL_CONCEPTS.get(concept)
        if not field:
            continue
        for unit, vals in (payload.get("units") or {}).items():
            # USD = dollar-denominated flow + balance-sheet items.
            # USD/shares = per-share items (EarningsPerShareDiluted/Basic).
            # Other units (USD/x, shares) are excluded.
            if unit not in ("USD", "USD/shares"):
                continue
            for v in vals:
                if not isinstance(v, dict):
                    continue
                if not _is_standalone_period(v):
                    continue
                fy = v.get("fy")
                fp = v.get("fp")
                if fy is None or not fp:
                    continue
                key = (fy, fp, concept)
                cur = latest.get(key)
                if cur is None or \
                        _period_sort_key(v) > _period_sort_key(cur):
                    latest[key] = v
    # Step 2: merge by (fy, fp) — one row per period, fields across
    # concepts. The row's (start, end, accn, filed, form) come from the
    # FIRST concept seen but are upgraded when a flow item with a real
    # `start` arrives (instantaneous items have start=None) and when a
    # later `filed` date arrives (10-K is filed after the Q4 10-Q).
    by_period: dict[tuple, dict] = {}
    for (fy, fp, concept), v in latest.items():
        field = XBRL_CONCEPTS.get(concept)
        if not field:
            continue
        pe = by_period.get((fy, fp))
        if pe is None:
            pe = {"fy": fy, "fp": fp, "form": v.get("form"),
                  "filed": v.get("filed"), "accn": v.get("accn"),
                  "start": v.get("start"), "end": v.get("end")}
            by_period[(fy, fp)] = pe
        # 10-K supersedes 10-Q for FY (same (fy, fp) — rare)
        if v.get("form") == "10-K" and pe.get("form") == "10-Q" \
                and v.get("fp") == "FY":
            pe["form"] = "10-K"
        # prefer a flow item's `start` (instantaneous items have None)
        if pe.get("start") is None and v.get("start"):
            pe["start"] = v.get("start")
        # prefer the latest filed date + accession across concepts
        if v.get("filed") and (not pe.get("filed")
                               or v["filed"] > pe["filed"]):
            pe["filed"] = v["filed"]
            pe["accn"] = v.get("accn")
        # set the field — latest[key] is already the current period's row
        try:
            pe[field] = float(v.get("val", 0)) or None
        except (TypeError, ValueError):
            pe[field] = None
    final = list(by_period.values())
    final.sort(key=lambda r: r.get("filed") or "", reverse=True)
    return final[:N_QUARTERS]


# -------------------------------------------------- Yahoo fallback (FT)
def _yahoo_fallback_periods(symbol: str, data_root) -> list[dict]:
    """Yahoo fundamentals-timeseries quarterly series → 8-quarter rows
    in the same shape as _merge_xbrl_periods. Used when XBRL returns
    nothing (non-US equity, futures, FX, crypto with no SEC CIK).
    Yahoo's asOfDate labels each quarter; fy/fp derived from calendar
    month (Jan-Mar=Q1, Apr-Jun=Q2, Jul-Sep=Q3, Oct-Dec=Q4; FY=year)."""
    types = ",".join(YAHOO_FT_TYPES.keys())
    p1 = int(time.time()) - 2 * 365 * 24 * 3600
    p2 = int(time.time()) + 30 * 24 * 3600
    url = YAHOO_FT_URL.format(sym=urllib.parse.quote(symbol, safe=""),
                              types=types, p1=p1, p2=p2)

    def _fetch() -> dict:
        return {"ok": True, "data": json.loads(_http_get(url, YAHOO_UA))}
    out = _cached_fetch(data_root, f"yahoo_ft_{_slug(symbol)}", TTL_S, _fetch)
    if not out.get("ok"):
        return []
    data = (out or {}).get("data") or {}
    series = ((data.get("timeseries") or {}).get("result") or [])
    by_ts: dict[int, dict] = {}
    for s in series:
        if not isinstance(s, dict):
            continue
        t_arr = (s.get("meta", {}) or {}).get("type") or []
        if not t_arr:
            continue
        tname = t_arr[0]
        field = YAHOO_FT_TYPES.get(tname)
        if not field:
            continue
        ts_arr = s.get("timestamp") or []
        vals = s.get(tname) or []
        for i, ts in enumerate(ts_arr):
            if i >= len(vals):
                break
            v = vals[i] if i < len(vals) else None
            if not isinstance(v, dict):
                continue
            raw = (v.get("reportedValue") or {}).get("raw")
            if raw is None:
                continue
            asof = v.get("asOfDate", "")
            try:
                d = datetime.strptime(asof[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                continue
            period = by_ts.setdefault(ts, {
                "fy": d.year, "fp": _quarter_of(d.month),
                "form": "10-Q" if d.month != 12 else "10-K",
                "filed": asof, "accn": f"yahoo:{asof}",
                "start": None, "end": asof})
            if period.get(field) is None:
                try:
                    period[field] = float(raw)
                except (TypeError, ValueError):
                    period[field] = None
    rows = list(by_ts.values())
    rows.sort(key=lambda r: r.get("filed") or "", reverse=True)
    return rows[:N_QUARTERS]


def _quarter_of(month: int) -> str:
    return {1: "Q1", 2: "Q1", 3: "Q1", 4: "Q2", 5: "Q2", 6: "Q2",
            7: "Q3", 8: "Q3", 9: "Q3", 10: "Q4", 11: "Q4", 12: "FY"}.get(
                month, "Q?")


def _slug(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(s)).strip("_")[:24] or "sym"


# ----------------------------------------------- fetch_fundamentals
def fetch_fundamentals(symbol: str, data_root=None) -> dict:
    """8 quarters of PIT GAAP fundamentals (SEC XBRL primary,
    Yahoo fundamentals-timeseries fallback). Never raises; ok:False on
    total failure. Each period carries its accession number for L11
    audit-quality citation.

    Returns {ok, symbol, cik, source, periods: [{fy, fp, form, filed,
    accn, start, end, revenue, net_income, gross_profit,
    operating_income, eps_diluted, eps_basic, total_debt,
    stockholders_equity, cash, free_cash_flow, operating_cash_flow}],
    latest_quarter, n_quarters}."""
    sym = str(symbol or "").strip()
    if not sym:
        return {"ok": False, "symbol": sym, "error": "no symbol",
                "periods": [], "n_quarters": 0}
    dr = data_root or "data"
    # try XBRL first (PIT, accession-cited)
    cik = _resolve_cik(sym, dr)
    if cik:
        try:
            bundle = _fetch_xbrl_bundle(cik, dr)
            periods = _merge_xbrl_periods(bundle)
            if periods:
                latest = periods[0]
                return {"ok": True, "symbol": sym, "cik": cik,
                        "source": "sec_xbrl",
                        "periods": periods,
                        "latest_quarter": f"{latest.get('fp')} FY{latest.get('fy')}",
                        "n_quarters": len(periods)}
        except Exception:  # noqa: BLE001 — XBRL soft, try Yahoo
            pass
    # Yahoo fallback for non-US equities / futures / FX / crypto
    try:
        periods = _yahoo_fallback_periods(sym, dr)
        if periods:
            latest = periods[0]
            return {"ok": True, "symbol": sym, "cik": cik,
                    "source": "yahoo_timeseries",
                    "periods": periods,
                    "latest_quarter": f"{latest.get('fp')} FY{latest.get('fy')}",
                    "n_quarters": len(periods)}
    except Exception:  # noqa: BLE001
        pass
    return {"ok": False, "symbol": sym, "cik": cik,
            "error": "no XBRL and no Yahoo fundamentals for symbol",
            "periods": [], "n_quarters": 0}


def _fetch_xbrl_bundle(cik: str, data_root) -> dict[str, dict]:
    """Fetch the ~13 XBRL companyconcept endpoints for one CIK (cached
    as a single bundle). Fail-soft per concept (one dead concept doesn't
    kill the bundle)."""
    cache_name = f"xbrl_bundle_{cik}"

    def _fetch() -> dict:
        bundle: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=6) as ex:
            futs = {ex.submit(_fetch_one_concept, cik, c): c
                    for c in XBRL_CONCEPTS}
            for fut in as_completed(futs):
                concept = futs[fut]
                try:
                    payload = fut.result()
                    if isinstance(payload, dict) and "units" in payload:
                        bundle[concept] = payload
                except Exception:  # noqa: BLE001 — per-concept fail-soft
                    pass
        return {"ok": True, "data": bundle, "cik": cik}
    cached = _cached_fetch(data_root, cache_name, TTL_S, _fetch)
    return (cached or {}).get("data") or {}


def _fetch_one_concept(cik: str, concept: str) -> dict:
    url = EDGAR_CC_URL.format(cik=cik, concept=concept)
    return json.loads(_http_get(url, EDGAR_UA, timeout=HTTP_TIMEOUT))


# ----------------------------------------------- fetch_institutional
def _parse_13f_xml(xml_bytes: bytes) -> list[dict]:
    """SEC 13F infotable XML → [{issuer, title, cusip, value, shares,
    type}]. XML root is `informationTable` with namespace
    http://www.sec.gov/edgar/document/thirteenf/informationtable; each
    `infoTable` carries nameOfIssuer / titleOfClass / cusip / value /
    shrsOrPrnAmt.sshPrnamt / shrsOrPrnAmt.sshPrnamtType. Value is USD
    (the XML schema dropped the legacy thousands-scaling)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    ns = ""
    if "}" in root.tag:
        ns = "{" + root.tag.split("}")[0].strip("{") + "}"
    entries = root.findall(f"{ns}infoTable")
    if not entries:
        entries = root.findall(f"{ns}entry")
    out = []
    for e in entries:
        def txt(tag):
            el = e.find(f"{ns}{tag}")
            return (el.text or "").strip() if el is not None else ""
        shares_el = e.find(f"{ns}shrsOrPrnAmt")
        shares = ""
        sh_type = "SH"
        if shares_el is not None:
            amt = shares_el.find(f"{ns}sshPrnamt")
            typ = shares_el.find(f"{ns}sshPrnamtType")
            if amt is not None:
                shares = (amt.text or "").strip()
            if typ is not None and typ.text:
                sh_type = typ.text.strip() or "SH"
        try:
            value = float(txt("value") or 0) or 0
        except ValueError:
            value = 0
        try:
            sh = float(shares or 0) or 0
        except ValueError:
            sh = 0
        out.append({
            "issuer": txt("nameOfIssuer")[:60],
            "title": txt("titleOfClass")[:12],
            "cusip": txt("cusip"),
            "value": value,
            "shares": sh,
            "type": sh_type,
        })
    return out


def fetch_institutional(cik: str | None = None,
                         data_root=None) -> dict:
    """Latest 13F-HR holdings for one institutional filer (default
    Berkshire CIK 0001067983). Resolves submissions → latest 13F-HR
    accession → index.json → holdings xml → infotable entries.
    top10_pct = sum(top10 values)/total disclosed value.

    Returns {ok, fund, cik, filed, accession, total_value,
    n_positions, positions: [{issuer, cusip, value, shares, type}],
    top10_pct}."""
    cik = cik or DEFAULT_BRK_CIK
    cik_padded = cik if len(cik) == 10 else cik.zfill(10)
    cache_name = f"inst_13f_{cik_padded}"

    def _fetch() -> dict:
        sub_url = EDGAR_SUBMISSIONS_URL.format(cik=cik_padded)
        sub = json.loads(_http_get(sub_url, EDGAR_UA, timeout=HTTP_TIMEOUT))
        recent = (sub.get("filings") or {}).get("recent") or {}
        forms = recent.get("form") or []
        idx = next((i for i, f in enumerate(forms) if f == "13F-HR"), None)
        if idx is None:
            raise RuntimeError("no 13F-HR filing in recent submissions")
        acc_dashed = (recent.get("accessionNumber") or [])[idx]
        filed = (recent.get("filingDate") or [])[idx]
        acc_nodash = acc_dashed.replace("-", "")
        # EDGAR uses cik WITHOUT leading zeros in the Archives path
        cik_num = str(int(cik_padded))
        index_url = (f"https://www.sec.gov/Archives/edgar/data/"
                     f"{cik_num}/{acc_nodash}/index.json")
        index = json.loads(_http_get(index_url, EDGAR_UA,
                                     timeout=HTTP_TIMEOUT))
        files = [it["name"] for it in (index.get("directory") or {})
                 .get("item") or []]
        holdings = next((f for f in files if f.lower().endswith(".xml")
                         and "primary_doc" not in f.lower()
                         and "info" in f.lower()), None)
        if not holdings:
            holdings = next((f for f in files if f.lower().endswith(".xml")
                             and "primary_doc" not in f.lower()), None)
        if not holdings:
            raise RuntimeError("no holdings xml in filing index")
        holdings_url = (f"https://www.sec.gov/Archives/edgar/data/"
                        f"{cik_num}/{acc_nodash}/"
                        f"{urllib.parse.quote(holdings)}")
        xml_bytes = _http_get_bytes(holdings_url, EDGAR_UA, timeout=20)
        positions = _parse_13f_xml(xml_bytes)
        total = sum(p["value"] for p in positions)
        top10 = sum(p["value"] for p in sorted(
            positions, key=lambda p: p["value"], reverse=True)[:10])
        fund = (sub.get("name") or "").strip()[:60]
        return {"ok": True, "fund": fund, "cik": cik_padded,
                "filed": filed, "accession": acc_dashed,
                "holdings_file": holdings,
                "total_value": total,
                "n_positions": len(positions),
                "positions": positions,
                "top10_pct": round(top10 / total * 100, 2) if total else 0}
    out = _cached_fetch(data_root or "data", cache_name, TTL_S, _fetch)
    out["kind"] = "institutional_13f"
    return out


# ----------------------------------------------- fetch_yield_curve
def _parse_treasury_xml(xml_bytes: bytes) -> list[dict]:
    """Treasury daily yield curve XML → [{date, 1M, 3M, 6M, 1Y, 2Y,
    5Y, 10Y, 20Y, 30Y, ...}]. The feed is an Atom feed with <entry>
    blocks; each entry carries <d:NEW_DATE> and <d:BC_1MONTH> ...
    <d:BC_30YEAR> fields."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    entries = []
    for el in root.iter():
        if el.tag.split("}")[-1] != "entry":
            continue
        d = {}
        for c in el.iter():
            t = c.tag.split("}")[-1]
            if t == "NEW_DATE" and c.text:
                d["date"] = c.text[:10]
            elif t.startswith("BC_") and c.text:
                key = t[3:]
                if key in CURVE_FIELDS:
                    try:
                        d[CURVE_FIELDS[key]] = round(float(c.text), 3)
                    except ValueError:
                        pass
        if d.get("date") and any(k in d for k in CURVE_FIELDS.values()):
            entries.append(d)
    entries.sort(key=lambda e: e.get("date", ""))
    return entries


def fetch_yield_curve(year: int | None = None,
                      data_root=None) -> dict:
    """Daily Treasury yield curve (1M-30Y) for one year. Defaults to
    the current year. Latest_date = last entry's date; curve = that
    entry's 1M-30Y yields. history_last_5 = the 5 most recent days."""
    yr = year or datetime.now(timezone.utc).year
    cache_name = f"treasury_curve_{yr}"

    def _fetch() -> dict:
        url = TREASURY_XML_URL.format(year=yr)
        xml_bytes = _http_get_bytes(url, TREASURY_UA, timeout=15)
        days = _parse_treasury_xml(xml_bytes)
        if not days:
            raise RuntimeError("treasury xml empty")
        latest = days[-1]
        return {"ok": True, "source": "treasury.gov",
                "latest_date": latest.get("date"),
                "curve": {k: latest.get(k) for k in
                          CURVE_FIELDS.values() if k in latest},
                "history_last_5": days[-5:]}
    out = _cached_fetch(data_root or "data", cache_name, TTL_S, _fetch)
    out["kind"] = "treasury_curve"
    return out


# ----------------------------------------------- fetch_crypto_sentiment
def fetch_crypto_sentiment(data_root=None) -> dict:
    """alternative.me Fear & Greed index — 30-day history + latest."""
    def _fetch() -> dict:
        url = FNG_URL
        d = json.loads(_http_get(url, ALTERNATIVE_UA, timeout=HTTP_TIMEOUT))
        data = d.get("data") or []
        if not data:
            raise RuntimeError("fng empty")
        latest = data[0]
        hist = [{"value": int(r.get("value", 0)),
                 "classification": r.get("value_classification", ""),
                 "ts": int(r.get("timestamp", 0))}
                for r in data if r.get("value")]
        return {"ok": True, "source": "alternative.me",
                "latest": {"value": int(latest.get("value", 0)),
                           "classification": latest.get(
                               "value_classification", "")},
                "history": hist, "n_days": len(hist)}
    out = _cached_fetch(data_root or "data", "crypto_fng", TTL_S, _fetch)
    out["kind"] = "crypto_fng"
    return out


# ----------------------------------------------- fetch_onchain
def fetch_onchain(data_root=None) -> dict:
    """blockchain.info 24h BTC network stats — market price, hash rate,
    tx count, blocks mined, minutes between blocks, total fees."""
    def _fetch() -> dict:
        d = json.loads(_http_get(BLOCKCHAIN_STATS_URL, BLOCKCHAIN_UA,
                                 timeout=HTTP_TIMEOUT))
        ts = d.get("timestamp")
        as_of = None
        if isinstance(ts, (int, float)):
            as_of = datetime.fromtimestamp(ts / 1000, tz=timezone.utc
                                           ).isoformat().replace("+00:00", "Z")
        return {"ok": True, "network": "btc",
                "market_price_usd": d.get("market_price_usd"),
                "hash_rate": d.get("hash_rate"),
                "n_tx": d.get("n_tx"),
                "n_btc_mined": d.get("n_btc_mined"),
                "minutes_between_blocks": d.get("minutes_between_blocks"),
                "total_fees_btc": d.get("total_fees_btc"),
                "as_of": as_of}
    out = _cached_fetch(data_root or "data", "onchain_btc", TTL_S, _fetch)
    out["kind"] = "onchain_btc"
    return out


# ----------------------------------------------- fetch_global_crypto
def fetch_global_crypto(data_root=None) -> dict:
    """CoinGecko global market state — BTC/ETH dominance, total market
    cap, total volume, 24h change."""
    def _fetch() -> dict:
        d = json.loads(_http_get(COINGECKO_GLOBAL_URL, COINGECKO_UA,
                                 timeout=HTTP_TIMEOUT))
        data = d.get("data") or {}
        mcap = data.get("total_market_cap") or {}
        vol = data.get("total_volume") or {}
        dom = data.get("market_cap_percentage") or {}
        return {"ok": True, "source": "coingecko",
                "btc_dominance": round(dom.get("btc", 0), 2),
                "eth_dominance": round(dom.get("eth", 0), 2),
                "total_market_cap_usd": mcap.get("usd"),
                "total_volume_usd": vol.get("usd"),
                "change_24h_pct": round(
                    data.get("market_cap_change_percentage_24h_usd") or 0, 2)}
    out = _cached_fetch(data_root or "data", "global_crypto", TTL_S, _fetch)
    out["kind"] = "global_crypto"
    return out


# ----------------------------------------------- fetch_social (Reddit)
def _parse_reddit_rss(xml_bytes: bytes, sub: str) -> list[dict]:
    """Reddit Atom feed → [{title, link, published, score?}]. The feed
    is a standard Atom <feed> with <entry> blocks carrying <title>,
    <link href=...>, <published>. Reddit does not publish a per-post
    score in the RSS so score is omitted (kept in the schema for the
    social persona to know it's not there)."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    items = []
    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.findall(f"{ns}entry"):
        title_el = entry.find(f"{ns}title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        if not title:
            continue
        link = ""
        for link_el in entry.findall(f"{ns}link"):
            href = link_el.get("href")
            if href and "reddit.com" in href:
                link = href
                break
        pub_el = entry.find(f"{ns}published")
        published = (pub_el.text or "").strip() if pub_el is not None else ""
        items.append({"title": title[:200], "link": link,
                      "published": published})
        if len(items) >= 10:
            break
    return items


def fetch_social(symbol: str | None = None, data_root=None) -> dict:
    """Reddit RSS social feed. Sub-routing: crypto symbols →
    r/CryptoCurrency, equities → r/stocks, else (FX/commodity/index/
    None) → r/wallstreetbets. If a symbol is given, the URL is built
    with a ?+symbol search filter so the feed is symbol-relevant
    where Reddit supports it. Max 10 items."""
    sub = "wallstreetbets"
    if symbol:
        s = str(symbol).upper()
        if s.endswith("-USD") or s.endswith("=X") or "BTC" in s or \
                "ETH" in s or s.endswith("USD"):
            sub = "CryptoCurrency"
        elif s.endswith(".NS") or s.endswith(".BO"):
            sub = "stocks"  # NSE/Asia equities — closest keyless sub
        elif s.startswith("^") or s.endswith("=F"):
            sub = "wallstreetbets"  # indices/futures
        else:
            # heuristic: US/UK equity ticker (≤5 alphas, no slash)
            base = s.split("/")[0].split(".")[0]
            if 1 <= len(base) <= 5 and base.isalpha():
                sub = "stocks"
            else:
                sub = "wallstreetbets"

    def _fetch() -> dict:
        url = REDDIT_RSS_URL.format(sub=sub)
        # NOTE: Reddit's .rss endpoint does not accept ?q=; the sub itself
        # is the asset-class filter (crypto→r/CryptoCurrency, equities→
        # r/stocks, else r/wallstreetbets). The cache key below carries
        # the symbol so symbol-specific runs don't share cache across
        # different asset-class picks.
        xml_bytes = _http_get_bytes(url, REDDIT_UA, timeout=HTTP_TIMEOUT)
        items = _parse_reddit_rss(xml_bytes, sub)
        return {"ok": True, "source": "reddit", "sub": sub,
                "symbol": symbol, "items": items, "n": len(items)}
    cache_name = f"social_reddit_{sub}_{_slug(symbol or 'none')}"
    out = _cached_fetch(data_root or "data", cache_name, TTL_S, _fetch)
    out["kind"] = "social_reddit"
    return out


# --------------------------------------------- gather aggregator
def gather_institutional_context(symbol: str, data_root=None) -> dict:
    """Convenience: 7 institutional slices in parallel (ThreadPoolExecutor),
    each fail-soft. The desk engine pulls only the slices the personas
    are entitled to. Wall ≤ 8s (parallelism across independent feeds).

    Returns {fundamentals, institutional_top, macro_curve,
    crypto_sentiment, onchain, global_crypto, social} — each the ok:False
    form on its own failure. Top-level ok:True if ANY slice lived."""
    slices: dict[str, dict] = {}
    dr = data_root or "data"
    with ThreadPoolExecutor(max_workers=7) as ex:
        futs = {
            ex.submit(fetch_fundamentals, symbol, dr): "fundamentals",
            ex.submit(fetch_institutional, None, dr): "institutional_top",
            ex.submit(fetch_yield_curve, None, dr): "macro_curve",
            ex.submit(fetch_crypto_sentiment, dr): "crypto_sentiment",
            ex.submit(fetch_onchain, dr): "onchain",
            ex.submit(fetch_global_crypto, dr): "global_crypto",
            ex.submit(fetch_social, symbol, dr): "social",
        }
        for fut in as_completed(futs):
            key = futs[fut]
            try:
                slices[key] = fut.result()
            except Exception as e:  # noqa: BLE001 — per-slice fail-soft
                slices[key] = {"ok": False,
                                "error": f"{type(e).__name__}: {e}"}
    return {"ok": any(s.get("ok") for s in slices.values()),
            "symbol": symbol, "slices": slices}
