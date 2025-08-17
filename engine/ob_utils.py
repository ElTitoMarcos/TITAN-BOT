from __future__ import annotations

"""Order book utility helpers used by strategies.

All functions are *pure* and operate on the book snapshot provided by the
caller.  The book structure is expected to match the output of
``MarketDataHub.get_order_book`` and **must not be mutated** by these
helpers.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Dict, Iterable, List, Tuple, Optional


@dataclass(frozen=True)
class OrderBook:
    bids: List[Tuple[float, float]]
    asks: List[Tuple[float, float]]


def _get_levels(book: Dict[str, Iterable[Tuple[float, float]]], side: str) -> List[Tuple[float, float]]:
    return list(book.get(side, []))


def try_fill_limit(
    book: Dict[str, List[Tuple[float, float]]],
    side: str,
    price: float,
    qty: float,
) -> Tuple[float, Optional[float]]:
    """Simulate a limit order fill against ``book``.

    Parameters
    ----------
    book:
        Order book snapshot with ``bids`` and ``asks`` lists. Each entry is a
        tuple ``(price, quantity)``. The structure is **not mutated**.
    side:
        ``"buy"`` or ``"sell"``.
    price:
        Limit price of the order.
    qty:
        Desired quantity.

    Returns
    -------
    filled_qty, vwap
        ``filled_qty`` is the executed amount. ``vwap`` is the volume weighted
        average price of the fill or ``None`` if the order does not cross.
    """

    if qty <= 0:
        return 0.0, None

    side = side.lower()
    if side == "buy":
        levels = _get_levels(book, "asks")
        cmp = lambda p: p <= price
    elif side == "sell":
        levels = _get_levels(book, "bids")
        cmp = lambda p: p >= price
    else:
        raise ValueError("side must be 'buy' or 'sell'")

    filled = 0.0
    cost = 0.0
    for lvl_price, lvl_qty in levels:
        if not cmp(lvl_price) or filled >= qty:
            break
        take = min(qty - filled, lvl_qty)
        if take <= 0:
            break
        filled += take
        cost += take * lvl_price
    if filled == 0:
        return 0.0, None
    vwap = cost / filled
    return filled, vwap


def compute_imbalance(book: Dict[str, List[Tuple[float, float]]]) -> float:
    """Return top level bid/ask imbalance ratio.

    The ratio is ``bid_qty / (bid_qty + ask_qty)`` producing a value between
    0 and 1. Returns ``0.0`` if either side is empty.
    """

    bids = _get_levels(book, "bids")
    asks = _get_levels(book, "asks")
    if not bids or not asks:
        return 0.0
    bid_qty = bids[0][1]
    ask_qty = asks[0][1]
    total = bid_qty + ask_qty
    return bid_qty / total if total else 0.0


def compute_spread_ticks(book: Dict[str, List[Tuple[float, float]]], tick_size: float) -> float:
    """Return the spread expressed in ticks."""

    bids = _get_levels(book, "bids")
    asks = _get_levels(book, "asks")
    if not bids or not asks or tick_size <= 0:
        return 0.0
    spread = asks[0][0] - bids[0][0]
    return spread / tick_size if tick_size else 0.0


def book_hash(book: Dict[str, List[Tuple[float, float]]], depth: int = 5) -> str:
    """Stable hash of the top ``depth`` levels of ``book``.

    Used for traceability in simulations. Only the top ``depth`` levels of bids
    and asks are considered so deeper book changes do not alter the hash.
    """

    top = {
        "bids": _get_levels(book, "bids")[:depth],
        "asks": _get_levels(book, "asks")[:depth],
    }
    payload = json.dumps(top, sort_keys=True)
    return sha256(payload.encode()).hexdigest()


__all__ = [
    "try_fill_limit",
    "compute_imbalance",
    "compute_spread_ticks",
    "book_hash",
]
