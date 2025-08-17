import math
from engine.ob_utils import (
    try_fill_limit,
    compute_imbalance,
    compute_spread_ticks,
    book_hash,
)


BOOK = {
    "bids": [(100.0, 1.0), (99.0, 2.0)],
    "asks": [(101.0, 1.0), (102.0, 3.0)],
}


def test_try_fill_limit_partial():
    filled, vwap = try_fill_limit(BOOK, "buy", 101.0, 0.5)
    assert filled == 0.5
    assert vwap == 101.0


def test_try_fill_limit_multi_level():
    filled, vwap = try_fill_limit(BOOK, "buy", 102.0, 2.0)
    assert filled == 2.0
    assert math.isclose(vwap, 101.5)


def test_try_fill_limit_no_cross():
    filled, vwap = try_fill_limit(BOOK, "buy", 100.0, 1.0)
    assert filled == 0.0
    assert vwap is None


def test_compute_imbalance():
    ratio = compute_imbalance(BOOK)
    assert math.isclose(ratio, 1.0 / (1.0 + 1.0))


def test_compute_spread_ticks():
    ticks = compute_spread_ticks(BOOK, 1.0)
    assert ticks == 1.0


def test_book_hash_stable():
    h1 = book_hash(BOOK)
    h2 = book_hash({**BOOK, "extra": 1})
    assert h1 == h2
