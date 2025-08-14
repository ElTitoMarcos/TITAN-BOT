import threading, queue, time
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk
from typing import Dict, Any, List
from config import UIColors, Defaults, AppState
from engine import Engine

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
                    if w.winfo_class() in ("TEntry","Entry"):
                        tv = w.cget("textvariable") if "textvariable" in w.keys() else ""
                        if tv in (key_name, sec_name, oai_name):
                            w.configure(state="normal")
                    if w.winfo_class() in ("TButton","Button"):
                        txt = w.cget("text") if "text" in w.keys() else ""
                        if any(lbl in str(txt) for lbl in ("Aplicar Binance", "Aplicar ChatGPT")):
                            w.configure(state="normal")
                except Exception:
                    pass
        except Exception:
            pass

    def _maybe_unlock_controls(self):
        key = self.var_bin_key.get().strip() if hasattr(self, "var_bin_key") else ""
        sec = self.var_bin_sec.get().strip() if hasattr(self, "var_bin_sec") else ""
        oai = self.var_oai_key.get().strip() if hasattr(self, "var_oai_key") else ""
        if key and sec and oai:
            try:
                self._ensure_exchange()
                self.exchange.load_markets()
                self._lock_controls(False)
            except Exception:
                pass
    
    def __init__(self):
        super().__init__(title="AutoBTC - Punto a Punto", themename="cyborg")
        self.geometry("1400x860")
        self.minsize(1300, 760)

        self.colors = UIColors()
        self.cfg = Defaults()
        self.state = AppState()
        self._snapshot: Dict[str, Any] = {}
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._engine_sim: Engine | None = None
        self._engine_live: Engine | None = None
        self.exchange = None

        self._build_ui()
        self.after(250, self._poll_log_queue)
        self.after(800, self._tick_ui_refresh)
        # Precarga de mercado y balance
        self._warmup_thread = threading.Thread(target=self._warmup_load_market, daemon=True)
        self._warmup_thread.start()
        self.after(2000, self._tick_balance_refresh)

    # ------------------- UI -------------------
    def _build_ui(self):
        # Grid principal
        self.columnconfigure(0, weight=3)
        self.columnconfigure(1, weight=2)
        self.rowconfigure(1, weight=1)
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

        # Left panes
        left = ttk.Frame(self, padding=(10, 0, 10, 10))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=2)
        left.rowconfigure(1, weight=1)
        left.rowconfigure(2, weight=1)
        left.columnconfigure(0, weight=1)

        # Mercado
        frm_mkt = ttk.Labelframe(left, text="Mercado", padding=6)
        frm_mkt.grid(row=0, column=0, sticky="nsew", pady=(0,8))
        frm_mkt.rowconfigure(0, weight=1); frm_mkt.columnconfigure(0, weight=1)
        cols = ("symbol","score","pct","price_sats","vol_base_k")
        self.tree = ttk.Treeview(frm_mkt, columns=cols, show="headings")
        style = tb.Style(); style.configure("Treeview", font=("Consolas", 10))
        headers = [("symbol","Símbolo",160,"w"),
                   ("score","Score",70,"e"),
                   ("pct","%24h",70,"e"),
                   ("price_sats","Precio (sats)",120,"e"),
                   ("vol_base_k","VolBase(k)",100,"e")]
        for c, txt, w, an in headers:
            self.tree.heading(c, text=txt); self.tree.column(c, width=w, anchor=an, stretch=False)
        vsb = ttk.Scrollbar(frm_mkt, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns")
        # Colores por score (fino)
        self.tree.tag_configure('score90', background='#0e7a3b')
        self.tree.tag_configure('score80', background='#16a34a')
        self.tree.tag_configure('score65', background='#22c55e')
        self.tree.tag_configure('score64', background='#ffd24d')
        self.tree.tag_configure('score59', background='#f59e0b')
        self.tree.tag_configure('score50', background='#fbbf24')
        self.tree.tag_configure('scoreLow', background='#9ca3af')
        self.tree.tag_configure('veto', background='#ef4444', foreground='white')

        # Órdenes abiertas
        frm_open = ttk.Labelframe(left, text="Órdenes abiertas", padding=6)
        frm_open.grid(row=1, column=0, sticky="nsew", pady=(0,8))
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
            self.tree_open.heading(c, text=txt); self.tree_open.column(c, width=w, anchor=an, stretch=False)
        vsb2 = ttk.Scrollbar(frm_open, orient="vertical", command=self.tree_open.yview)
        self.tree_open.configure(yscrollcommand=vsb2.set)
        self.tree_open.grid(row=0, column=0, sticky="nsew"); vsb2.grid(row=0, column=1, sticky="ns")

        # Órdenes cerradas
        frm_closed = ttk.Labelframe(left, text="Órdenes cerradas", padding=6)
        frm_closed.grid(row=2, column=0, sticky="nsew")
        frm_closed.rowconfigure(0, weight=1); frm_closed.columnconfigure(0, weight=1)
        cols_c = ("ts","symbol","side","mode","price","qty_usd")
        self.tree_closed = ttk.Treeview(frm_closed, columns=cols_c, show="headings")
        for c, txt, w, an in [("ts","Tiempo",170,"w"),
                               ("symbol","Símbolo",160,"w"),
                               ("side","Lado",90,"w"),
                               ("mode","Modo",80,"w"),
                               ("price","Precio",120,"e"),
                               ("qty_usd","USD",100,"e")]:
            self.tree_closed.heading(c, text=txt); self.tree_closed.column(c, width=w, anchor=an, stretch=False)
        vsb3 = ttk.Scrollbar(frm_closed, orient="vertical", command=self.tree_closed.yview)
        self.tree_closed.configure(yscrollcommand=vsb3.set)
        self.tree_closed.grid(row=0, column=0, sticky="nsew"); vsb3.grid(row=0, column=1, sticky="ns")

        # Right panel
        right = ttk.Frame(self, padding=(0, 0, 10, 10))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)

        ttk.Label(right, text="Ajustes").grid(row=0, column=0, sticky="w", pady=(0,6))

        # Tamaños + toggle mínimo + apply
        frm_size = ttk.Labelframe(right, text="Tamaño por operación (USD)", padding=8)
        frm_size.grid(row=1, column=0, sticky="ew", pady=6)
        self.var_size_sim = tb.DoubleVar(value=self.cfg.size_usd_sim)
        self.var_size_live = tb.DoubleVar(value=self.cfg.size_usd_live)
        self.var_use_min_live = tb.BooleanVar(value=True)
        ttk.Label(frm_size, text="SIM").grid(row=0, column=0, sticky="w")
        self.ent_size_sim = ttk.Entry(frm_size, textvariable=self.var_size_sim, width=14)
        self.ent_size_sim.grid(row=0, column=1, sticky="e")
        ttk.Label(frm_size, text="LIVE (mínimo Binance)").grid(row=1, column=0, sticky="w")
        self.ent_size_live = ttk.Entry(frm_size, textvariable=self.var_size_live, width=14, state="disabled")
        self.ent_size_live.grid(row=1, column=1, sticky="e")
        ttk.Checkbutton(frm_size, text="Usar mínimo Binance (LIVE)", variable=self.var_use_min_live, style="info.Roundtoggle", command=self._on_toggle_min_live).grid(row=2, column=0, sticky="w")
        self.lbl_min_marker = ttk.Label(frm_size, text="Mínimo permitido por Binance: --")
        self.lbl_min_marker.grid(row=3, column=0, columnspan=3, sticky="w", pady=(4,0))
        ttk.Button(frm_size, text="Aplicar tamaño", command=self._apply_sizes).grid(row=0, column=2, rowspan=2, padx=6)

        # API keys
        frm_api = ttk.Labelframe(right, text="Claves API", padding=8)
        frm_api.grid(row=2, column=0, sticky="ew", pady=6)
        self.var_bin_key = tb.StringVar(value="")
        self.var_bin_sec = tb.StringVar(value="")
        ttk.Label(frm_api, text="Binance KEY").grid(row=0, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_bin_key, width=28).grid(row=0, column=1, sticky="e")
        ttk.Label(frm_api, text="Binance SECRET").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_bin_sec, width=28, show="•").grid(row=1, column=1, sticky="e")
        ttk.Button(frm_api, text="Aplicar Binance", command=self._apply_binance_keys).grid(row=0, column=2, rowspan=2, padx=6)

        self.var_oai_key = tb.StringVar(value="")
        ttk.Label(frm_api, text="ChatGPT API Key").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_oai_key, width=28, show="•").grid(row=2, column=1, sticky="e")
        ttk.Button(frm_api, text="Aplicar ChatGPT", command=self._apply_openai_key).grid(row=2, column=2, padx=6)

        # LLM config minimal: model + seconds + apply button
        frm_llm = ttk.Labelframe(right, text="LLM (decisor)", padding=8)
        frm_llm.grid(row=3, column=0, sticky="ew", pady=6)
        self.var_llm_model = tb.StringVar(value=self.cfg.llm_model)
        self.var_llm_secs = tb.IntVar(value=max(1, int(self.cfg.llm_call_interval_ms/1000)))
        ttk.Label(frm_llm, text="Modelo").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm_llm, textvariable=self.var_llm_model, values=["gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini"], width=14, state="readonly").grid(row=0, column=1, sticky="e")
        ttk.Label(frm_llm, text="Segundos entre llamadas").grid(row=1, column=0, sticky="w")
        ttk.Entry(frm_llm, textvariable=self.var_llm_secs, width=14).grid(row=1, column=1, sticky="e")
        ttk.Button(frm_llm, text="Aplicar LLM", command=self._apply_llm).grid(row=0, column=2, rowspan=2, padx=6)

        # Estado
        st = ttk.Labelframe(right, text="Estado", padding=8)
        st.grid(row=4, column=0, sticky="ew", pady=6)
        self.lbl_ws = ttk.Label(st, text="WS: 0 ms")
        self.lbl_rest = ttk.Label(st, text="REST: 0 ms")
        self.lbl_ws.grid(row=0, column=0, sticky="w")
        self.lbl_rest.grid(row=0, column=1, sticky="e")

        # Info / razones
        frm_info = ttk.Labelframe(right, text="Información / Razones", padding=8)
        frm_info.grid(row=5, column=0, sticky="nsew", pady=6)
        frm_info.rowconfigure(0, weight=1); frm_info.columnconfigure(0, weight=1)
        self.txt_info = ScrolledText(frm_info, height=12, autohide=True, wrap="word")
        self.txt_info.grid(row=0, column=0, sticky="nsew")

        # Footer log
        footer = ttk.Labelframe(self, text="Log", padding=10)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew")
        footer.columnconfigure(0, weight=1)
        self.txt_log = ScrolledText(footer, height=6, autohide=True, wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="ew")

        # Bindings
        self.var_bot_sim.trace_add("write", self._on_bot_sim)
        self.var_bot_live.trace_add("write", self._on_bot_live)
        self.var_live_confirm.trace_add("write", self._on_live_confirm)
        self._lock_controls(True)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_min_marker())

    # ------------------- Helpers -------------------
    def _ensure_exchange(self):
        if self.exchange is None:
            from exchange_utils import BinanceExchange
            self.exchange = BinanceExchange(rate_limit=True, sandbox=False)

    # ------------------- Warmup -------------------
    def _warmup_load_market(self):
        try:
            self._ensure_exchange()
            uni = self.exchange.fetch_universe("BTC")[:100]
            if uni:
                pairs = self.exchange.fetch_top_metrics(uni[: min(20, len(uni))])
                if not self._snapshot:
                    self._refresh_market_table(pairs)
            # Mínimo global BTC en el marcador
            try:
                min_usd = self.exchange.global_min_notional_usd()
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {min_usd:.2f} USDT")
            except Exception:
                pass
        except Exception as e:
            self.log_append(f"[ENGINE] Warmup error: {e}")

    # ------------------- Engine binding -------------------
    def _on_bot_sim(self, *_):
        en = bool(self.var_bot_sim.get())
        if en and (self._engine_sim is None or not self._engine_sim.is_alive()):
            self._start_engine_sim()
            self.lbl_state_sim.configure(text="SIM: ON", bootstyle=SUCCESS)
            self.log_append("[ENGINE SIM] Bot SIM iniciado.")
        elif not en and self._engine_sim:
            self._engine_sim.stop()
            self._engine_sim = None
            self.lbl_state_sim.configure(text="SIM: OFF", bootstyle=SECONDARY)
            self.log_append("[ENGINE SIM] Bot SIM detenido.")

    def _on_bot_live(self, *_):
        en = bool(self.var_bot_live.get())
        if en and not bool(self.var_live_confirm.get()):
            # No permitimos arrancar LIVE sin confirmación
            self.var_bot_live.set(False)
            self.lbl_state_live.configure(text="LIVE: OFF", bootstyle=SECONDARY)
            self.log_append("[RISK] LIVE bloqueado: activa 'Confirm LIVE' para iniciar.")
            return
        if en and (self._engine_live is None or not self._engine_live.is_alive()):
            self._start_engine_live()
            self.lbl_state_live.configure(text="LIVE: ON", bootstyle=WARNING)
            self.log_append("[ENGINE LIVE] Bot LIVE iniciado.")
        elif not en and self._engine_live:
            self._engine_live.stop()
            self._engine_live = None
            self.lbl_state_live.configure(text="LIVE: OFF", bootstyle=SECONDARY)
            self.log_append("[ENGINE LIVE] Bot LIVE detenido.")

    def _on_live_confirm(self, *_):
        lc = bool(self.var_live_confirm.get())
        if self._engine_live:
            self._engine_live.state.live_confirmed = lc
        self.log_append(f"[RISK] Confirm LIVE = {lc}")

    def _start_engine_sim(self):
        def push_snapshot(snap: Dict[str, Any]):
            self._snapshot = snap
        self._ensure_exchange()
        self._engine_sim = Engine(ui_push_snapshot=push_snapshot, ui_log=self.log_append, exchange=self.exchange, name="SIM")
        self._engine_sim.mode = "SIM"
        self._engine_sim.cfg.size_usd_sim = float(self.var_size_sim.get())
        # LLM
        self._engine_sim.llm.set_model(self.var_llm_model.get())
        secs = max(1, int(self.var_llm_secs.get()))
        self._engine_sim.cfg.llm_call_interval_ms = secs * 1000
        self._engine_sim.start()

    def _start_engine_live(self):
        def push_snapshot(snap: Dict[str, Any]):
            self._snapshot = snap
        self._ensure_exchange()
        self._engine_live = Engine(ui_push_snapshot=push_snapshot, ui_log=self.log_append, exchange=self.exchange, name="LIVE")
        self._engine_live.mode = "LIVE"
        # tamaño LIVE (mínimo global si toggle ON)
        if bool(self.var_use_min_live.get()):
            try:
                min_usd = self.exchange.global_min_notional_usd()
                usd = float(min_usd)
                self._engine_live.cfg.size_usd_live = float(usd if usd > 0 else self._engine_live.cfg.size_usd_live)
                self.var_size_live.set(round(self._engine_live.cfg.size_usd_live, 2))
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {usd:.2f} USDT")
            except Exception:
                pass
        # LLM
        self._engine_live.llm.set_model(self.var_llm_model.get())
        secs = max(1, int(self.var_llm_secs.get()))
        self._engine_live.cfg.llm_call_interval_ms = secs * 1000
        # Confirm gate
        self._engine_live.state.live_confirmed = bool(self.var_live_confirm.get())
        self._engine_live.start()

    # ------------------- Actions -------------------
    def _on_toggle_min_live(self):
        use_min = bool(self.var_use_min_live.get())
        if use_min:
            self.ent_size_live.configure(state="disabled")
            try:
                self._ensure_exchange()
                min_usd = self.exchange.global_min_notional_usd()
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {min_usd:.2f} USDT")
            except Exception:
                self.lbl_min_marker.configure(text="Mínimo permitido por Binance: --")
        else:
            self.ent_size_live.configure(state="normal")
            self.lbl_min_marker.configure(text="Mínimo permitido por Binance: (no aplicado)")

    def _apply_binance_keys(self):
        key = self.var_bin_key.get().strip()
        sec = self.var_bin_sec.get().strip()
        self._ensure_exchange()
        self.exchange.set_api_keys(key, sec)
        if self._engine_sim: self._engine_sim.exchange.set_api_keys(key, sec)
        if self._engine_live: self._engine_live.exchange.set_api_keys(key, sec)
        self.log_append("[ENGINE] Claves de Binance aplicadas.")
        self._maybe_unlock_controls()

    def _apply_openai_key(self):
        k = self.var_oai_key.get().strip()
        if self._engine_sim:
            self._engine_sim.llm.set_api_key(k)
            self._engine_sim.cfg.openai_api_key = k
        if self._engine_live:
            self._engine_live.llm.set_api_key(k)
            self._engine_live.cfg.openai_api_key = k
        self.log_append("[LLM] Clave de ChatGPT aplicada.")
        self._maybe_unlock_controls()

    def _apply_llm(self):
        if self._engine_sim:
            self._engine_sim.llm.set_model(self.var_llm_model.get())
            secs = max(1, int(self.var_llm_secs.get()))
            self._engine_sim.cfg.llm_call_interval_ms = secs * 1000
            self._engine_sim._last_loop_ts = time.monotonic()
        if self._engine_live:
            self._engine_live.llm.set_model(self.var_llm_model.get())
            secs = max(1, int(self.var_llm_secs.get()))
            self._engine_live.cfg.llm_call_interval_ms = secs * 1000
            self._engine_live._last_loop_ts = time.monotonic()
        self.log_append("[LLM] Configuración aplicada.")

    def _apply_sizes(self):
        # SIM: editable
        try:
            if self._engine_sim:
                self._engine_sim.cfg.size_usd_sim = float(self.var_size_sim.get())
        except Exception:
            pass
        # LIVE: mínimo global si toggle
        if bool(self.var_use_min_live.get()):
            try:
                self._ensure_exchange()
                min_usd = self.exchange.global_min_notional_usd()
                usd = float(min_usd)
                self.var_size_live.set(round(usd, 2))
                if self._engine_live:
                    self._engine_live.cfg.size_usd_live = float(usd)
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {usd:.2f} USDT")
            except Exception as e:
                self.log_append(f"[ENGINE] Error obteniendo mínimo: {e}")
        else:
            self.ent_size_live.configure(state="normal")

    def _selected_symbol(self):
        item = self.tree.focus()
        return self.tree.item(item,"values")[0] if item else None

    def _update_min_marker(self):
        # solo informativo, global ya mostrado
        pass

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
        except Exception: pass
        self.lbl_rest.configure(text=f"REST: {gs.get('latency_rest_ms',0):.0f} ms")
        self.lbl_ws.configure(text=f"WS: {gs.get('latency_ws_ms',0):.0f} ms")

        # Tablas
        self._refresh_market_table(snap.get("pairs", []))
        self._refresh_open_orders(snap.get("open_orders", []))
        self._refresh_closed_orders(snap.get("closed_orders", []))

        # Razones
        reasons = snap.get("reasons", [])
        if reasons:
            self.txt_info.delete("1.0","end")
            for r in reasons:
                self.txt_info.insert("end", f"• {r}\n")
                self.log_append(f"[ENGINE] {r}")

        self.after(1000, self._tick_ui_refresh)

    def _refresh_market_table(self, pairs: List[Dict[str, Any]]):
        for i in self.tree.get_children():
            self.tree.delete(i)
        for p in pairs:
            item = self.tree.insert("", "end", values=(
                p.get("symbol",""),
                f"{p.get('score',0.0):.1f}",
                f"{p.get('pct_change_window',0.0):+.2f}",
                f"{(p.get('price_last',0.0)*1e8):.0f}",
                f"{(p.get('depth',{}).get('buy',0.0)+p.get('depth',{}).get('sell',0.0))/1000:.1f}",
            ))
            try:
                sc = float(p.get('score',0.0))
                tag = 'scoreLow'
                if sc >= 90: tag = 'score90'
                elif sc >= 80: tag = 'score80'
                elif sc >= 65: tag = 'score65'
                elif sc >= 60: tag = 'score64'
                elif sc >= 50: tag = 'score59'
                self.tree.item(item, tags=(tag,))
            except Exception:
                pass

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
