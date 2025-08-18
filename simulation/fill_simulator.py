from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass
class FillEvent:
    executed: float
    remaining: float
    latency_ms: float


class SimulatedFiller:
    """Probabilistic partial fill simulator."""

    def __init__(
        self,
        alpha: float = 0.6,
        beta: float = 0.9,
        gamma: float = 1.0,
        base_latency: float = 250.0,
        overload_threshold: int = 5,
        rand: Optional[Any] = None,
    ) -> None:
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma
        self.base_latency = base_latency
        self.overload_threshold = overload_threshold
        # Allow dependency injection of the random module for testing
        self.random = rand or random

    def tick(self, order: Dict[str, Any], order_book: Dict[str, Any]) -> FillEvent | None:
        remaining = float(order.get("amount", 0.0)) - float(order.get("filled", 0.0))
        if remaining <= 0:
            return FillEvent(0.0, 0.0, 0.0)

        bids = order_book.get("bids") or []
        asks = order_book.get("asks") or []
        if not bids or not asks:
            return None

        side = str(order.get("side", "")).lower()
        tick_size = float(order_book.get("tickSize") or 1.0)
        if side == "buy":
            best_price, same_vol = bids[0][0], bids[0][1]
            opp_price, opp_vol = asks[0][0], asks[0][1]
        else:
            best_price, same_vol = asks[0][0], asks[0][1]
            opp_price, opp_vol = bids[0][0], bids[0][1]

        ticks_away = abs(float(order.get("price", 0.0)) - best_price) / tick_size
        imbalance_boost = opp_vol / (same_vol + 1e-9)
        p = self.gamma * self.alpha * math.exp(-self.beta * ticks_away) * imbalance_boost
        p = max(0.0, min(p, 0.85))
        if self.random.random() >= p:
            return None

        liquidity_ratio = min(1.0, opp_vol / (remaining + 1e-9))
        qty = self.random.uniform(0.05, 0.35) * remaining * liquidity_ratio * self.gamma
        qty = max(0.0, min(qty, remaining))
        order["filled"] = float(order.get("filled", 0.0)) + qty
        remaining = float(order.get("amount", 0.0)) - float(order.get("filled", 0.0))
        return FillEvent(executed=qty, remaining=remaining, latency_ms=self.latency_ms(1))

    def latency_ms(self, pending_orders: int) -> float:
        jitter = self.random.uniform(0.8, 1.3)
        overload = max(0, pending_orders - self.overload_threshold)
        return self.base_latency * jitter * (1 + 0.05 * overload)
