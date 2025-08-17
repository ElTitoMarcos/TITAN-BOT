"""Parameter driven implementation of the original BTC strategy."""
from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from dataclasses import dataclass
from enum import Enum, auto
from .strategy_params import Params
from .ob_utils import book_hash, compute_imbalance, compute_spread_ticks, try_fill_limit
from exchange_utils.exchange_meta import exchange_meta
from exchange_utils.orderbook_service import MarketDataHub


class OrderLifecycle(Enum):
    """Lifecycle states for a trade."""

    SELECT_PAIR = auto()
    PREP_BUY = auto()
    SUBMIT_BUY = auto()
    MONITOR_BUY = auto()
    SUBMIT_SELL = auto()
    MONITOR_SELL = auto()
    DONE = auto()
    ABORT = auto()


@dataclass
class OrderOutcome:
    pnl: float = 0.0
    pnl_pct: float = 0.0
    slippage_ticks: int | None = None
    expected_fill_time_s: float | None = None
    actual_fill_time_s: float | None = None


class OrderLifecycle(Enum):
    """Lifecycle states for a trade."""

    SELECT_PAIR = auto()
    PREP_BUY = auto()
    SUBMIT_BUY = auto()
    MONITOR_BUY = auto()
    SUBMIT_SELL = auto()
    MONITOR_SELL = auto()
    DONE = auto()
    ABORT = auto()


@dataclass
class OrderOutcome:
    pnl: float = 0.0
    pnl_pct: float = 0.0
    slippage_ticks: int | None = None
    expected_fill_time_s: float | None = None
    actual_fill_time_s: float | None = None

class StrategyBase:
    """Execute the base BTC strategy under mutable parameters."""

    def __init__(self, exchange: Any) -> None:
        self.exchange = exchange

    async def select_pairs(self, params: Params, hub: MarketDataHub) -> List[str]:
        """Return BTC symbols that satisfy volume and fee constraints."""
        markets = await self.exchange.get_markets()
        candidates: List[Tuple[str, float, int, float, float]] = []
        for sym, info in markets.items():
            if not sym.endswith("BTC"):
                continue
            tick_data = hub.get_book_ticker(sym)
            if not tick_data:
                continue
            filters = exchange_meta.price_filters(sym)
            tick = float(filters.get("priceIncrement") or info.get("price_increment", 1e-8))
            last = (
                (tick_data.get("bid", 0.0) + tick_data.get("ask", 0.0)) / 2.0
                if tick_data.get("bid") and tick_data.get("ask")
                else tick_data.get("bid") or tick_data.get("ask") or 0.0
            )
            fees = float(info.get("maker", 0.001)) + float(info.get("taker", 0.001))
            fees_ticks = (last * fees) / tick if tick else 0.0
            if params.sell_k_ticks <= fees_ticks + params.commission_buffer_ticks:
                continue
            try:
                ticker = await self.exchange.get_ticker(sym)
            except Exception:
                continue
            vol_btc = float(
                ticker.get("quoteVolume")
                or ticker.get("base_volume")
                or ticker.get("baseVolume")
                or 0.0
            )
            if vol_btc < params.min_vol_btc_24h:
                continue
            spread_ticks = (
                int(round((tick_data.get("ask", 0.0) - tick_data.get("bid", 0.0)) / tick))
                if tick
                else 0
            )
            bid_qty = tick_data.get("bid_qty", 0.0)
            ask_qty = tick_data.get("ask_qty", 0.0)
            imbalance = (
                (bid_qty / (bid_qty + ask_qty) * 100.0)
                if (bid_qty + ask_qty) > 0
                else 50.0
            )
            if imbalance < params.imbalance_buy_threshold_pct:
                continue
            latency_ms = (time.time() - tick_data.get("ts", time.time())) * 1000.0
            candidates.append((sym, last, spread_ticks, imbalance, latency_ms))
        candidates.sort(key=lambda x: (x[1], x[2], -x[3], x[4]))
        return [s for s, *_ in candidates]

    async def prepare_buy(self, params: Params, symbol: str) -> Optional[Dict[str, Any]]:
        """Prepare buy order parameters for ``symbol``."""

        book = await self.exchange.get_order_book(symbol)
        if not book:
            return None
        return await self.analyze_book(params, symbol, book, mode="LIVE")

    async def submit_buy_live(self, symbol: str, price: float, qty: float) -> Dict[str, Any]:
        from engine.trade_live import place_limit

        return await asyncio.to_thread(place_limit, self.exchange, symbol, "buy", price, qty)

    async def simulate_buy(self, book: Dict[str, Any], price: float, qty: float) -> Optional[Tuple[float, float]]:
        filled, vwap = try_fill_limit(book, "buy", price, qty)
        if filled < qty:
            return None
        return filled, vwap

    async def monitor_buy_live(
        self, symbol: str, order_id: str, timeout_s: float
    ) -> Dict[str, Any]:
        from engine.trade_live import fetch_order_status

        return await asyncio.to_thread(
            fetch_order_status, self.exchange, symbol, order_id, timeout_s
        )

    async def monitor_buy_sim(self, expected_time_s: float) -> float:
        return expected_time_s

    async def submit_sell_live(self, symbol: str, price: float, qty: float) -> Dict[str, Any]:
        from engine.trade_live import place_limit

        return await asyncio.to_thread(place_limit, self.exchange, symbol, "sell", price, qty)

    async def simulate_sell(self, book: Dict[str, Any], price: float, qty: float) -> Optional[Tuple[float, float]]:
        filled, vwap = try_fill_limit(book, "sell", price, qty)
        if filled < qty:
            return None
        return filled, vwap

    async def monitor_sell_live(
        self, symbol: str, order_id: str, timeout_s: float
    ) -> Dict[str, Any]:
        from engine.trade_live import fetch_order_status

        return await asyncio.to_thread(
            fetch_order_status, self.exchange, symbol, order_id, timeout_s
        )

    async def monitor_sell_sim(self, expected_time_s: float) -> float:
        return expected_time_s

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

        # Enforce exchange minNotional on ``order_size_usd``
        filters = exchange_meta.get_symbol_filters(symbol)
        min_notional = float(filters.get("minNotional", 0.0))
        min_usd = 0.0
        if min_notional:
            quote = info.get("quote") or (symbol[-4:] if symbol.upper().endswith("USDT") else symbol[-3:])
            if hasattr(self.exchange, "_quote_to_usd"):
                try:
                    px = self.exchange._quote_to_usd(quote)
                    min_usd = min_notional * float(px)
                except Exception:
                    min_usd = 0.0
        effective_usd = max(params.order_size_usd, min_usd + params.min_notional_margin)

        raw_amount = effective_usd / ask_price if ask_price else 0.0
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
