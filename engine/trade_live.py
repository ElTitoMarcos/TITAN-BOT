"""Helpers to place and track real orders when running in LIVE mode.

These functions are thin wrappers around a ccxt-style exchange client. They
apply Binance symbol filters using :mod:`exchange_utils.exchange_meta` before
issuing any request so price and quantity obey ``LOT_SIZE``, ``PRICE_FILTER``
and ``MIN_NOTIONAL`` constraints.

All functions are synchronous and side-effect free regarding the caller's
state. They should be executed in a thread when used from async code to avoid
blocking the event loop.
"""
from __future__ import annotations

import time
from typing import Any, Dict

from exchange_utils.exchange_meta import exchange_meta


def place_limit(exchange: Any, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
    """Place a limit order ensuring it complies with exchange filters.

    Parameters
    ----------
    exchange:
        ccxt-like client exposing ``create_order`` and ``fetch_order``.
    symbol:
        Trading pair symbol.
    side:
        ``"buy"`` or ``"sell"``.
    price:
        Desired price before rounding.
    qty:
        Desired quantity before rounding.

    Returns
    -------
    dict
        Order information as returned by the exchange after creation.

    Raises
    ------
    ValueError
        If the resulting notional is below ``minNotional`` or rounding
        results in zero quantity.
    """

    price, qty, _ = exchange_meta.round_price_qty(symbol, price, qty)
    if qty <= 0:
        raise ValueError("quantity rounded to zero")

    try:
        order = exchange.create_order(symbol, "limit", side, qty, price)
    except Exception as exc:  # pragma: no cover - transport errors hard to simulate
        # Binance uses error -1007 when order creation times out but the order
        # might have been accepted. In such case we cannot know the order id.
        code = getattr(exc, "code", None)
        if code == -1007:
            raise TimeoutError("order creation timeout") from exc
        raise
    return order


def cancel_order(exchange: Any, symbol: str, order_id: str) -> Dict[str, Any]:
    """Cancel an existing order."""

    return exchange.cancel_order(order_id, symbol)


def fetch_order_status(
    exchange: Any, symbol: str, order_id: str, timeout_s: float = 10.0
) -> Dict[str, Any]:
    """Poll the order status until it reaches a terminal state or timeout.

    Terminal states considered are ``FILLED``, ``PARTIALLY_FILLED``, ``NEW`` or
    ``REJECTED``. The function performs a simple exponential backoff between
    polls.
    """

    start = time.time()
    delay = 0.5
    last_exc: Exception | None = None
    while time.time() - start < timeout_s:
        try:
            order = exchange.fetch_order(order_id, symbol)
            status = str(order.get("status", "")).upper()
            if status in {"FILLED", "PARTIALLY_FILLED", "NEW", "REJECTED", "CANCELED"}:
                return order
        except Exception as exc:  # pragma: no cover - network issues
            last_exc = exc
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    if last_exc:
        raise last_exc
    raise TimeoutError("fetch_order_status timeout")
