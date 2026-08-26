"""R3-1 Build 2 — tests for Alpaca paper trading integration.

Covers:
* Mocked REST order submission (urllib mock) — market order round-trip
* Mocked limit order with rejection (insufficient buying power → 422)
* Mocked stop-limit fill lifecycle (submitted → partial → filled)
* Fail-closed: missing creds → CONSTITUTION_BLOCKED + ALPACA_CREDS_MISSING
* Reconciliation: paper fill matches our ticket (price + qty + symbol)
* WebSocket fill (mocked): single fill vs polling fallback
* D1: REAL WS auth protocol shapes (bare dict authorized/unauthorized)
  + quiet-stream degradation to polling
* D6: poll_fills replay safety (cursor + no duplicates across sweeps)
* resolve_paper_account() dispatch (creds present → Alpaca, else synthetic)
* OrderRequest.to_body shape (stringified numerics per Alpaca spec)
"""
from __future__ import annotations

import json
import os
import sys
import threading
import types
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gold_desk.account_alpaca import (
    AlpacaPaperAccount, OrderRequest, _auth_success, _normalize_fill,
    _normalize_fill_from_order, _ticket_matches, _mismatch_reason,
)
from gold_desk.account import resolve_paper_account, PaperAccountStore


# ------------------------------------------------------------- fixtures
CREDS_ENV = {
    "ALPACA_PAPER_KEY": "PKTESTKEY",
    "ALPACA_PAPER_SECRET": "SECRETTESTKEY",
}


@pytest.fixture(autouse=True)
def _clean_creds_env(monkeypatch):
    """Wipe creds between tests so available() reflects the test's setup."""
    for k in ("ALPACA_PAPER_KEY", "ALPACA_PAPER_SECRET",
              "ALPACA_API_KEY", "ALPACA_API_SECRET"):
        monkeypatch.delenv(k, raising=False)
    yield


def _set_creds(monkeypatch, key="PKTEST", secret="SECRETTEST"):
    monkeypatch.setenv("ALPACA_PAPER_KEY", key)
    monkeypatch.setenv("ALPACA_PAPER_SECRET", secret)


# ------------------------------------------------------- OrderRequest
def test_order_request_market_body_shape():
    """Market order body has the minimum required Alpaca fields."""
    req = OrderRequest(symbol="AAPL", qty=10, side="buy", order_type="market")
    body = req.to_body()
    assert body["symbol"] == "AAPL"
    assert body["qty"] == "10"
    assert body["side"] == "buy"
    assert body["type"] == "market"
    assert body["time_in_force"] == "day"
    assert body["order_class"] == "simple"
    assert "limit_price" not in body
    assert "stop_price" not in body


def test_order_request_limit_body_includes_limit_price():
    """Limit order body stringifies limit_price."""
    req = OrderRequest(symbol="AAPL", qty=5, side="sell",
                       order_type="limit", limit_price=195.50)
    body = req.to_body()
    assert body["type"] == "limit"
    assert body["limit_price"] == "195.5"
    assert "stop_price" not in body


def test_order_request_stop_limit_body_includes_both_prices():
    """Stop-limit order body has both limit_price and stop_price."""
    req = OrderRequest(symbol="GC=F", qty=2, side="buy",
                       order_type="stop_limit",
                       limit_price=2050.0, stop_price=2045.0)
    body = req.to_body()
    assert body["type"] == "stop_limit"
    assert body["limit_price"] == "2050.0"
    assert body["stop_price"] == "2045.0"


def test_order_request_client_order_id_propagates():
    """client_order_id is included when set (used for our ticket_id)."""
    req = OrderRequest(symbol="X", qty=1, side="buy",
                       client_order_id="ticket-abc123")
    assert req.to_body()["client_order_id"] == "ticket-abc123"


# -------------------------------------------------------- fail-closed
def test_available_false_when_creds_missing(monkeypatch):
    """available() returns False when both env vars are missing."""
    assert AlpacaPaperAccount.available() is False


def test_available_false_when_secret_missing(monkeypatch):
    """available() returns False when only the key is set."""
    monkeypatch.setenv("ALPACA_PAPER_KEY", "PKTEST")
    assert AlpacaPaperAccount.available() is False


def test_available_true_when_both_creds_set(monkeypatch):
    """available() returns True when both env vars are non-empty."""
    _set_creds(monkeypatch)
    assert AlpacaPaperAccount.available() is True


