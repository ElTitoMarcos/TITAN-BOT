import ttkbootstrap as tb
from ttkbootstrap.constants import *
from tkinter import ttk


class SettingsFrame(ttk.Frame):
    """Frame que agrupa los controles de tamaño por operación."""

    def __init__(self, master, apply_cb, toggle_cb, cfg):
        super().__init__(master, padding=0)
        self.columnconfigure(0, weight=1)

        frm_size = ttk.Labelframe(self, text="Tamaño por operación (USD)", padding=8)
        frm_size.grid(row=0, column=0, sticky="ew", pady=6)
        frm_size.columnconfigure(1, weight=1)

        self.var_size_sim = tb.DoubleVar(value=cfg.size_usd_sim)
        self.var_size_live = tb.DoubleVar(value=cfg.size_usd_live)
        self.var_use_min_bin = tb.BooleanVar(value=False)

        ttk.Label(frm_size, text="SIM").grid(row=0, column=0, sticky="w")
        self.ent_size_sim = ttk.Entry(frm_size, textvariable=self.var_size_sim, width=14)
        self.ent_size_sim.grid(row=0, column=1, sticky="ew")
        ttk.Label(frm_size, text="LIVE").grid(row=1, column=0, sticky="w")
        self.ent_size_live = ttk.Entry(frm_size, textvariable=self.var_size_live, width=14)
        self.ent_size_live.grid(row=1, column=1, sticky="ew")
        ttk.Button(frm_size, text="Aplicar tamaño", command=apply_cb).grid(row=0, column=2, rowspan=2, padx=6)
        self.lbl_min_marker = ttk.Label(frm_size, text="Mínimo Binance: --")
        self.lbl_min_marker.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4,0))
        ttk.Checkbutton(
            frm_size,
            text="Min Binance",
            variable=self.var_use_min_bin,
            style="info.Switch",
            command=toggle_cb,
        ).grid(row=2, column=2, padx=6, pady=(4,0))
