import asyncio
import time
from orchestrator.supervisor import Supervisor
from orchestrator.models import BotConfig
from orchestrator.storage import SQLiteStorage
from engine.strategy_base import StrategyBase
from engine.strategy_params import map_mutations_to_params


class DummyExchange:
    async def get_market(self, symbol):
        return {"price_increment": 0.1, "quote": "USDT"}

    async def get_markets(self):  # pragma: no cover
        return {"ETHUSDT": {"price_increment": 0.1}}

    async def get_ticker(self, sym):  # pragma: no cover
        return {"last": 100.0}

    def _quote_to_usd(self, q):
        return 1.0


async def _place_one(storage, cfg, size, oid):
    sup = Supervisor(storage=storage)
    sup._current_generation = [cfg]
    sup.set_order_size_usd(size)
    params = map_mutations_to_params(cfg.mutations)
    strat = StrategyBase(DummyExchange())
    book = {"bids": [(99, 1)], "asks": [(100, 1)], "ts": time.time()}
    data = await strat.analyze_book(params, "ETHUSDT", book)
    storage.save_order(
        {
            "order_id": f"{cfg.id}-{oid}",
            "bot_id": cfg.id,
            "cycle_id": cfg.cycle,
            "symbol": "ETHUSDT",
            "side": "buy",
            "qty": data["amount"],
            "price": data["price"],
        }
    )


def test_order_size_persists(tmp_path):
    db = tmp_path / "t.db"
    storage = SQLiteStorage(db_path=str(db))
    cfg = BotConfig(id=1, cycle=1, name="bot", mutations={}, seed_parent=None)
    asyncio.run(_place_one(storage, cfg, 10.0, 1))
    asyncio.run(_place_one(storage, cfg, 25.0, 2))
    rows = storage.iter_orders(1, 1)
    assert len(rows) == 2
    notionals = [r["price"] * r["qty"] for r in rows]
    assert abs(notionals[0] - 10.0) < 1e-6
    assert abs(notionals[1] - 25.0) < 1e-6