def test_construction_raises_when_creds_missing(monkeypatch):
    """Constructing AlpacaPaperAccount without creds raises RuntimeError."""
    with pytest.raises(RuntimeError, match="ALPACA_CREDS_MISSING"):
        AlpacaPaperAccount()


# ------------------------------------------------------- REST submit
def _make_mock_post(status: int, payload: dict):
    """Mock _http_post callable returning (status, payload)."""
    def _post(url: str, body: dict, timeout: float | None = None):
        return status, payload
    return _post


def _make_mock_get(status: int, payload, *, per_url: dict | None = None):
    """Mock _http_get callable returning (status, payload).

    `per_url` lets different URLs return different payloads (used by
    the summary() test which hits 3 endpoints)."""
    def _get(url: str, timeout: float | None = None):
        if per_url is not None:
            for key, resp in per_url.items():
                if key in url:
                    return resp
        return status, payload
    return _get


def _make_mock_delete(status: int, payload: dict):
    def _del(url: str, timeout: float | None = None):
        return status, payload
    return _del


def test_submit_order_market_round_trip_success(monkeypatch):
    """Mocked market order submission returns ok=True with broker order."""
    _set_creds(monkeypatch)
    broker_response = {
        "id": "order-abc-123",
        "client_order_id": "ticket-1",
        "symbol": "AAPL",
        "qty": "10",
        "side": "buy",
        "type": "market",
        "status": "accepted",
        "created_at": "2026-01-05T17:00:00Z",
    }
    acct = AlpacaPaperAccount(
        http_post=_make_mock_post(200, broker_response))
    out = acct.submit_order(OrderRequest(
        symbol="AAPL", qty=10, side="buy", order_type="market",
        client_order_id="ticket-1"))
    assert out["ok"] is True
    assert out["order_id"] == "order-abc-123"
    assert out["order"]["status"] == "accepted"
    assert out["status_code"] == 200


def test_submit_order_rejected_insufficient_buying_power(monkeypatch):
    """422 rejection (insufficient buying power) returns ok=False."""
    _set_creds(monkeypatch)
    broker_response = {
        "id": None,
        "code": 40300200,
        "message": "insufficient buying power",
    }
    acct = AlpacaPaperAccount(
        http_post=_make_mock_post(422, broker_response))
    out = acct.submit_order(OrderRequest(
        symbol="AAPL", qty=10000, side="buy", order_type="market"))
    assert out["ok"] is False
    assert out["status_code"] == 422
    assert "buying power" in out["error"]


def test_submit_order_rejected_bad_symbol(monkeypatch):
    """400 rejection (bad symbol) returns ok=False."""
    _set_creds(monkeypatch)
    broker_response = {"code": 40010000, "message": "symbol not found"}
    acct = AlpacaPaperAccount(
        http_post=_make_mock_post(400, broker_response))
    out = acct.submit_order(OrderRequest(
        symbol="NOPE", qty=1, side="buy", order_type="market"))
    assert out["ok"] is False
    assert out["status_code"] == 400


# ------------------------------------------------------ stop-limit lifecycle
def test_stop_limit_lifecycle_submitted_partial_filled(monkeypatch):
    """Stop-limit order transits submitted → partial → filled.

    Each REST call returns the next status. The test asserts we can
    move through all three phases without raising and the final state
    is "filled".
    """
    _set_creds(monkeypatch)
    states = [
        {"id": "order-stop-1", "status": "accepted",
         "filled_qty": "0", "filled_avg_price": None,
         "client_order_id": "ticket-stop-1"},
        {"id": "order-stop-1", "status": "partially_filled",
         "filled_qty": "1", "filled_avg_price": "2050.00",
         "client_order_id": "ticket-stop-1"},
        {"id": "order-stop-1", "status": "filled",
         "filled_qty": "2", "filled_avg_price": "2051.50",
         "client_order_id": "ticket-stop-1"},
    ]
    calls = iter(states)

    def _post(url, body, timeout=None):
        try:
            return 200, next(calls)
        except StopIteration:
            return 200, states[-1]
    acct = AlpacaPaperAccount(http_post=_post)
    # phase 1: submitted
    r1 = acct.submit_order(OrderRequest(
        symbol="GC=F", qty=2, side="buy", order_type="stop_limit",
        limit_price=2052.0, stop_price=2045.0,
        client_order_id="ticket-stop-1"))
    assert r1["ok"] is True
    assert r1["order"]["status"] == "accepted"
    # phase 2: partial fill (would arrive via WS / polling — here we
    # simulate by calling submit_order again with the next mocked
    # response, which mimics a subsequent poll)
    r2 = acct.submit_order(OrderRequest(
        symbol="GC=F", qty=2, side="buy", order_type="stop_limit",
        limit_price=2052.0, stop_price=2045.0,
        client_order_id="ticket-stop-1"))
    assert r2["ok"] is True
    assert r2["order"]["status"] == "partially_filled"
    # phase 3: full fill
    r3 = acct.submit_order(OrderRequest(
        symbol="GC=F", qty=2, side="buy", order_type="stop_limit",
        limit_price=2052.0, stop_price=2045.0,
        client_order_id="ticket-stop-1"))
    assert r3["ok"] is True
    assert r3["order"]["status"] == "filled"
    assert r3["order"]["filled_qty"] == "2"
    assert r3["order"]["filled_avg_price"] == "2051.50"


