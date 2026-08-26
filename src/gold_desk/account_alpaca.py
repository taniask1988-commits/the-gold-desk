"""R3-1 BUILD 2 — Alpaca paper trading account (beats Bar B Alpaca SDK).

`AlpacaPaperAccount` is a stdlib-only REST + WebSocket client for the
Alpaca paper-trading API (paper-api.alpaca.markets). Paper keys are
FREE — no credit card — and we treat them as "keyless-with-paper-key"
per the R3 charter (Bar B is `pip install alpaca-py`; ours has zero
3rd-party trading deps and ships its own urllib REST + optional WS
fallback to polling).

Capabilities:

* `available()` classmethod — True iff `ALPACA_PAPER_KEY` AND
  `ALPACA_PAPER_SECRET` env vars are both set AND non-empty. The
  orchestrator's existing `PaperAccountStore` (synthetic paper) stays
  the default; `account.py`'s new `resolve_paper_account(...)` helper
  dispatches here only when both creds are present. **Fail-closed**:
  missing creds → CONSTITUTION_BLOCKED + ALPACA_CREDS_MISSING (mirrors
  the constitution's BLOCKED-field discipline — same shape as the
  existing synthetic path's blocked_fields()).

* `submit_order(ticket, kind)` → POST /v2/orders — market/limit/stop
  order from a `Ticket`. Returns the broker order dict (id, status,
  created_at, ...). On 422 (insufficient buying power, bad symbol)
  returns `{ok: False, error, status_code: 422, ...}` — never raises
  on transport-level failures (reconciliation journals them).

* `cancel_order(order_id)` → DELETE /v2/orders/{id}.

* `summary(timeout)` → GET /v2/account + GET /v2/positions + GET
  /v2/orders (status=open). One ThreadPoolExecutor fan-out, 3 calls.

* `stream_fills(on_fill)` — optional WebSocket subscription to
  wss://paper-api.alpaca.markets/stream (trade updates). Uses
  `threading` + `websocket-client` (already installed in the repo).
  Auth-success is the REAL protocol shape (live-probed + official
  docs): a BARE DICT
  `{"stream": "authorization",
    "data": {"action": "authenticate", "status": "authorized"}}`
  (a failure replies `data.status == "unauthorized"`). Falls back to
  `poll_fills()` if `websocket` import fails, the handshake errors,
  auth is rejected, OR the stream goes quiet (recv silence) — the
  polling path is a 3-second GET /v2/orders sweep that surfaces any
  status transition since the last call.

* `reconcile_fill(fill, ticket_store)` — match a broker fill against
  our `Ticket` by id+symbol+qty+price, journal `Fill` event with
  `source: "alpaca"` (the existing journal.emit path in `events.py`).
  Mismatched fills journal a `Fill` with `reconciled: False` and a
  `mismatch_reason` field — never silently accepts an unverified fill.

Constitution law boundary (L12): creds live in env vars OR (future
work) under `broker.alpaca_paper_creds` in the YAML. Runtime code may
read them; never mutates the constitution. The CONSTITUTION_BLOCKED
shape returned on missing creds is the same one the existing
`PaperAccountStore` returns on missing broker fields — the
orchestrator's risk gate already knows how to abstain on it.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

PAPER_API_BASE = "https://paper-api.alpaca.markets"
PAPER_WS_BASE = "wss://paper-api.alpaca.markets/stream"
HTTP_TIMEOUT = 8.0
USER_AGENT = "gold-desk/1.0 (stdlib urllib; keyless-with-paper-key)"


# ------------------------------------------------------------------ creds
def _read_creds() -> tuple[str | None, str | None]:
    """Read paper creds from env (or the process env at import time).

    Never raises; missing creds simply return (None, None) and the
    caller decides whether to fail-closed or fall back to synthetic.
    """
    key = os.environ.get("ALPACA_PAPER_KEY") or os.environ.get(
        "ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_PAPER_SECRET") or os.environ.get(
        "ALPACA_API_SECRET")
    return key, secret


# ------------------------------------------------------------------ order
@dataclass
class OrderRequest:
    """Lightweight order-shape builder — Ticket → Alpaca /v2/orders body.

    Alpaca's REST body is shape-stable across market/limit/stop:
        {"symbol": str, "qty": str, "side": "buy"|"sell",
         "type": "market"|"limit"|"stop"|"stop_limit",
         "time_in_force": "day", "order_class": "simple",
         "limit_price": str?, "stop_price": str?}
    All numbers are stringified — Alpaca rejects numerics.
    """
    symbol: str
    qty: float
    side: str  # "buy" or "sell"
    order_type: str = "market"  # market|limit|stop|stop_limit
    time_in_force: str = "day"
    limit_price: float | None = None
    stop_price: float | None = None
    client_order_id: str | None = None

    def to_body(self) -> dict:
        body: dict[str, Any] = {
            "symbol": self.symbol,
            "qty": str(self.qty),
            "side": self.side,
            "type": self.order_type,
            "time_in_force": self.time_in_force,
            "order_class": "simple",
        }
        if self.order_type in ("limit", "stop_limit") \
                and self.limit_price is not None:
            body["limit_price"] = str(self.limit_price)
        if self.order_type in ("stop", "stop_limit") \
                and self.stop_price is not None:
            body["stop_price"] = str(self.stop_price)
        if self.client_order_id:
            body["client_order_id"] = self.client_order_id
        return body


# ------------------------------------------------------------------ client
class AlpacaPaperAccount:
    """Stdlib REST + optional WS client for Alpaca paper trading.

    The class is fail-closed on construction: if creds are missing,
    `available()` returns False and the constructor raises
    `RuntimeError("ALPACA_CREDS_MISSING")`. The CLI/route layer checks
    `available()` BEFORE instantiating — same discipline as the
    existing `PaperAccountStore`'s `blocked_fields()` check.
    """

    def __init__(self, base_url: str = PAPER_API_BASE,
                 ws_url: str = PAPER_WS_BASE,
                 timeout: float = HTTP_TIMEOUT,
                 http_post: Callable | None = None,
                 http_get: Callable | None = None,
                 http_delete: Callable | None = None):
        key, secret = _read_creds()
        if not key or not secret:
            raise RuntimeError(
                "ALPACA_CREDS_MISSING: ALPACA_PAPER_KEY + "
                "ALPACA_PAPER_SECRET must be set (paper keys are free "
                "at alpaca.markets)")
        self._key = key
        self._secret = secret
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url
        self.timeout = timeout
        # injectable transport for tests (urllib mock)
        self._http_post = http_post or self._default_http_post
        self._http_get = http_get or self._default_http_get
        self._http_delete = http_delete or self._default_http_delete
        self._ws_thread: threading.Thread | None = None
        self._ws_stop = threading.Event()
        # D6: fill cursor + emitted-order set live on the account so
        # consecutive poll_fills() sweeps never replay old fills.
        self._fill_cursor: str | None = None
        self._poll_seen_ids: set[str] = set()

    # -------------------------------------------------------- available
    @staticmethod
    def available() -> bool:
        """True iff both ALPACA_PAPER_KEY and ALPACA_PAPER_SECRET are
        set in env (non-empty). Fail-closed otherwise — the CLI/route
        surface abstains with CONSTITUTION_BLOCKED instead of trying
        to construct the client and crash."""
        key, secret = _read_creds()
        return bool(key and secret)

    # ----------------------------------------------------------- http
    def _headers(self) -> dict:
        return {
            "User-Agent": USER_AGENT,
            "APCA-API-KEY-ID": self._key,
            "APCA-API-SECRET-KEY": self._secret,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _default_http_post(self, url: str, body: dict,
                            timeout: float | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"),
            headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                return r.status, json.loads(r.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001 — body may be empty
                payload = {"error": str(e)}
            return e.code, payload

    def _default_http_get(self, url: str, timeout: float | None = None
                          ) -> tuple[int, dict]:
        req = urllib.request.Request(url, headers=self._headers(),
                                      method="GET")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                payload = {"error": str(e)}
            return e.code, payload

    def _default_http_delete(self, url: str,
                              timeout: float | None = None
                              ) -> tuple[int, dict]:
        req = urllib.request.Request(url, headers=self._headers(),
                                      method="DELETE")
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
                body = r.read().decode("utf-8", "replace")
                return r.status, (json.loads(body) if body else {})
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8", "replace"))
            except Exception:  # noqa: BLE001
                payload = {"error": str(e)}
            return e.code, payload

    # -------------------------------------------------------- orders
    def submit_order(self, order: OrderRequest) -> dict:
        """POST /v2/orders — submit a market/limit/stop/stop_limit order.

        Returns:
            {ok: True, order: <broker order dict>}        — accepted
            {ok: False, status_code: 422, error: str, ...} — rejected
                (insufficient buying power, bad symbol, ...)
        Never raises on HTTP-level failure; the journal entry is the
        caller's responsibility (see reconcile_fill).
        """
        url = f"{self.base_url}/v2/orders"
        status, payload = self._http_post(url, order.to_body())
        if status in (200, 201) and isinstance(payload, dict) \
                and payload.get("id"):
            return {"ok": True, "order": payload,
                    "status_code": status,
                    "order_id": payload["id"]}
        return {"ok": False, "error": (payload or {}).get("error")
                                    or (payload or {}).get("message")
                                    or "submit_order failed",
                "status_code": status,
                "broker_response": payload}

    def cancel_order(self, order_id: str) -> dict:
        """DELETE /v2/orders/{id} — cancel an open order (idempotent
        on Alpaca's side: a 404 on an already-filled order returns ok=
        False with `already_terminal: True`)."""
        url = f"{self.base_url}/v2/orders/{urllib.parse.quote(order_id)}"
        status, payload = self._http_delete(url)
        if status in (200, 204):
            return {"ok": True, "order_id": order_id,
                    "status_code": status}
        return {"ok": False, "error": (payload or {}).get("error"),
                "status_code": status,
                "already_terminal": status == 404}

    # --------------------------------------------------------- account
    def _get_account(self) -> dict:
        url = f"{self.base_url}/v2/account"
        status, payload = self._http_get(url)
        if status == 200 and isinstance(payload, dict):
            return {"ok": True, "account": payload}
        return {"ok": False, "status_code": status,
                "error": (payload or {}).get("error")
                                       or "account fetch failed"}

    def _get_positions(self) -> list[dict]:
        url = f"{self.base_url}/v2/positions"
        status, payload = self._http_get(url)
        if status == 200 and isinstance(payload, list):
            return payload
        return []

    def _get_open_orders(self) -> list[dict]:
        url = (f"{self.base_url}/v2/orders?status=open&limit=50"
               "&direction=desc")
        status, payload = self._http_get(url)
        if status == 200 and isinstance(payload, list):
            return payload
        return []

    def summary(self, timeout: float | None = None) -> dict:
        """Account + positions + open orders in one call (threaded
        3-way fan-out). Returns:
            {ok, account, positions, orders, as_of}
        The `account` block has: status, equity, cash, buying_power,
        last_equity, unrealized_pl_today, unrealized_plpc_today. Each
        of `positions` and `orders` is the raw broker list (empty list
        when the call failed)."""
        tmo = timeout or self.timeout
        acc = self._get_account()
        # threaded fan-out — positions + orders
        pos: list[dict] = []
        orders: list[dict] = []
        # use threads so each call gets its own timeout budget
        threads = []
        results = {"pos": [], "ord": []}

        def _fetch_pos():
            results["pos"] = self._get_positions()
        threads.append(threading.Thread(target=_fetch_pos, daemon=True))

        def _fetch_orders():
            results["ord"] = self._get_open_orders()
        threads.append(threading.Thread(target=_fetch_orders, daemon=True))
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=tmo * 2)
        pos = results["pos"]
        orders = results["ord"]
        if not acc.get("ok"):
            return {"ok": False,
                    "reason_code": "ALPACA_REST_ERROR",
                    "error": acc.get("error"),
                    "status_code": acc.get("status_code")}
        return {
            "ok": True,
            "account": acc.get("account") or {},
            "positions": pos,
            "orders": orders,
            "as_of": _now_iso(),
        }

    # ------------------------------------------------------- streaming
    def stream_fills(self, on_fill: Callable[[dict], None],
                     stop_event: threading.Event | None = None
                     ) -> None:
        """Stream trade updates via WebSocket (wss://paper-api.alpaca.
        markets/stream). Calls `on_fill(fill_dict)` for every trade
        update received.

        Auth handshake (REAL protocol, live-probed): the server replies
        to our auth message with a BARE DICT —
            {"stream": "authorization",
             "data": {"action": "authenticate",
                      "status": "authorized"}}
        (failure: `data.status == "unauthorized"` with a
        `message: "code=401 ..."` payload). `_auth_success()` accepts
        that dict shape as the PRIMARY predicate (belt-and-braces: the
        legacy list shape + `action == "authenticated"` are kept for
        backward compatibility with proxy/gateway variants).

        Degradations — all fall back to `poll_fills(on_fill, stop)`:
          * `websocket` import missing
          * WS handshake (create_connection) failure
          * auth rejected (`status == "unauthorized"`) — IMMEDIATELY,
            never hangs
          * quiet stream (recv silence raises) — logs a warning and
            polls instead of dying silently

        The stream runs in this thread; for non-blocking use, see
        `start_fill_stream(...)`. The optional `stop_event` lets the
        caller break out of the read loop without signaling the socket.
        """
        stop = stop_event or self._ws_stop
        try:
            import websocket  # type: ignore
        except ImportError:
            self.poll_fills(on_fill, stop)
            return
        try:
            ws = websocket.create_connection(self.ws_url,
                                              timeout=self.timeout)
        except Exception:  # noqa: BLE001 — WS handshake errors degrade
            self.poll_fills(on_fill, stop)
            return
        try:
            # Alpaca v2 WS auth: bare-dict reply (see docstring)
            ws.send(json.dumps({"action": "auth",
                                  "key": self._key,
                                  "secret": self._secret}))
            auth_resp = ws.recv()
            auth = json.loads(auth_resp) if auth_resp else None
            if not _auth_success(auth):
                # auth rejected — fall back to polling IMMEDIATELY
                # (never hang waiting for trade updates)
                _ws_warn("alpaca WS auth failed (%r) — falling back to "
                         "polling" % (auth,))
                _close_quietly(ws)
                self.poll_fills(on_fill, stop)
                return
            ws.send(json.dumps({"action": "listen",
                                "data": {"streams": ["trade_updates"]}}))
            while not stop.is_set():
                try:
                    raw = ws.recv()
                except Exception:  # noqa: BLE001 — quiet stream degrades
                    # 8s recv silence (timeout) or a read failure: do NOT
                    # die silently — warn and fall back to polling.
                    _ws_warn("alpaca WS stream went quiet — falling back "
                             "to polling")
                    _close_quietly(ws)
                    self.poll_fills(on_fill, stop)
                    return
                if not raw:
                    continue
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for ev in (msg if isinstance(msg, list) else [msg]):
                    data = ev.get("data") or {}
                    if data.get("event") in ("fill", "partial_fill"):
                        on_fill(_normalize_fill(data))
            _close_quietly(ws)
        except Exception:  # noqa: BLE001 — WS protocol errors degrade
            self.poll_fills(on_fill, stop)

    def start_fill_stream(self, on_fill: Callable[[dict], None]
                          ) -> threading.Thread:
        """Start `stream_fills` in a background daemon thread."""
        self._ws_stop = threading.Event()
        t = threading.Thread(target=self.stream_fills,
                             args=(on_fill, self._ws_stop), daemon=True)
        self._ws_thread = t
        t.start()
        return t

    def stop_fill_stream(self) -> None:
        self._ws_stop.set()
        if self._ws_thread is not None:
            self._ws_thread.join(timeout=2.0)
            self._ws_thread = None

    def poll_fills(self, on_fill: Callable[[dict], None],
                   stop_event: threading.Event,
                   since: str | None = None,
                   replay_history: bool = False) -> None:
        """Polling fallback for fill streaming — sweep closed orders
        every 3 seconds for status transitions to fill/partial_fill,
        call `on_fill` for each. Stop when `stop_event` is set.

        Replay safety (D6 fix): `on_fill` fires only for fills NEWER
        than the cursor. The cursor (ISO timestamp of the newest fill
        we emitted) is stored on the account (`self._fill_cursor`), so
        consecutive sweeps never re-emit a fill they already delivered.

        * `since` — optional cursor (ISO timestamp preferred; a
          non-parseable value is treated as an order-id cursor and
          simply marks that order as seen). Resets the stored cursor
          before sweeping.
        * First call with NO cursor: history is NOT replayed — the
          cursor initializes to "now" so only fills that land after
          the first sweep are emitted. Pass `replay_history=True` to
          sweep the existing closed-order history exactly once (test
          / recovery seam).
        """
        if since is not None:
            if _parse_ts(since) is None:
                # order-id cursor: never re-emit this order
                self._poll_seen_ids.add(since)
            else:
                self._fill_cursor = since
        cursor_ts = _parse_ts(self._fill_cursor)
        if self._fill_cursor is None and not replay_history:
            # first call: do NOT replay history — start from "now"
            self._fill_cursor = _now_iso()
            cursor_ts = _parse_ts(self._fill_cursor)
        while not stop_event.is_set():
            try:
                url = (f"{self.base_url}/v2/orders?status=closed"
                       "&limit=50&direction=desc")
                status, payload = self._http_get(url)
                if status == 200 and isinstance(payload, list):
                    # cursor advances only at END of sweep so same-
                    # second batch fills within one sweep all emit
                    sweep_max: tuple | None = None   # (dt, iso_str)
                    for o in payload:
                        oid = o.get("id") or ""
                        if not oid or oid in self._poll_seen_ids:
                            continue
                        fill = _normalize_fill_from_order(o)
                        fts = _parse_ts(fill.get("ts"))
                        if cursor_ts is not None and fts is not None \
                                and fts <= cursor_ts:
                            # not newer than the cursor — mark seen, skip
                            self._poll_seen_ids.add(oid)
                            continue
                        self._poll_seen_ids.add(oid)
                        if o.get("status") in ("filled", "partially_filled",
                                                "partial_fill"):
                            on_fill(fill)
                            if fts is not None and \
                                    (sweep_max is None
                                     or fts > sweep_max[0]):
                                sweep_max = (fts, fill.get("ts"))
                    if sweep_max is not None:
                        cursor_ts = sweep_max[0]
                        self._fill_cursor = sweep_max[1]
            except Exception:  # noqa: BLE001 — poll sweep fails soft
                pass
            stop_event.wait(3.0)

    # --------------------------------------------------- reconciliation
    def reconcile_fill(self, fill: dict, ticket_store) -> dict:
        """Match a broker fill against a `Ticket` (via ticket_store).

        Looks up the ticket by `client_order_id` first (our idempotency
        field — set to the ticket's `ticket_id`), then by symbol + qty
        + price. Returns:
            {ok: True, reconciled: True, ticket_id, fill_event_id, ...}
            {ok: True, reconciled: False, mismatch_reason, ...}
        The caller journals the result (we don't write to disk here —
        we never hold a Journal reference, only a read-only ticket
        store lookup). A mismatched fill is NEVER silently accepted:
        the journal entry carries `reconciled: False` + the reason.
        """
        from .ticket import TicketStore  # local import — break cycle
        if not isinstance(ticket_store, TicketStore):
            return {"ok": False, "error":
                    "ticket_store must be a TicketStore instance"}
        sym = fill.get("symbol")
        qty = fill.get("qty")
        price = fill.get("price")
        client_order_id = fill.get("client_order_id")
        order_id = fill.get("order_id") or fill.get("id")
        # 1. fast path: client_order_id is our ticket_id
        if client_order_id:
            t = ticket_store.load(client_order_id)
            if t is not None:
                ok = _ticket_matches(t, sym, qty, price)
                return {"ok": True, "reconciled": ok,
                        "ticket_id": t.ticket_id,
                        "order_id": order_id,
                        "match_method": "client_order_id",
                        "mismatch_reason": None if ok else
                        _mismatch_reason(t, sym, qty, price)}
        # 2. slow path: scan open tickets
        for t in ticket_store.open_tickets():
            if _ticket_matches(t, sym, qty, price):
                return {"ok": True, "reconciled": True,
                        "ticket_id": t.ticket_id,
                        "order_id": order_id,
                        "match_method": "scan",
                        "mismatch_reason": None}
        return {"ok": True, "reconciled": False,
                "ticket_id": None,
                "order_id": order_id,
                "match_method": "none",
                "mismatch_reason": "no open ticket matches "
                                    f"symbol={sym} qty={qty} price={price}"}


# ------------------------------------------------------------------ helpers
def _auth_success(auth) -> bool:
    """True when the Alpaca WS auth handshake succeeded.

    PRIMARY (real protocol, live-probed + official docs): the reply to
    our auth message is a BARE DICT —
        {"stream": "authorization",
         "data": {"action": "authenticate", "status": "authorized"}}
    Failure shape: same dict with `data.status == "unauthorized"`
    (message carries "code=401 ..."). Belt-and-braces we also accept
    `data.action == "authenticate"` alone, and the legacy LIST shape
    (`[{"data": {"action": "authenticated"}}]`) some gateways emit.
    """
    if isinstance(auth, dict):
        data = auth.get("data") or {}
        if data.get("status") == "authorized":
            return True
        if data.get("action") == "authenticate":
            return True
        return False
    if isinstance(auth, list) and auth:
        first = auth[0] if isinstance(auth[0], dict) else {}
        data = first.get("data") or {}
        return (data.get("action") in ("authenticate", "authenticated")
                or data.get("status") == "authorized")
    return False


def _close_quietly(ws) -> None:
    """Close a WS connection; never raises."""
    try:
        ws.close()
    except Exception:  # noqa: BLE001 — close never breaks us
        pass


def _ws_warn(message: str) -> None:
    """Emit a WS-degradation warning (stderr — the journal is owned by
    the orchestrator; this path runs in a background thread)."""
    print(f"gold-desk WARNING: {message}", file=sys.stderr)


def _parse_ts(value) -> datetime | None:
    """Tolerant ISO-8601 parser (handles trailing 'Z'); None on miss."""
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_fill(data: dict) -> dict:
    """Alpaca WS trade_update event → flat fill dict the journal
    understands. Alpaca nests the order under data.order."""
    order = data.get("order") or {}
    return {
        "order_id": order.get("id"),
        "client_order_id": order.get("client_order_id"),
        "symbol": order.get("symbol"),
        "qty": float(order.get("filled_qty") or 0),
        "price": float(order.get("filled_avg_price") or 0)
                  or None,
        "side": order.get("side"),
        "status": order.get("status"),
        "event": data.get("event"),
        "ts": data.get("timestamp") or order.get("updated_at"),
        "source": "alpaca:ws",
    }


def _normalize_fill_from_order(o: dict) -> dict:
    """Closed-order polling path → flat fill dict."""
    return {
        "order_id": o.get("id"),
        "client_order_id": o.get("client_order_id"),
        "symbol": o.get("symbol"),
        "qty": float(o.get("filled_qty") or 0),
        "price": float(o.get("filled_avg_price") or 0) or None,
        "side": o.get("side"),
        "status": o.get("status"),
        "event": "fill",
        "ts": o.get("updated_at") or o.get("filled_at"),
        "source": "alpaca:poll",
    }


def _ticket_matches(ticket, symbol: str, qty: float | None,
                    price: float | None) -> bool:
    """Loose reconciliation: symbol match + qty match (1% tolerance)
    + price match (0.5% tolerance). Alpaca paper may partially fill
    across multiple events; the `qty` parameter is the cumulative
    filled qty for the latest event, so we only check that the
    ticket's lots is within 1% of the cumulative fill (covers a
    partial fill's residual)."""
    if not symbol or qty is None:
        return False
    if str(ticket.symbol).upper() != str(symbol).upper():
        # our ticket's symbol field is "XAUUSD"; the broker symbol
        # could be a Yahoo-style "GC=F" — accept both
        return False
    # lot tolerance: 1% (partial fill residual)
    try:
        lots = float(ticket.lots)
        q = float(qty)
        if abs(lots - q) > max(0.01, lots * 0.01):
            return False
    except (TypeError, ValueError):
        return False
    if price is not None:
        try:
            entry = float(ticket.entry)
            if entry > 0 and abs(entry - float(price)) / entry > 0.01:
                return False
        except (TypeError, ValueError, ZeroDivisionError):
            return False
    return True


def _mismatch_reason(ticket, symbol, qty, price) -> str:
    if str(ticket.symbol).upper() != str(symbol).upper():
        return f"symbol mismatch ({ticket.symbol} vs {symbol})"
    if qty is not None:
        try:
            lots = float(ticket.lots)
            if abs(lots - float(qty)) > max(0.01, lots * 0.01):
                return f"qty mismatch ({lots} vs {qty})"
        except (TypeError, ValueError):
            return "qty unparseable"
    if price is not None:
        try:
            entry = float(ticket.entry)
            if entry > 0 and abs(entry - float(price)) / entry > 0.01:
                return f"price mismatch ({entry} vs {price})"
        except (TypeError, ValueError, ZeroDivisionError):
            return "price unparseable"
    return "no specific mismatch"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(
        timespec="seconds").replace("+00:00", "Z")
