import os
import sys
import pytest

sys.path.append(os.path.dirname(os.path.dirname(__file__)))

from trading.modes import MassModeFiller


class DummyExchange:
    def fetch_order_book(self, symbol):
        return {
            "bids": [[99.0, 50.0]],
            "asks": [[101.0, 100.0]],
            "tickSize": 1.0,
        }


def test_mass_mode_partial_fill(monkeypatch):
    ex = DummyExchange()
    filler = MassModeFiller(ex, alpha=1.0, beta=0.0, gamma=0.5)
    order = {"symbol": "XYZ", "side": "buy", "price": 100.0, "amount": 1.0, "status": "NEW", "filled": 0.0}

    monkeypatch.setattr("trading.modes.random.random", lambda: 0.0)
    monkeypatch.setattr(
        "exchange_utils.exchange_meta.exchange_meta.round_price_qty",
        lambda s, p, q: (p, q, {}),
    )
    evt = filler.tick(order, {})
    assert evt is not None
    assert order["status"] == "PARTIALLY_FILLED"
    assert evt.qty == pytest.approx(order["filled"])
    assert evt.executed == pytest.approx(order["filled"])
    assert evt.remaining == pytest.approx(order["amount"] - order["filled"])
    assert evt.reason == "sim_mass"
    assert order["executedQty"] == order["filled"]

    # complete fill with further ticks
    while order["status"] != "FILLED":
        filler.tick(order, {})
    assert order["status"] == "FILLED"


def test_latency(monkeypatch):
    ex = DummyExchange()
    filler = MassModeFiller(ex, base_latency=1.0, overload_threshold=2)
    monkeypatch.setattr("trading.modes.random.uniform", lambda a, b: 1.0)
    assert filler.latency_s(1) == pytest.approx(1.0)
    assert filler.latency_s(5) == pytest.approx(1.0 * (1 + 0.05 * 3))