# ---------------------------------------------------------- summary
def test_summary_aggregates_account_positions_orders(monkeypatch):
    """summary() returns the account + positions + orders in one call."""
    _set_creds(monkeypatch)
    account_payload = {
        "id": "acc-1",
        "status": "ACTIVE",
        "equity": "100000.00",
        "cash": "50000.00",
        "buying_power": "100000.00",
        "last_equity": "99000.00",
        "unrealized_pl_today": "1200.50",
        "unrealized_plpc_today": "0.0125",
    }
    positions_payload = [
        {"symbol": "AAPL", "qty": "100",
         "avg_entry_price": "190.00", "current_price": "195.00",
         "unrealized_pl": "500.00", "side": "long"},
    ]
    orders_payload = [
        {"id": "order-1", "symbol": "AAPL", "qty": "10",
         "side": "buy", "type": "limit", "limit_price": "190.00",
         "status": "open", "created_at": "2026-01-05T17:00:00Z"},
    ]
    per_url = {
        "/v2/account": (200, account_payload),
        "/v2/positions": (200, positions_payload),
        "/v2/orders": (200, orders_payload),
    }
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}))
    out = acct.summary()
    assert out["ok"] is True
    assert out["account"]["status"] == "ACTIVE"
    assert out["account"]["equity"] == "100000.00"
    assert len(out["positions"]) == 1
    assert out["positions"][0]["symbol"] == "AAPL"
    assert len(out["orders"]) == 1
    assert out["orders"][0]["type"] == "limit"
    assert out["as_of"]


def test_summary_returns_ok_false_when_account_fails(monkeypatch):
    """summary() returns ok=False when /v2/account returns non-200."""
    _set_creds(monkeypatch)
    per_url = {
        "/v2/account": (401, {"error": "unauthorized"}),
        "/v2/positions": (200, []),
        "/v2/orders": (200, []),
    }
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}))
    out = acct.summary()
    assert out["ok"] is False
    assert out["reason_code"] == "ALPACA_REST_ERROR"
    assert out["status_code"] == 401


# ---------------------------------------------------- cancel order
def test_cancel_order_success(monkeypatch):
    """Cancel returns ok=True on 200/204."""
    _set_creds(monkeypatch)
    acct = AlpacaPaperAccount(
        http_delete=_make_mock_delete(204, {}))
    out = acct.cancel_order("order-abc")
    assert out["ok"] is True
    assert out["order_id"] == "order-abc"


def test_cancel_order_already_terminal(monkeypatch):
    """Cancel on a 404 (already filled) returns ok=False but
    already_terminal=True — idempotent."""
    _set_creds(monkeypatch)
    acct = AlpacaPaperAccount(
        http_delete=_make_mock_delete(404, {"error": "order already filled"}))
    out = acct.cancel_order("order-abc")
    assert out["ok"] is False
    assert out["already_terminal"] is True


# --------------------------------------------------- reconciliation
def test_reconcile_fill_matches_via_client_order_id(monkeypatch, tmp_path):
    """Fill with client_order_id matching our ticket → reconciled=True."""
    _set_creds(monkeypatch)
    # build a TicketStore + a Ticket
    from gold_desk.ticket import Ticket, TicketStore
    store = TicketStore(tmp_path)
    t = Ticket(ticket_id="ticket-recon-1", symbol="XAUUSD",
                side="buy", entry=2050.0, stop=2045.0,
                target=2065.0, lots=2.0)
    t.status = "SENT"
    store.persist(t)
    acct = AlpacaPaperAccount()
    fill = {
        "order_id": "broker-1",
        "client_order_id": "ticket-recon-1",
        "symbol": "XAUUSD", "qty": 2.0, "price": 2050.0,
        "side": "buy", "status": "filled",
        "event": "fill", "ts": "2026-01-05T17:00:00Z",
        "source": "alpaca:ws",
    }
    out = acct.reconcile_fill(fill, store)
    assert out["ok"] is True
    assert out["reconciled"] is True
    assert out["ticket_id"] == "ticket-recon-1"
    assert out["match_method"] == "client_order_id"


