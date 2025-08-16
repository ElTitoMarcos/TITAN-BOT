from typing import Callable

import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import ttk

from orchestrator.models import SupervisorEvent

class TesteosFrame(ttk.Frame):
    """Frame que muestra y controla los testeos masivos."""

    def __init__(
        self,
        parent: ttk.Widget,
        on_toggle: Callable[[bool], None],
        on_load_winner: Callable[[], None],
    ) -> None:
        super().__init__(parent, padding=10)
        self._on_toggle = on_toggle
        self._on_load_winner = on_load_winner
        self._running = False
        self._build()

    def _build(self) -> None:
        """Construye los widgets principales."""
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

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

        ttk.Button(self, text="Subir Bot Sim", command=self._on_load_winner).grid(row=2, column=0, sticky="w", pady=(8, 0))

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
    def handle_event(self, event: SupervisorEvent) -> None:
        """Actualiza la tabla según los eventos del supervisor."""
        try:
            if event.message == "cycle_start":
                for item in self.tree.get_children():
                    self.tree.delete(item)
            elif event.message == "bot_start":
                self.tree.insert(
                    "",
                    "end",
                    iid=str(event.bot_id),
                    values=(event.bot_id, event.cycle, "", "", "RUNNING", ""),
                )
            elif event.message == "bot_finished" and event.payload:
                stats = event.payload.get("stats", {})
                pnl = stats.get("pnl", 0.0)
                orders = stats.get("orders", 0)
                self.tree.item(
                    str(event.bot_id),
                    values=(
                        event.bot_id,
                        event.cycle,
                        orders,
                        f"{pnl:+.2f}",
                        "DONE",
                        "",
                    ),
                )
            elif event.message == "cycle_winner" and event.payload:
                winner_id = event.payload.get("winner_id")
                if winner_id is not None and self.tree.exists(str(winner_id)):
                    vals = list(self.tree.item(str(winner_id), "values"))
                    vals[-1] = "✅"
                    self.tree.item(str(winner_id), values=vals)
        except Exception:
            pass
