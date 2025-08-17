import asyncio
import gzip
import json
import time

from orchestrator.runner import BotRunner
from orchestrator.models import BotConfig
from orchestrator.storage import SQLiteStorage
from engine.strategy_params import Params


class DummyStrategy:
    async def select_pairs(self, params: Params, hub):
        return ["ETHUSDT"]

    async def analyze_book(self, params: Params, symbol: str, book, mode="SIM"):
        return {
            "symbol": symbol,
            "price": 100.0,
            "amount": 1.0,
            "tick_size": 0.1,
            "imbalance_pct": 10.0,
            "spread_ticks": 1.0,
            "top3_depth": {"bids": [(99.0, 1.0)], "asks": [(100.0, 1.0)]},
            "book_hash": "hash",
            "latency_ms": 0,
        }

    def build_sell_order(self, params: Params, buy_order, mode="SIM"):
        return {
            "symbol": buy_order["symbol"],
            "price": buy_order["price"] + params.sell_k_ticks * buy_order["tick_size"],
            "amount": buy_order["amount"],
            "tick_size": buy_order["tick_size"],
        }


class DummyHub:
    def subscribe_depth(self, symbol: str):
        pass

    def get_order_book(self, symbol: str, top: int = 100):
        return {"bids": [(99.0, 5.0)], "asks": [(100.0, 5.0)], "ts": time.time()}

    def get_trade_rate(self, symbol: str, price: float, side: str):
        return 1.0


async def _run_bot(tmp_db, log_file):
    storage = SQLiteStorage(db_path=tmp_db)
    cfg = BotConfig(id=1, cycle=1, name="bot", mutations={}, seed_parent=None)
    strategy = DummyStrategy()
    hub = DummyHub()
    limits = {"max_orders": 2, "max_scans": 1}
    runner = BotRunner(cfg, limits, exchange=None, strategy=strategy, storage=storage, hub=hub, mode="SIM")
    await runner.run()


def test_operation_logging(tmp_path, monkeypatch):
    log_file = tmp_path / "timeline.jsonl.gz"
    monkeypatch.setattr("data_logger._LOG_PATH", str(log_file))
    db = tmp_path / "t.db"
    asyncio.run(_run_bot(str(db), str(log_file)))

    with gzip.open(log_file, "rt", encoding="utf-8") as fh:
        events = [json.loads(line) for line in fh]

    types = [e["event"] for e in events]
    assert types == [
        "pair_selected",
        "buy_submitted",
        "buy_filled",
        "sell_submitted",
        "sell_filled",
        "order_complete",
        "bot_summary",
    ]
    for e in events:
        if e["event"] != "bot_summary":
            assert e["bot_id"] == 1
            assert e.get("symbol") == "ETHUSDT"

    storage = SQLiteStorage(db_path=str(db))
    db_events = storage.get_events()
    assert [ev.message for ev in db_events][-2:] == ["order_complete", "bot_summary"]
