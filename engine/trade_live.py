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
from typing import Any, Dict, Tuple, Optional

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


def cancel_replace(
    exchange: Any,
    symbol: str,
    order_id: str,
    side: str,
    new_price: float,
    qty: float,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Cancel an order and place a new one with ``new_price``.

    Parameters
    ----------
    exchange: ccxt-like client
    symbol: trading pair
    order_id: id of the order to cancel
    side: "buy" or "sell"
    new_price: desired new price before rounding
    qty: quantity to use for the new order before rounding

    Returns
    -------
    tuple(dict, dict)
        Tuple of (cancel_result, new_order).
    """

    cancel_res = cancel_order(exchange, symbol, order_id)
    new_order = place_limit(exchange, symbol, side, new_price, qty)
    return cancel_res, new_order


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
            code = getattr(exc, "code", None)
            if code != -1007:
                last_exc = exc
        time.sleep(delay)
        delay = min(delay * 1.5, 2.0)
    if last_exc:
        raise last_exc
    raise TimeoutError("fetch_order_status timeout")


def parse_fills(order: Dict[str, Any]) -> Tuple[float, float, float, Optional[str]]:
    """Extract fill quantity, average price and commission from ``order``."""

    filled = float(order.get("filled") or order.get("executedQty") or 0.0)
    avg = float(order.get("average") or order.get("price") or 0.0)
    commission = 0.0
    asset: Optional[str] = None
    fills = order.get("trades") or order.get("fills") or []
    for f in fills:
        commission += float(f.get("commission") or f.get("fee") or f.get("cost") or 0.0)
        asset = f.get("commissionAsset") or f.get("asset") or f.get("currency") or asset
    fee = order.get("fee")
    if isinstance(fee, dict):
        commission = float(fee.get("cost") or fee.get("commission") or commission)
        asset = fee.get("currency") or fee.get("asset") or asset
    return filled, avg, commission, asset
