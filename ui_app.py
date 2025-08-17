import threading, queue, time, json, os, copy
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk
from typing import Dict, Any, List

from config import UIColors, Defaults, AppState as CoreAppState
from engine import Engine, load_sim_config
from llm_client import LLMClient as EngineLLMClient
from llm import LLMClient as MassLLMClient
from components.testeos_frame import TesteosFrame
from state.app_state import AppState as MassTestState
from orchestrator.supervisor import Supervisor

BADGE_SIM = "🔧SIM"
BADGE_LIVE = "⚡LIVE"
BADGE_BUY = "🟢BUY"
BADGE_SELL = "🔴SELL"

class App(tb.Window):

    def _iter_all_widgets(self, parent=None):
        parent = parent or self
        try:
            kids = parent.winfo_children()
        except Exception:
            return
        for w in kids:
            yield w
            yield from self._iter_all_widgets(w)

    def _lock_controls(self, locked: bool):
        # disable everything
        try:
            for w in self._iter_all_widgets():
                try:
                    if hasattr(w, "configure") and "state" in w.keys():
                        w.configure(state=("disabled" if locked else "normal"))
                except Exception:
                    pass
            # re-enable API widgets
            key_name = str(self.var_bin_key) if hasattr(self, "var_bin_key") else None
            sec_name = str(self.var_bin_sec) if hasattr(self, "var_bin_sec") else None
            oai_name = str(self.var_oai_key) if hasattr(self, "var_oai_key") else None
            for w in self._iter_all_widgets():
                try:
                    if w.winfo_class() in ("TEntry", "Entry"):
                        tv = w.cget("textvariable") if "textvariable" in w.keys() else ""
                        if tv in (key_name, sec_name, oai_name):
                            w.configure(state="normal")
                    if w.winfo_class() in ("TButton", "Button"):
                        txt = w.cget("text") if "text" in w.keys() else ""
                        if "Confirmar APIs" in str(txt):
                            w.configure(state="normal")
                except Exception:
                    pass
            if getattr(self, "var_use_min_bin", None) and self.var_use_min_bin.get():
                try:
                    self.ent_size_live.configure(state="disabled")
                except Exception:
                    pass
        except Exception:
            pass

    def _handle_rate_limit(self, err: Exception) -> bool:
        msg = str(err)
        keywords = ["way too much request weight", "ip banned", "418 i'm a teapot"]
        if any(k in msg.lower() for k in keywords):
            self.log_append("[SECURITY] IP ban detectado, deteniendo bots.")
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
                self.var_bot_sim.set(False)
                self.lbl_state_sim.configure(text="SIM: OFF", bootstyle=SECONDARY)
            if self._engine_live and self._engine_live.is_alive():
                self._engine_live.stop()
                self.var_bot_live.set(False)
                self.lbl_state_live.configure(text="LIVE: OFF", bootstyle=SECONDARY)
            return True
        return False

    def __init__(self):
        super().__init__(title="AutoBTC - Punto a Punto", themename="cyborg")
        self.geometry("1400x860")
        self.minsize(1300, 760)

        self.colors = UIColors()
        self.cfg = Defaults()
        self.state = CoreAppState()
        self.mass_state = MassTestState.load()
        self.mass_state.save()

        self._snapshot: Dict[str, Any] = {}
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._event_queue: "queue.Queue" = queue.Queue()
        self._engine_sim: Engine | None = None
        self._engine_live: Engine | None = None
        self.exchange = None
        self._tester = None
        self.var_min_orders = tb.IntVar(value=50)
        self._winner_cfg = None

        self._keys_file = os.path.join(os.path.dirname(__file__), ".api_keys.json")

        self._build_ui()
        # Instanciar LLM y supervisor después de construir UI para cablear logs
        llm_client = MassLLMClient(on_log=self.testeos_frame.append_llm_log)
        self._supervisor = Supervisor(app_state=self.mass_state, llm_client=llm_client)
        self._supervisor.stream_events(lambda ev: self._event_queue.put(ev))
        self._load_saved_keys()
        self._lock_controls(True)
        self.after(250, self._poll_log_queue)
        self.after(250, self._poll_event_queue)
        self.after(4000, self._tick_ui_refresh)
        self.after(3000, self._tick_open_orders)
        self.after(3000, self._tick_closed_orders)
        self.after(2000, self._tick_balance_refresh)

    # ------------------- UI -------------------
    def _build_ui(self):
        # Grid principal
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=2)
        self.rowconfigure(2, weight=1)
        try:
            self._ensure_exchange()
        except Exception:
            pass

        # Header
        header = ttk.Frame(self, padding=10)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        for c in range(4):
            header.columnconfigure(c, weight=1)

        # Controles SIM/LIVE
        self.var_bot_sim = tb.BooleanVar(value=False)
        self.var_bot_live = tb.BooleanVar(value=False)
        self.var_live_confirm = tb.BooleanVar(value=False)

        ttk.Checkbutton(header, text="BOT SIM", variable=self.var_bot_sim, style="success.Roundtoggle").grid(row=0, column=0, sticky="w", padx=5)
        ttk.Checkbutton(header, text="BOT LIVE", variable=self.var_bot_live, style="warning.Roundtoggle").grid(row=0, column=1, sticky="w", padx=5)
        ttk.Checkbutton(header, text="Confirm LIVE", variable=self.var_live_confirm, style="danger.Roundtoggle").grid(row=0, column=2, sticky="w", padx=5)

        self.lbl_state_sim = ttk.Label(header, text="SIM: OFF", bootstyle=SECONDARY)
        self.lbl_state_live = ttk.Label(header, text="LIVE: OFF", bootstyle=SECONDARY)
        self.lbl_state_sim.grid(row=1, column=0, sticky="w")
        self.lbl_state_live.grid(row=1, column=1, sticky="w")

        self.lbl_pnl = ttk.Label(header, text="PNL Sesión: +0.00%  (+$0.00)", font=("Segoe UI", 16, "bold"), bootstyle=SUCCESS)
        self.lbl_bal = ttk.Label(header, text="Balance: $0.00", font=("Segoe UI", 16, "bold"), bootstyle=INFO)
        self.lbl_pnl.grid(row=1, column=2, sticky="e", padx=5)
        self.lbl_bal.grid(row=1, column=3, sticky="e", padx=5)

        # Pestañas principales
        self.notebook = ttk.Notebook(self)
        self.notebook.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(10,10), pady=(0,8))
        self.testeos_frame = TesteosFrame(
            self.notebook,
            self.on_toggle_mass_tests,
            self.on_load_winner_for_sim,
        )
        self.notebook.add(self.testeos_frame, text="Testeos Masivos")

        # Panel inferior izquierdo para órdenes
        left = ttk.Frame(self, padding=(10,0,10,10))
        left.grid(row=2, column=0, sticky="nsew")
        left.rowconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        left.columnconfigure(0, weight=1)

        # Órdenes abiertas
        frm_open = ttk.Labelframe(left, text="Órdenes abiertas", padding=6)
        frm_open.grid(row=0, column=0, sticky="nsew", pady=(0,8))
        frm_open.rowconfigure(0, weight=1); frm_open.columnconfigure(0, weight=1)
        cols_o = ("id","symbol","side","mode","price","qty_usd","age")
        self.tree_open = ttk.Treeview(frm_open, columns=cols_o, show="headings")
        for c, txt, w, an in [("id","ID",160,"w"),
                               ("symbol","Símbolo",160,"w"),
                               ("side","Lado",90,"w"),
                               ("mode","Modo",80,"w"),
                               ("price","Precio",120,"e"),
                               ("qty_usd","USD",100,"e"),
                               ("age","Edad(s)",80,"e")]:
            self.tree_open.heading(c, text=txt); self.tree_open.column(c, width=w, anchor=an, stretch=True)
        vsb2 = ttk.Scrollbar(frm_open, orient="vertical", command=self.tree_open.yview)
        self.tree_open.configure(yscrollcommand=vsb2.set)
        self.tree_open.grid(row=0, column=0, sticky="nsew"); vsb2.grid(row=0, column=1, sticky="ns")

        # Órdenes cerradas
        frm_closed = ttk.Labelframe(left, text="Órdenes cerradas", padding=6)
        frm_closed.grid(row=1, column=0, sticky="nsew")
        frm_closed.rowconfigure(0, weight=1); frm_closed.columnconfigure(0, weight=1)
        cols_c = ("ts","symbol","side","mode","price","qty_usd")
        self.tree_closed = ttk.Treeview(frm_closed, columns=cols_c, show="headings")
        for c, txt, w, an in [("ts","Tiempo",170,"w"),
                               ("symbol","Símbolo",160,"w"),
                               ("side","Lado",90,"w"),
                               ("mode","Modo",80,"w"),
                               ("price","Precio",120,"e"),
                               ("qty_usd","USD",100,"e")]:
            self.tree_closed.heading(c, text=txt); self.tree_closed.column(c, width=w, anchor=an, stretch=True)
        vsb3 = ttk.Scrollbar(frm_closed, orient="vertical", command=self.tree_closed.yview)
        self.tree_closed.configure(yscrollcommand=vsb3.set)
        self.tree_closed.grid(row=0, column=0, sticky="nsew"); vsb3.grid(row=0, column=1, sticky="ns")

        # Right panel
        right = ttk.Frame(self, padding=(0, 0, 10, 10))
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=0)
        right.rowconfigure(5, weight=1)
        right.rowconfigure(6, weight=1)

        ttk.Label(right, text="Ajustes").grid(row=0, column=0, sticky="w", pady=(0,6))

        # Tamaños + toggle mínimo + apply
        frm_size = ttk.Labelframe(right, text="Tamaño por operación (USD)", padding=8)
        frm_size.grid(row=1, column=0, sticky="ew", pady=6)
        frm_size.columnconfigure(1, weight=1)
        self.var_size_sim = tb.DoubleVar(value=self.cfg.size_usd_sim)
        self.var_size_live = tb.DoubleVar(value=self.cfg.size_usd_live)
        self.var_use_min_bin = tb.BooleanVar(value=False)
        ttk.Label(frm_size, text="SIM").grid(row=0, column=0, sticky="w")
        self.ent_size_sim = ttk.Entry(frm_size, textvariable=self.var_size_sim, width=14)
        self.ent_size_sim.grid(row=0, column=1, sticky="ew")
        ttk.Label(frm_size, text="LIVE").grid(row=1, column=0, sticky="w")
        self.ent_size_live = ttk.Entry(frm_size, textvariable=self.var_size_live, width=14)
        self.ent_size_live.grid(row=1, column=1, sticky="ew")
        ttk.Button(frm_size, text="Aplicar tamaño", command=self._apply_sizes).grid(row=0, column=2, rowspan=2, padx=6)
        self.lbl_min_marker = ttk.Label(frm_size, text="Mínimo Binance: --")
        self.lbl_min_marker.grid(row=2, column=0, columnspan=2, sticky="w", pady=(4,0))
        ttk.Checkbutton(
            frm_size,
            text="Min Binance",
            variable=self.var_use_min_bin,
            style="info.Switch",
            command=self._toggle_min_binance,
        ).grid(row=2, column=2, padx=6, pady=(4,0))

        # API keys
        frm_api = ttk.Labelframe(right, text="Claves API", padding=8)
        frm_api.grid(row=2, column=0, sticky="ew", pady=6)
        frm_api.columnconfigure(1, weight=1)
        self.var_bin_key = tb.StringVar(value="")
        self.var_bin_sec = tb.StringVar(value="")
        ttk.Label(frm_api, text="Binance KEY").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_bin_key, width=28).grid(row=0, column=1, sticky="ew")
        ttk.Label(frm_api, text="Binance SECRET").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_bin_sec, width=28, show="•").grid(row=1, column=1, sticky="ew")

        self.var_oai_key = tb.StringVar(value="")
        ttk.Label(frm_api, text="ChatGPT API Key").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_oai_key, width=28, show="•").grid(row=2, column=1, sticky="ew")
        ttk.Button(frm_api, text="Confirmar APIs", command=self._confirm_apis).grid(row=0, column=2, rowspan=3, padx=6)

        # LLM config minimal: model + seconds + apply button
        frm_llm = ttk.Labelframe(right, text="LLM (decisor)", padding=8)
        frm_llm.grid(row=3, column=0, sticky="ew", pady=6)
        frm_llm.columnconfigure(1, weight=1)
        self.var_llm_model = tb.StringVar(value=self.cfg.llm_model)
        ttk.Label(frm_llm, text="Modelo").grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            frm_llm,
            textvariable=self.var_llm_model,
            values=["gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini"],
            width=14,
            state="readonly",
        ).grid(row=0, column=1, sticky="ew")
        ttk.Button(frm_llm, text="Aplicar LLM", command=self._apply_llm).grid(row=0, column=2, padx=6)

        # Consulta LLM
        frm_llm_manual = ttk.Labelframe(right, text="Consulta LLM", padding=8)
        frm_llm_manual.grid(row=4, column=0, sticky="nsew")
        frm_llm_manual.columnconfigure(0, weight=1)
        self.var_llm_query = tb.StringVar()
        ttk.Entry(frm_llm_manual, textvariable=self.var_llm_query).grid(row=0, column=0, sticky="ew")
        ttk.Button(frm_llm_manual, text="Enviar", command=self._send_llm_query).grid(row=0, column=1, padx=4)
        frm_llm_manual.rowconfigure(1, weight=1)
        self.txt_llm_resp = ScrolledText(frm_llm_manual, height=3, autohide=True, wrap="word")
        self.txt_llm_resp.grid(row=1, column=0, columnspan=2, sticky="nsew")

        # Información / Razones
        frm_info = ttk.Labelframe(right, text="Información / Razones", padding=8)
        frm_info.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        frm_info.rowconfigure(0, weight=1); frm_info.columnconfigure(0, weight=1); frm_info.columnconfigure(1, weight=1)
        self.txt_info = ScrolledText(frm_info, height=6, autohide=True, wrap="word")
        self.txt_info.grid(row=0, column=0, columnspan=2, sticky="nsew")
        ttk.Label(frm_info, text="Órdenes mínimas").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_info, textvariable=self.var_min_orders, width=10).grid(row=1, column=1, sticky="e")
        ttk.Button(frm_info, text="Aplicar mín. órdenes", command=self._apply_min_orders).grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(4, 0)
        )
        ttk.Button(frm_info, text="Revertir patch", command=self._revert_patch).grid(row=3, column=0, sticky="ew", pady=(4,0))
        ttk.Button(frm_info, text="Aplicar a LIVE", command=self._apply_winner_live).grid(row=3, column=1, sticky="ew", pady=(4,0))

        # Log
        frm_log = ttk.Labelframe(right, text="Log", padding=8)
        frm_log.grid(row=6, column=0, sticky="nsew", pady=6)
        frm_log.rowconfigure(0, weight=1); frm_log.columnconfigure(0, weight=1)
        self.txt_log = ScrolledText(frm_log, height=6, autohide=True, wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        # Bindings
        self.var_bot_sim.trace_add("write", self._on_bot_sim)
        self.var_bot_live.trace_add("write", self._on_bot_live)
        self.var_live_confirm.trace_add("write", self._on_live_confirm)

    # ------------------- Helpers -------------------
    def _ensure_exchange(self):
        if self.exchange is None:
            from exchange_utils import BinanceExchange
            self.exchange = BinanceExchange(rate_limit=True, sandbox=False)

    def _load_saved_keys(self):
        try:
            with open(self._keys_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.var_bin_key.set(data.get("bin_key", ""))
            self.var_bin_sec.set(data.get("bin_sec", ""))
            self.var_oai_key.set(data.get("oai_key", ""))
        except Exception:
            pass

    def _save_api_keys(self):
        try:
            data = {
                "bin_key": self.var_bin_key.get(),
                "bin_sec": self.var_bin_sec.get(),
                "oai_key": self.var_oai_key.get(),
            }
            with open(self._keys_file, "w", encoding="utf-8") as f:
                json.dump(data, f)
        except Exception:
            pass

    def _confirm_apis(self):
        """Confirma y guarda las claves API ingresadas en la UI."""
        self._save_api_keys()
        key = self.var_bin_key.get().strip()
        sec = self.var_bin_sec.get().strip()
        oai = self.var_oai_key.get().strip()
        try:
            self._ensure_exchange()
            self.exchange.set_api_keys(key, sec)
        except Exception:
            pass
        for eng in (self._engine_sim, self._engine_live):
            try:
                if eng:
                    eng.exchange.set_api_keys(key, sec)
                    eng.llm.set_api_key(oai)
            except Exception:
                pass
        self.log_append("[API] Claves actualizadas")
        self._lock_controls(False)

    def _on_engine_snapshot(self, snap: Dict[str, Any]):
        """Callback para recibir snapshots del motor."""
        self._snapshot = snap

    def _on_bot_sim(self, *_):
        if self.var_bot_sim.get():
            if not self._engine_sim or not self._engine_sim.is_alive():
                self._ensure_exchange()
                self._engine_sim = Engine(self._on_engine_snapshot, self.log_append, exchange=self.exchange, name="SIM")
                self._engine_sim.mode = "SIM"
                self._engine_sim.start()
            self.lbl_state_sim.configure(text="SIM: ON", bootstyle=SUCCESS)
        else:
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
            self.lbl_state_sim.configure(text="SIM: OFF", bootstyle=SECONDARY)

    def _on_bot_live(self, *_):
        if self.var_bot_live.get():
            if not self._engine_live or not self._engine_live.is_alive():
                self._ensure_exchange()
                self._engine_live = Engine(self._on_engine_snapshot, self.log_append, exchange=self.exchange, name="LIVE")
                self._engine_live.mode = "LIVE"
                self._engine_live.state.live_confirmed = self.state.live_confirmed
                self._engine_live.start()
            self.lbl_state_live.configure(text="LIVE: ON", bootstyle=SUCCESS)
        else:
            if self._engine_live and self._engine_live.is_alive():
                self._engine_live.stop()
            self.lbl_state_live.configure(text="LIVE: OFF", bootstyle=SECONDARY)

    def _on_live_confirm(self, *_):
        val = bool(self.var_live_confirm.get())
        self.state.live_confirmed = val
        if self._engine_live:
            self._engine_live.state.live_confirmed = val
        self.log_append(f"[LIVE] Confirmación {'activada' if val else 'desactivada'}")

    def _apply_llm(self):
        model = self.var_llm_model.get()
        self.cfg.llm_model = model
        for eng in (self._engine_sim, self._engine_live):
            try:
                if eng:
                    eng.llm.set_model(model)
            except Exception:
                pass
        self.log_append(f"[LLM] Modelo aplicado: {model}")

    def _send_llm_query(self):
        query = self.var_llm_query.get().strip()
        if not query:
            return
        llm = None
        if self._engine_sim:
            llm = self._engine_sim.llm
        elif self._engine_live:
            llm = self._engine_live.llm
        else:
            llm = EngineLLMClient(
                model=self.var_llm_model.get(), api_key=self.var_oai_key.get()
            )
        resp = ""
        try:
            resp = llm.ask(query)
        except Exception:
            resp = ""
        self.txt_llm_resp.delete("1.0", "end")
        self.txt_llm_resp.insert("end", resp)

    def _revert_patch(self):
        for eng in (self._engine_sim, self._engine_live):
            try:
                if eng:
                    eng.revert_last_patch()
            except Exception:
                pass

    def _apply_winner_live(self):
        self.log_append("[TEST] Aplicar ganador a LIVE presionado")

    # ------------------- Configuración -------------------
    def _apply_sizes(self):

        """Aplica los tamaños por operación para SIM y LIVE."""
        try:
            if self._engine_sim:
                self._engine_sim.cfg.size_usd_sim = float(self.var_size_sim.get())
        except Exception:
            pass
        try:
            if self._engine_live:
                self._engine_live.cfg.size_usd_live = float(self.var_size_live.get())
        except Exception:
            pass

    def _toggle_min_binance(self):
        """Activa el tamaño mínimo permitido por Binance para LIVE."""
        use_min = bool(self.var_use_min_bin.get())
        if use_min:
            try:
                self._ensure_exchange()
                min_usd = self.exchange.global_min_notional_usd()
                self.var_size_live.set(min_usd)
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo Binance: {min_usd:.2f} USDT")
            except Exception:
                self.var_use_min_bin.set(False)
                self.ent_size_live.configure(state="normal")
                self.lbl_min_marker.configure(text="Mínimo Binance: --")
        else:
            self.ent_size_live.configure(state="normal")

    def _apply_min_orders(self):
        """Aplica el mínimo de órdenes requerido para la sesión de test."""
        try:
            val = int(self.var_min_orders.get())
            self.log_append(f"[TEST] Órdenes mínimas = {val}")
        except Exception:
            self.log_append("[TEST] Valor inválido para órdenes mínimas")

    # ------------------- Testeos masivos -------------------
    def on_toggle_mass_tests(self, running: bool, params: Dict[str, Any]) -> None:
        """Inicia o detiene los ciclos de testeos masivos."""
        if running:
            self.log_append("[TEST] Iniciar Testeos presionado")
            st = self._supervisor.state
            st.max_depth_symbols = int(params.get("max_depth_symbols", st.max_depth_symbols))
            st.depth_speed = params.get("depth_speed", st.depth_speed)
            st.bots_per_cycle = int(params.get("num_bots", st.bots_per_cycle))
            st.mode = params.get("mode", st.mode)
            st.save()
            self._supervisor.mode = st.mode
            self._supervisor.start_mass_tests(num_bots=st.bots_per_cycle)
        else:
            self.log_append("[TEST] Testeos detenidos")
            self._supervisor.stop_mass_tests()

    def on_load_winner_for_sim(self) -> None:
        """Carga la configuración ganadora en el bot SIM."""
        if not self._winner_cfg:
            self.log_append("[TEST] No hay ganador disponible")
            return
        try:
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
            self._engine_sim = load_sim_config(self._winner_cfg.mutations)
            self._engine_sim.start()
            self.var_bot_sim.set(True)
            self.lbl_state_sim.configure(text="SIM: ON", bootstyle=SUCCESS)
            self.log_append("[TEST] Bot ganador cargado en modo SIM")
        except Exception as exc:
            self.log_append(f"[TEST] Error al cargar ganador: {exc}")

    # ------------------- Log helpers -------------------
    def log_append(self, msg: str):
        self._log_queue.put(msg)

    def _poll_log_queue(self):
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self.txt_log.insert("end", msg + "\n")
                self.txt_log.see("end")
        except queue.Empty:
            pass
        self.after(200, self._poll_log_queue)

    def _poll_event_queue(self):
        try:
            while True:
                ev = self._event_queue.get_nowait()
                if ev.message == "cycle_start":
                    self.testeos_frame.clear()
                elif ev.message == "bot_start":
                    self.testeos_frame.update_bot_row(
                        {
                            "bot_id": ev.bot_id,
                            "cycle": ev.cycle,
                            "orders": 0,
                            "pnl": 0.0,
                            "status": "RUNNING",
                        }
                    )
                elif ev.message == "bot_progress" and ev.payload:
                    self.testeos_frame.update_bot_row(
                        {
                            "bot_id": ev.bot_id,
                            "cycle": ev.cycle,
                            "orders": ev.payload.get("orders", 0),
                            "pnl": ev.payload.get("pnl", 0.0),
                            "status": "RUNNING",
                        }
                    )
                elif ev.message == "bot_finished" and ev.payload:
                    stats = ev.payload.get("stats", {})
                    stats.update({"bot_id": ev.bot_id, "cycle": ev.cycle, "status": "DONE"})
                    self.testeos_frame.update_bot_row(stats)
                elif ev.message == "cycle_winner" and ev.payload:
                    wid = ev.payload.get("winner_id")
                    reason = ev.payload.get("reason", "")
                    if wid is not None:
                        self._winner_cfg = self._supervisor.storage.get_bot(wid)
                        self.testeos_frame.set_winner(int(wid), reason)
                elif ev.message == "cycle_finished" and ev.payload:
                    info = ev.payload
                    info["cycle"] = ev.cycle
                    self.testeos_frame.add_cycle_history(info)
                elif ev.scope == "llm":
                    if ev.message == "llm_request" and ev.payload:
                        self.log_append(f"[LLM] request {json.dumps(ev.payload)}")
                    elif ev.message == "llm_response" and ev.payload:
                        self.log_append(f"[LLM] response {json.dumps(ev.payload)}")
                    elif ev.message == "llm_error" and ev.payload:
                        self.log_append(f"[LLM] error {ev.payload.get('error')}")

        except queue.Empty:
            pass
        self.after(200, self._poll_event_queue)

    # ------------------- UI refresh -------------------
    def _tick_balance_refresh(self):
        try:
            self._ensure_exchange()
            b = self.exchange.fetch_balances_summary()
            if b:
                self.lbl_bal.configure(text=f"Balance: ${b.get('balance_usd',0.0):,.2f}")
        except Exception:
            pass
        self.after(5000, self._tick_balance_refresh)

    def _tick_ui_refresh(self):
        snap = self._snapshot or {}
        gs = snap.get("global_state", {})
        pnlp = gs.get("pnl_intraday_percent", 0.0)
        pnlu = gs.get("pnl_intraday_usd", 0.0)
        self.lbl_pnl.configure(text=f"PNL Sesión: {pnlp:+.2f}%  ({pnlu:+.2f} USD)")
        try:
            if pnlu >= 0: self.lbl_pnl.configure(bootstyle=SUCCESS)
            else: self.lbl_pnl.configure(bootstyle=DANGER)
        except Exception:
            pass

        # Razones
        reasons = snap.get("reasons", [])
        if reasons:
            self.txt_info.delete("1.0","end")
            for r in reasons:
                self.txt_info.insert("end", f"• {r}\n")
                self.log_append(f"[ENGINE] {r}")

        self.after(4000, self._tick_ui_refresh)

    def _tick_open_orders(self):
        snap = self._snapshot or {}
        self._refresh_open_orders(snap.get("open_orders", []))
        self.after(4000, self._tick_open_orders)

    def _tick_closed_orders(self):
        snap = self._snapshot or {}
        self._refresh_closed_orders(snap.get("closed_orders", []))
        self.after(4000, self._tick_closed_orders)

    def _refresh_open_orders(self, orders: List[Dict[str, Any]]):
        for i in self.tree_open.get_children():
            self.tree_open.delete(i)
        now = time.time()*1000
        for o in orders:
            side = f"{BADGE_BUY}" if o.get("side")=="buy" else f"{BADGE_SELL}"
            mode = f"{BADGE_LIVE}" if o.get("mode")=="LIVE" else f"{BADGE_SIM}"
            age = max(0, (now - o.get('ts', now)) / 1000.0)
            self.tree_open.insert("", "end", values=(
                o.get("id",""),
                o.get("symbol",""),
                side,
                mode,
                f"{o.get('price',0.0):.8f}",
                f"{o.get('qty_usd',0.0):.2f}",
                f"{age:.1f}"
            ))

    def _refresh_closed_orders(self, orders: List[Dict[str, Any]]):
        for i in self.tree_closed.get_children():
            self.tree_closed.delete(i)
        for o in orders[-200:]:
            side = f"{BADGE_BUY}" if o.get("side")=="buy" else f"{BADGE_SELL}"
            mode = f"{BADGE_LIVE}" if o.get("mode")=="LIVE" else f"{BADGE_SIM}"
            self.tree_closed.insert("", "end", values=(
                o.get("ts",""),
                o.get("symbol",""),
                side,
                mode,
                f"{o.get('price',0.0):.8f}",
                f"{o.get('qty_usd',0.0):.2f}",
            ))

def launch():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    launch()
