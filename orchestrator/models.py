from __future__ import annotations

"""Modelos de datos para el orquestador de testeos masivos."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional


@dataclass
class BotConfig:
    """Configuración de un bot en un ciclo de torneos."""

    id: int
    cycle: int
    name: str
    mutations: Dict[str, Any]
    seed_parent: Optional[str]


@dataclass
class BotStats:
    """Estadísticas resultantes de la ejecución de un bot."""

    bot_id: int
    cycle: int
    orders: int
    pnl: float
    pnl_pct: float
    runtime_s: int
    wins: int
    losses: int


@dataclass
class SupervisorEvent:
    """Evento emitido por el supervisor para consumo de la UI."""

    ts: datetime
    level: str
    scope: str
    cycle: Optional[int]
    bot_id: Optional[int]
    message: str
    payload: Optional[Dict[str, Any]]
