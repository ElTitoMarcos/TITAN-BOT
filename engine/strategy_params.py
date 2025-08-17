"""Mapping from mutation dictionaries to validated strategy parameters."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


@dataclass
class CancelReplaceRules:
    """Rules controlling order cancel/replace behaviour."""

    enable: bool = False
    max_moves: int = 0
    min_depth_ratio: float = 0.5


@dataclass
class RiskLimits:
    """Simple risk guard rails for the strategy."""

    max_open_orders: int = 1
    per_pair_exposure_usd: float = 100.0


@dataclass
class Params:
    """Concrete parameters consumed by :mod:`strategy_base`."""

    order_size_usd: float = 50.0
    min_notional_margin: float = 1.0
    buy_level_rule: str = "accum_bids"
    sell_k_ticks: int = 1
    max_wait_s: int = 30
    imbalance_buy_threshold_pct: float = 20.0
    cancel_replace_rules: CancelReplaceRules = field(default_factory=CancelReplaceRules)
    pair_ranking_window_s: int = 60
    min_vol_btc_24h: float = 10.0
    commission_buffer_ticks: int = 1
    risk_limits: RiskLimits = field(default_factory=RiskLimits)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def map_mutations_to_params(
    mutations: Dict[str, Any] | None, order_size_usd: float | None = None
) -> Params:
    """Translate mutation dictionaries into :class:`Params`.

    Unknown keys are ignored. Basic validation/clamping is applied so the
    resulting parameters are always sane for the strategy.
    """

    params = Params()
    if not mutations:
        return params

    if "order_size_usd" in mutations:
        try:
            params.order_size_usd = float(mutations["order_size_usd"])
        except (TypeError, ValueError):
            pass

    if "min_notional_margin" in mutations:
        try:
            params.min_notional_margin = float(mutations["min_notional_margin"])
        except (TypeError, ValueError):
            pass

    if mutations.get("sell_rule") == "+k_ticks":
        params.sell_k_ticks = int(mutations.get("k_ticks", params.sell_k_ticks))
        params.max_wait_s = int(mutations.get("max_wait_s", params.max_wait_s))

    if "imbalance_buy_threshold_pct" in mutations:
        pct = float(mutations["imbalance_buy_threshold_pct"])
        params.imbalance_buy_threshold_pct = _clamp(pct, 0.0, 100.0)

    if "pair_ranking_window_s" in mutations:
        params.pair_ranking_window_s = int(mutations["pair_ranking_window_s"])

    if "min_vol_btc_24h" in mutations:
        params.min_vol_btc_24h = float(mutations["min_vol_btc_24h"])

    if "commission_buffer_ticks" in mutations:
        params.commission_buffer_ticks = int(mutations["commission_buffer_ticks"])

    cr = mutations.get("cancel_replace_rules")
    if isinstance(cr, dict):
        params.cancel_replace_rules.enable = bool(cr.get("enable", params.cancel_replace_rules.enable))
        params.cancel_replace_rules.max_moves = int(cr.get("max_moves", params.cancel_replace_rules.max_moves))
        params.cancel_replace_rules.min_depth_ratio = float(
            cr.get("min_depth_ratio", params.cancel_replace_rules.min_depth_ratio)
        )

    rl = mutations.get("risk_limits")
    if isinstance(rl, dict):
        params.risk_limits.max_open_orders = int(
            rl.get("max_open_orders", params.risk_limits.max_open_orders)
        )
        params.risk_limits.per_pair_exposure_usd = float(
            rl.get("per_pair_exposure_usd", params.risk_limits.per_pair_exposure_usd)
        )

    if order_size_usd is not None:
        try:
            params.order_size_usd = float(order_size_usd)
        except (TypeError, ValueError):
            pass

    return params


__all__ = ["Params", "CancelReplaceRules", "RiskLimits", "map_mutations_to_params"]
