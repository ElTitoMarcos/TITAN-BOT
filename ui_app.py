import threading, queue, time, json, os, copy, asyncio
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk, messagebox
from typing import Dict, Any, List

from config import UIColors, Defaults, AppState as CoreAppState
from engine import Engine, load_sim_config, create_engine
from llm_client import LLMClient as EngineLLMClient
from llm import LLMClient as MassLLMClient
from components.testeos_frame import TesteosFrame
from components.auth_frame import AuthFrame
from components.info_frame import InfoFrame, clean_text
from components.settings_frame import SettingsFrame

from state.app_state import AppState as MassTestState
from orchestrator.supervisor import Supervisor
import exchange_utils.binance_check as binance_check
from ui_styles import apply_order_tags

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
        self.geometry("1600x900")
        self.minsize(900, 600)

        self.colors = UIColors()
        self.cfg = Defaults()
        self.state = CoreAppState()
        self.mass_state = MassTestState.load()
        self.mass_state.save()

        self._snapshot: Dict[str, Any] = {}
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
        llm_client = MassLLMClient(on_log=self.info_frame.append_llm_log)
        # guardar referencia para futuras consultas (meta-ganador, etc.)
        self.llm_client = llm_client
        self._supervisor = Supervisor(
            app_state=self.mass_state,
            llm_client=llm_client,
            min_orders=int(self.var_min_orders.get()),
        )

        self._supervisor.stream_events(lambda ev: self._event_queue.put(ev))
        self._load_saved_keys()
        self.mass_state.apis_verified = {"binance": False, "llm": False}
        self.mass_state.save()
        self.auth_frame.update_badges(self.mass_state.apis_verified)
        self._lock_controls(True)
        self.after(250, self._poll_event_queue)
        self.after(4000, self._tick_ui_refresh)
        self.after(3000, self._tick_open_orders)
        self.after(3000, self._tick_closed_orders)
        self.after(2000, self._tick_balance_refresh)

    # ------------------- UI -------------------
    def _build_ui(self):
        # Grid principal
        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
        self.rowconfigure(2, weight=2)
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

        style = tb.Style()
        style.configure("Large.TButton", font=("Segoe UI", 12, "bold"), padding=10)

        self.btn_bot_sim = ttk.Button(
            header,
            text="BOT SIM OFF",
            style="Large.TButton",
            bootstyle=(SUCCESS, OUTLINE),
            command=self._toggle_bot_sim,
        )
        self.btn_bot_sim.grid(row=0, column=0, sticky="w", padx=5)

        self.btn_bot_live = ttk.Button(
            header,
            text="BOT LIVE OFF",
            style="Large.TButton",
            bootstyle=(WARNING, OUTLINE),
            command=self._toggle_bot_live,
            state="disabled",
        )
        self.btn_bot_live.grid(row=0, column=1, sticky="w", padx=5)

        self.btn_confirm_live = ttk.Button(
            header,
            text="Confirm LIVE OFF",
            style="Large.TButton",
            bootstyle=(DANGER, OUTLINE),
            command=self._toggle_confirm_live,
        )
        self.btn_confirm_live.grid(row=0, column=2, sticky="w", padx=5)

        self._update_bot_buttons()

        self.btn_review = ttk.Button(
            header,
            text="Revisar y promover",
            bootstyle=INFO,
            command=self._on_review_promote,
        )
        self.btn_review.grid(row=0, column=3, sticky="e")
        self.btn_review.grid_remove()

        self.lbl_state_sim = ttk.Label(header, text="SIM: OFF", bootstyle=SECONDARY)
        self.lbl_state_live = ttk.Label(header, text="LIVE: OFF", bootstyle=SECONDARY)
        self.lbl_state_sim.grid(row=1, column=0, sticky="w")
        self.lbl_state_live.grid(row=1, column=1, sticky="w")

        self.lbl_pnl = ttk.Label(header, text="PNL Sesión: +0.00%  (+$0.00)", font=("Segoe UI", 16, "bold"), bootstyle=SUCCESS)
        self.lbl_bal = ttk.Label(header, text="Balance: $0.00", font=("Segoe UI", 16, "bold"), bootstyle=INFO)
        self.lbl_pnl.grid(row=1, column=2, sticky="e", padx=5)
        self.lbl_bal.grid(row=1, column=3, sticky="e", padx=5)

        # Panel fijo para testeos masivos
        container = ttk.Frame(self, padding=(10, 0, 10, 8))
        container.grid(row=1, column=0, columnspan=2, sticky="nsew")
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        self.testeos_frame = TesteosFrame(
            container,
            self.on_toggle_mass_tests,
            self.on_load_winner_for_sim,
        )
        self.testeos_frame.grid(row=0, column=0, sticky="nsew")

        # Panel inferior izquierdo para órdenes
        left = ttk.Frame(self, padding=(10,0,10,10))
        left.grid(row=2, column=0, sticky="nsew")
        left.rowconfigure(0, weight=0)
        left.rowconfigure(1, weight=0)
        left.columnconfigure(0, weight=1)

        # Órdenes abiertas
        frm_open = ttk.Labelframe(left, text="Órdenes abiertas", padding=6)
        frm_open.grid(row=0, column=0, sticky="nsew", pady=(0,8))
        frm_open.rowconfigure(0, weight=1); frm_open.columnconfigure(0, weight=1)
        cols_o = ("id","symbol","price","qty_usd","age")
        self.tree_open = ttk.Treeview(frm_open, columns=cols_o, show="headings")
        for c, txt, w, an in [
                               ("id","ID",160,"w"),
                               ("symbol","Símbolo",160,"w"),
                               ("price","Precio",120,"e"),
                               ("qty_usd","USD",100,"e"),
                               ("age","Edad(s)",80,"e")]:
            self.tree_open.heading(c, text=txt); self.tree_open.column(c, width=w, anchor=an, stretch=True)
        apply_order_tags(self.tree_open)
        vsb2 = ttk.Scrollbar(frm_open, orient="vertical", command=self.tree_open.yview)
        self.tree_open.configure(yscrollcommand=vsb2.set)
        self.tree_open.grid(row=0, column=0, sticky="nsew"); vsb2.grid(row=0, column=1, sticky="ns")

        # Órdenes cerradas
        frm_closed = ttk.Labelframe(left, text="Órdenes cerradas", padding=6)
        frm_closed.grid(row=1, column=0, sticky="nsew")
        frm_closed.rowconfigure(0, weight=1); frm_closed.columnconfigure(0, weight=1)
        cols_c = ("ts","symbol","price","qty_usd")
        self.tree_closed = ttk.Treeview(frm_closed, columns=cols_c, show="headings")
        for c, txt, w, an in [
                               ("ts","Tiempo",170,"w"),
                               ("symbol","Símbolo",160,"w"),
                               ("price","Precio",120,"e"),
                               ("qty_usd","USD",100,"e")]:
            self.tree_closed.heading(c, text=txt); self.tree_closed.column(c, width=w, anchor=an, stretch=True)
        apply_order_tags(self.tree_closed)
        vsb3 = ttk.Scrollbar(frm_closed, orient="vertical", command=self.tree_closed.yview)
        self.tree_closed.configure(yscrollcommand=vsb3.set)
        self.tree_closed.grid(row=0, column=0, sticky="nsew"); vsb3.grid(row=0, column=1, sticky="ns")

        # Right panel
        right = ttk.Frame(self, padding=(0, 0, 10, 10))
        right.grid(row=2, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(4, weight=1)
        right.rowconfigure(5, weight=3)
        right.rowconfigure(6, weight=2)

        ttk.Label(right, text="Ajustes").grid(row=0, column=0, sticky="w", pady=(0,6))

        # Tamaños + toggle mínimo + apply (SettingsFrame)
        self.settings_frame = SettingsFrame(
            right,
            self._apply_sizes,
            self._toggle_min_binance,
            self.cfg,
        )
        self.settings_frame.grid(row=1, column=0, sticky="ew", pady=6)
        self.var_size_sim = self.settings_frame.var_size_sim
        self.var_size_live = self.settings_frame.var_size_live
        self.var_use_min_bin = self.settings_frame.var_use_min_bin
        self.ent_size_sim = self.settings_frame.ent_size_sim
        self.ent_size_live = self.settings_frame.ent_size_live
        self.lbl_min_marker = self.settings_frame.lbl_min_marker

        # API keys and verification badges
        self.auth_frame = AuthFrame(right, self._start_confirm_apis)
        self.auth_frame.grid(row=2, column=0, sticky="ew", pady=6)
        # expose vars for _lock_controls helper
        self.var_bin_key = self.auth_frame.var_bin_key
        self.var_bin_sec = self.auth_frame.var_bin_sec
        self.var_oai_key = self.auth_frame.var_oai_key

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
        btn_apply_llm = ttk.Button(
            frm_llm, text="Aplicar LLM", command=self._apply_llm, bootstyle=INFO
        )
        btn_apply_llm.grid(row=0, column=2, padx=6)

        # Consulta LLM
        frm_llm_manual = ttk.Labelframe(right, text="Consulta LLM", padding=8)
        frm_llm_manual.grid(row=4, column=0, sticky="nsew")
        frm_llm_manual.columnconfigure(0, weight=1)
        self.var_llm_query = tb.StringVar()
        ttk.Entry(frm_llm_manual, textvariable=self.var_llm_query).grid(row=0, column=0, sticky="ew")
        ttk.Button(
            frm_llm_manual, text="Enviar", command=self._send_llm_query, bootstyle=INFO
        ).grid(row=0, column=1, padx=4)
        frm_llm_manual.rowconfigure(1, weight=1)
        self.txt_llm_resp = ScrolledText(frm_llm_manual, height=3, autohide=True, wrap="word")
        self.txt_llm_resp.grid(row=1, column=0, columnspan=2, sticky="nsew")

        # Información / Razones (logs del LLM)
        self.info_frame = InfoFrame(
            right,
            self.var_min_orders,
            self._apply_min_orders,
            self._revert_patch,
            self._apply_winner_live,
            self._submit_patch,
        )
        self.info_frame.grid(row=5, column=0, sticky="nsew", pady=(6, 0))

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

    def _toggle_bot_sim(self) -> None:
        self.var_bot_sim.set(not self.var_bot_sim.get())

    def _toggle_bot_live(self) -> None:
        if self.btn_bot_live.instate(["disabled"]):
            return
        self.var_bot_live.set(not self.var_bot_live.get())

    def _toggle_confirm_live(self) -> None:
        self.var_live_confirm.set(not self.var_live_confirm.get())

    def _update_bot_buttons(self) -> None:
        self.btn_bot_sim.configure(
            text=f"BOT SIM {'ON' if self.var_bot_sim.get() else 'OFF'}",
            bootstyle=SUCCESS if self.var_bot_sim.get() else (SUCCESS, OUTLINE),
        )
        self.btn_bot_live.configure(
            text=f"BOT LIVE {'ON' if self.var_bot_live.get() else 'OFF'}",
            bootstyle=WARNING if self.var_bot_live.get() else (WARNING, OUTLINE),
        )
        self.btn_confirm_live.configure(
            text=f"Confirm LIVE {'ON' if self.var_live_confirm.get() else 'OFF'}",
            bootstyle=DANGER if self.var_live_confirm.get() else (DANGER, OUTLINE),
        )

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

    def _start_confirm_apis(self) -> None:
        self._lock_controls(True)
        threading.Thread(target=lambda: asyncio.run(self._confirm_apis()), daemon=True).start()

    async def _confirm_apis(self) -> None:
        """Verifica credenciales de Binance y LLM."""
        self._save_api_keys()
        key = self.var_bin_key.get().strip()
        sec = self.var_bin_sec.get().strip()
        oai = self.var_oai_key.get().strip()
        os.environ["OPENAI_API_KEY"] = oai
        llm_client = MassLLMClient(api_key=oai)
        tasks = [
            asyncio.to_thread(binance_check.verify, key, sec),
            asyncio.to_thread(llm_client.check_credentials),
        ]
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
        try:
            self._supervisor.llm.set_api_key(oai)
        except Exception:
            pass
        try:
            bin_ok, llm_ok = await asyncio.gather(*tasks)
        except Exception:
            bin_ok = llm_ok = False
        self.mass_state.apis_verified = {
            "binance": bool(bin_ok),
            "llm": bool(llm_ok),
        }
        self.mass_state.save()
        self.after(0, lambda: self.auth_frame.update_badges(self.mass_state.apis_verified))
        def _log_result() -> None:
            if bin_ok and llm_ok:
                self.log_append("[API] Verificación exitosa")
            else:
                missing: List[str] = []
                if not bin_ok:
                    missing.append("Binance")
                if not llm_ok:
                    missing.append("LLM")
                self.log_append(f"[API] Error en {' y '.join(missing)}")
        self.after(0, _log_result)
        self.after(0, self._apply_api_locks)

    def _apply_api_locks(self) -> None:
        """Habilita o deshabilita controles según verificación de APIs."""
        bin_ok = self.mass_state.apis_verified.get("binance", False)
        llm_ok = self.mass_state.apis_verified.get("llm", False)
        self._lock_controls(not (bin_ok and llm_ok))

    def _on_engine_snapshot(self, snap: Dict[str, Any]):
        """Callback para recibir snapshots del motor."""
        self._snapshot = snap

    def _on_bot_sim(self, *_):
        if self.var_bot_sim.get():
            if not (
                self.mass_state.apis_verified.get("binance")
                and self.mass_state.apis_verified.get("llm")
            ):
                self.log_append("[SIM] APIs no verificadas")
                self.var_bot_sim.set(False)
                return
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
        self._update_bot_buttons()

    def _on_bot_live(self, *_):
        if self.var_bot_live.get():
            if not (
                self.mass_state.apis_verified.get("binance")
                and self.mass_state.apis_verified.get("llm")
            ):
                self.log_append("[LIVE] APIs no verificadas")
                self.var_bot_live.set(False)
                return
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
        self._update_bot_buttons()

    def _on_live_confirm(self, *_):
        val = bool(self.var_live_confirm.get())
        self.state.live_confirmed = val
        if self._engine_live:
            self._engine_live.state.live_confirmed = val
        self.btn_bot_live.configure(state=("normal" if val else "disabled"))
        if not val and self.var_bot_live.get():
            self.var_bot_live.set(False)
        self.log_append(f"[LIVE] Confirmación {'activada' if val else 'desactivada'}")
        self._update_bot_buttons()

    def _apply_llm(self):
        model = self.var_llm_model.get()
        self.cfg.llm_model = model
        for eng in (self._engine_sim, self._engine_live):
            try:
                if eng:
                    eng.llm.set_model(model)
            except Exception:
                pass
        self.info_frame.append_llm_log("config", {"model": model})

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
            self.info_frame.append_llm_log("request", {"ask": query})
            resp = llm.ask(query)
            self.info_frame.append_llm_log("response", resp)
        except Exception as e:
            self.info_frame.append_llm_log("response", {"error": str(e)})
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

    def _submit_patch(self):
        """Placeholder to submit the last LLM patch as a PR."""
        self.log_append("[LLM] Solicitud de PR enviada (dummy)")

    def _apply_winner_live(self):
        self.log_append("[TEST] Aplicar ganador a LIVE presionado")

    # ------------------- Configuración -------------------
    def _apply_sizes(self):

        """Aplica los tamaños por operación para SIM y LIVE."""
        margin = 1.0
        min_usd = 0.0
        try:
            self._ensure_exchange()
            min_usd = self.exchange.global_min_notional_usd()
        except Exception:
            pass
        # SIM size
        try:
            size_sim = float(self.var_size_sim.get())
            eff_sim = max(size_sim, min_usd + margin)
            if eff_sim != size_sim:
                self.var_size_sim.set(eff_sim)
                self.log_append(
                    f"[ENGINE] Tamaño SIM ajustado a {eff_sim:.2f} USD (mínimo {min_usd:.2f})"
                )
            if self._engine_sim:
                self._engine_sim.cfg.size_usd_sim = eff_sim
        except Exception:
            pass
        # LIVE size
        try:
            size_live = float(self.var_size_live.get())
            eff_live = max(size_live, min_usd + margin)
            if eff_live != size_live:
                self.var_size_live.set(eff_live)
                self.log_append(
                    f"[ENGINE] Tamaño LIVE ajustado a {eff_live:.2f} USD (mínimo {min_usd:.2f})"
                )
            if self._engine_live:
                self._engine_live.cfg.size_usd_live = eff_live
            self._supervisor.set_order_size_usd(eff_live)
        except Exception:
            pass

    def _toggle_min_binance(self):
        """Activa el tamaño mínimo permitido por Binance para LIVE."""
        use_min = bool(self.var_use_min_bin.get())
        if use_min:
            try:
                self._ensure_exchange()
                margin = 1.0
                min_usd = self.exchange.global_min_notional_usd() + margin
                self.var_size_live.set(min_usd)
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo Binance: {min_usd:.2f} USDT")
            except Exception:
                self.var_use_min_bin.set(False)
                self.ent_size_live.configure(state="normal")
                self.lbl_min_marker.configure(text="Mínimo Binance: --")
        else:
            self.ent_size_live.configure(state="normal")
        self._apply_sizes()

    def _apply_min_orders(self):
        """Aplica el mínimo de órdenes requerido para la sesión de test."""
        try:
            val = int(self.var_min_orders.get())
            self._supervisor.set_min_orders(val)
            self.log_append(f"[TEST] Órdenes mínimas = {val}")
        except Exception:
            self.log_append("[TEST] Valor inválido para órdenes mínimas")

    # ------------------- Testeos masivos -------------------
    def on_toggle_mass_tests(self, running: bool, params: Dict[str, Any]) -> None:
        """Inicia o detiene los ciclos de testeos masivos."""
        if running:
            self.log_append("[TEST] Iniciar Testeos presionado")
            if not (
                self.mass_state.apis_verified.get("binance")
                and self.mass_state.apis_verified.get("llm")
            ):
                self.log_append("[TEST] APIs no verificadas")
                self.testeos_frame.btn_toggle.configure(text="Iniciar Testeos", bootstyle=SUCCESS)
                return

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

    def _on_review_promote(self) -> None:
        if not self._winner_cfg:
            self.log_append("[PROMOTE] No hay configuración ganadora")
            return
        try:
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
            self._engine_sim = load_sim_config(self._winner_cfg.mutations)
            self._engine_sim.set_order_hook(self._log_order)
            self._engine_sim.start()
            self.var_bot_sim.set(True)
            self.lbl_state_sim.configure(text="SIM: ON", bootstyle=SUCCESS)
            self.log_append("[PROMOTE] Bot cargado en SIM para revisión")
        except Exception as exc:
            self.log_append(f"[PROMOTE] Error al iniciar SIM: {exc}")
            return
        if not messagebox.askyesno("Promover", "¿Promover este bot a LIVE?"):
            return
        try:
            if self._engine_live and self._engine_live.is_alive():
                self._engine_live.stop()
            self._ensure_exchange()
            self._engine_live = create_engine(
                exchange=self.exchange,
                mutations=self._winner_cfg.mutations,
                on_order=self._log_order,
            )
            self._engine_live.mode = "LIVE"
            self._engine_live.start()
            self.var_bot_live.set(True)
            self.lbl_state_live.configure(text="LIVE: ON", bootstyle=SUCCESS)
            self.log_append("[PROMOTE] Configuración promovida a LIVE")
        except Exception as exc:
            self.log_append(f"[PROMOTE] Error al iniciar LIVE: {exc}")
            return
        finally:
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
            self.var_bot_sim.set(False)
            self.lbl_state_sim.configure(text="SIM: OFF", bootstyle=SECONDARY)
        self.btn_review.grid_remove()

    def on_load_winner_for_sim(self) -> None:
        """Selecciona meta-ganador histórico y lo carga en el bot SIM."""
        try:
            winners = self._supervisor.storage.list_winners()
        except Exception:
            winners = []

        if not winners:
            self.log_append("[TEST] No hay ganadores históricos")
            return

        # Pedir al LLM que elija el meta-ganador
        try:
            res = self.llm_client.pick_meta_winner(winners)
            bot_id = res.get("bot_id")
            reason = res.get("reason", "")
        except Exception:
            bot_id = None
            reason = ""

        if bot_id is None:
            self.log_append("[TEST] No se pudo determinar meta-ganador")
            return

        cfg = self._supervisor.storage.get_bot(int(bot_id))
        if not cfg:
            self.log_append("[TEST] Configuración del ganador no encontrada")
            return

        # Guardar para posible uso posterior (aplicar a LIVE)
        self._winner_cfg = cfg

        try:
            if self._engine_sim and self._engine_sim.is_alive():
                self._engine_sim.stop()
            self._engine_sim = load_sim_config(cfg.mutations)
            self._engine_sim.start()
            self.var_bot_sim.set(True)
            self.lbl_state_sim.configure(text="SIM: ON", bootstyle=SUCCESS)
            self.info_frame.append_llm_log("meta_winner", {"bot_id": bot_id, "reason": reason})
            self.log_append("[TEST] Bot meta-ganador cargado en modo SIM")
        except Exception as exc:
            self.log_append(f"[TEST] Error al cargar meta-ganador: {exc}")

    # ------------------- Log helpers -------------------
    def _log_order(self, order: Dict[str, Any]) -> None:
        sym = order.get("symbol")
        side = order.get("side")
        price = order.get("price")
        qty = order.get("qty_usd") or order.get("qty")
        mode = order.get("mode", "")
        self.log_append(f"[ORDER {mode}] {side} {sym} {qty} @ {price}")

    def log_append(self, msg: str):
        """Append log messages to the UI log with timestamp and level."""
        level = "INFO"
        for prefix in ("[ERROR]", "[WARN]", "[INFO]"):
            if msg.startswith(prefix):
                level = prefix[1:-1]
                msg = msg[len(prefix):].strip()
                break

        if msg.startswith("[LLM]"):
            self.info_frame.append_llm_log("info", msg[5:].strip())

        if not hasattr(self, "_log_queue"):
            self._log_queue = queue.Queue()

        ts = time.strftime("%H:%M:%S")
        formatted = f"{ts} [{level}] {clean_text(msg)}"
        self._log_queue.put(formatted)

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
                    self.btn_review.grid_remove()
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
                    reason = clean_text(ev.payload.get("reason", ""))
                    if wid is not None:
                        self._winner_cfg = self._supervisor.storage.get_bot(wid)
                        self.testeos_frame.set_winner(int(wid), reason)
                        self.info_frame.append_llm_log("winner", {
                            "bot_id": wid,
                            "reason": reason,
                        })
                        self.btn_review.grid()
                elif ev.message == "cycle_finished" and ev.payload:
                    info = ev.payload
                    info["cycle"] = ev.cycle
                    self.testeos_frame.add_cycle_history(info)
                elif ev.message == "global_insights" and ev.payload:
                    self.info_frame.append_llm_log("global_insights", ev.payload)
                elif ev.message == "global_patch" and ev.payload:
                    self.info_frame.append_llm_log(
                        "global_patch", ev.payload.get("diff", "")
                    )

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

        reasons = snap.get("reasons", [])
        for r in reasons:
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
            age = max(0, (now - o.get('ts', now)) / 1000.0)
            tags = [
                "side_buy" if o.get("side") == "buy" else "side_sell",
                "mode_live" if o.get("mode") == "LIVE" else "mode_sim",
            ]
            self.tree_open.insert(
                "",
                "end",
                values=(
                    o.get("id", ""),
                    o.get("symbol", ""),
                    f"{o.get('price', 0.0):.8f}",
                    f"{o.get('qty_usd', 0.0):.2f}",
                    f"{age:.1f}",
                ),
                tags=tags,
            )

    def _refresh_closed_orders(self, orders: List[Dict[str, Any]]):
        for i in self.tree_closed.get_children():
            self.tree_closed.delete(i)
        for o in orders[-200:]:
            tags = [
                "side_buy" if o.get("side") == "buy" else "side_sell",
                "mode_live" if o.get("mode") == "LIVE" else "mode_sim",
            ]
            self.tree_closed.insert(
                "",
                "end",
                values=(
                    o.get("ts", ""),
                    o.get("symbol", ""),
                    f"{o.get('price', 0.0):.8f}",
                    f"{o.get('qty_usd', 0.0):.2f}",
                ),
                tags=tags,
            )

def launch():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    launch()
