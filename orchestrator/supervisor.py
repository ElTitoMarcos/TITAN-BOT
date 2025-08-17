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
from typing import Any, Callable, Dict, List, Optional, Tuple

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
        self._order_size_usd: float = float(self.state.order_size_usd)
        self._order_size_mode: str = str(self.state.order_size_mode)
        self._active_runners: List[Any] = []
        self.min_orders_per_bot = int(min_orders)
        self._global_thread: Optional[threading.Thread] = None
        self._global_stop: Optional[threading.Event] = None
        self._global_interval_s: int = 6 * 3600

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

    def set_order_size_usd(self, size: float, mode: Optional[str] = None) -> None:
        """Actualiza el tamaño por operación y lo propaga a los bots activos."""

        self._order_size_usd = float(size)
        if mode is not None:
            self._order_size_mode = mode
            self.state.order_size_mode = mode
        self.state.order_size_usd = self._order_size_usd
        self.state.save()
        for cfg in self._current_generation:
            muts = cfg.mutations or {}
            muts["order_size_usd"] = self._order_size_usd
            cfg.mutations = muts
            self.storage.save_bot(cfg)
        for r in list(self._active_runners):
            try:
                r.update_order_size(self._order_size_usd)
            except Exception:
                pass

    def register_runner(self, runner: Any) -> None:
        """Registra un ``BotRunner`` activo para broadcasts en caliente."""
        self._active_runners.append(runner)

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

        # arranca también el scheduler de análisis global si no está activo
        if not self._global_thread:
            self.start_global_scheduler(self._global_interval_s)

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
    # Global analysis scheduler
    def start_global_scheduler(self, interval_s: int) -> None:
        """Inicia el scheduler periódico de análisis global."""
        self._global_interval_s = int(interval_s)
        if self._global_thread and self._global_thread.is_alive():
            return
        self._global_stop = threading.Event()
        self._global_thread = threading.Thread(
            target=self._global_loop, daemon=True
        )
        self._global_thread.start()

    def stop_global_scheduler(self) -> None:
        """Detiene el scheduler de análisis global."""
        if self._global_stop:
            self._global_stop.set()
        if self._global_thread:
            self._global_thread.join(timeout=1)
            self._global_thread = None

    def _global_loop(self) -> None:
        while self._global_stop and not self._global_stop.is_set():
            try:
                self.run_global_analysis()
            except Exception as e:
                self._emit("ERROR", "llm", None, None, "global_analysis_fail", {"error": str(e)})
            time.sleep(self._global_interval_s)

    def run_global_analysis(self) -> None:
        summary = self.storage.gather_global_summary()
        self._emit("INFO", "llm", None, None, "global_summary", summary)
        insights = self.llm.analyze_global(summary)
        self._emit("INFO", "llm", None, None, "global_insights", insights)

        # Persist report to disk
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        reports_dir = Path("reports")
        reports_dir.mkdir(exist_ok=True)
        with open(reports_dir / f"global_insights_{ts}.json", "w", encoding="utf-8") as fh:
            json.dump({"summary": summary, "insights": insights}, fh, ensure_ascii=False, indent=2)

        # Generate optional patch in dry-run mode
        diff = self.llm.propose_patch(insights)
        if diff:
            self._emit("INFO", "llm", None, None, "global_patch", {"diff": diff})

    # ------------------------------------------------------------------
    def _loop(self) -> None:
        """Bucle principal ejecutado en un hilo aparte."""

        while self.mass_tests_enabled:
            cycle = self.state.current_cycle + 1
            asyncio.run(self.run_cycle(cycle))
            cycle_summary = self.build_llm_cycle_summary(cycle)
            if not cycle_summary.get("bots"):
                self._emit("ERROR", "cycle", cycle, None, "no_stats", {})
                self.state.current_cycle = cycle
                self.state.next_bot_id = self._next_bot_id
                self.state.save()
                continue
            self._emit(
                "INFO", "llm", cycle, None, "llm_request", {"summary": cycle_summary}
            )
            try:
                decision = self.llm.analyze_cycle_and_pick_winner(
                    cycle_summary, self.state.metric_weights
                )
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
                    fallback = self.llm.pick_winner_local(
                        cycle_summary, self.state.metric_weights
                    )
                    winner_id = int(fallback.get("winner_bot_id", -1))
                    winner_reason = str(fallback.get("reason", "weighted_score"))
                    winner_cfg = self.storage.get_bot(winner_id)
                    if winner_cfg is None:
                        raise ValueError("winner cfg not found")
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
            total_pnl = sum(b["stats"]["pnl"] for b in cycle_summary["bots"])
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
                var = (
                    variations[i]
                    if i < len(variations)
                    else {"name": f"Bot-{self._next_bot_id + i}", "mutations": {}}
                )
                muts = var.get("mutations", {}) or {}
                muts["order_size_usd"] = self._order_size_usd
                cfg = BotConfig(
                    id=self._next_bot_id + i,
                    cycle=cycle,
                    name=str(var.get("name", f"Bot-{self._next_bot_id + i}")),
                    mutations=muts,
                    seed_parent=None,
                )
                self.storage.save_bot(cfg)
                self._current_generation.append(cfg)
            self._next_bot_id += self._num_bots
        else:
            # actualizar ciclo en configs existentes
            for cfg in self._current_generation:
                cfg.cycle = cycle
                muts = cfg.mutations or {}
                muts["order_size_usd"] = self._order_size_usd
                cfg.mutations = muts
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

    def build_llm_cycle_summary(self, cycle: int) -> Dict[str, Any]:
        """Wrapper that enriches storage summary with global parameters."""

        summary = self.storage.build_llm_cycle_summary(cycle)
        summary["global_params"] = {"order_size_usd": self._order_size_usd}
        if summary.get("bots"):
            muts = summary["bots"][0].get("mutations", {})
            buf = muts.get("commission_buffer_ticks")
            if buf is not None:
                summary["global_params"]["commission_buffer_ticks"] = buf
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