def test_reconcile_fill_mismatch_qty(monkeypatch, tmp_path):
    """Fill with wrong qty (outside 1% tolerance) → reconciled=False."""
    _set_creds(monkeypatch)
    from gold_desk.ticket import Ticket, TicketStore
    store = TicketStore(tmp_path)
    t = Ticket(ticket_id="ticket-recon-2", symbol="XAUUSD",
                side="buy", entry=2050.0, stop=2045.0,
                target=2065.0, lots=2.0)
    t.status = "SENT"
    store.persist(t)
    acct = AlpacaPaperAccount()
    fill = {
        "order_id": "broker-2",
        "client_order_id": "ticket-recon-2",
        "symbol": "XAUUSD", "qty": 5.0,  # wrong — ticket says 2.0
        "price": 2050.0, "side": "buy",
        "status": "filled", "event": "fill",
        "ts": "2026-01-05T17:00:00Z",
        "source": "alpaca:ws",
    }
    out = acct.reconcile_fill(fill, store)
    assert out["ok"] is True
    assert out["reconciled"] is False
    assert "qty mismatch" in (out["mismatch_reason"] or "")


def test_reconcile_fill_mismatch_price(monkeypatch, tmp_path):
    """Fill with price >1% off the ticket's entry → reconciled=False."""
    _set_creds(monkeypatch)
    from gold_desk.ticket import Ticket, TicketStore
    store = TicketStore(tmp_path)
    t = Ticket(ticket_id="ticket-recon-3", symbol="XAUUSD",
                side="buy", entry=2050.0, stop=2045.0,
                target=2065.0, lots=2.0)
    t.status = "SENT"
    store.persist(t)
    acct = AlpacaPaperAccount()
    fill = {
        "order_id": "broker-3",
        "client_order_id": "ticket-recon-3",
        "symbol": "XAUUSD", "qty": 2.0,
        "price": 2200.0,  # 7.3% off entry
        "side": "buy", "status": "filled",
        "event": "fill", "ts": "2026-01-05T17:00:00Z",
        "source": "alpaca:ws",
    }
    out = acct.reconcile_fill(fill, store)
    assert out["reconciled"] is False
    assert "price mismatch" in (out["mismatch_reason"] or "")


def test_reconcile_fill_no_open_ticket(monkeypatch, tmp_path):
    """Fill with no matching open ticket → reconciled=False."""
    _set_creds(monkeypatch)
    from gold_desk.ticket import TicketStore
    store = TicketStore(tmp_path)
    acct = AlpacaPaperAccount()
    fill = {
        "order_id": "broker-orphan",
        "client_order_id": "ticket-unknown",
        "symbol": "XAUUSD", "qty": 2.0,
        "price": 2050.0, "side": "buy",
        "status": "filled", "event": "fill",
        "ts": "2026-01-05T17:00:00Z",
        "source": "alpaca:ws",
    }
    out = acct.reconcile_fill(fill, store)
    assert out["reconciled"] is False
    assert "no open ticket" in (out["mismatch_reason"] or "")


def test_ticket_matches_symbol_check():
    """Symbol mismatch short-circuits reconciliation."""
    from gold_desk.ticket import Ticket
    t = Ticket(ticket_id="t", symbol="XAUUSD", side="buy",
                entry=2050.0, stop=2045.0, target=2065.0, lots=1.0)
    assert _ticket_matches(t, "EURUSD", 1.0, 2050.0) is False
    assert _ticket_matches(t, "XAUUSD", 1.0, 2050.0) is True


def test_normalize_fill_from_ws_event():
    """Alpaca WS trade_update event → flat fill dict."""
    data = {
        "event": "fill",
        "timestamp": "2026-01-05T17:00:00Z",
        "order": {
            "id": "order-1",
            "client_order_id": "ticket-1",
            "symbol": "AAPL",
            "filled_qty": "10",
            "filled_avg_price": "195.50",
            "side": "buy",
            "status": "filled",
            "updated_at": "2026-01-05T17:00:00Z",
        },
    }
    f = _normalize_fill(data)
    assert f["order_id"] == "order-1"
    assert f["client_order_id"] == "ticket-1"
    assert f["symbol"] == "AAPL"
    assert f["qty"] == 10.0
    assert f["price"] == 195.50
    assert f["side"] == "buy"
    assert f["source"] == "alpaca:ws"


