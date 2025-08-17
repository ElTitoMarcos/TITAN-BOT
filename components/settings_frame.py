import ttkbootstrap as tb
from tkinter import ttk


class SettingsFrame(ttk.Frame):
    """Controles de tamaño por operación para todos los motores."""

    def __init__(self, master, apply_cb, cfg) -> None:
        super().__init__(master, padding=0)
        self.columnconfigure(0, weight=1)

        frm_size = ttk.Labelframe(self, text="Tamaño por operación (USD)", padding=8)
        frm_size.grid(row=0, column=0, sticky="ew", pady=6)
        frm_size.columnconfigure(1, weight=1)

        self.var_size = tb.DoubleVar(value=getattr(cfg, "size_usd_live", 50.0))
        self.var_mode = tb.StringVar(value="Fijo")

        ttk.Entry(frm_size, textvariable=self.var_size, width=14).grid(
            row=0, column=0, sticky="ew"
        )
        ttk.Combobox(
            frm_size,
            textvariable=self.var_mode,
            values=["Fijo", "Auto", "%Balance"],
            state="readonly",
            width=12,
        ).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(frm_size, text="Aplicar", command=apply_cb).grid(
            row=0, column=2, padx=6
        )
