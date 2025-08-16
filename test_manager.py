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
    ):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.llm = llm
        self.log = log
        self.info = info
        self.min_orders = int(min_orders)
        self._stop = threading.Event()
        self.winner_cfg: Optional[Any] = None
        self.history: List[Dict[str, Any]] = []

    def stop(self):
        self._stop.set()

    def run(self):
        base_cfg = {k: getattr(self.cfg, k) for k in dir(self.cfg) if not k.startswith("_")}
        prompt = (
            "Genera 10 variantes pequeñas de la siguiente configuración de trading en formato JSON. "
            "Cada elemento debe tener los campos id (1-10), description y changes (objeto con las claves a modificar).\n"
            f"Configuración base: {base_cfg}\nDevuelve solo JSON válido."
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
            base_thr = float(getattr(self.cfg, "opportunity_threshold_percent", 0.2))
            for i in range(10):
                delta = (i - 5) * 0.01
                thr = max(0.0, base_thr * (1.0 + delta))
                variants.append({
                    "id": i + 1,
                    "description": f"thr={thr:.4f}",
                    "changes": {"opportunity_threshold_percent": thr},
                })

        self.info("Variantes generadas:")
        for v in variants:
            self.info(f"Bot {v.get('id')}: cambios {json.dumps(v.get('changes', {}))}")
        for v in variants:
            if self._stop.is_set():
                break
            cfg_copy = copy.deepcopy(self.cfg)
            for k, val in v.get("changes", {}).items():
                try:
                    setattr(cfg_copy, k, val)
                except Exception:
                    pass
            self.info(f"Iniciando Bot {v.get('id')}: {v.get('description','')}")
            def bot_log(msg: str, bot_id=v.get('id')):
                if any(tag in msg for tag in ("Orden", "FILL")):
                    self.info(f"Bot {bot_id}: {msg}")
                self.log(f"[TEST-{bot_id}] {msg}")
            eng = Engine(ui_push_snapshot=lambda _: None, ui_log=bot_log, name=f"TEST-{v.get('id')}")
            eng.cfg = cfg_copy
            eng.mode = "SIM"
            eng.llm = self.llm
            eng.start()
            start = time.time()
            while not self._stop.is_set() and len(eng._closed_orders) < self.min_orders:
                time.sleep(1)
                if time.time() - start > 300:
                    break
            v["pnl"] = eng.state.pnl_intraday_percent
            eng.stop()
            try:
                eng.join(timeout=5)
            except Exception:
                pass
            log_dir = os.path.join("logs", "tests")
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, f"bot_{v.get('id')}_orders.jsonl"), "w", encoding="utf-8") as f:
                for tr in eng._closed_orders:
                    json.dump(tr, f)
                    f.write("\n")
                    self.info(
                        f"Bot {v.get('id')}: {tr.get('side')} {tr.get('symbol')} {tr.get('qty_usd',0):.2f}USD @ {tr.get('price',0):.8f}"
                    )

            desc = v.get("description", "")
            self.info(f"Bot {v.get('id')}: {desc} -> pnl {v['pnl']:.2f}")
            self.history.append(v)
        if not self.history:
            return
        summary = "\n".join(
            [
                f"Bot {v['id']}: {v.get('description','')}, pnl={v['pnl']:.2f}"
                for v in self.history
            ]
        )
        prompt = (
            "Analiza los siguientes resultados de estrategias de trading y selecciona el número "
            "de la estrategia con mejor rendimiento:\n" + summary +
            "\nResponde solo con el número del bot ganador."
        )
        resp = self.llm.ask(prompt).strip()
        idx = None
        for tok in resp.split():
            if tok.isdigit():
                idx = int(tok)
                break
        if idx is None or not any(v["id"] == idx for v in self.history):
            idx = max(self.history, key=lambda x: x["pnl"])["id"]
        winner = next(v for v in self.history if v["id"] == idx)
        cfg_winner = copy.deepcopy(self.cfg)
        for k, val in winner.get("changes", {}).items():
            try:
                setattr(cfg_winner, k, val)
            except Exception:
                pass
        self.winner_cfg = cfg_winner
        self.info(f"Ganadora: Bot {winner['id']} con pnl {winner['pnl']:.2f}")
        self.log(
            f"[TEST] Ganadora ciclo actual: Bot {winner['id']} changes={winner.get('changes')}"
        )
        summary_dir = os.path.join("logs", "tests")
        os.makedirs(summary_dir, exist_ok=True)
        with open(os.path.join(summary_dir, "summary.json"), "w", encoding="utf-8") as f:
            json.dump(self.history, f, indent=2)
