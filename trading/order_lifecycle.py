from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from data_logger import log_event
from exchange_utils.exchange_meta import exchange_meta


class OrderLifecycle:
    """Manage the lifecycle of a single limit order.

    The class is mode agnostic and exposes a small callback based API so the UI
    layer can react to events without this module knowing about the UI itself.

    Parameters
    ----------
    exchange:
        CCXT like exchange implementation.
    mode:
        ``"MASS"``, ``"SIM"`` or ``"LIVE"``. Only ``LIVE`` performs real
        network requests.
    default_qty:
        Quantity to use when opening orders if ``open_limit`` is invoked without
        a pre-existing order.

    Examples
    --------
    >>> class DummyExchange:
    ...     def create_order(self, symbol, type_, side, qty, price):
    ...         return {"id": "1", "symbol": symbol, "status": "NEW", "price": price, "amount": qty}
    ...     def fetch_order(self, order_id, symbol):
    ...         return {"id": order_id, "symbol": symbol, "status": "FILLED", "price": 100.0, "amount": 1.0, "filled": 1.0}
    ...     def cancel_order(self, order_id, symbol):
    ...         return {"id": order_id, "symbol": symbol, "status": "CANCELED"}
    >>> ex = DummyExchange()
    >>> ol = OrderLifecycle(ex, mode="SIM", default_qty=1.0)
    >>> events = []
    >>> ol.on_filled = lambda o: events.append(o["status"])
    >>> order = ol.open_limit("buy", "ETHUSDT", 100.0)
    >>> ol.start_monitoring(order)
    >>> events
    ['FILLED']
    """

    def __init__(
        self,
        exchange: Any,
        mode: str = "SIM",
        default_qty: float | None = None,
    ) -> None:
        self.exchange = exchange
        self.mode = mode.upper()
        self.default_qty = default_qty or 0.0
        self.current_order: Optional[Dict[str, Any]] = None
        # Optional callbacks injected by upper layers / UI
        self.on_order_opened: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_partial_fill: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_filled: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_canceled: Optional[Callable[[Dict[str, Any]], None]] = None

    # ------------------------------------------------------------------
    def open_limit(
        self,
        side: str,
        symbol: str,
        price: float,
        mode: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Open a limit order.

        Parameters
        ----------
        side: ``"buy"`` or ``"sell"``.
        symbol: trading symbol.
        price: desired price before rounding.
        mode: execution mode overriding the instance level ``mode``.
        """

        use_mode = (mode or self.mode).upper()
        if self.current_order and str(self.current_order.get("status", "")).upper() in {
            "NEW",
            "PARTIALLY_FILLED",
        }:
            return self.current_order

        qty = self.current_order.get("amount") if self.current_order else self.default_qty
        price, qty, _ = exchange_meta.round_price_qty(symbol, price, qty)
        if qty <= 0:
            raise ValueError("quantity rounded to zero")

        if use_mode == "LIVE":
            try:
                order = self.exchange.create_order(symbol, "limit", side, qty, price)
            except Exception as exc:
                code = getattr(exc, "code", None)
                if code == -1007:  # Binance timeout yet order may exist
                    raise TimeoutError("order creation timeout") from exc
                raise
        else:
            order = {
                "id": f"SIM-{int(time.time()*1000)}",
                "symbol": symbol,
                "side": side,
                "price": price,
                "amount": qty,
                "status": "NEW",
                "filled": 0.0,
            }
        self.current_order = order
        log_event({"event": "order_opened", "symbol": symbol, "side": side, "price": price, "qty": qty})
        if self.on_order_opened:
            self.on_order_opened(order)
        return order

    # ------------------------------------------------------------------
    def start_monitoring(self, order: Dict[str, Any], mode: Optional[str] = None) -> None:
        """Poll ``order`` until completion, firing callbacks on progress."""

        use_mode = (mode or self.mode).upper()
        symbol = order.get("symbol")
        order_id = order.get("id")
        if use_mode != "LIVE":
            order["status"] = "FILLED"
            order["filled"] = order.get("amount", self.default_qty)
            log_event({"event": "order_filled", "symbol": symbol, "order_id": order_id, "qty": order["filled"]})
            if self.on_filled:
                self.on_filled(order)
            self.current_order = None
            return

        filled_qty = 0.0
        while True:
            try:
                info = self.exchange.fetch_order(order_id, symbol)
            except Exception:
                time.sleep(1)
                continue
            fqty = float(info.get("filled") or info.get("executedQty") or 0.0)
            status = str(info.get("status", "")).upper()
            if fqty > filled_qty and fqty < info.get("amount", 0.0):
                filled_qty = fqty
                log_event({"event": "order_partial", "symbol": symbol, "order_id": order_id, "qty": fqty})
                if self.on_partial_fill:
                    self.on_partial_fill(info)
            if status == "FILLED":
                log_event({"event": "order_filled", "symbol": symbol, "order_id": order_id, "qty": info.get("filled", fqty)})
                if self.on_filled:
                    self.on_filled(info)
                self.current_order = None
                return
            if status == "CANCELED":
                log_event({"event": "order_canceled", "symbol": symbol, "order_id": order_id})
                if self.on_canceled:
                    self.on_canceled(info)
                self.current_order = None
                return
            time.sleep(1)

    # ------------------------------------------------------------------
    def cancel(self, order: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Cancel ``order`` in a best effort manner.

        Returns the cancellation payload from the exchange (if any).
        """

        symbol = order.get("symbol")
        order_id = order.get("id")
        result: Optional[Dict[str, Any]] = None
        try:
            if self.mode == "LIVE":
                result = self.exchange.cancel_order(order_id, symbol)
            order["status"] = "CANCELED"
            log_event({"event": "order_canceled", "symbol": symbol, "order_id": order_id})
            if self.on_canceled:
                self.on_canceled(order)
        finally:
            self.current_order = None
        return result
