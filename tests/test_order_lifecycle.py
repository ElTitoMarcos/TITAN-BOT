import os
import sys

import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from trading.order_lifecycle import OrderLifecycle


class DummyExchange:
    def __init__(self):
        self.orders = {}

    def create_order(self, symbol, type_, side, qty, price):
        order_id = f"{side}-{len(self.orders)}"
        order = {
            "id": order_id,
            "symbol": symbol,
            "status": "NEW",
            "price": price,
            "amount": qty,
            "filled": 0.0,
        }
        self.orders[order_id] = order
        return order

    def fetch_order(self, order_id, symbol):
        return self.orders[order_id]

    def cancel_order(self, order_id, symbol):
        self.orders[order_id]["status"] = "CANCELED"
        return self.orders[order_id]


def test_sim_open_and_fill(monkeypatch):
    events = []
    monkeypatch.setattr("trading.order_lifecycle.log_event", lambda e: events.append(e))
    monkeypatch.setattr(
        "trading.order_lifecycle.exchange_meta.round_price_qty",
        lambda symbol, price, qty: (price, qty, {}),
    )
    ex = DummyExchange()
    ol = OrderLifecycle(ex, mode="SIM", default_qty=1.0)
    opened = []
    ol.on_order_opened = lambda o: opened.append(o["status"])
    filled = []
    ol.on_filled = lambda o: filled.append(o["status"])
    order = ol.open_limit("buy", "ETHUSDT", 100.0)
    ol.start_monitoring(order)
    assert opened == ["NEW"]
    assert filled == ["FILLED"]
    assert events[0]["event"] == "order_opened"
    assert events[1]["event"] == "order_filled"
