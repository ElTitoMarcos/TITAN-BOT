import threading, random
from typing import Callable, List, Dict, Optional

class TestManager(threading.Thread):
    """Runs iterative testing cycles for strategy variations."""
    def __init__(self, cfg, llm, log: Callable[[str], None], info: Callable[[str], None]):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.llm = llm
        self.log = log
        self.info = info
        self._stop = threading.Event()
        self.winner_thr: Optional[float] = None

    def stop(self):
        self._stop.set()

    def run(self):
        base = float(getattr(self.cfg, 'opportunity_threshold_percent', 0.2))
        while not self._stop.is_set():
            variants: List[Dict[str, float]] = []
            for i in range(10):
                delta = (i - 5) * 0.01  # +/-5%
                thr = max(0.0, base * (1.0 + delta))
                variants.append({"id": i + 1, "thr": thr})
                self.info(f"Bot {i + 1}: opportunity_threshold_percent={thr:.4f}")

            for v in variants:
                pnl = 0.0
                buys = sells = 0
                while buys < 50 or sells < 50:
                    change = random.uniform(-0.5, 0.5) + (v["thr"] - base)
                    pnl += change
                    if random.random() > 0.5 and buys < 50:
                        buys += 1
                    elif sells < 50:
                        sells += 1
                v["pnl"] = pnl

            summary = "\n".join(
                [f"Bot {v['id']}: thr={v['thr']:.4f}, pnl={v['pnl']:.2f}" for v in variants]
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
            if idx is None or idx < 1 or idx > 10:
                idx = max(variants, key=lambda x: x['pnl'])['id']
            winner = next(v for v in variants if v['id'] == idx)
            self.winner_thr = winner['thr']
            base = winner['thr']
            self.info(f"Ganadora: Bot {winner['id']} con pnl {winner['pnl']:.2f}")
            self.log(f"[TEST] Ganadora ciclo actual: Bot {winner['id']} thr={winner['thr']:.4f}")

            # Loop to next generation automatically
            if self._stop.wait(0.5):
                break
