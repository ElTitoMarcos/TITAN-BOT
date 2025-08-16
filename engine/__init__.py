"""Trading engine package exposing strategy utilities and helpers."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .legacy import Engine
from .strategy_base import StrategyBase
from .strategy_params import map_mutations


def create_engine(
    exchange: Optional[Any] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
    mutations: Optional[Dict[str, Any]] = None,
    on_order: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Engine:
    """Instantiate :class:`Engine` applying overrides and hooks.

    Parameters
    ----------
    exchange: object, optional
        Exchange implementation to use. If ``None`` a default Binance
        exchange is created by :class:`Engine`.
    config_overrides: dict, optional
        Values to override in the engine configuration.
    mutations: dict, optional
        Strategy mutations to store in the engine instance for external
        introspection.
    on_order: callable, optional
        Callback invoked when the engine places or fills an order.
    """
    engine = Engine(ui_push_snapshot=lambda _: None, exchange=exchange)
    if config_overrides:
        for key, value in config_overrides.items():
            setattr(engine.cfg, key, value)
    engine.mutations = mutations or {}
    if on_order:
        engine.set_order_hook(on_order)
    return engine

def load_sim_config(mutations: Dict[str, Any]) -> Engine:
    """Create a SIM engine applying strategy mutations."""
    params = map_mutations(mutations)
    return create_engine(config_overrides=params, mutations=mutations)

__all__ = ["Engine", "StrategyBase", "map_mutations", "create_engine", "load_sim_config"]