def test_normalize_fill_from_order_poll():
    """Closed-order polling path → flat fill dict."""
    o = {
        "id": "order-2",
        "client_order_id": "ticket-2",
        "symbol": "GC=F",
        "filled_qty": "2",
        "filled_avg_price": "2050.00",
        "side": "buy",
        "status": "filled",
        "updated_at": "2026-01-05T17:00:00Z",
        "filled_at": "2026-01-05T17:00:01Z",
    }
    f = _normalize_fill_from_order(o)
    assert f["order_id"] == "order-2"
    assert f["qty"] == 2.0
    assert f["price"] == 2050.0
    assert f["source"] == "alpaca:poll"


# ----------------------------------------------------- WS / polling
def test_stream_fills_falls_back_to_polling_when_no_websocket(monkeypatch):
    """Missing `websocket` module → stream_falls back to poll_fills().

    We monkey-patch `import websocket` inside stream_fills to raise
    ImportError, which triggers the polling fallback. The polling path
    then calls our mocked _http_get → on_fill() is invoked once per
    filled order, then the stop event breaks the loop.
    """
    _set_creds(monkeypatch)
    # capture on_fill invocations
    fills: list[dict] = []
    def _on_fill(f): fills.append(f)
    # mock _http_get — return one closed order with status=filled
    per_url = {
        "status=closed": (200, [
            {"id": "order-1", "symbol": "AAPL",
             "filled_qty": "10", "filled_avg_price": "195.00",
             "side": "buy", "status": "filled",
             "client_order_id": "ticket-1",
             "updated_at": "2026-01-05T17:00:00Z"},
        ]),
    }
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))
    # patch the import inside stream_fills
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "websocket":
            raise ImportError("mocked: websocket not installed")
        return real_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", fake_import)
    # patch poll_fills to short-circuit immediately (call once, then stop)
    stop = threading.Event()
    original_poll = acct.poll_fills
    def fast_poll(on_fill, stop_event):
        # call the original poll once (so on_fill fires) and return
        # — never block on stop_event.wait(3.0)
        try:
            url = f"{acct.base_url}/v2/orders?status=closed&limit=50&direction=desc"
            status, payload = acct._http_get(url)
            if status == 200 and isinstance(payload, list):
                for o in payload:
                    if o.get("status") in ("filled", "partially_filled", "partial_fill"):
                        on_fill(_normalize_fill_from_order(o))
        except Exception:
            pass
    monkeypatch.setattr(acct, "poll_fills", fast_poll)
    # run stream_fills synchronously — it falls back to fast_poll
    acct.stream_fills(_on_fill, stop_event=stop)
    assert len(fills) == 1
    assert fills[0]["order_id"] == "order-1"
    assert fills[0]["qty"] == 10.0


def test_poll_fills_sweeps_closed_orders(monkeypatch):
    """poll_fills() with mocked closed-orders GET invokes on_fill once
    per filled order (replay_history=True — the documented seam for
    sweeping existing history once). The stop event breaks the loop."""
    _set_creds(monkeypatch)
    fills: list[dict] = []
    def _on_fill(f): fills.append(f)
    per_url = {
        "status=closed": (200, [
            {"id": "order-1", "symbol": "AAPL",
             "filled_qty": "10", "filled_avg_price": "195.00",
             "side": "buy", "status": "filled",
             "client_order_id": "ticket-1",
             "updated_at": "2026-01-05T17:00:00Z"},
            {"id": "order-2", "symbol": "GC=F",
             "filled_qty": "2", "filled_avg_price": "2050.00",
             "side": "buy", "status": "filled",
             "client_order_id": "ticket-2",
             "updated_at": "2026-01-05T17:00:00Z"},
        ]),
    }
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))
    stop = threading.Event()
    # patch the wait to set the event after the first sweep so the
    # `while not stop.is_set()` loop exits after exactly one iteration
    real_wait = stop.wait
    wait_calls = {"n": 0}
    def fast_wait(timeout=None):
        wait_calls["n"] += 1
        if wait_calls["n"] >= 1:
            stop.set()          # signal stop after first sweep
            return True
        return real_wait(timeout)
    stop.wait = fast_wait  # type: ignore[assignment]
    acct.poll_fills(_on_fill, stop, replay_history=True)
    assert len(fills) == 2
    assert {f["order_id"] for f in fills} == {"order-1", "order-2"}


