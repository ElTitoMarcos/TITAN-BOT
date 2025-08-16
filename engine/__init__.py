"""Trading engine package exposing strategy utilities and helpers."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .legacy import Engine
from .strategy_base import StrategyBase
from .strategy_params import map_mutations_to_params


def create_engine(
    exchange: Optional[Any] = None,
    config_overrides: Optional[Dict[str, Any]] = None,
    mutations: Optional[Dict[str, Any]] = None,
    on_order: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Engine:
    """Instantiate :class:`Engine` applying overrides and hooks."""
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
    params = map_mutations_to_params(mutations)
    overrides = {k: getattr(params, k) for k in ("order_size_usd",)}
    return create_engine(config_overrides=overrides, mutations=mutations)


__all__ = [
    "Engine",
    "StrategyBase",
    "map_mutations_to_params",
    "create_engine",
    "load_sim_config",
]
