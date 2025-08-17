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
from engine.ob_utils import estimate_fill_time, try_fill_limit
from exchange_utils.orderbook_service import MarketDataHub, market_data_hub
from data_logger import log_event


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
            symbols = await self.strategy.select_pairs(params, self.hub)
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

                # Estimate fill time before placing the buy order
                book_full = self.hub.get_order_book(sym, top=100)
                trade_rate = self.hub.get_trade_rate(sym, buy_price, "buy")
                est = (
                    estimate_fill_time(book_full, "buy", buy_price, amount, trade_rate)
                    if book_full
                    else None
                )
                if not est or est[1] > params.max_wait_s:
                    continue
                queue_qty, t_est = est
                buy_metrics = {
                    "expected_fill_time_s": t_est,
                    "queue_ahead_qty": queue_qty,
                    "trade_rate_qty_per_s": trade_rate,
                }

                # Log pair selection after preliminary checks pass
                log_event(
                    {
                        "event": "pair_selected",
                        "bot_id": self.config.id,
                        "cycle_id": self.config.cycle,
                        "symbol": sym,
                        "data": buy_data,
                    }
                )

                if self.mode == "LIVE":
                    try:
                        order = await self.strategy.submit_buy_live(
                            sym, buy_price, amount
                        )
                        res = await self.strategy.monitor_buy_live(
                            params,
                            sym,
                            order.get("id"),
                            buy_price,
                            amount,
                            tick,
                            self.hub,
                        )
                    except Exception:
                        res = None
                else:
                    if hasattr(self.strategy, "monitor_buy_sim"):
                        res = await self.strategy.monitor_buy_sim(
                            params, sym, buy_price, amount, tick, self.hub
                        )
                    else:
                        res = {
                            "filled_qty": amount,
                            "avg_price": buy_price,
                            "commission_paid": 0.0,
                            "commission_asset": None,
                            "cancel_replace_count": 0,
                            "monitor_events": [],
                            "actual_fill_time_s": t_est,
                        }
                if not res:
                    continue
                buy_vwap = res["avg_price"]
                amount = res["filled_qty"]
                buy_metrics.update(
                    {
                        "actual_fill_time_s": res.get("actual_fill_time_s"),
                        "monitor_events": res.get("monitor_events"),
                        "commission_paid": res.get("commission_paid"),
                        "commission_asset": res.get("commission_asset"),
                        "cancel_replace_count": res.get("cancel_replace_count"),
                    }
                )
                buy_metrics["slippage_ticks"] = int(
                    round((buy_vwap - buy_price) / tick)
                )

                log_event(
                    {
                        "event": "buy_order",
                        "bot_id": self.config.id,
                        "cycle_id": self.config.cycle,
                        "symbol": sym,
                        "price": buy_price,
                        "qty": amount,
                        "metrics": buy_metrics,
                    }
                )

                sell_order = self.strategy.build_sell_order(
                    params, {**buy_data, "price": buy_price, "amount": amount}, mode=self.mode
                )
                sell_price = sell_order["price"]

                # Estimate fill time for the sell order
                book2 = self.hub.get_order_book(sym, top=100)
                trade_rate2 = self.hub.get_trade_rate(sym, sell_price, "sell")
                est2 = (
                    estimate_fill_time(book2, "sell", sell_price, amount, trade_rate2)
                    if book2
                    else None
                )
                if not est2 or est2[1] > params.max_wait_s:
                    continue
                queue_qty2, t_est2 = est2
                sell_metrics = {
                    "expected_fill_time_s": t_est2,
                    "queue_ahead_qty": queue_qty2,
                    "trade_rate_qty_per_s": trade_rate2,
                }
                if self.mode == "LIVE":
                    try:
                        sorder = await self.strategy.submit_sell_live(
                            sym, sell_price, amount
                        )
                        res = await self.strategy.monitor_sell_live(
                            params,
                            sym,
                            sorder.get("id"),
                            sell_price,
                            amount,
                            tick,
                            self.hub,
                            buy_vwap + tick * params.commission_buffer_ticks,
                        )
                    except Exception:
                        res = None
                else:
                    if hasattr(self.strategy, "monitor_sell_sim"):
                        res = await self.strategy.monitor_sell_sim(
                            params,
                            sym,
                            sell_price,
                            amount,
                            tick,
                            self.hub,
                            buy_vwap + tick * params.commission_buffer_ticks,
                        )
                    else:
                        res = {
                            "filled_qty": amount,
                            "avg_price": sell_price,
                            "commission_paid": 0.0,
                            "commission_asset": None,
                            "cancel_replace_count": 0,
                            "monitor_events": [],
                            "actual_fill_time_s": t_est2,
                        }
                if not res:
                    continue
                sell_vwap = res["avg_price"]
                sell_metrics.update(
                    {
                        "actual_fill_time_s": res.get("actual_fill_time_s"),
                        "monitor_events": res.get("monitor_events"),
                        "commission_paid": res.get("commission_paid"),
                        "commission_asset": res.get("commission_asset"),
                        "cancel_replace_count": res.get("cancel_replace_count"),
                    }
                )
                sell_metrics["slippage_ticks"] = int(
                    round((sell_vwap - sell_price) / tick)
                )

                log_event(
                    {
                        "event": "sell_order",
                        "bot_id": self.config.id,
                        "cycle_id": self.config.cycle,
                        "symbol": sym,
                        "price": sell_price,
                        "qty": amount,
                        "metrics": sell_metrics,
                    }
                )

                hold_time = (
                    (buy_metrics or {}).get("actual_fill_time_s", 0.0)
                    + (sell_metrics or {}).get("actual_fill_time_s", 0.0)
                )
                cost = buy_vwap * amount + (buy_metrics.get("commission_paid") or 0.0)
                revenue = sell_vwap * amount - (sell_metrics.get("commission_paid") or 0.0)
                profit = revenue - cost
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
                    "fee_asset": buy_metrics.get("commission_asset"),
                    "fee_amount": buy_metrics.get("commission_paid"),
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
                    "cancel_replace_count": buy_metrics.get("cancel_replace_count", 0),
                    "time_in_force": None,
                    "hold_time_s": None,
                    "raw_json": json.dumps(
                        {
                            "price": buy_price,
                            "amount": amount,
                            "book_hash": buy_data.get("book_hash"),
                            **(buy_metrics or {}),
                        }
                    ),
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
                    "fee_asset": sell_metrics.get("commission_asset"),
                    "fee_amount": sell_metrics.get("commission_paid"),
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
                    "cancel_replace_count": sell_metrics.get("cancel_replace_count", 0),
                    "time_in_force": None,
                    "hold_time_s": hold_time,
                    "raw_json": json.dumps(
                        {
                            "price": sell_price,
                            "amount": amount,
                            "book_hash": buy_data.get("book_hash"),
                            **(sell_metrics or {}),
                        }
                    ),
                }

                self.storage.save_order(buy_record)
                self.storage.save_order(sell_record)
                log_event(
                    {
                        "event": "order_complete",
                        "bot_id": self.config.id,
                        "cycle_id": self.config.cycle,
                        "symbol": sym,
                        "profit": profit,
                        "hold_time_s": hold_time,
                    }
                )
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

