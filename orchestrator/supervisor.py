"""Supervisor que coordina ciclos de testeos masivos."""
from __future__ import annotations

import asyncio
import random
import threading
import time
from datetime import datetime
from typing import Callable, Dict, List, Optional, Tuple

from llm import LLMClient

from .models import BotConfig, BotStats, SupervisorEvent
from .storage import InMemoryStorage


class Supervisor:
    """Orquesta ciclos de bots ejecutados en paralelo."""

    def __init__(self, storage: Optional[InMemoryStorage] = None) -> None:
        self.storage = storage or InMemoryStorage()
        self._callbacks: List[Callable[[SupervisorEvent], None]] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._num_bots = 10
        self._next_bot_id = 1
        self._current_generation: List[BotConfig] = []

    # ------------------------------------------------------------------
    # Streaming de eventos
    def stream_events(self, callback: Callable[[SupervisorEvent], None]) -> None:
        """Registra un callback que recibirá eventos del supervisor."""
        self._callbacks.append(callback)

    def _emit(
        self,
        level: str,
        scope: str,
        cycle: Optional[int],
        bot_id: Optional[int],
        message: str,
        payload: Optional[Dict[str, object]] = None,
    ) -> None:
        event = SupervisorEvent(
            ts=datetime.utcnow(),
            level=level,
            scope=scope,
            cycle=cycle,
            bot_id=bot_id,
            message=message,
            payload=payload,
        )
        self.storage.append_event(event)
        for cb in list(self._callbacks):
            try:
                cb(event)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def start_mass_tests(self, num_bots: int = 10) -> None:
        """Inicia el ciclo continuo de testeos en un hilo aparte."""
        if self._running:
            return
        self._num_bots = num_bots
        self._running = True
        # Generación inicial vacía -> se creará en el primer ciclo
        self._current_generation = []
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_mass_tests(self) -> None:
        """Detiene los ciclos de testeos."""
        self._running = False

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        cycle = 1
        while self._running:
            asyncio.run(self.run_cycle(cycle))
            stats = self.gather_results(cycle)
            winner_id, winner_cfg = self.pick_winner(cycle)
            self._emit(
                "INFO",
                "cycle",
                cycle,
                None,
                "cycle_winner",
                {"winner_id": winner_id},
            )
            self.spawn_next_generation_from_winner(winner_cfg)
            cycle += 1
        self._running = False

    # ------------------------------------------------------------------
    async def run_cycle(self, cycle: int) -> None:
        """Ejecuta un ciclo completo simulando bots."""
        # Generar bots si es la primera vez
        if not self._current_generation:
            variations: List[Dict[str, object]] = []
            if cycle == 1:
                try:
                    client = LLMClient()
                    variations = client.generate_initial_variations("")
                except Exception:
                    variations = []

            self._current_generation = []
            for i in range(self._num_bots):
                var = variations[i] if i < len(variations) else {"name": f"Bot-{self._next_bot_id + i}", "mutations": {}}
                cfg = BotConfig(
                    id=self._next_bot_id + i,
                    cycle=cycle,
                    name=str(var.get("name", f"Bot-{self._next_bot_id + i}")),
                    mutations=var.get("mutations", {}),
                    seed_parent=None,
                )
                self.storage.save_bot(cfg)
                self._current_generation.append(cfg)
            self._next_bot_id += self._num_bots
        else:
            # actualizar ciclo en configs existentes
            for cfg in self._current_generation:
                cfg.cycle = cycle
                self.storage.save_bot(cfg)

        self._emit("INFO", "cycle", cycle, None, "cycle_start", {})

        async def simulate_bot(cfg: BotConfig) -> None:
            self._emit("INFO", "bot", cycle, cfg.id, "bot_start", {})
            start = time.time()
            await asyncio.sleep(random.uniform(0.5, 1.5))
            orders = random.randint(10, 100)
            pnl = random.uniform(-10.0, 10.0)
            pnl_pct = random.uniform(-5.0, 5.0)
            runtime_s = int(time.time() - start)
            wins = random.randint(0, orders)
            losses = orders - wins
            stats = BotStats(
                bot_id=cfg.id,
                cycle=cycle,
                orders=orders,
                pnl=pnl,
                pnl_pct=pnl_pct,
                runtime_s=runtime_s,
                wins=wins,
                losses=losses,
            )
            self.storage.save_bot_stats(stats)
            self._emit(
                "INFO",
                "bot",
                cycle,
                cfg.id,
                "bot_finished",
                {"stats": stats.__dict__},
            )

        await asyncio.gather(*(simulate_bot(cfg) for cfg in self._current_generation))

    # ------------------------------------------------------------------
    def gather_results(self, cycle: int) -> List[BotStats]:
        """Obtiene las estadísticas de un ciclo."""
        return [s for s in self.storage.iter_stats() if s.cycle == cycle]

    def pick_winner(self, cycle: int) -> Tuple[int, BotConfig]:
        """Selecciona el bot con mayor PNL."""
        stats = self.gather_results(cycle)
        if not stats:
            raise ValueError("No hay estadísticas para seleccionar ganador")
        winner = max(stats, key=lambda s: s.pnl)
        cfg = self.storage.get_bot(winner.bot_id)
        if cfg is None:
            raise ValueError("Configuración de bot ganadora no encontrada")
        return winner.bot_id, cfg

    def spawn_next_generation_from_winner(self, winner_config: BotConfig) -> List[BotConfig]:
        """Genera nuevas configuraciones basadas en el ganador."""
        next_cycle = winner_config.cycle + 1
        new_generation: List[BotConfig] = []
        for _ in range(self._num_bots):
            bot_id = self._next_bot_id
            self._next_bot_id += 1
            cfg = BotConfig(
                id=bot_id,
                cycle=next_cycle,
                name=f"Bot-{bot_id}",
                mutations={"mut": random.random()},
                seed_parent=winner_config.name,
            )
            self.storage.save_bot(cfg)
            new_generation.append(cfg)
        self._current_generation = new_generation
        return new_generation
