from typing import Callable

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import ttk

class TesteosFrame(ttk.Frame):
    """Frame que muestra y controla los testeos masivos."""

    def __init__(self, parent: ttk.Widget, on_start: Callable[[], None], on_load_winner: Callable[[], None]) -> None:
        super().__init__(parent, padding=10)
        self._on_start = on_start
        self._on_load_winner = on_load_winner
        self._build()

    def _build(self) -> None:
        """Construye los widgets principales."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        ttk.Button(self, text="Iniciar Testeos", command=self._on_start).grid(row=0, column=0, sticky="w")

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

        ttk.Button(self, text="Subir Bot Sim", command=self._on_load_winner).grid(row=2, column=0, sticky="w", pady=(8, 0))