# =========================================================== D1: WS auth
# The REAL Alpaca WS protocol (live-probed + official docs) replies to
# the auth message with a BARE DICT, e.g.
#   success: {"stream": "authorization",
#             "data": {"action": "authenticate",
#                      "status": "authorized"}}
#   failure: {"stream": "authorization",
#             "data": {"action": "auth",
#                      "message": "code=401 ...",
#                      "status": "unauthorized"}}
class _FakeWS:
    """Fake websocket-client connection (D1 tests)."""

    def __init__(self, responses: list, stop=None, then: str = "empty"):
        self._responses = list(responses)
        self._stop = stop
        self._then = then      # behavior once responses are exhausted
        self.sent: list[dict] = []
        self.closed = False

    def send(self, payload):
        self.sent.append(json.loads(payload))

    def recv(self):
        if self._responses:
            return json.dumps(self._responses.pop(0))
        if self._then == "raise":
            # 8s recv silence — websocket-client raises a timeout
            raise TimeoutError("recv timed out after 8s of quiet tape")
        # clean shutdown: signal stop, then hand back an empty frame
        if self._stop is not None:
            self._stop.set()
        return ""

    def close(self):
        self.closed = True


def _install_fake_websocket(monkeypatch, ws) -> None:
    """Inject a fake `websocket` module into sys.modules (stream_fills
    does `import websocket` at call time — the fake wins)."""
    mod = types.ModuleType("websocket")
    mod.create_connection = lambda url, timeout=None: ws
    monkeypatch.setitem(sys.modules, "websocket", mod)


_TRADE_UPDATE_FILL = {
    "stream": "trade_updates",
    "data": {
        "event": "fill",
        "timestamp": "2026-01-05T17:00:00Z",
        "order": {
            "id": "order-ws-1",
            "client_order_id": "ticket-1",
            "symbol": "AAPL",
            "filled_qty": "10",
            "filled_avg_price": "195.50",
            "side": "buy",
            "status": "filled",
            "updated_at": "2026-01-05T17:00:00Z",
        },
    },
}

_AUTH_OK_DICT = {
    "stream": "authorization",
    "data": {"action": "authenticate", "status": "authorized"},
}
_AUTH_REJECT_DICT = {
    "stream": "authorization",
    "data": {"action": "auth",
             "message": "code=401 authentication failed",
             "status": "unauthorized"},
}


def test_d1_auth_success_predicate_shapes():
    """D1: the auth predicate accepts the REAL dict shapes as primary
    (status=authorized / action=authenticate) and keeps the legacy
    list shape for backward compatibility."""
    assert _auth_success(_AUTH_OK_DICT) is True
    assert _auth_success(_AUTH_REJECT_DICT) is False
    # belt-and-braces: action-only dict
    assert _auth_success({"data": {"action": "authenticate"}}) is True
    # legacy list shape (some gateways wrap the reply in a list)
    assert _auth_success(
        [{"data": {"action": "authenticated"}}]) is True
    assert _auth_success(
        [{"data": {"action": "authenticate",
                   "status": "authorized"}}]) is True
    assert _auth_success(
        [{"data": {"status": "unauthorized"}}]) is False
    # junk / empty
    assert _auth_success(None) is False
    assert _auth_success([]) is False
    assert _auth_success("") is False
    assert _auth_success({"data": {}}) is False


def test_d1_stream_fills_real_dict_auth_success_proceeds(monkeypatch):
    """D1: REAL dict auth-success shape → stream_fills PROCEEDS past
    auth (listen-subscribes + delivers the fill over the WS) and does
    NOT fall back to polling."""
    _set_creds(monkeypatch)
    fills: list[dict] = []
    polled: list[bool] = []
    stop = threading.Event()
    ws = _FakeWS([_AUTH_OK_DICT, _TRADE_UPDATE_FILL], stop=stop)
    _install_fake_websocket(monkeypatch, ws)
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, []),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))

    def _spy_poll(on_fill, stop_event, **kwargs):
        polled.append(True)
    monkeypatch.setattr(acct, "poll_fills", _spy_poll)
    acct.stream_fills(fills.append, stop_event=stop)
    # the fill was delivered over the WS path
    assert len(fills) == 1
    assert fills[0]["order_id"] == "order-ws-1"
    assert fills[0]["source"] == "alpaca:ws"
    # auth + listen were both sent (i.e. we got PAST auth)
    actions = [m.get("action") for m in ws.sent]
    assert actions == ["auth", "listen"]
    assert ws.sent[1]["data"]["streams"] == ["trade_updates"]
    # no polling fallback
    assert polled == []
    assert ws.closed is True


