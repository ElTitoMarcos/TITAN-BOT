"""Supervisor que coordina ciclos de testeos masivos."""
from __future__ import annotations

import asyncio
import csv
import json
import hashlib
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from llm import LLMClient
from .models import BotConfig, BotStats, SupervisorEvent
from .storage import SQLiteStorage
from state.app_state import AppState
import exchange_utils.orderbook_service as ob_service
import exchange_utils.exchange_meta as exchange_meta_mod
from exchange_utils.orderbook_service import MarketDataHub
from exchange_utils.exchange_meta import ExchangeMeta

POPULAR_SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "BNBUSDT",
    "XRPUSDT",
    "SOLUSDT",
    "ADAUSDT",
    "DOGEUSDT",
    "MATICUSDT",
    "LTCUSDT",
    "TRXUSDT",
]

class Supervisor:
    """Orquesta ciclos de bots ejecutados en paralelo."""

    def __init__(
        self,
        storage: Optional[SQLiteStorage] = None,
        app_state: Optional[AppState] = None,
        llm_client: Optional[LLMClient] = None,
        mode: str = "SIM",
        min_orders: int = 50,
    ) -> None:
        """Crea el supervisor.

        Parameters
        ----------
        storage:
            Manejador de persistencia. Si no se provee se crea uno nuevo.
        app_state:
            Estado global persistido entre ejecuciones.
        llm_client:
            Cliente LLM utilizado para prompts de generación y evaluación.
        """

        self.storage = storage or SQLiteStorage()
        self.state = app_state or AppState.load()
        self.llm = llm_client or LLMClient()
        self.mode = mode.upper()
        self._callbacks: List[Callable[[SupervisorEvent], None]] = []
        self.mass_tests_enabled = False
        self._thread: Optional[threading.Thread] = None
        self._num_bots = self.state.bots_per_cycle
        self._next_bot_id = self.state.next_bot_id
        self._current_generation: List[BotConfig] = []
        self.hub: Optional[MarketDataHub] = None
        self.exchange_meta: Optional[ExchangeMeta] = None
        self._last_symbols: set[str] = set()
        self.min_orders_per_bot = int(min_orders)

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

    def set_min_orders(self, num: int) -> None:
        """Configura el mínimo de órdenes requerido por bot."""
        self.min_orders_per_bot = int(num)

    # ------------------------------------------------------------------
    def start_mass_tests(self, num_bots: int = 10) -> None:
        """Inicia el ciclo continuo de testeos en un hilo aparte."""
        if self.mass_tests_enabled:
            return
        if not (
            self.state.apis_verified.get("binance")
            and self.state.apis_verified.get("llm")
        ):
            self._emit("ERROR", "auth", None, None, "apis_not_verified", {})
            return
        self._num_bots = num_bots
        if not self.hub:
            try:
                ob_service.market_data_hub.close()
            except Exception:
                pass
            self.hub = ob_service.MarketDataHub(self.state.max_depth_symbols)
            ob_service.market_data_hub = self.hub
            self.exchange_meta = exchange_meta_mod.ExchangeMeta()
            exchange_meta_mod.exchange_meta = self.exchange_meta
        else:
            self.hub._sub_mgr.max_depth = self.state.max_depth_symbols
        self.mass_tests_enabled = True
        # Generación inicial vacía -> se creará en el primer ciclo
        self._current_generation = []
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop_mass_tests(self) -> None:
        """Detiene los ciclos de testeos."""
        self.mass_tests_enabled = False
        if self.hub:
            try:
                self.hub.close()
            except Exception:
                pass
            self.hub = None

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        """Bucle principal ejecutado en un hilo aparte."""

        while self.mass_tests_enabled:
            cycle = self.state.current_cycle + 1
            asyncio.run(self.run_cycle(cycle))
            stats = self.gather_results(cycle)
            if not stats:
                self._emit("ERROR", "cycle", cycle, None, "no_stats", {})
                self.state.current_cycle = cycle
                self.state.next_bot_id = self._next_bot_id
                self.state.save()
                continue

            cycle_summary = self._compose_cycle_summary(cycle, stats)
            self._emit(
                "INFO", "llm", cycle, None, "llm_request", {"summary": cycle_summary}
            )
            try:
                decision = self.llm.analyze_cycle_and_pick_winner(cycle_summary)
                self._emit("INFO", "llm", cycle, None, "llm_response", decision)

                winner_id = int(decision.get("winner_bot_id", -1))
                winner_reason = str(decision.get("reason", ""))
                winner_cfg = self.storage.get_bot(winner_id)
                if winner_cfg is None:
                    raise ValueError("winner cfg not found")
            except Exception as exc:
                self._emit(
                    "ERROR", "llm", cycle, None, "llm_error", {"error": str(exc)}
                )
                try:
                    winner_id, winner_cfg = self.pick_winner(cycle)
                    winner_reason = "max_pnl"
                except ValueError as err:
                    self._emit(
                        "ERROR",
                        "cycle",
                        cycle,
                        None,
                        "winner_selection_failed",
                        {"error": str(err)},
                    )
                    self.state.current_cycle = cycle
                    self.state.next_bot_id = self._next_bot_id
                    self.state.save()
                    continue
            total_pnl = sum(s.pnl for s in stats)
            cycle_summary["winner_bot_id"] = winner_id
            cycle_summary["winner_reason"] = winner_reason

            self._emit(
                "INFO",
                "cycle",
                cycle,
                None,
                "cycle_winner",
                {"winner_id": winner_id, "reason": winner_reason},
            )
            self._emit("INFO", "bot", cycle, winner_id, "bot_winner", {"reason": winner_reason})
            finished_at = datetime.utcnow().isoformat()
            self.storage.save_cycle_summary(
                cycle,
                {
                    "finished_at": finished_at,
                    "winner_bot_id": winner_id,
                    "winner_reason": winner_reason,
                },
            )
            self.export_report(cycle, cycle_summary)
            self._emit(
                "INFO",
                "cycle",
                cycle,
                None,
                "cycle_finished",
                {
                    "total_pnl": total_pnl,
                    "winner_id": winner_id,
                    "winner_reason": winner_reason,
                    "finished_at": finished_at,
                },

            )
            self.spawn_next_generation_from_winner(winner_cfg)
            self.state.current_cycle = cycle
            self.state.next_bot_id = self._next_bot_id
            self.state.save()
        self.mass_tests_enabled = False

    # ------------------------------------------------------------------
    async def run_cycle(self, cycle: int) -> None:
        """Ejecuta un ciclo completo simulando bots."""
        # Persist start of cycle
        self.storage.save_cycle_summary(cycle, {"started_at": datetime.utcnow().isoformat()})
        if self.hub is None:
            self._emit("ERROR", "cycle", cycle, None, "hub_not_initialized", {})
            return
        self.hub._sub_mgr.max_depth = self.state.max_depth_symbols
        symbols = self._prepare_candidate_symbols()
        for sym in symbols:
            self.hub.subscribe_depth(sym, self.state.depth_speed)
        for sym in self._last_symbols - set(symbols):
            self.hub.unsubscribe_depth(sym)
        self._last_symbols = set(symbols)

        # Generar bots si es la primera vez
        if not self._current_generation:
            variations: List[Dict[str, object]] = []
            if cycle == 1:
                try:
                    variations = self.llm.generate_initial_variations("")
                except Exception:
                    variations = []
            else:
                # generar a partir del ganador del ciclo previo
                try:
                    prev_winner_id = self.storage.get_cycle_winner(cycle - 1)
                    winner_cfg = self.storage.get_bot(prev_winner_id) if prev_winner_id else None
                    history = [self._fingerprint(b.mutations) for b in self.storage.iter_bots()]
                    if winner_cfg:
                        variations = self.llm.new_generation_from_winner(
                            winner_cfg.mutations, history
                        )
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
            """Simula un bot de manera asíncrona emitiendo progreso."""

            self._emit("INFO", "bot", cycle, cfg.id, "bot_start", {})
            start = time.time()
            target_orders = max(self.min_orders_per_bot, random.randint(10, 100))
            total_pnl = random.uniform(-10.0, 10.0)
            steps = max(1, target_orders // 10)
            for step in range(steps):
                if not self.mass_tests_enabled:
                    return
                await asyncio.sleep(random.uniform(0.2, 0.5))
                partial_orders = int(target_orders * (step + 1) / steps)
                partial_pnl = total_pnl * ((step + 1) / steps)
                self._emit(
                    "INFO",
                    "bot",
                    cycle,
                    cfg.id,
                    "bot_progress",
                    {"orders": partial_orders, "pnl": partial_pnl},
                )
            pnl_pct = random.uniform(-5.0, 5.0)
            runtime_s = int(time.time() - start)
            wins = random.randint(0, target_orders)
            losses = target_orders - wins
            stats = BotStats(
                bot_id=cfg.id,
                cycle=cycle,
                orders=target_orders,
                pnl=total_pnl,
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

    def _compose_cycle_summary(self, cycle: int, stats: List[BotStats]) -> Dict[str, object]:
        """Construye el payload que se envía al LLM para análisis."""

        summary: Dict[str, object] = {"cycle": cycle, "bots": []}
        for s in stats:
            cfg = self.storage.get_bot(s.bot_id)
            orders = self.storage.iter_orders(cycle, s.bot_id)
            pairs: Dict[str, float] = {}
            for o in orders:
                sym = o.get("symbol")
                pnl = float(o.get("pnl") or 0)
                if sym:
                    pairs[sym] = pairs.get(sym, 0.0) + pnl
            top3 = [
                {"symbol": sym, "pnl": pnl}
                for sym, pnl in sorted(pairs.items(), key=lambda x: x[1], reverse=True)[:3]
            ]
            summary["bots"].append(
                {
                    "bot_id": s.bot_id,
                    "mutations": cfg.mutations if cfg else {},
                    "stats": {
                        "orders": s.orders,
                        "pnl": s.pnl,
                        "pnl_pct": s.pnl_pct,
                        "win_rate": s.wins / s.orders if s.orders else 0.0,
                        "avg_hold_s": 0.0,
                        "avg_slippage_ticks": 0.0,
                        "timeouts": 0,
                        "cancel_replace_count": 0,
                    },
                    "top3_pairs": top3,
                    "hourly_dist": {},
                }
            )
        return summary

    def _prepare_candidate_symbols(self) -> List[str]:
        k = min(self._num_bots, len(POPULAR_SYMBOLS))
        return random.sample(POPULAR_SYMBOLS, k)

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
        """Genera nuevas configuraciones basadas en el ganador previo."""

        next_cycle = winner_config.cycle + 1
        client = self.llm
        # fingerprints históricos para evitar duplicados
        history = [self._fingerprint(b.mutations) for b in self.storage.iter_bots()]
        try:
            variations = client.new_generation_from_winner(winner_config.mutations, history)
        except Exception:
            variations = []

        new_generation: List[BotConfig] = []
        seen = set(history)
        for i in range(self._num_bots):
            var = variations[i] if i < len(variations) else {"name": f"Bot-{self._next_bot_id}", "mutations": {}}
            muts = var.get("mutations", {})
            fp = self._fingerprint(muts)
            if fp in seen:
                # evitar duplicado generando uno aleatorio simple
                muts = {"seed": random.random()}
                fp = self._fingerprint(muts)
            seen.add(fp)
            bot_id = self._next_bot_id
            self._next_bot_id += 1
            cfg = BotConfig(
                id=bot_id,
                cycle=next_cycle,
                name=str(var.get("name", f"Bot-{bot_id}")),
                mutations=muts,
                seed_parent=winner_config.name,
            )
            self.storage.save_bot(cfg)
            new_generation.append(cfg)
        self._current_generation = new_generation
        return new_generation

    # ------------------------------------------------------------------
    def _fingerprint(self, mutations: Dict[str, object]) -> str:
        """Crea un hash de los parámetros para evitar duplicados."""
        return hashlib.sha256(json.dumps(mutations, sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------
    def export_report(self, cycle: int, summary: Dict[str, object]) -> None:
        """Exporta un resumen del ciclo en JSON y CSV."""
        reports = Path("reports")
        reports.mkdir(exist_ok=True)
        json_path = reports / f"cycle_{cycle}.json"
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        csv_rows = []
        for bot in summary.get("bots", []):
            row = {
                "bot_id": bot.get("bot_id"),
                "pnl": bot.get("stats", {}).get("pnl"),
                "pnl_pct": bot.get("stats", {}).get("pnl_pct"),
                "orders": bot.get("stats", {}).get("orders"),
            }
            csv_rows.append(row)
        if csv_rows:
            csv_path = reports / f"cycle_{cycle}.csv"
            with csv_path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
                writer.writeheader()
                writer.writerows(csv_rows)
