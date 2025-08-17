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
from typing import Any, Dict, Tuple, Optional, List

from exchange_utils.exchange_meta import exchange_meta
from trading.order_lifecycle import OrderLifecycle


def place_limit(exchange: Any, symbol: str, side: str, price: float, qty: float) -> Dict[str, Any]:
    """Deprecated thin wrapper around :class:`trading.order_lifecycle.OrderLifecycle`.

    Prefer instantiating :class:`OrderLifecycle` and calling
    :meth:`OrderLifecycle.open_limit` directly.
    """

    ol = OrderLifecycle(exchange, mode="LIVE", default_qty=qty)
    return ol.open_limit(side, symbol, price)


def cancel_order(exchange: Any, symbol: str, order_id: str) -> Dict[str, Any]:
    """Deprecated thin wrapper around :class:`OrderLifecycle.cancel`."""

    ol = OrderLifecycle(exchange, mode="LIVE")
    return ol.cancel({"id": order_id, "symbol": symbol}) or {}


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


def parse_fills(
    order: Dict[str, Any]
) -> Tuple[float, float, float, Optional[str], List[Dict[str, float]]]:
    """Extract fill quantity, average price and detailed commissions.

    Returns
    -------
    tuple
        ``(filled_qty, avg_price, total_fee, fee_asset, fills)`` where ``fills``
        is a list of dicts each containing ``price``, ``qty``, ``fee`` and
        ``fee_asset`` entries parsed from the order payload.
    """

    filled = float(order.get("filled") or order.get("executedQty") or 0.0)
    avg = float(order.get("average") or order.get("price") or 0.0)
    commission = 0.0
    asset: Optional[str] = None
    fills_detail: List[Dict[str, float]] = []
    fills = order.get("trades") or order.get("fills") or []
    for f in fills:
        fee = float(f.get("commission") or f.get("fee") or f.get("cost") or 0.0)
        commission += fee
        asset = (
            f.get("commissionAsset")
            or f.get("asset")
            or f.get("currency")
            or asset
        )
        fills_detail.append(
            {
                "price": float(f.get("price") or f.get("rate") or f.get("info", {}).get("price") or 0.0),
                "qty": float(
                    f.get("qty")
                    or f.get("amount")
                    or f.get("quantity")
                    or f.get("info", {}).get("qty")
                    or 0.0
                ),
                "fee": fee,
                "fee_asset": asset,
            }
        )

    fee = order.get("fee")
    if isinstance(fee, dict):
        commission = float(fee.get("cost") or fee.get("commission") or commission)
        asset = fee.get("currency") or fee.get("asset") or asset
    return filled, avg, commission, asset, fills_detail