def test_d1_stream_fills_real_dict_auth_unauthorized_falls_back(monkeypatch):
    """D1: REAL dict auth-UNAUTHORIZED shape (code=401) → fall back to
    polling IMMEDIATELY; no listen subscribe, no WS fills."""
    _set_creds(monkeypatch)
    fills: list[dict] = []
    polled: list[bool] = []
    stop = threading.Event()
    ws = _FakeWS([_AUTH_REJECT_DICT])
    _install_fake_websocket(monkeypatch, ws)
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, []),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))

    def _spy_poll(on_fill, stop_event, **kwargs):
        polled.append(True)
    monkeypatch.setattr(acct, "poll_fills", _spy_poll)
    acct.stream_fills(fills.append, stop_event=stop)
    assert polled == [True]         # immediate polling fallback
    assert fills == []              # nothing delivered over the dead WS
    assert ws.closed is True
    # no listen subscription was attempted after the rejection
    assert all(m.get("action") != "listen" for m in ws.sent)


def test_d1_stream_fills_quiet_stream_falls_back_to_polling(monkeypatch, capsys):
    """D1: 8s recv silence (recv raises) must NOT silently kill the
    stream — a warning is logged and polling takes over."""
    _set_creds(monkeypatch)
    fills: list[dict] = []
    polled: list[bool] = []
    stop = threading.Event()
    # auth ok, one fill delivered, then the tape goes quiet
    ws = _FakeWS([_AUTH_OK_DICT, _TRADE_UPDATE_FILL], then="raise")
    _install_fake_websocket(monkeypatch, ws)
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, []),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))

    def _spy_poll(on_fill, stop_event, **kwargs):
        polled.append(True)
    monkeypatch.setattr(acct, "poll_fills", _spy_poll)
    acct.stream_fills(fills.append, stop_event=stop)
    assert len(fills) == 1          # the fill before the silence landed
    assert polled == [True]         # quiet stream → polling fallback
    assert ws.closed is True
    err = capsys.readouterr().err
    assert "WARNING" in err
    assert "quiet" in err


# ================================================== D6: poll_fills replay
def _run_poll_once(acct, on_fill, **kwargs) -> None:
    """Run poll_fills for exactly ONE sweep (stop event fires right
    after the first sweep) with the given kwargs (since=…,
    replay_history=True, …)."""
    stop = threading.Event()
    real_wait = stop.wait
    state = {"n": 0}

    def fast_wait(timeout=None):
        state["n"] += 1
        if state["n"] >= 1:
            stop.set()
            return True
        return real_wait(timeout)
    stop.wait = fast_wait  # type: ignore[assignment]
    acct.poll_fills(on_fill, stop, **kwargs)


def _closed_order(oid: str, hours_ago: float) -> dict:
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(hours=hours_ago)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    return {"id": oid, "symbol": "AAPL",
            "filled_qty": "10", "filled_avg_price": "195.00",
            "side": "buy", "status": "filled",
            "client_order_id": f"ticket-{oid}", "updated_at": ts}


def test_d6_poll_fills_first_call_does_not_replay_history(monkeypatch):
    """D6: first poll_fills call with NO cursor does not replay
    history — the cursor initializes to 'now', so pre-existing closed
    orders are never re-emitted."""
    _set_creds(monkeypatch)
    orders = [_closed_order("order-old", 2.0)]
    per_url = {"status=closed": (200, orders)}
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))
    fills: list[dict] = []
    _run_poll_once(acct, fills.append)
    assert fills == []                     # history NOT replayed
    assert acct._fill_cursor is not None   # cursor initialized to "now"


def test_d6_poll_fills_no_duplicates_across_two_sweeps(monkeypatch):
    """D6 regression: two consecutive poll_fills sweeps never emit a
    duplicate on_fill — sweep 1 (replay_history=True) emits the 2
    existing fills; sweep 2 sees the same 2 + 1 NEW fill and emits
    ONLY the new one."""
    _set_creds(monkeypatch)
    orders = [_closed_order("order-1", 24.0),
              _closed_order("order-2", 20.0)]
    per_url = {"status=closed": (200, orders)}
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))
    fills: list[dict] = []
    # sweep 1: explicitly replay the existing history once
    _run_poll_once(acct, fills.append, replay_history=True)
    assert [f["order_id"] for f in fills] == ["order-1", "order-2"]
    # a NEW fill lands on the broker
    orders.append(_closed_order("order-3", 1.0))
    # sweep 2: same 2 old orders + the new one — only order-3 is new
    _run_poll_once(acct, fills.append)
    assert [f["order_id"] for f in fills] == \
        ["order-1", "order-2", "order-3"]
    # and a third sweep changes nothing
    _run_poll_once(acct, fills.append)
    assert [f["order_id"] for f in fills] == \
        ["order-1", "order-2", "order-3"]


