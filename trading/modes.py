from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from simulation.fill_simulator import SimulatedFiller


@dataclass
class PartialFillEvent:
    """Event returned by :meth:`BaseModeFiller.tick` when new quantity is filled."""

    qty: float
    order: Dict[str, Any]
    executed: float | None = None
    remaining: float | None = None
    reason: str | None = None


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
    """Filler used for MASS backtests with probabilistic partial fills."""

    def __init__(
        self,
        exchange: Any,
        alpha: float = 0.6,
        beta: float = 0.9,
        base_latency: float = 0.25,
        overload_threshold: int = 5,
    ) -> None:
        super().__init__(exchange)
        self.sim = SimulatedFiller(
            alpha=alpha,
            beta=beta,
            base_latency=base_latency * 1000,
            overload_threshold=overload_threshold,
        )

    # ------------------------------------------------------------------
    def _snapshot(self, symbol: str, market_snapshot: Dict[str, Any]) -> Dict[str, Any]:
        if market_snapshot:
            return market_snapshot
        try:
            return self.exchange.fetch_order_book(symbol)
        except Exception:
            return {"bids": [], "asks": []}

    # ------------------------------------------------------------------
    def tick(
        self, order: Dict[str, Any], market_snapshot: Dict[str, Any]
    ) -> Optional[PartialFillEvent]:
        remaining = float(order.get("amount", 0.0)) - float(order.get("filled", 0.0))
        if remaining <= 0:
            order["status"] = "FILLED"
            return None

        snap = self._snapshot(order["symbol"], market_snapshot)
        event = self.sim.tick(order, snap)
        if not event:
            return None
        qty = event.executed
        remaining = event.remaining
        order["executedQty"] = order.get("filled", 0.0)
        if remaining <= 1e-9:
            order["status"] = "FILLED"
            side = str(order.get("side", "")).lower()
            if side == "buy" and not order.get("_chained_sell"):
                from exchange_utils.exchange_meta import exchange_meta
                opp_price = snap.get("asks", [[order["price"], 0]])[0][0]
                price, qty_r, _ = exchange_meta.round_price_qty(order["symbol"], opp_price, order["amount"])
                order["_chained_sell"] = {
                    "symbol": order["symbol"],
                    "side": "sell",
                    "price": price,
                    "amount": qty_r,
                    "status": "NEW",
                    "filled": 0.0,
                }
        else:
            order["status"] = "PARTIALLY_FILLED"

        return PartialFillEvent(
            qty=qty,
            order=order,
            executed=order["filled"],
            remaining=remaining,
            reason="sim_mass",
        )

    # ------------------------------------------------------------------
    def latency_s(self, pending_orders: int) -> float:
        return self.sim.latency_ms(pending_orders) / 1000.0


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
