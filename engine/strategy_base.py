"""Base trading strategy executing the original BTC method.

The strategy is purposely simple and parameter driven so that mutation
values can tweak its behaviour. All operations are expected to run on an
exchange object exposing ``get_order_book`` and order creation methods.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, List, Tuple


class StrategyBase:
    """Implements the minimal trading operations used by bots."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    async def select_pairs(self, params: Dict[str, Any]) -> List[str]:
        """Select tradeable symbols based on the original BTC method."""
        universe: Iterable[str] = params.get("universe", [])
        return [sym for sym in universe if sym.endswith("/BTC")]

    async def place_buy(self, params: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        """Place a buy order one tick above the best bid."""
        book = await self.exchange.get_order_book(symbol)
        price = book["best_bid"] + params.get("tick_size", 0.0)
        amount = params.get("trade_size", 0.0)
        return await self.exchange.create_limit_buy_order(symbol, amount, price)

    async def place_sell_plus_ticks(
        self, params: Dict[str, Any], symbol: str, buy_order: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Place a sell order a number of ticks above the buy price."""
        tick = params.get("tick_size", 0.0)
        price = buy_order["price"] + tick * params.get("sell_ticks", 1)
        amount = buy_order["amount"]
        return await self.exchange.create_limit_sell_order(symbol, amount, price)

    async def monitor_and_adjust(
        self,
        params: Dict[str, Any],
        orders: List[Tuple[Dict[str, Any], Dict[str, Any]]],
        order_book_provider: Any,
    ) -> List[Dict[str, Any]]:
        """Monitor orders until they are filled and compute PNL."""
        updates: List[Dict[str, Any]] = []
        for buy, sell in orders:
            # In mock mode orders are filled instantly. A real implementation
            # would poll ``order_book_provider`` and adjust orders here.
            await asyncio.sleep(0)
            pnl = (sell["price"] - buy["price"]) * buy["amount"]
            updates.append({"symbol": buy["symbol"], "pnl": pnl})
        return updates