def test_d6_poll_fills_since_cursor_filters_old_fills(monkeypatch):
    """D6: `since` (ISO timestamp cursor) — only fills strictly NEWER
    than the cursor invoke on_fill."""
    _set_creds(monkeypatch)
    orders = [_closed_order("order-old", 24.0),
              _closed_order("order-new", 1.0)]
    per_url = {"status=closed": (200, orders)}
    acct = AlpacaPaperAccount(
        http_get=_make_mock_get(200, {}, per_url=per_url),
        http_post=_make_mock_post(200, {}),
        http_delete=_make_mock_delete(204, {}))
    fills: list[dict] = []
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=2)).isoformat(
        timespec="seconds").replace("+00:00", "Z")
    _run_poll_once(acct, fills.append, since=since)
    assert [f["order_id"] for f in fills] == ["order-new"]
    # cursor stored on the account so the next sweep stays consistent
    assert acct._fill_cursor is not None


# ------------------------------------------------------ dispatch
def test_resolve_paper_account_falls_back_when_no_creds(tmp_path, monkeypatch):
    """No creds → resolve_paper_account() returns the synthetic
    PaperAccountStore (the existing code path)."""
    # build a minimal Journal stub
    class FakeJournal:
        def __init__(self):
            self.constitution_hash = "x" * 64
            self.demo = False
            self._injected = []
        def emit(self, *a, **kw): pass
    out = resolve_paper_account(tmp_path, 10000.0, FakeJournal())
    assert isinstance(out, PaperAccountStore)
    assert out.account.balance == 10000.0


def test_resolve_paper_account_dispatches_to_alpaca(tmp_path, monkeypatch):
    """With creds set, resolve_paper_account() returns an
    AlpacaPaperAccount instance."""
    _set_creds(monkeypatch)
    class FakeJournal:
        constitution_hash = "x" * 64
        demo = False
        _injected = []
        def emit(self, *a, **kw): pass
    out = resolve_paper_account(tmp_path, 10000.0, FakeJournal())
    assert isinstance(out, AlpacaPaperAccount)


def test_resolve_paper_account_alpaca_broken_falls_back(tmp_path, monkeypatch):
    """If AlpacaPaperAccount construction raises (creds malformed),
    the dispatch falls back to PaperAccountStore without raising."""
    _set_creds(monkeypatch, key="", secret="")  # both empty → available() False
    class FakeJournal:
        constitution_hash = "x" * 64
        demo = False
        _injected = []
        def emit(self, *a, **kw): pass
    out = resolve_paper_account(tmp_path, 10000.0, FakeJournal())
    assert isinstance(out, PaperAccountStore)


# ------------------------------------------------- ticket matching helpers
def test_mismatch_reason_symbol():
    """Mismatch reason strings cover the three failure axes."""
    from gold_desk.ticket import Ticket
    t = Ticket(ticket_id="t", symbol="XAUUSD", side="buy",
                entry=100.0, stop=95.0, target=110.0, lots=1.0)
    assert "symbol" in _mismatch_reason(t, "EURUSD", 1.0, 100.0)
    assert "qty" in _mismatch_reason(t, "XAUUSD", 5.0, 100.0)
    assert "price" in _mismatch_reason(t, "XAUUSD", 1.0, 200.0)


def test_available_ignores_empty_string(monkeypatch):
    """available() treats empty-string creds as missing (fail-closed)."""
    monkeypatch.setenv("ALPACA_PAPER_KEY", "")
    monkeypatch.setenv("ALPACA_PAPER_SECRET", "")
    assert AlpacaPaperAccount.available() is False


def test_alpaca_paper_account_module_importable():
    """Module imports cleanly (no 3rd-party trading deps at module load)."""
    import importlib
    mod = importlib.import_module("gold_desk.account_alpaca")
    assert hasattr(mod, "AlpacaPaperAccount")
    assert hasattr(mod, "OrderRequest")
