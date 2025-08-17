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
            expected_ticks = params.sell_k_ticks

            self.storage.save_order(
                {
                    "order_id": buy.get("id"),
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": buy.get("symbol", sym),
                    "side": "buy",
                    "qty": buy.get("amount"),
                    "price": buy.get("price"),
                    "resulting_fill_price": None,
                    "fee_asset": None,
                    "fee_amount": None,
                    "ts": datetime.utcnow().isoformat(),
                    "status": "open",
                    "pnl": None,
                    "pnl_pct": None,
                    "expected_profit_ticks": expected_ticks,
                    "actual_profit_ticks": None,
                    "spread_ticks": buy.get("spread_ticks"),
                    "imbalance_pct": buy.get("imbalance_pct"),
                    "top3_depth": json.dumps(buy.get("top3_depth")) if buy.get("top3_depth") else None,
                    "book_hash": buy.get("book_hash"),
                    "latency_ms": buy.get("latency_ms"),
                    "cancel_replace_count": 0,
                    "time_in_force": buy.get("time_in_force"),
                    "hold_time_s": None,
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
                    "resulting_fill_price": None,
                    "fee_asset": None,
                    "fee_amount": None,
                    "ts": datetime.utcnow().isoformat(),
                    "status": "open",
                    "pnl": None,
                    "pnl_pct": None,
                    "expected_profit_ticks": expected_ticks,
                    "actual_profit_ticks": None,
                    "spread_ticks": buy.get("spread_ticks"),
                    "imbalance_pct": buy.get("imbalance_pct"),
                    "top3_depth": None,
                    "book_hash": None,
                    "latency_ms": None,
                    "cancel_replace_count": 0,
                    "time_in_force": sell.get("time_in_force"),
                    "hold_time_s": None,
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
            tick = buy.get("tick_size") or 1
            expected_ticks = params.sell_k_ticks
            actual_ticks = int(round((sell["price"] - buy["price"]) / tick))
            
            for side, order in (("buy", buy), ("sell", sell)):
                data = {
                    "order_id": order.get("id"),
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": order.get("symbol"),
                    "side": side,
                    "qty": order.get("amount"),
                    "price": order.get("price"),
                    "resulting_fill_price": order.get("price"),
                    "fee_asset": (order.get("fee") or {}).get("currency"),
                    "fee_amount": (order.get("fee") or {}).get("cost"),
                    "ts": datetime.utcnow().isoformat(),
                    "status": "filled",
                    "pnl": upd.get("pnl") if side == "sell" else None,
                    "pnl_pct": upd.get("pnl_pct") if side == "sell" else None,
                    "expected_profit_ticks": expected_ticks,
                    "actual_profit_ticks": actual_ticks if side == "sell" else None,
                    "spread_ticks": upd.get("spread_ticks"),
                    "imbalance_pct": upd.get("imbalance_pct"),
                    "top3_depth": json.dumps(upd.get("top3_depth")) if upd.get("top3_depth") else None,
                    "book_hash": upd.get("book_hash"),
                    "latency_ms": upd.get("latency_ms"),
                    "cancel_replace_count": upd.get("cancel_replace_count"),
                    "time_in_force": order.get("time_in_force"),
                    "hold_time_s": upd.get("hold_time_s"),

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
