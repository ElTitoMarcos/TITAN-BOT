from typing import Any, Dict, List
import tkinter as tk
from tkinter import ttk
from ttkbootstrap.constants import *

from utils.timefmt import fmt_ts


class OrdersOpenFrame(ttk.Frame):
    """Frame que muestra las órdenes abiertas."""

    def __init__(self, parent: ttk.Widget) -> None:
        super().__init__(parent, padding=10)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        cols = ("id", "symbol", "price", "qty_usd", "fecha")
        self.tree = ttk.Treeview(self, columns=cols, show="headings", height=4)
        headings = [
            ("id", "ID", 80),
            ("symbol", "Par", 80),
            ("price", "Precio", 100),
            ("qty_usd", "USD", 100),
            ("fecha", "Fecha", 150),
        ]
        for col, txt, width in headings:
            self.tree.heading(col, text=txt)
            self.tree.column(col, width=width, anchor="center", stretch=True)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")

    def refresh(self, orders: List[Dict[str, Any]]) -> None:
        """Refresca el listado de órdenes abiertas."""
        for item in self.tree.get_children():
            self.tree.delete(item)
        for o in orders:
            self.tree.insert(
                "",
                "end",
                values=(
                    o.get("id", ""),
                    o.get("symbol", ""),
                    f"{o.get('price', 0.0):.8f}",
                    f"{o.get('qty_usd', 0.0):.2f}",
                    fmt_ts(o.get("ts")),
                ),
            )
