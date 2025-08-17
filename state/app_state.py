"""Persistencia simple del estado de testeos masivos."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Any, Dict, Optional
import json
import os


@dataclass
class AppState:
    """Estado persistente para los testeos masivos."""
    current_cycle: int = 0
    next_bot_id: int = 1
    max_depth_symbols: int = 20
    depth_speed: str = "100ms"
    bots_per_cycle: int = 10
    mode: str = "SIM"
    winner_config: Optional[Dict[str, Any]] = None
    apis_verified: Dict[str, bool] = field(
        default_factory=lambda: {"binance": False, "llm": False, "codex": False}
    )
    _file: str = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._file = os.path.join(os.path.dirname(__file__), "state.json")

    def save(self) -> None:
        """Guarda el estado en ``state.json``."""
        data = asdict(self)
        data.pop("_file", None)
        with open(self._file, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls) -> "AppState":
        """Carga el estado desde disco si existe."""
        path = os.path.join(os.path.dirname(__file__), "state.json")
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            obj = cls(**data)
        except FileNotFoundError:
            obj = cls()
        obj._file = path
        return obj
