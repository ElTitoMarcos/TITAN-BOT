"""Asynchronous runner executing a single bot instance."""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from .models import BotConfig, BotStats
from engine.strategy_base import StrategyBase
from engine.strategy_params import Params, map_mutations_to_params
from engine.ob_utils import try_fill_limit
from exchange_utils.orderbook_service import MarketDataHub, market_data_hub


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
        hub: MarketDataHub = market_data_hub,
        mode: str = "SIM",
    ) -> None:
        self.config = config
        self.limits = limits
        self.exchange = exchange
        self.strategy = strategy
        self.storage = storage
        self.ui_callback = ui_callback or (lambda _: None)
        self.hub = hub
        self.mode = mode.upper()

    async def run(self) -> BotStats:
        """Execute the bot respecting the provided limits."""
        params: Params = map_mutations_to_params(self.config.mutations)
        start = time.time()
        orders_count = 0
        wins = 0
        losses = 0
        pnl = 0.0

        max_orders = self.limits.get("max_orders", float("inf"))
        max_scans = self.limits.get("max_scans", 1)
        max_runtime = self.limits.get("max_runtime_s", float("inf"))

        scans = 0
        while (
            orders_count < max_orders
            and scans < max_scans
            and time.time() - start < max_runtime
        ):
            scans += 1
            symbols = await self.strategy.select_pairs(params)
            for sym in symbols:
                if (
                    orders_count >= max_orders
                    or time.time() - start >= max_runtime
                ):
                    break

                # ensure depth subscription
                self.hub.subscribe_depth(sym)

                book = self.hub.get_order_book(sym)
                if not book:
                    await asyncio.sleep(0.1)
                    continue

                buy_data = await self.strategy.analyze_book(
                    params, sym, book, mode=self.mode
                )
                if not buy_data:
                    continue

                amount = buy_data["amount"]
                tick = buy_data["tick_size"]
                buy_price = buy_data["price"]
                buy_start = time.time()
                buy_vwap = None
                buy_cancels = 0

                if self.mode == "LIVE":
                    from engine.trade_live import (
                        place_limit,
                        fetch_order_status,
                        cancel_order,
                    )

                    try:
                        order = await asyncio.to_thread(
                            place_limit, self.exchange, sym, "buy", buy_price, amount
                        )
                        oid = order.get("id")
                        order = await asyncio.to_thread(
                            fetch_order_status,
                            self.exchange,
                            sym,
                            oid,
                            params.max_wait_s,
                        )
                    except Exception:
                        if "oid" in locals():
                            try:
                                await asyncio.to_thread(cancel_order, self.exchange, sym, oid)
                            except Exception:
                                pass
                        continue
                    filled = float(order.get("filled") or order.get("executedQty") or 0.0)
                    buy_vwap = float(order.get("average") or order.get("price") or 0.0)
                    if filled < amount:
                        try:
                            await asyncio.to_thread(cancel_order, self.exchange, sym, oid)
                        except Exception:
                            pass
                        continue
                    buy_price = float(order.get("price", buy_price))
                else:
                    while time.time() - buy_start < params.max_wait_s:
                        b = self.hub.get_order_book(sym)
                        if b:
                            qty, vwap = try_fill_limit(b, "buy", buy_price, amount)
                            if qty >= amount and vwap is not None:
                                buy_vwap = vwap
                                break
                        buy_price += tick
                        buy_cancels += 1
                        await asyncio.sleep(0.1)

                    if buy_vwap is None:
                        continue

                sell_order = self.strategy.build_sell_order(
                    params, {**buy_data, "price": buy_price}, mode=self.mode
                )
                sell_price = sell_order["price"]
                sell_vwap = None
                sell_cancels = 0
                sell_start = time.time()

                if self.mode == "LIVE":
                    try:
                        sorder = await asyncio.to_thread(
                            place_limit, self.exchange, sym, "sell", sell_price, amount
                        )
                        soid = sorder.get("id")
                        sorder = await asyncio.to_thread(
                            fetch_order_status,
                            self.exchange,
                            sym,
                            soid,
                            params.max_wait_s,
                        )
                    except Exception:
                        if "soid" in locals():
                            try:
                                await asyncio.to_thread(
                                    cancel_order, self.exchange, sym, soid
                                )
                            except Exception:
                                pass
                        continue
                    filled = float(sorder.get("filled") or sorder.get("executedQty") or 0.0)
                    sell_vwap = float(sorder.get("average") or sorder.get("price") or 0.0)
                    if filled < amount:
                        try:
                            await asyncio.to_thread(
                                cancel_order, self.exchange, sym, soid
                            )
                        except Exception:
                            pass
                        continue
                    sell_price = float(sorder.get("price", sell_price))
                else:
                    while time.time() - sell_start < params.max_wait_s:
                        b2 = self.hub.get_order_book(sym)
                        if b2:
                            qty, vwap = try_fill_limit(b2, "sell", sell_price, amount)
                            if qty >= amount and vwap is not None:
                                sell_vwap = vwap
                                break
                        sell_price -= tick
                        sell_cancels += 1
                        await asyncio.sleep(0.1)

                    if sell_vwap is None:
                        continue

                hold_time = time.time() - buy_start
                profit = (sell_vwap - buy_vwap) * amount
                pnl += profit
                if profit >= 0:
                    wins += 1
                else:
                    losses += 1
                actual_ticks = int(round((sell_vwap - buy_vwap) / tick))
                expected_ticks = params.sell_k_ticks
                top3_json = (
                    json.dumps(buy_data.get("top3_depth"))
                    if buy_data.get("top3_depth")
                    else None
                )
                notional = buy_vwap * amount
                pnl_pct = (profit / notional * 100.0) if notional else 0.0

                buy_record = {
                    "order_id": f"{sym}-buy-{orders_count}",
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": sym,
                    "side": "buy",
                    "qty": amount,
                    "price": buy_price,
                    "resulting_fill_price": buy_vwap,
                    "fee_asset": None,
                    "fee_amount": None,
                    "ts": datetime.utcnow().isoformat(),
                    "status": "filled",
                    "pnl": None,
                    "pnl_pct": None,
                    "expected_profit_ticks": expected_ticks,
                    "actual_profit_ticks": None,
                    "spread_ticks": buy_data.get("spread_ticks"),
                    "imbalance_pct": buy_data.get("imbalance_pct"),
                    "top3_depth": top3_json,
                    "book_hash": buy_data.get("book_hash"),
                    "latency_ms": buy_data.get("latency_ms"),
                    "cancel_replace_count": buy_cancels,
                    "time_in_force": None,
                    "hold_time_s": None,
                    "raw_json": json.dumps({"price": buy_price, "amount": amount}),
                }

                sell_record = {
                    "order_id": f"{sym}-sell-{orders_count}",
                    "bot_id": self.config.id,
                    "cycle_id": self.config.cycle,
                    "symbol": sym,
                    "side": "sell",
                    "qty": amount,
                    "price": sell_price,
                    "resulting_fill_price": sell_vwap,
                    "fee_asset": None,
                    "fee_amount": None,
                    "ts": datetime.utcnow().isoformat(),
                    "status": "filled",
                    "pnl": profit,
                    "pnl_pct": pnl_pct,
                    "expected_profit_ticks": expected_ticks,
                    "actual_profit_ticks": actual_ticks,
                    "spread_ticks": buy_data.get("spread_ticks"),
                    "imbalance_pct": buy_data.get("imbalance_pct"),
                    "top3_depth": None,
                    "book_hash": None,
                    "latency_ms": None,
                    "cancel_replace_count": sell_cancels,
                    "time_in_force": None,
                    "hold_time_s": hold_time,
                    "raw_json": json.dumps({"price": sell_price, "amount": amount}),
                }

                self.storage.save_order(buy_record)
                self.storage.save_order(sell_record)
                self.ui_callback(
                    {
                        "bot_id": self.config.id,
                        "symbol": sym,
                        "pnl": profit,
                        "hold_time_s": hold_time,
                    }
                )
                orders_count += 2

            await asyncio.sleep(0)

        runtime_s = int(time.time() - start)
        notional_total = params.order_size_usd * (orders_count / 2)
        pnl_pct_total = (pnl / notional_total * 100.0) if notional_total else 0.0
        stats = BotStats(
            bot_id=self.config.id,
            cycle=self.config.cycle,
            orders=orders_count,
            pnl=pnl,
            pnl_pct=pnl_pct_total,
            runtime_s=runtime_s,
            wins=wins,
            losses=losses,
        )
        self.storage.save_bot_stats(stats)
        return stats

