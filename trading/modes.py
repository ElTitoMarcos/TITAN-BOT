from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class PartialFillEvent:
    """Event returned by :meth:`BaseModeFiller.tick` when new quantity is filled."""

    qty: float
    order: Dict[str, Any]


@dataclass
class AdjustAction:
    """Action indicating how an order should be auto adjusted."""

    price: Optional[float] = None
    qty: Optional[float] = None


class BaseModeFiller:
    """Interface for mode specific order filling behaviour."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    def prepare_open(self, order: Dict[str, Any]) -> Dict[str, Any]:
        """Hook to adjust ``order`` before sending it to the exchange."""

        return order

    def tick(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[PartialFillEvent]:
        """Advance the state of ``order`` returning a ``PartialFillEvent`` if new
        quantity was filled."""

        raise NotImplementedError

    def latency_s(self, pending_orders: int) -> float:
        """Seconds to wait before next monitoring cycle."""

        return 0.0

    def should_autoadjust(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[AdjustAction]:
        """Return an :class:`AdjustAction` if the order should be adjusted."""

        return None


class MassModeFiller(BaseModeFiller):
    """Filler used for MASS backtests; fills orders instantly."""

    def tick(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[PartialFillEvent]:
        order["filled"] = order.get("amount", 0.0)
        order["status"] = "FILLED"
        return None


class SimModeFiller(BaseModeFiller):
    """Filler used for SIM tests; also fills instantly."""

    def tick(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[PartialFillEvent]:
        order["filled"] = order.get("amount", 0.0)
        order["status"] = "FILLED"
        return None


class LiveModeFiller(BaseModeFiller):
    """Filler that polls the exchange for real fills."""

    def tick(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[PartialFillEvent]:
        info = self.exchange.fetch_order(order["id"], order["symbol"])
        filled = float(info.get("filled") or info.get("executedQty") or 0.0)
        prev = float(order.get("filled") or 0.0)
        order.update(info)
        if filled > prev and filled < float(order.get("amount", 0.0)):
            order["status"] = "PARTIALLY_FILLED"
            return PartialFillEvent(qty=filled, order=order)
        return None

    def latency_s(self, pending_orders: int) -> float:
        return 1.0


def get_mode_filler(mode: str, exchange: Any) -> BaseModeFiller:
    """Factory returning the appropriate filler implementation for ``mode``."""

    mode = mode.upper()
    if mode == "MASS":
        return MassModeFiller(exchange)
    if mode == "SIM":
        return SimModeFiller(exchange)
    return LiveModeFiller(exchange)
