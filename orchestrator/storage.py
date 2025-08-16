"""Almacenamiento en memoria para el orquestador."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import BotConfig, BotStats, SupervisorEvent


class InMemoryStorage:
    """Persistencia simple utilizando estructuras en memoria."""

    def __init__(self) -> None:
        self._events: List[SupervisorEvent] = []
        self._bots: Dict[int, BotConfig] = {}
        self._bot_stats: Dict[int, BotStats] = {}
        self._cycle_summary: Dict[int, Dict[str, Any]] = {}

    # -- Eventos --
    def append_event(self, event: SupervisorEvent) -> None:
        self._events.append(event)

    def get_events(self) -> List[SupervisorEvent]:
        return list(self._events)

    # -- Bots --
    def save_bot(self, bot_config: BotConfig) -> None:
        self._bots[bot_config.id] = bot_config

    def get_bot(self, bot_id: int) -> Optional[BotConfig]:
        return self._bots.get(bot_id)

    # -- Stats --
    def save_bot_stats(self, stats: BotStats) -> None:
        self._bot_stats[stats.bot_id] = stats

    def get_bot_stats(self, bot_id: int) -> Optional[BotStats]:
        return self._bot_stats.get(bot_id)

    def iter_stats(self) -> List[BotStats]:
        return list(self._bot_stats.values())

    # -- Ciclos --
    def save_cycle_summary(self, cycle: int, summary: Dict[str, Any]) -> None:
        self._cycle_summary[cycle] = summary

    def get_cycle_summary(self, cycle: int) -> Optional[Dict[str, Any]]:
        return self._cycle_summary.get(cycle)
