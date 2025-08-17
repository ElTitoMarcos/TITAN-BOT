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
        super().__init__(parent, text="Información / Razones", padding=4)
        self.columnconfigure((0, 1, 2), weight=1)
        self.rowconfigure(0, weight=1)

        self._log_queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self.paused = False

        self.txt_logs = ScrolledText(self, height=3, autohide=True, wrap="word")
        self.txt_logs.grid(row=0, column=0, columnspan=3, sticky="nsew")

        ttk.Label(self, text="Órdenes mínimas").grid(
            row=1, column=0, sticky="w", padx=2, pady=2
        )
        ttk.Entry(self, textvariable=var_min_orders, width=10).grid(
            row=1, column=1, columnspan=2, sticky="w", padx=2, pady=2
        )

        ttk.Button(
            self,
            text="Limpiar log",
            command=self.clear_logs,
            width=12,
            bootstyle=INFO,
        ).grid(row=2, column=0, sticky="ew", padx=2, pady=2)
        self.btn_pause = ttk.Button(
            self,
            text="Pausar log",
            command=self.toggle_pause,
            width=12,
            bootstyle=SECONDARY,
        )
        self.btn_pause.grid(row=2, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(
            self,
            text="Aplicar mín. órdenes",
            command=on_apply_min_orders,
            width=12,
            bootstyle=WARNING,
        ).grid(row=2, column=2, sticky="ew", padx=2, pady=2)

        ttk.Button(
            self,
            text="Revertir patch",
            command=on_revert_patch,
            width=12,
            bootstyle=INFO,
        ).grid(row=3, column=0, sticky="ew", padx=2, pady=2)
        ttk.Button(
            self,
            text="Aplicar a LIVE",
            command=on_apply_winner_live,
            width=12,
            bootstyle=WARNING,
        ).grid(row=3, column=1, sticky="ew", padx=2, pady=2)
        ttk.Button(
            self,
            text="Crear PR patch",
            command=on_submit_patch,
            width=12,
            bootstyle=SECONDARY,
        ).grid(row=3, column=2, sticky="ew", padx=2, pady=2)

        self._last_prompt: Optional[str] = None

        self.after(200, self._process_log_queue)

    # ------------------------------------------------------------------
    def append_llm_log(
        self, tag: str, payload: Any, label: Optional[str] = None
    ) -> None:
        """Encola eventos del LLM para mostrarlos."""
        text = sanitize_log(clean_text(payload))
        if tag == "request":
            prompt_text = (
                sanitize_log(clean_text(label)) if label is not None else text
            )
            self._last_prompt = prompt_text[:80]
        elif tag == "response":
            response_text = text[:120]
            prompt = self._last_prompt or ""
            self._log_queue.put(
                lambda: self.render_prompt_response(prompt, response_text)
            )
            self._last_prompt = None
        else:
            self._log_queue.put(
                lambda: self._insert_text(f"[LLM {tag}] {text}")
            )

    def render_prompt_response(self, prompt: str, response: str) -> None:
        self._insert_text(f"Prompt: {prompt} | Resp: {response}")

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
