from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional


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
        gamma: float = 0.7,
        base_latency: float = 0.25,
        overload_threshold: int = 5,
    ) -> None:
        super().__init__(exchange)
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.base_latency = base_latency
        self.overload_threshold = overload_threshold

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
        bids = snap.get("bids") or []
        asks = snap.get("asks") or []
        if not bids or not asks:
            return None

        side = str(order.get("side", "")).lower()
        tick_size = float(
            snap.get("tickSize")
            or snap.get("priceIncrement")
            or 1.0
        )

        if side == "buy":
            best_price, same_vol = bids[0][0], bids[0][1]
            opp_price, opp_vol = asks[0][0], asks[0][1]
        else:
            best_price, same_vol = asks[0][0], asks[0][1]
            opp_price, opp_vol = bids[0][0], bids[0][1]

        ticks_away = abs(float(order.get("price", 0.0)) - best_price) / tick_size
        imbalance_boost = opp_vol / (same_vol + 1e-9)
        p = self.alpha * math.exp(-self.beta * ticks_away) * imbalance_boost
        p = max(0.0, min(p, 0.85))
        if random.random() >= p:
            return None

        liquidity_ratio = min(1.0, opp_vol / (remaining + 1e-9))
        qty = self.gamma * remaining * liquidity_ratio
        qty = max(0.05 * remaining, min(qty, 0.35 * remaining))
        qty = min(qty, remaining)
        order["filled"] = float(order.get("filled", 0.0)) + qty
        remaining = float(order.get("amount", 0.0)) - float(order.get("filled", 0.0))
        order["executedQty"] = order["filled"]
        if remaining <= 1e-9:
            order["status"] = "FILLED"
            if side == "buy" and not order.get("_chained_sell"):
                from exchange_utils.exchange_meta import exchange_meta

                price, qty_r, _ = exchange_meta.round_price_qty(
                    order["symbol"], opp_price, order["amount"]
                )
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
        jitter = random.uniform(0.8, 1.3)
        overload = max(0, pending_orders - self.overload_threshold)
        return self.base_latency * jitter * (1 + 0.05 * overload)


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
