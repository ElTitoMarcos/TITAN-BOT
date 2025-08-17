from __future__ import annotations

import time
from typing import Any, Callable, Dict, Optional

from data_logger import log_event
from exchange_utils.exchange_meta import exchange_meta
from .modes import get_mode_filler, BaseModeFiller


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
        mode: Optional[str | BaseModeFiller] = None,
    ) -> Dict[str, Any]:
        """Open a limit order.

        Parameters
        ----------
        side: ``"buy"`` or ``"sell"``.
        symbol: trading symbol.
        price: desired price before rounding.
        mode: execution mode overriding the instance level ``mode``.
        """

        filler = self._resolve_mode(mode)
        use_mode = self._mode_of(filler)
        if self.current_order and str(self.current_order.get("status", "")).upper() in {
            "NEW",
            "PARTIALLY_FILLED",
        }:
            return self.current_order

        qty = self.current_order.get("amount") if self.current_order else self.default_qty
        price, qty, _ = exchange_meta.round_price_qty(symbol, price, qty)
        if qty <= 0:
            raise ValueError("quantity rounded to zero")

        draft = {"symbol": symbol, "side": side, "price": price, "amount": qty}
        draft = filler.prepare_open(draft)

        if use_mode == "LIVE":
            try:
                order = self.exchange.create_order(symbol, "limit", side, draft["amount"], draft["price"])
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
                "price": draft["price"],
                "amount": draft["amount"],
                "status": "NEW",
                "filled": 0.0,
            }
        self.current_order = order
        log_event({"event": "order_opened", "symbol": symbol, "side": side, "price": price, "qty": qty})
        if self.on_order_opened:
            self.on_order_opened(order)
        return order

    # ------------------------------------------------------------------
    def start_monitoring(
        self,
        order: Dict[str, Any],
        mode: Optional[str | BaseModeFiller] = None,
        market_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Poll ``order`` until completion, delegating to mode specific fillers."""

        filler = self._resolve_mode(mode)
        symbol = order.get("symbol")
        order_id = order.get("id")
        last_qty = float(order.get("filled") or 0.0)
        while True:
            evt = filler.tick(order, market_snapshot or {})
            filled = float(order.get("filled") or 0.0)
            status = str(order.get("status", "")).upper()
            if evt or (filled > last_qty and status not in {"FILLED", "CANCELED"}):
                last_qty = filled
                log_event({"event": "order_partial", "symbol": symbol, "order_id": order_id, "qty": filled})
                if self.on_partial_fill:
                    self.on_partial_fill(order)
            if status == "FILLED":
                log_event({"event": "order_filled", "symbol": symbol, "order_id": order_id, "qty": filled})
                if self.on_filled:
                    self.on_filled(order)
                self.current_order = None
                return
            if status == "CANCELED":
                log_event({"event": "order_canceled", "symbol": symbol, "order_id": order_id})
                if self.on_canceled:
                    self.on_canceled(order)
                self.current_order = None
                return
            time.sleep(filler.latency_s(1))

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

    # ------------------------------------------------------------------
    def _resolve_mode(self, mode: Optional[str | BaseModeFiller]) -> BaseModeFiller:
        if hasattr(mode, "tick"):
            return mode  # type: ignore[return-value]
        use_mode = (mode or self.mode).upper()
        return get_mode_filler(use_mode, self.exchange)

    def _mode_of(self, filler: BaseModeFiller) -> str:
        return filler.__class__.__name__.replace("ModeFiller", "").upper()
