"""Asynchronous runner executing a single bot instance."""
from __future__ import annotations

import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import BotConfig, BotStats
from engine.strategy_base import StrategyBase
from engine.strategy_params import map_mutations_to_params, Params


class BotRunner:
    """Run a trading bot applying parameter mutations."""

    def __init__(
        self,
        config: BotConfig,
        limits: Dict[str, int],
        exchange: Any,
        strategy: StrategyBase,
        storage: Any,
        ui_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config
        self.limits = limits
        self.exchange = exchange
        self.strategy = strategy
        self.storage = storage
        self.ui_callback = ui_callback or (lambda _: None)

    async def run(self) -> BotStats:
        """Execute the bot respecting the provided limits."""
        params: Params = map_mutations_to_params(self.config.mutations)
        start = time.time()
        orders_count = 0
        wins = 0
        losses = 0
        pnl = 0.0

        symbols = await self.strategy.select_pairs(params)
        scans = 1
        if self.limits.get("max_scans") is not None and scans > self.limits["max_scans"]:
            raise RuntimeError("scan limit exceeded")

        open_orders: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
        for sym in symbols:
            if orders_count + 2 > self.limits.get("max_orders", float("inf")):
                break
            buy = await self.strategy.place_buy(params, sym)
            self.storage.save_order(
                {
                    "order_id": buy.get("id"),
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": buy.get("symbol", sym),
                    "side": "buy",
                    "qty": buy.get("amount"),
                    "price": buy.get("price"),
                    "ts": datetime.utcnow().isoformat(),
                    "status": "open",
                    "raw_json": json.dumps(buy),
                }
            )
            sell = await self.strategy.place_sell_plus_ticks(params, sym, buy)
            self.storage.save_order(
                {
                    "order_id": sell.get("id"),
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": sell.get("symbol", sym),
                    "side": "sell",
                    "qty": sell.get("amount"),
                    "price": sell.get("price"),
                    "ts": datetime.utcnow().isoformat(),
                    "status": "open",
                    "raw_json": json.dumps(sell),
                }
            )
            open_orders.append((buy, sell))
            orders_count += 2

        updates = await self.strategy.monitor_and_adjust(
            params, open_orders, self.exchange.get_order_book
        )
        for (buy, sell), upd in zip(open_orders, updates):
            pnl += upd.get("pnl", 0.0)
            if upd.get("pnl", 0.0) >= 0:
                wins += 1
            else:
                losses += 1
            for side, order in (("buy", buy), ("sell", sell)):
                data = {
                    "order_id": order.get("id"),
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": order.get("symbol"),
                    "side": side,
                    "qty": order.get("amount"),
                    "price": order.get("price"),
                    "ts": datetime.utcnow().isoformat(),
                    "status": "filled",
                    "pnl": upd.get("pnl") if side == "sell" else None,
                    "raw_json": json.dumps(order),
                }
                self.storage.save_order(data)
            self.ui_callback({"bot_id": self.config.id, **upd})

        runtime_s = int(time.time() - start)
        notional = params.order_size_usd * (orders_count / 2)
        pnl_pct = (pnl / notional * 100.0) if notional else 0.0

        stats = BotStats(
            bot_id=self.config.id,
            cycle=self.config.cycle,
            orders=orders_count,
            pnl=pnl,
            pnl_pct=pnl_pct,
            runtime_s=runtime_s,
            wins=wins,
            losses=losses,
        )
        self.storage.save_bot_stats(stats)
        return stats
