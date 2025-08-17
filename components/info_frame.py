import queue
import re
import tkinter as tk
from typing import Any, Callable, Optional

from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk

def clean_text(payload: Any) -> str:
    """Return a plain text representation without brackets, quotes or hashes."""
    text = str(payload)
    text = re.sub(r"\b[a-fA-F0-9]{64}\b", "", text)
    return text.translate(str.maketrans("", "", "{}[]\"'"))


def sanitize_log(text: str) -> str:
    """Reduce sequences of commas and empty lists for cleaner logs."""
    # collapse repeating commas/spaces like ", , ," -> ", "
    text = re.sub(r"(\s*,\s*){3,}", ", ", text)
    # remove repeated empty lists "[], [], []" -> "[]"
    text = re.sub(r"(?:\[\s*\]\s*,\s*){2,}\[\s*\]", "[]", text)
    return text

class InfoFrame(ttk.Labelframe):
    """Frame que muestra información y logs del LLM."""

    def __init__(
        self,
        parent: ttk.Widget,
        var_min_orders: tk.IntVar,
        on_apply_min_orders: Callable[[], None],
        on_revert_patch: Callable[[], None],
        on_apply_winner_live: Callable[[], None],
        on_submit_patch: Callable[[], None],
    ) -> None:
        super().__init__(parent, text="Información / Razones", padding=8)
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)
        self.rowconfigure(0, weight=1)

        self._log_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.paused = False

        self.txt_logs = ScrolledText(self, height=6, autohide=True, wrap="word")
        self.txt_logs.grid(row=0, column=0, columnspan=2, sticky="nsew")

        ttk.Button(self, text="Limpiar log", command=self.clear_logs).grid(
            row=1, column=0, sticky="ew", pady=(4, 0)
        )
        self.btn_pause = ttk.Button(self, text="Pausar log", command=self.toggle_pause)
        self.btn_pause.grid(row=1, column=1, sticky="ew", pady=(4, 0))

        ttk.Label(self, text="Órdenes mínimas").grid(row=2, column=0, sticky="w")
        ttk.Entry(self, textvariable=var_min_orders, width=10).grid(row=2, column=1, sticky="e")
        ttk.Button(
            self,
            text="Aplicar mín. órdenes",
            command=on_apply_min_orders,
        ).grid(row=3, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        ttk.Button(self, text="Revertir patch", command=on_revert_patch).grid(
            row=4, column=0, sticky="ew", pady=(4, 0)
        )
        ttk.Button(self, text="Aplicar a LIVE", command=on_apply_winner_live).grid(
            row=4, column=1, sticky="ew", pady=(4, 0)
        )
        ttk.Button(self, text="Crear PR patch", command=on_submit_patch).grid(
            row=5, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )

        self.after(200, self._process_log_queue)

    # ------------------------------------------------------------------
    def append_llm_log(
        self, tag: str, payload: Any, label: Optional[str] = None
    ) -> None:
        """Encola eventos del LLM para mostrarlos."""
        text = sanitize_log(clean_text(payload))
        if tag == "request":
            self._log_queue.put(
                lambda: self.render_llm_request(text, label)
            )
        elif tag == "response":
            self._log_queue.put(lambda: self.render_llm_response(text))
        else:
            self._log_queue.put(
                lambda: self._insert_text(f"[LLM {tag}] {text}")
            )

    def render_llm_request(self, text: str, label: Optional[str]) -> None:
        msg = f'Envío LLM: Prompt "{label}"' if label else f"Envío LLM: {text}"
        self._insert_text(msg)

    def render_llm_response(self, text: str) -> None:
        self._insert_text(f"Respuesta LLM: {text}")

    def _insert_text(self, line: str) -> None:
        self.txt_logs.insert("end", line + "\n")
        self.txt_logs.see("end")

    def clear_logs(self) -> None:
        """Borra el contenido visible y la cola."""
        self.txt_logs.delete("1.0", "end")
        with self._log_queue.mutex:
            self._log_queue.queue.clear()

    def toggle_pause(self) -> None:
        """Alterna el estado de pausa de los logs."""
        self.paused = not self.paused
        self.btn_pause.configure(
            text="Reanudar log" if self.paused else "Pausar log"
        )

    def _process_log_queue(self) -> None:
        if not self.paused:
            try:
                while True:
                    func = self._log_queue.get_nowait()
                    func()
            except queue.Empty:
                pass
        self.after(200, self._process_log_queue)
