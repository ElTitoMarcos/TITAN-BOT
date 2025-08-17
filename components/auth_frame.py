from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict


class AuthFrame(ttk.Labelframe):
    """Frame para ingresar claves de APIs y mostrar estado de verificación."""

    def __init__(self, parent: ttk.Widget, on_confirm: Callable[[], None]) -> None:
        super().__init__(parent, text="Claves API", padding=8)
        self.on_confirm = on_confirm

        self.columnconfigure(1, weight=1)

        self.var_bin_key = tk.StringVar(value="")
        self.var_bin_sec = tk.StringVar(value="")
        self.var_oai_key = tk.StringVar(value="")
        self.var_codex_key = tk.StringVar(value="")
        self.var_use_codex = tk.BooleanVar(value=False)

        ttk.Label(self, text="Binance KEY").grid(row=0, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.var_bin_key, width=28).grid(row=0, column=1, sticky="ew")
        self.lbl_bin_status = ttk.Label(self, text="❌")
        self.lbl_bin_status.grid(row=0, column=3, padx=4)

        ttk.Label(self, text="Binance SECRET").grid(row=1, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.var_bin_sec, width=28, show="•").grid(row=1, column=1, sticky="ew")

        ttk.Label(self, text="ChatGPT API Key").grid(row=2, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.var_oai_key, width=28, show="•").grid(row=2, column=1, sticky="ew")
        self.lbl_llm_status = ttk.Label(self, text="❌")
        self.lbl_llm_status.grid(row=2, column=3, padx=4)

        ttk.Label(self, text="Codex API Key").grid(row=3, column=0, sticky="w")
        ttk.Entry(self, textvariable=self.var_codex_key, width=28, show="•").grid(row=3, column=1, sticky="ew")
        self.lbl_codex_status = ttk.Label(self, text="❌")
        self.lbl_codex_status.grid(row=3, column=3, padx=4)

        ttk.Checkbutton(self, text="Usar Codex", variable=self.var_use_codex).grid(row=4, column=0, sticky="w", pady=(4, 0))
        self.btn_confirm = ttk.Button(self, text="Confirmar APIs", command=self._on_confirm)
        self.btn_confirm.grid(row=0, column=2, rowspan=4, padx=6)

    # ------------------------------------------------------------------
    def _on_confirm(self) -> None:
        if self.on_confirm:
            try:
                self.btn_confirm.configure(state="disabled")
            except Exception:
                pass
            self.on_confirm()

    # ------------------------------------------------------------------
    def update_badges(self, status: Dict[str, bool]) -> None:
        """Actualiza badges de estado para cada servicio."""
        self.lbl_bin_status.configure(text="✅" if status.get("binance") else "❌")
        self.lbl_llm_status.configure(text="✅" if status.get("llm") else "❌")
        self.lbl_codex_status.configure(text="✅" if status.get("codex") else "❌")
        try:
            self.btn_confirm.configure(state="normal")
        except Exception:
            pass
