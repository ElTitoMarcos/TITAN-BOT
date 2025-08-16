from typing import Callable, Dict, Any

from ttkbootstrap.constants import *
from tkinter import ttk


class TesteosFrame(ttk.Frame):
    """Frame que muestra y controla los testeos masivos."""

    def __init__(
        self,
        parent: ttk.Widget,
        on_toggle: Callable[[bool], None],
        on_load_winner_for_sim: Callable[[], None],
    ) -> None:
        super().__init__(parent, padding=10)
        self._on_toggle = on_toggle
        self._on_load_winner_for_sim = on_load_winner_for_sim
        self._running = False
        self._build()

    def _build(self) -> None:
        """Construye los widgets principales."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(4, weight=1)

        self.btn_toggle = ttk.Button(
            self,
            text="Iniciar Testeos",
            bootstyle=SUCCESS,
            command=self._toggle,
        )
        self.btn_toggle.grid(row=0, column=0, sticky="w")

        cols = ("bot_id", "cycle", "orders", "pnl", "status", "winner")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=10)
        headings = [
            ("bot_id", "BotID", 80),
            ("cycle", "Ciclo", 80),
            ("orders", "Órdenes", 100),
            ("pnl", "PNL", 100),
            ("status", "Estado", 120),
            ("winner", "EsGanador", 100),
        ]
        for col, txt, width in headings:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=width, anchor="center", stretch=True)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=1, column=0, sticky="nsew")
        vsb.grid(row=1, column=1, sticky="ns")

        ttk.Button(
            self, text="Subir Bot Sim", command=self.on_load_winner_for_sim
        ).grid(row=2, column=0, sticky="w", pady=(8, 0))

        self.lbl_winner = ttk.Label(self, text="Ganador: -", anchor="w")
        self.lbl_winner.grid(row=3, column=0, sticky="w", pady=(6, 0))

        cols_c = ("cycle", "pnl", "winner", "fecha")
        self.tree_cycles = ttk.Treeview(self, columns=cols_c, show="headings", height=5)
        for c, txt, w in [
            ("cycle", "Ciclo", 80),
            ("pnl", "PNL Total", 100),
            ("winner", "Ganador", 120),
            ("fecha", "Fecha", 150),
        ]:
            self.tree_cycles.heading(c, text=txt)
            self.tree_cycles.column(c, width=w, anchor="center", stretch=True)
        vsb_c = ttk.Scrollbar(self, orient="vertical", command=self.tree_cycles.yview)
        self.tree_cycles.configure(yscrollcommand=vsb_c.set)
        self.tree_cycles.grid(row=4, column=0, sticky="nsew", pady=(8, 0))
        vsb_c.grid(row=4, column=1, sticky="ns")

    def _toggle(self) -> None:
        """Alterna el estado de los testeos y actualiza el botón."""
        self._running = not self._running
        if self._running:
            self.btn_toggle.configure(text="Detener Testeos", bootstyle=DANGER)
        else:
            self.btn_toggle.configure(text="Iniciar Testeos", bootstyle=SUCCESS)
        try:
            self._on_toggle(self._running)
        except Exception:
            pass

    # ------------------------------------------------------------------
    def clear(self) -> None:
        """Elimina todas las filas del árbol."""
        for item in self.tree.get_children():
            self.tree.delete(item)

    def update_bot_row(self, stats: Dict[str, Any]) -> None:
        """Inserta o actualiza una fila con estadísticas de un bot."""
        bot_id = int(stats.get("bot_id"))
        cycle = stats.get("cycle", "")
        orders = stats.get("orders", 0)
        pnl = stats.get("pnl", 0.0)
        status = stats.get("status", "")
        is_winner = stats.get("winner", False)
        values = (
            bot_id,
            cycle,
            orders,
            f"{pnl:+.2f}",
            status,
            "✅" if is_winner else "",
        )
        if self.tree.exists(str(bot_id)):
            self.tree.item(str(bot_id), values=values)
        else:
            self.tree.insert("", "end", iid=str(bot_id), values=values)

    def set_winner(self, bot_id: int, reason: str) -> None:
        """Marca el bot ganador y muestra la razón."""
        if self.tree.exists(str(bot_id)):
            vals = list(self.tree.item(str(bot_id), "values"))
            vals[-1] = "✅"
            self.tree.item(str(bot_id), values=vals)
        self.lbl_winner.configure(text=f"Ganador: Bot {bot_id} - {reason}")

    def add_cycle_history(self, info: Dict[str, Any]) -> None:
        """Agrega una fila al historial de ciclos."""
        values = (
            info.get("cycle"),
            f"{info.get('total_pnl', 0.0):+.2f}",
            f"Bot {info.get('winner_id')}",
            info.get("finished_at", ""),
        )
        self.tree_cycles.insert("", "end", values=values)

    def on_load_winner_for_sim(self) -> None:
        """Invoca el callback para cargar el bot ganador en modo SIM."""
        try:
            self._on_load_winner_for_sim()
        except Exception:
            pass
