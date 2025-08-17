"""Settings frame containing order-size controls for SIM and LIVE engines."""

import ttkbootstrap as tb
from tkinter import ttk
from ttkbootstrap.constants import INFO


class SettingsFrame(ttk.Frame):
    """Controles de tamaño por operación y mínimo de Binance."""

    def __init__(self, master, apply_cb, toggle_min_cb, cfg) -> None:

        super().__init__(master, padding=0)
        self.columnconfigure(0, weight=1)

        frm_size = ttk.Labelframe(self, text="Tamaño por operación (USD)", padding=8)
        frm_size.grid(row=0, column=0, sticky="ew", pady=6)
        frm_size.columnconfigure(1, weight=1)

        # Variables expuestas
        self.var_size_sim = tb.DoubleVar(value=getattr(cfg, "size_usd_sim", 50.0))
        self.var_size_live = tb.DoubleVar(value=getattr(cfg, "size_usd_live", 50.0))
        self.var_use_min_bin = tb.BooleanVar(value=False)

        ttk.Label(frm_size, text="SIM").grid(row=0, column=0, sticky="w")
        self.ent_size_sim = ttk.Entry(frm_size, textvariable=self.var_size_sim, width=12)
        self.ent_size_sim.grid(row=0, column=1, sticky="ew", padx=(4, 4))

        ttk.Label(frm_size, text="LIVE").grid(row=1, column=0, sticky="w")
        self.ent_size_live = ttk.Entry(frm_size, textvariable=self.var_size_live, width=12)
        self.ent_size_live.grid(row=1, column=1, sticky="ew", padx=(4, 4))

        ttk.Checkbutton(
            frm_size,
            text="Mínimo Binance",
            variable=self.var_use_min_bin,
            command=toggle_min_cb,
            bootstyle="round-toggle",
        ).grid(row=1, column=2, padx=(6, 0))

        ttk.Button(
            frm_size, text="Aplicar", command=apply_cb, bootstyle=INFO
        ).grid(row=0, column=2, padx=(6, 0))

        self.lbl_min_marker = ttk.Label(frm_size, text="Mínimo Binance: --")
        self.lbl_min_marker.grid(row=2, column=0, columnspan=3, sticky="w", pady=(4, 0))
