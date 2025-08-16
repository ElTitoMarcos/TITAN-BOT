import threading, time, copy, json, os
from typing import Callable, List, Dict, Optional, Any
from engine import Engine

class TestManager(threading.Thread):
    """Runs iterative testing cycles for strategy variations."""
    def __init__(
        self,
        cfg,
        llm,
        log: Callable[[str], None],
        info: Callable[[str], None],
        min_orders: int = 50,
        on_winner: Callable[[Any], None] | None = None,
    ):
        """Crea el administrador de testeos masivos.

        Parameters
        ----------
        cfg: objeto de configuración base.
        llm: cliente LLM utilizado para generar variaciones y evaluar resultados.
        log: función para registrar mensajes de depuración.
        info: función para mostrar mensajes informativos en la UI.
        min_orders: número mínimo de órdenes simuladas por bot antes de evaluarlo.
        on_winner: callback opcional que se invoca al finalizar cada ciclo con la
            configuración ganadora.
        """
        super().__init__(daemon=True)
        self.cfg = cfg
        self.llm = llm
        self.log = log
        self.info = info
        self.min_orders = int(min_orders)
        self._stop = threading.Event()
        self.on_winner = on_winner
        # configuración ganadora del último ciclo
        self.winner_cfg: Optional[Any] = None
        # historial acumulado de variantes evaluadas
        self.history: List[Dict[str, Any]] = []

    def stop(self):
        self._stop.set()

    def run(self):
        """Ejecuta ciclos sucesivos de testeo en paralelo.

        En cada ciclo se generan 10 configuraciones distintas a partir de la
        configuración de partida, se lanzan 10 motores en paralelo y se recopilan
        sus resultados hasta alcanzar el número mínimo de órdenes. El LLM
        selecciona la variante ganadora y se utiliza como base para el siguiente
        ciclo mientras el proceso no sea detenido manualmente.
        """
        current_cfg = copy.deepcopy(self.cfg)
        cycle = 0
        while not self._stop.is_set():
            cycle += 1
            base_cfg_dict = {k: getattr(current_cfg, k) for k in dir(current_cfg) if not k.startswith("_")}
            prompt = (
                "Genera 10 variantes pequeñas de la siguiente configuración de trading en formato JSON. "
                "Cada elemento debe tener los campos id (1-10), description y changes (objeto con las claves a modificar).\n"
                f"Configuración base: {base_cfg_dict}\nDevuelve solo JSON válido."
            )
            variants: List[Dict[str, Any]] = []
            try:
                resp = self.llm.ask(prompt)
                data = json.loads(resp)
                if isinstance(data, list):
                    variants = data
            except Exception:
                variants = []
            if not variants:
                base_thr = float(getattr(current_cfg, "opportunity_threshold_percent", 0.2))
                for i in range(10):
                    delta = (i - 5) * 0.01
                    thr = max(0.0, base_thr * (1.0 + delta))
                    variants.append({
                        "id": i + 1,
                        "description": f"thr={thr:.4f}",
                        "changes": {"opportunity_threshold_percent": thr},
                    })

            self.info(f"Variantes generadas ciclo {cycle}:")
            for v in variants:
                self.info(f"Bot {v.get('id')}: cambios {json.dumps(v.get('changes', {}))}")

            bots: List[Dict[str, Any]] = []
            order_count: Dict[int, int] = {}
            for v in variants:
                if self._stop.is_set():
                    break
                cfg_copy = copy.deepcopy(current_cfg)
                for k, val in v.get("changes", {}).items():
                    try:
                        setattr(cfg_copy, k, val)
                    except Exception:
                        pass
                bot_id = int(v.get("id", 0))
                order_count[bot_id] = 0
                self.info(f"Iniciando Bot {bot_id}: {v.get('description','')}")

                def bot_log(msg: str, bot_id=bot_id):
                    if "FILL" in msg:
                        order_count[bot_id] += 1
                        self.info(f"Bot {bot_id}: órdenes {order_count[bot_id]}")
                    self.log(f"[TEST-{bot_id}] {msg}")

                eng = Engine(ui_push_snapshot=lambda _: None, ui_log=bot_log, name=f"TEST-{bot_id}")
                eng.cfg = cfg_copy
                eng.mode = "SIM"
                eng.llm = self.llm
                eng.start()
                bots.append({"variant": v, "eng": eng, "id": bot_id})

            # Monitorear hasta que todos los bots finalicen
            pending = len(bots)
            while pending > 0 and not self._stop.is_set():
                for b in bots:
                    if b.get("done"):
                        continue
                    eng = b["eng"]
                    if len(eng._closed_orders) >= self.min_orders:
                        b["pnl"] = eng.state.pnl_intraday_percent
                        b["orders"] = list(eng._closed_orders)
                        eng.stop()
                        try:
                            eng.join(timeout=5)
                        except Exception:
                            pass
                        log_dir = os.path.join("logs", "tests")
                        os.makedirs(log_dir, exist_ok=True)
                        with open(os.path.join(log_dir, f"bot_{b['id']}_orders.jsonl"), "w", encoding="utf-8") as f:
                            for tr in b["orders"]:
                                json.dump(tr, f)
                                f.write("\n")
                        self.info(
                            f"Bot {b['id']}: completado {len(b['orders'])} órdenes, pnl {b['pnl']:.2f}"
                        )
                        b["done"] = True
                        pending -= 1
                time.sleep(1)

            # detener bots restantes si se detuvo el ciclo
            for b in bots:
                if not b.get("done"):
                    b["eng"].stop()
                    try:
                        b["eng"].join(timeout=5)
                    except Exception:
                        pass

            cycle_history: List[Dict[str, Any]] = []
            for b in bots:
                v = b["variant"]
                v["pnl"] = b.get("pnl", 0.0)
                v["orders"] = b.get("orders", [])
                cycle_history.append(v)

            if not cycle_history:
                break

            # LLM: elegir ganador del ciclo
            prompt = (
                "Analiza los siguientes resultados de estrategias de trading y selecciona el número "
                "de la estrategia con mejor rendimiento. Devuelve solo el número del bot ganador.\n"
                + json.dumps(cycle_history)
            )
            resp = self.llm.ask(prompt).strip()
            idx = None
            for tok in resp.split():
                if tok.isdigit():
                    idx = int(tok)
                    break
            if idx is None or not any(v.get("id") == idx for v in cycle_history):
                idx = max(cycle_history, key=lambda x: x.get("pnl", 0.0))["id"]
            winner = next(v for v in cycle_history if v.get("id") == idx)
            cfg_winner = copy.deepcopy(current_cfg)
            for k, val in winner.get("changes", {}).items():
                try:
                    setattr(cfg_winner, k, val)
                except Exception:
                    pass
            self.winner_cfg = cfg_winner
            self.info(
                f"Ganadora ciclo {cycle}: Bot {winner['id']} -> pnl {winner.get('pnl',0):.2f}"
            )
            self.log(
                f"[TEST] Ganadora ciclo {cycle}: Bot {winner['id']} changes={winner.get('changes')}"
            )
            if self.on_winner:
                try:
                    self.on_winner(cfg_winner)
                except Exception:
                    pass
            # guardar historial y preparar siguiente ciclo
            self.history.extend(cycle_history)
            current_cfg = cfg_winner

        # fin de todos los ciclos
        summary_dir = os.path.join("logs", "tests")
        os.makedirs(summary_dir, exist_ok=True)
        with open(os.path.join(summary_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)
