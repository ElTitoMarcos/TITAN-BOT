"""Parameter driven implementation of the original BTC strategy."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Tuple

from .strategy_params import Params


class StrategyBase:
    """Execute the base BTC strategy under mutable parameters."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    async def select_pairs(self, params: Params) -> List[str]:
        """Return symbols that meet profitability and volume constraints."""
        markets = await self.exchange.get_markets()
        symbols: List[Tuple[str, float]] = []
        for sym, info in markets.items():
            if not sym.endswith("BTC"):
                continue
            tick = float(info.get("price_increment", 1e-8))
            fees = float(info.get("maker", 0.001)) + float(info.get("taker", 0.001))
            ticker = await self.exchange.get_ticker(sym)
            last = float(ticker.get("last", 0.0))
            vol = float(ticker.get("base_volume", 0.0))
            if vol < params.min_vol_btc_24h:
                continue
            fees_ticks = (last * fees) / tick if tick else 0.0
            if 1 <= fees_ticks + params.commission_buffer_ticks:
                continue
            symbols.append((sym, last))
        symbols.sort(key=lambda x: x[1])
        return [s for s, _ in symbols]

    async def place_buy(self, params: Params, symbol: str) -> Dict[str, Any]:
        """Place a buy on the best ask when bid imbalance is high."""
        book = await self.exchange.get_order_book(symbol)
        bids = book.get("bids", [])
        asks = book.get("asks", [])
        bid_price, bid_qty = bids[0]
        ask_price, ask_qty = asks[0]
        imbalance = bid_qty / (bid_qty + ask_qty) * 100 if (bid_qty + ask_qty) else 0
        if imbalance < params.imbalance_buy_threshold_pct:
            raise RuntimeError("bid imbalance too low")
        info = await self.exchange.get_market(symbol)
        tick = float(info.get("price_increment", 1e-8))
        amount = params.order_size_usd / ask_price
        spread_ticks = (ask_price - bid_price) / tick if tick else 0.0
        order = await self.exchange.create_limit_buy_order(symbol, amount, ask_price)
        order.update({"imbalance_pct": imbalance, "spread_ticks": spread_ticks, "tick_size": tick})
        return order

    async def place_sell_plus_ticks(self, params: Params, symbol: str, buy_order: Dict[str, Any]) -> Dict[str, Any]:
        """Sell ``sell_k_ticks`` above the buy price."""
        tick = buy_order.get("tick_size") or (await self.exchange.get_market(symbol)).get("price_increment", 1e-8)
        price = buy_order["price"] + tick * params.sell_k_ticks
        amount = buy_order["amount"]
        order = await self.exchange.create_limit_sell_order(symbol, amount, price)
        return order

    async def monitor_and_adjust(
        self,
        params: Params,
        orders: List[Tuple[Dict[str, Any], Dict[str, Any]]],
        order_book_provider: Any,
    ) -> List[Dict[str, Any]]:
        """Monitor until filled or timeout, returning PNL and metrics."""
        updates: List[Dict[str, Any]] = []
        start = time.time()
        for buy, sell in orders:
            await asyncio.sleep(0)  # simulation: instant fills
            hold = time.time() - start
            pnl = (sell["price"] - buy["price"]) * buy["amount"]
            notional = buy["price"] * buy["amount"]
            pnl_pct = (pnl / notional * 100.0) if notional else 0.0
            updates.append(
                {
                    "symbol": buy["symbol"],
                    "pnl": pnl,
                    "pnl_pct": pnl_pct,
                    "imbalance_pct": buy.get("imbalance_pct"),
                    "spread_ticks": buy.get("spread_ticks"),
                    "hold_time_s": hold,
                    "cancel_replace_count": 0,
                }
            )
        return updates

