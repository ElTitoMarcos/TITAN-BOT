import asyncio
import time
from engine.strategy_base import StrategyBase
from engine.strategy_params import Params
from exchange_utils.exchange_meta import exchange_meta


class DummyExchange:
    async def get_market(self, symbol):
        return {"price_increment": 0.1, "quote": "USDT"}

    async def get_markets(self):
        return {"ETHUSDT": {"price_increment": 0.1, "maker": 0.001, "taker": 0.001}}

    async def get_ticker(self, sym):
        return {"last": 100.0}

    def _quote_to_usd(self, q):
        return 1.0


async def _run_test():
    filters = {"priceIncrement": 0.1, "stepSize": 0.01, "minNotional": 10}
    orig = exchange_meta.get_symbol_filters
    exchange_meta.get_symbol_filters = lambda s: filters
    try:
        ex = DummyExchange()
        strat = StrategyBase(ex)
        params = Params(order_size_usd=5.0, min_notional_margin=1.0)
        book = {"bids": [(99, 1)], "asks": [(100, 1)], "ts": time.time()}
        data = await strat.analyze_book(params, "ETHUSDT", book)
    finally:
        exchange_meta.get_symbol_filters = orig
    return data


def test_analyze_book_enforces_min_notional():
    data = asyncio.run(_run_test())
    assert data is not None
    # amount should correspond to (minNotional + margin) / price = (10 + 1) / 100
    assert abs(data["amount"] - 0.11) < 1e-6
