"""Parameter driven implementation of the original BTC strategy."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .strategy_params import Params
from .ob_utils import book_hash, compute_imbalance, compute_spread_ticks
from exchange_utils.exchange_meta import exchange_meta

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

    async def analyze_book(
        self, params: Params, symbol: str, book: Dict[str, Any], mode: str = "SIM"
    ) -> Optional[Dict[str, Any]]:
        """Evaluate order book and return buy order data if conditions met.

        Parameters
        ----------
        params:
            Strategy parameters controlling thresholds.
        symbol:
            Trading pair symbol.
        book:
            Order book snapshot obtained from :class:`MarketDataHub`.

        Returns
        -------
        dict or None
            Dictionary with order data and metrics or ``None`` if no trade
            should be attempted.
        """

        bids = book.get("bids", [])
        asks = book.get("asks", [])
        if not bids or not asks:
            return None

        bid_price, _ = bids[0]
        ask_price, _ = asks[0]
        imbalance_ratio = compute_imbalance(book)
        imbalance_pct = imbalance_ratio * 100.0
        if imbalance_pct < params.imbalance_buy_threshold_pct:
            return None

        info = await self.exchange.get_market(symbol)
        tick = float(info.get("price_increment", 1e-8))
        raw_amount = params.order_size_usd / ask_price if ask_price else 0.0
        if mode.upper() == "LIVE":
            try:
                ask_price, amount, filters = exchange_meta.round_price_qty(
                    symbol, ask_price, raw_amount
                )
            except ValueError:
                return None
            tick = float(filters.get("priceIncrement", tick))
        else:
            amount = raw_amount

        spread_ticks = compute_spread_ticks(book, tick)
        top3 = {"bids": bids[:3], "asks": asks[:3]}
        latency_ms = int((time.time() - book.get("ts", time.time())) * 1000)
        return {
            "symbol": symbol,
            "price": ask_price,
            "amount": amount,
            "tick_size": tick,
            "imbalance_pct": imbalance_pct,
            "spread_ticks": spread_ticks,
            "top3_depth": top3,
            "book_hash": book_hash(book),
            "latency_ms": latency_ms,
        }

    def build_sell_order(
        self, params: Params, buy_order: Dict[str, Any], mode: str = "SIM"
    ) -> Dict[str, Any]:
        """Return a sell order ``sell_k_ticks`` above the buy price."""

        tick = buy_order.get("tick_size", 0.0)
        price = buy_order["price"] + tick * params.sell_k_ticks
        amount = buy_order["amount"]
        if mode.upper() == "LIVE":
            price, amount, _ = exchange_meta.round_price_qty(
                buy_order["symbol"], price, amount
            )
        return {
            "symbol": buy_order["symbol"],
            "price": price,
            "amount": amount,
            "tick_size": tick,
        }

        tick = buy_order.get("tick_size", 0.0)
        price = buy_order["price"] + tick * params.sell_k_ticks
        return {"symbol": buy_order["symbol"], "price": price, "amount": buy_order["amount"], "tick_size": tick}
