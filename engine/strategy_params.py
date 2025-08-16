"""Mapping between mutation dictionaries and concrete strategy parameters."""
from __future__ import annotations

from typing import Any, Dict

DEFAULT_PARAMS: Dict[str, Any] = {
    "trade_size": 1.0,
    "tick_size": 0.1,
    "sell_ticks": 1,
    "universe": ["ETH/BTC", "LTC/BTC", "XRP/BTC"],
}


def map_mutations(mutations: Dict[str, Any] | None) -> Dict[str, Any]:
    """Translate raw mutation values into concrete strategy parameters.

    Parameters
    ----------
    mutations: dict or None
        Mutation values produced by the LLM. Unknown keys are ignored.
    """
    params = DEFAULT_PARAMS.copy()
    if not mutations:
        return params
    for key, value in mutations.items():
        if key in params:
            params[key] = value
    return params
