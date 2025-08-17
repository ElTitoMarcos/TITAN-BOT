import pytest

from engine.trade_live import place_limit, fetch_order_status
from exchange_utils.exchange_meta import exchange_meta


class DummyExchange:
    def __init__(self):
        self.created = None
        self.status = {"status": "FILLED"}

    def create_order(self, symbol, type, side, amount, price):  # pragma: no cover - just invoked
        self.created = {
            "symbol": symbol,
            "type": type,
            "side": side,
            "amount": amount,
            "price": price,
            "id": "1",
            "status": "FILLED",
            "filled": amount,
            "average": price,
        }
        return self.created

    def fetch_order(self, order_id, symbol):
        return self.status

    def cancel_order(self, order_id, symbol):  # pragma: no cover - not used here
        return {"id": order_id, "status": "canceled"}


def test_place_limit_rounds(monkeypatch):
    filters = {"priceIncrement": 0.1, "stepSize": 0.01, "minNotional": 5}
    monkeypatch.setattr(exchange_meta, "get_symbol_filters", lambda s: filters)
    ex = DummyExchange()
    order = place_limit(ex, "BTC/USDT", "buy", 100.03, 0.123)
    assert order["price"] == 100.0
    assert order["amount"] == 0.12


def test_place_limit_min_notional(monkeypatch):
    filters = {"priceIncrement": 0.1, "stepSize": 0.01, "minNotional": 10}
    monkeypatch.setattr(exchange_meta, "get_symbol_filters", lambda s: filters)
    ex = DummyExchange()
    with pytest.raises(ValueError):
        place_limit(ex, "BTC/USDT", "buy", 100.0, 0.05)


def test_fetch_order_status(monkeypatch):
    class Exchange:
        def __init__(self):
            self.calls = 0

        def fetch_order(self, order_id, symbol):
            self.calls += 1
            status = "PENDING" if self.calls == 1 else "FILLED"
            return {"id": order_id, "status": status}

    ex = Exchange()
    order = fetch_order_status(ex, "BTC/USDT", "1", timeout_s=1.0)
    assert order["status"] == "FILLED"
