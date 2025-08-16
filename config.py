from dataclasses import dataclass, field
from typing import Dict, Any

@dataclass
class UIColors:
    GREEN80: str = "#16a34a"
    GREEN65: str = "#22c55e"
    AMBER50: str = "#f59e0b"
    GREY_LT: str = "#9ca3af"
    RED_VETO: str = "#ef4444"


@dataclass
class Defaults:
    # Fees y umbrales
    fee_per_side: float = 0.001  # 0.10%
    opportunity_threshold_percent: float = 0.2  # 2*fee por defecto

    # Tamaños por modo
    # Tamaño por defecto para operaciones simuladas
    size_usd_sim: float = 500.0
    size_usd_live: float = 50.0

    # LLM / engine
    llm_call_interval_ms: int = 120000
    llm_timeout_ms: int = 1500
    llm_max_actions_per_cycle: int = 6
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.15
    openai_api_key: str = ""

    # Selección / ranking
    weights: Dict[str, int] = field(default_factory=lambda: {
        "trend_w": 25,
        "trend_d": 20,
        "pressure": 15,
        "flow": 12,
        "trend_h": 10,
        "depth": 8,
        "trend_m": 5,
        "momentum": 3,
        "spread": 1,
        "microvol": 1,
    })
    pct_window: str = "1h"  # "24h"|"1h"|"5m"

    # Universo / otros
    universe_quote: str = "ALL"
    topN: int = 20  # pares que mostramos al LLM / UI
    log_dir: str = "./logs"
    initial_balance_usd: float = 10000.0


@dataclass
class AppState:
    live_confirmed: bool = False
    bot_enabled: bool = False
    pnl_intraday_percent: float = 0.0
    pnl_intraday_usd: float = 0.0
    balance_usd: float = 0.0
    balance_btc: float = 0.0
    latency_ws_ms: float = 0.0
    latency_rest_ms: float = 0.0

    def global_state_dict(self) -> Dict[str, Any]:
        return {
            "pnl_intraday_percent": self.pnl_intraday_percent,
            "pnl_intraday_usd": self.pnl_intraday_usd,
            "balance_usd": self.balance_usd,
            "balance_btc": self.balance_btc,
            "latency_ws_ms": self.latency_ws_ms,
            "latency_rest_ms": self.latency_rest_ms,
        }
