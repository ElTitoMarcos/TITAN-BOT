import queue
import re
import tkinter as tk
from typing import Any, Callable

from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk

def clean_text(payload: Any) -> str:
    """Return a plain text representation without brackets, quotes or hashes."""
    text = str(payload)
    text = re.sub(r"\b[a-fA-F0-9]{64}\b", "", text)
    return text.translate(str.maketrans("", "", "{}[]\"'"))

class InfoFrame(ttk.Labelframe):
    """Frame que muestra información y logs del LLM."""

    def __init__(
        self,
        parent: ttk.Widget,
        var_min_orders: tk.IntVar,
        on_apply_min_orders: Callable[[], None],
        on_revert_patch: Callable[[], None],
        on_apply_winner_live: Callable[[], None],
    ) -> None:
        super().__init__(parent, text="Información / Razones", padding=8)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._log_queue: "queue.Queue[str]" = queue.Queue()

        self.txt_logs = ScrolledText(self, height=6, autohide=True, wrap="word")
        self.txt_logs.grid(row=0, column=0, columnspan=2, sticky="nsew")

        ttk.Label(self, text="Órdenes mínimas").grid(row=1, column=0, sticky="w")
        ttk.Entry(self, textvariable=var_min_orders, width=10).grid(row=1, column=1, sticky="e")
        ttk.Button(
            self,
            text="Aplicar mín. órdenes",
            command=on_apply_min_orders,
        ).grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(self, text="Revertir patch", command=on_revert_patch).grid(
            row=3, column=0, sticky="ew", pady=(4, 0)
        )
        ttk.Button(self, text="Aplicar a LIVE", command=on_apply_winner_live).grid(
            row=3, column=1, sticky="ew", pady=(4, 0)
        )

        self.after(200, self._process_log_queue)

    # ------------------------------------------------------------------
    def append_llm_log(self, tag: str, payload: Any) -> None:
        """Encola eventos del LLM para mostrarlos."""
        text = clean_text(payload)
        self._log_queue.put(f"[LLM {tag}] {text}")

    def _process_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self.txt_logs.insert("end", line + "\n")
                self.txt_logs.see("end")
        except queue.Empty:
            pass
        self.after(200, self._process_log_queue)
