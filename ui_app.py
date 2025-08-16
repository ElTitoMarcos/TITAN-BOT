import threading, queue, time, json, os, copy
import ttkbootstrap as tb
from ttkbootstrap.constants import *
from ttkbootstrap.scrolled import ScrolledText
from tkinter import ttk
from typing import Dict, Any, List
from config import UIColors, Defaults, AppState
from engine import Engine
from scoring import compute_score
from test_manager import TestManager
from llm_client import LLMClient

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
        except Exception:
            pass

    def _fmt_sats(self, price: float) -> str:
        """Formato legible para precios en satoshis"""
        try:
            sats = int(float(price) * 1e8)
        except Exception:
            sats = 0
        if sats >= 1000:
            return f"{sats:,}".replace(",", ".")
        return str(sats)

    def _coerce(self, val: str, col: str):
        v = str(val)
        if col == "symbol":
            return v
        if col == "price_sats":
            v = v.replace(".", "")
        try:
            return float(v)
        except Exception:
            return v

    def _sort_tree(self, col: str, reverse: bool, preserve: bool = False):
        data = [
            (self._coerce(self.tree.set(k, col), col), k)
            for k in self.tree.get_children("")
        ]
        try:
            data.sort(reverse=reverse)
        except Exception:
            pass
        for idx, (_, k) in enumerate(data):
            self.tree.move(k, "", idx)
        if not preserve:
            self._sort_col, self._sort_reverse = col, reverse
            self.tree.heading(col, command=lambda: self._sort_tree(col, not reverse))

    def __init__(self):
        super().__init__(title="AutoBTC - Punto a Punto", themename="cyborg")
        self.geometry("1400x860")
        self.minsize(1300, 760)

        self.colors = UIColors()
        self.cfg = Defaults()
        self.state = AppState()
        self._snapshot: Dict[str, Any] = {}
        self._last_pair_values: Dict[str, Dict[str, float]] = {}
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._engine_sim: Engine | None = None
        self._engine_live: Engine | None = None
        self.exchange = None
        self._tester: TestManager | None = None
        self.var_min_orders = tb.IntVar(value=50)
        self.metric_defaults = dict(self.cfg.weights)
        self.metric_vars: Dict[str, tb.BooleanVar] = {}

        self._keys_file = os.path.join(os.path.dirname(__file__), ".api_keys.json")

        self._build_ui()
        self._load_saved_keys()
        self._lock_controls(True)
        self.after(250, self._poll_log_queue)
        self.after(4000, self._tick_ui_refresh)

        # Precarga de mercado y balance
        self._warmup_thread = threading.Thread(target=self._warmup_load_market, daemon=True)
        self._warmup_thread.start()
        self.after(2000, self._tick_balance_refresh)
        self._last_cand_refresh = 0.0

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

        # Mercado (full width)
        frm_mkt = ttk.Labelframe(self, text="Mercado", padding=6)
        frm_mkt.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=(10,10), pady=(0,8))
        frm_mkt.rowconfigure(0, weight=1); frm_mkt.columnconfigure(0, weight=1)
        cols = (
            "symbol","score","pct","price_sats",
            "trend_w","trend_d","trend_h","trend_m",
            "pressure","flow","depth_buy","depth_sell",
            "momentum","spread","micro_vol",
        )
        self.tree = ttk.Treeview(frm_mkt, columns=cols, show="headings")
        style = tb.Style(); style.configure("Treeview", font=("Consolas", 10))
        self._sort_col: str | None = None
        self._sort_reverse: bool = False
        headers = [
            ("symbol", "Símbolo", 150, "w"),
            ("score", "Score", 70, "e"),
            ("pct", "%24h", 70, "e"),
            ("price_sats", "Precio (sats)", 120, "e"),
            ("trend_w", "Trend W", 90, "e"),
            ("trend_d", "Trend D", 90, "e"),
            ("trend_h", "Trend H", 90, "e"),
            ("trend_m", "Trend M", 90, "e"),
            ("pressure", "Presión", 90, "e"),
            ("flow", "Flujo", 90, "e"),
            ("depth_buy", "DepthBuy", 90, "e"),
            ("depth_sell", "DepthSell", 90, "e"),
            ("momentum", "Momentum", 90, "e"),
            ("spread", "Spread", 90, "e"),
            ("micro_vol", "MicroVol", 90, "e"),
        ]
        for c, txt, w, an in headers:
            self.tree.heading(c, text=txt, command=lambda col=c: self._sort_tree(col, False))
            self.tree.column(c, width=w, anchor=an, stretch=True)
        vsb = ttk.Scrollbar(frm_mkt, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.grid(row=0, column=0, sticky="nsew"); vsb.grid(row=0, column=1, sticky="ns")

        # Colores por score (fino) en texto
        self.tree.tag_configure('score95', foreground='#166534')
        self.tree.tag_configure('score90', foreground='#15803d')
        self.tree.tag_configure('score80', foreground='#16a34a')
        self.tree.tag_configure('score70', foreground='#22c55e')
        self.tree.tag_configure('score60', foreground='#84cc16')
        self.tree.tag_configure('score50', foreground='#eab308')
        self.tree.tag_configure('score40', foreground='#f97316')
        self.tree.tag_configure('score30', foreground='#f43f5e')
        self.tree.tag_configure('scoreLow', foreground='#b91c1c')
        self.tree.tag_configure('veto', background='#ef4444', foreground='white')
        self.tree.tag_configure('candidate', font=('Consolas', 10, 'bold'))

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
        right.rowconfigure(6, weight=0)
        right.rowconfigure(7, weight=1)

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

        self.var_oai_key = tb.StringVar(value="")
        ttk.Label(frm_api, text="ChatGPT API Key").grid(row=2, column=0, sticky="w")
        ttk.Entry(frm_api, textvariable=self.var_oai_key, width=28, show="•").grid(row=2, column=1, sticky="e")
        ttk.Button(frm_api, text="Confirmar APIs", command=self._confirm_apis).grid(row=0, column=2, rowspan=3, padx=6)

        # LLM config minimal: model + seconds + apply button
        frm_llm = ttk.Labelframe(right, text="LLM (decisor)", padding=8)
        frm_llm.grid(row=3, column=0, sticky="ew", pady=6)
        self.var_llm_model = tb.StringVar(value=self.cfg.llm_model)
        ttk.Label(frm_llm, text="Modelo").grid(row=0, column=0, sticky="w")
        ttk.Combobox(frm_llm, textvariable=self.var_llm_model, values=["gpt-4o","gpt-4o-mini","gpt-4.1","gpt-4.1-mini"], width=14, state="readonly").grid(row=0, column=1, sticky="e")
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
        self.btn_tests = ttk.Button(frm_info, text="Iniciar Testeos", command=self._toggle_tests)
        self.btn_tests.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(4,0))
        ttk.Button(frm_info, text="Revertir patch", command=self._revert_patch).grid(row=3, column=0, sticky="ew", pady=(4,0))
        ttk.Button(frm_info, text="Aplicar a LIVE", command=self._apply_winner_live).grid(row=3, column=1, sticky="ew", pady=(4,0))

        # Métricas de Score
        frm_met = ttk.Labelframe(right, text="Métricas Score", padding=8)
        frm_met.grid(row=6, column=0, sticky="ew", pady=6)

        for idx, (key, label) in enumerate([
            ("trend_w", "Trend semanal"),
            ("trend_d", "Trend diaria"),
            ("pressure", "Presión libro"),
            ("flow", "Flujo órdenes"),
            ("trend_h", "Trend horas"),
            ("depth", "Profundidad"),
            ("trend_m", "Trend minutos"),
            ("momentum", "Momentum"),
            ("spread", "Spread"),
            ("microvol", "Microvol"),
        ]):
            var = tb.BooleanVar(value=self.cfg.weights.get(key, 0) > 0)
            self.metric_vars[key] = var
            ttk.Checkbutton(frm_met, text=label, variable=var, command=self._apply_metric_weights).grid(row=idx//2, column=idx%2, sticky="w")

        # Log
        frm_log = ttk.Labelframe(right, text="Log", padding=8)
        frm_log.grid(row=7, column=0, sticky="nsew", pady=6)
        frm_log.rowconfigure(0, weight=1); frm_log.columnconfigure(0, weight=1)
        self.txt_log = ScrolledText(frm_log, height=6, autohide=True, wrap="none")
        self.txt_log.grid(row=0, column=0, sticky="nsew")

        # Bindings
        self.var_bot_sim.trace_add("write", self._on_bot_sim)
        self.var_bot_live.trace_add("write", self._on_bot_live)
        self.var_live_confirm.trace_add("write", self._on_live_confirm)
        self.tree.bind("<<TreeviewSelect>>", self._on_pair_select)

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

    # ------------------- Warmup -------------------
    def _warmup_load_market(self):
        try:
            self._ensure_exchange()
            uni = [s for s in self.exchange.fetch_universe("BTC") if s.endswith("/BTC")][:100]
            if uni:
                pairs = self.exchange.fetch_top_metrics(uni[: min(20, len(uni))])
                try:
                    self.exchange.ensure_collector([p['symbol'] for p in pairs], interval_ms=800)
                except Exception:
                    pass

                store = self.exchange.market_summary_for([p['symbol'] for p in pairs])
                trends = self.exchange.fetch_trend_metrics([p['symbol'] for p in pairs])
                for p in pairs:
                    ms = store.get(p['symbol'], {})
                    tr = trends.get(p['symbol'], {})
                    features = {
                        "imbalance": ms.get("imbalance", p.get("imbalance", 0.5)),
                        "spread_abs": ms.get("spread_abs", abs(p.get("best_ask",0.0)-p.get("best_bid",0.0))),
                        "pct_change_window": p.get("pct_change_window", 0.0),
                        "depth_buy": ms.get("depth_buy", p.get("depth",{}).get("buy",0.0)),
                        "depth_sell": ms.get("depth_sell", p.get("depth",{}).get("sell",0.0)),
                        "best_bid_qty": ms.get("bid_top_qty", p.get("bid_top_qty",0.0)),
                        "best_ask_qty": ms.get("ask_top_qty", p.get("ask_top_qty",0.0)),
                        "trade_flow_buy_ratio": ms.get("trade_flow", {}).get("buy_ratio", p.get("trade_flow", {}).get("buy_ratio", 0.5)),
                        "mid": ms.get("mid", p.get("mid",0.0)),
                        "spread_bps": p.get("spread_bps", 0.0),
                        "tick_price_bps": p.get("tick_price_bps", 8.0),
                        "base_volume": p.get("depth", {}).get("buy", 0.0) + p.get("depth", {}).get("sell", 0.0),
                        "micro_volatility": p.get("micro_volatility", 0.0),
                        "trend_w": tr.get("trend_w", 0.0),
                        "trend_d": tr.get("trend_d", 0.0),
                        "trend_h": tr.get("trend_h", 0.0),
                        "trend_m": tr.get("trend_m", 0.0),
                        "weights": self.cfg.weights,
                    }
                    p['best_bid'] = ms.get('best_bid', p.get('best_bid',0.0))
                    p['best_ask'] = ms.get('best_ask', p.get('best_ask',0.0))
                    p['bid_top_qty'] = features['best_bid_qty']
                    p['ask_top_qty'] = features['best_ask_qty']
                    p['imbalance'] = features['imbalance']
                    p['depth'] = {"buy": features['depth_buy'], "sell": features['depth_sell']}
                    p['score'] = compute_score(features)
                if not self._snapshot:
                    self._refresh_market_table(pairs, [])
            # Mínimo global BTC en el marcador
            try:
                min_usd = self.exchange.global_min_notional_usd()
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {min_usd:.2f} USDT")
            except Exception:
                pass
        except Exception as e:
            self.log_append(f"[ENGINE] Warmup error: {e}")

    def _refresh_market_candidates(self):
        try:
            self._ensure_exchange()
            uni = [s for s in self.exchange.fetch_universe("BTC") if s.endswith("/BTC")][:100]

            if not uni:
                return
            pairs = self.exchange.fetch_top_metrics(uni[: min(20, len(uni))])
            try:
                self.exchange.ensure_collector([p['symbol'] for p in pairs], interval_ms=800)
            except Exception:
                pass
            store = self.exchange.market_summary_for([p['symbol'] for p in pairs])
            trends = self.exchange.fetch_trend_metrics([p['symbol'] for p in pairs])
            cands: List[Dict[str, Any]] = []

            for p in pairs:
                ms = store.get(p['symbol'], {})
                tr = trends.get(p['symbol'], {})
                features = {
                    "imbalance": ms.get("imbalance", p.get("imbalance", 0.5)),
                    "spread_abs": ms.get("spread_abs", abs(p.get("best_ask",0.0)-p.get("best_bid",0.0))),
                    "pct_change_window": p.get("pct_change_window", 0.0),
                    "depth_buy": ms.get("depth_buy", p.get("depth",{}).get("buy",0.0)),
                    "depth_sell": ms.get("depth_sell", p.get("depth",{}).get("sell",0.0)),
                    "best_bid_qty": ms.get("bid_top_qty", p.get("bid_top_qty",0.0)),
                    "best_ask_qty": ms.get("ask_top_qty", p.get("ask_top_qty",0.0)),
                    "trade_flow_buy_ratio": ms.get("trade_flow", {}).get("buy_ratio", p.get("trade_flow", {}).get("buy_ratio", 0.5)),
                    "mid": ms.get("mid", p.get("mid",0.0)),
                    "spread_bps": p.get("spread_bps", 0.0),
                    "tick_price_bps": p.get("tick_price_bps", 8.0),
                    "base_volume": p.get("depth", {}).get("buy", 0.0) + p.get("depth", {}).get("sell", 0.0),
                    "micro_volatility": p.get("micro_volatility", 0.0),
                    "trend_w": tr.get("trend_w", 0.0),
                    "trend_d": tr.get("trend_d", 0.0),
                    "trend_h": tr.get("trend_h", 0.0),
                    "trend_m": tr.get("trend_m", 0.0),
                    "weights": self.cfg.weights,
                }
                p['best_bid'] = ms.get('best_bid', p.get('best_bid',0.0))
                p['best_ask'] = ms.get('best_ask', p.get('best_ask',0.0))
                p['bid_top_qty'] = features['best_bid_qty']
                p['ask_top_qty'] = features['best_ask_qty']
                p['imbalance'] = features['imbalance']
                p['depth'] = {"buy": features['depth_buy'], "sell": features['depth_sell']}
                p['score'] = compute_score(features)
                tot = features['best_bid_qty'] + features['best_ask_qty']
                p['pressure'] = features['best_bid_qty']/tot if tot else 0.0
                p['flow'] = features.get('trade_flow_buy_ratio',0.5)
                p['trend_w'] = features['trend_w']
                p['trend_d'] = features['trend_d']
                p['trend_h'] = features['trend_h']
                p['trend_m'] = features['trend_m']
                p['depth_buy'] = features['depth_buy']
                p['depth_sell'] = features['depth_sell']
                p['momentum'] = abs(features.get('pct_change_window',0.0))
                p['spread_abs'] = features['spread_abs']
                p['micro_volatility'] = features['micro_volatility']
                p['is_candidate'] = True
                cands.append(p)
            cands.sort(key=lambda x: x.get('score',0.0), reverse=True)

            if not ((self._engine_sim and self._engine_sim.is_alive()) or (self._engine_live and self._engine_live.is_alive())):
                self._snapshot = {**self._snapshot, "pairs": pairs, "candidates": cands}
            self._refresh_market_table(pairs, cands)
        except Exception as e:
            self.log_append(f"[ENGINE] Error al refrescar mercado: {e}")

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
        self._engine_sim.cfg = copy.deepcopy(self.cfg)
        self._engine_sim.cfg.size_usd_sim = float(self.var_size_sim.get())
        # LLM
        self._engine_sim.llm.set_model(self.var_llm_model.get())
        self._engine_sim.start()

    def _start_engine_live(self):
        def push_snapshot(snap: Dict[str, Any]):
            self._snapshot = snap
        self._ensure_exchange()
        self._engine_live = Engine(
            ui_push_snapshot=push_snapshot,
            ui_log=self.log_append,
            exchange=self.exchange,
            name="LIVE"
        )
        self._engine_live.mode = "LIVE"
        # tamaño LIVE (mínimo global si toggle ON)
        if bool(self.var_use_min_live.get()):
            try:
                min_usd = self.exchange.global_min_notional_usd()
                usd = float(min_usd) + 0.1
                self._engine_live.cfg.size_usd_live = float(
                    usd if usd > 0 else self._engine_live.cfg.size_usd_live
                )
                self.var_size_live.set(round(self._engine_live.cfg.size_usd_live, 2))
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {min_usd:.2f} USDT")
            except Exception:
                pass
        # LLM
        self._engine_live.llm.set_model(self.var_llm_model.get())
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
        if not key or not sec:
            self.log_append("[ENGINE] Claves de Binance incompletas.")
            return False
        self._ensure_exchange()
        self.exchange.set_api_keys(key, sec)
        if self._engine_sim:
            self._engine_sim.exchange.set_api_keys(key, sec)
        if self._engine_live:
            self._engine_live.exchange.set_api_keys(key, sec)
        self.log_append("[ENGINE] Claves de Binance aplicadas.")
        try:
            self.exchange.load_markets()
        except Exception as e:
            self.log_append(f"[ENGINE] No se pudieron verificar las claves ({e}). Continuando de todos modos.")
        return True

    def _apply_openai_key(self):
        k = self.var_oai_key.get().strip()
        if not k:
            self.log_append("[LLM] Clave de ChatGPT no proporcionada. Usando heurística local.")
            return True
        client = None
        if self._engine_sim:
            self._engine_sim.llm.set_api_key(k)
            self._engine_sim.cfg.openai_api_key = k
            client = self._engine_sim.llm
        if self._engine_live:
            self._engine_live.llm.set_api_key(k)
            self._engine_live.cfg.openai_api_key = k
            client = client or self._engine_live.llm
        if client is None:
            from llm_client import LLMClient
            client = LLMClient(api_key=k)
        self.log_append("[LLM] Clave de ChatGPT aplicada.")
        try:
            if client._openai:
                client._openai.models.list()
                return True
        except Exception:
            pass
        return False

    def _confirm_apis(self):
        ok_bin = self._apply_binance_keys()
        ok_oai = self._apply_openai_key()
        if ok_bin:
            if not ok_oai:
                self.log_append("[APP] Clave de ChatGPT inválida. Continuando sin LLM.")
            self._save_api_keys()
            self._lock_controls(False)
            self.log_append("[APP] APIs verificadas. Desbloqueando interfaz.")
            self._refresh_market_candidates()
        else:
            self.log_append("[APP] Error verificando APIs de Binance. Revísalas e intenta nuevamente.")

    def _apply_llm(self):
        if self._engine_sim:
            self._engine_sim.llm.set_model(self.var_llm_model.get())
        if self._engine_live:
            self._engine_live.llm.set_model(self.var_llm_model.get())
        self.log_append("[LLM] Configuración aplicada.")

    def _apply_metric_weights(self):
        for key, var in self.metric_vars.items():
            self.cfg.weights[key] = self.metric_defaults.get(key, 0) if var.get() else 0
        if self._engine_sim:
            self._engine_sim.cfg.weights = dict(self.cfg.weights)
        if self._engine_live:
            self._engine_live.cfg.weights = dict(self.cfg.weights)
        threading.Thread(target=self._refresh_market_candidates, daemon=True).start()

    def _toggle_tests(self):
        if self._tester and self._tester.is_alive():
            self._tester.stop()
            try:
                self._tester.join(timeout=2)
            except Exception:
                pass
            self._tester = None
            self.btn_tests.configure(text="Iniciar Testeos")
            self.log_append("[TEST] Ciclo de testeo detenido")
            return
        if self._tester and not self._tester.is_alive():
            self._tester = None
        def info(msg: str):
            def upd():
                self.txt_info.insert("end", msg + "\n")
                self.txt_info.see("end")
            self.after(0, upd)
        self.txt_info.delete("1.0", "end")
        min_orders = max(1, int(self.var_min_orders.get()))
        llm = self._engine_sim.llm if self._engine_sim else LLMClient(model=self.var_llm_model.get(), api_key=self.var_oai_key.get())
        self._tester = TestManager(copy.deepcopy(self.cfg), llm, self.log_append, info, min_orders=min_orders)
        self._tester.start()
        self.btn_tests.configure(text="Detener Testeos")
        self.log_append("[TEST] Ciclo de testeo iniciado")
        self.after(500, self._check_tester_done)

    def _check_tester_done(self):
        if self._tester and self._tester.is_alive():
            self.after(500, self._check_tester_done)
            return
        if not self._tester:
            return
        if self._engine_sim and self._tester.winner_cfg:
            self._engine_sim.cfg = copy.deepcopy(self._tester.winner_cfg)
            self.cfg = copy.deepcopy(self._tester.winner_cfg)
            self.log_append("[TEST] Config ganadora aplicada al bot SIM.")
        self.btn_tests.configure(text="Iniciar Testeos")
        self.log_append("[TEST] Ciclo de testeo finalizado")
        self._tester = None

    def _apply_winner_live(self):
        if not self._tester or not self._tester.winner_cfg:
            self.log_append("[TEST] No hay versión ganadora disponible")
            return
        if not self._engine_live:
            self.log_append("[TEST] Motor LIVE no iniciado")
            return
        self._engine_live.cfg = copy.deepcopy(self._tester.winner_cfg)
        self.log_append("[TEST] Config LIVE actualizada con variante ganadora")

    def _send_llm_query(self):
        q = self.var_llm_query.get().strip()
        if not q:
            return
        def worker():
            eng = self._engine_live or self._engine_sim
            resp = ""
            if eng and eng.llm:
                resp = eng.llm.ask(q)
            else:
                try:
                    from llm_client import LLMClient
                    llm = LLMClient(model=self.var_llm_model.get(), api_key=self.var_oai_key.get())
                    resp = llm.ask(q)
                except Exception:
                    resp = ""
            def update():
                self.txt_llm_resp.delete("1.0", "end")
                self.txt_llm_resp.insert("end", resp or "[sin respuesta]")
                lines = int(self.txt_llm_resp.index('end-1c').split('.')[0])
                self.txt_llm_resp.configure(height=min(10, max(3, lines)))
            self.after(0, update)
        threading.Thread(target=worker, daemon=True).start()

    def _revert_patch(self):
        if self._engine_sim:
            self._engine_sim.revert_last_patch()
        if self._engine_live:
            self._engine_live.revert_last_patch()
        self.log_append("[LLM] Patches revertidos.")

    def _apply_patch_live(self):
        if self._engine_sim and self._engine_live and getattr(self._engine_sim, "_last_patch_code", ""):
            code = self._engine_sim._last_patch_code
            self._engine_live.apply_llm_patch(code)
            self.log_append("[LLM] Patch aplicado al LIVE.")

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
                usd = float(min_usd) + 0.1
                self.var_size_live.set(round(usd, 2))
                if self._engine_live:
                    self._engine_live.cfg.size_usd_live = float(usd)
                self.ent_size_live.configure(state="disabled")
                self.lbl_min_marker.configure(text=f"Mínimo permitido por Binance: {min_usd:.2f} USDT")
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
        except Exception:
            pass

        # Tablas
        self._refresh_market_table(
            snap.get("pairs", []),
            snap.get("candidates", []),
        )
        threading.Thread(target=self._refresh_market_candidates, daemon=True).start()

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

    def _on_pair_select(self, _event):
        sel = self.tree.selection()
        if not sel:
            return
        self.after(300, lambda: [self.tree.selection_remove(i) for i in sel])

    def _refresh_market_table(self, pairs: List[Dict[str, Any]], candidates: List[Dict[str, Any]]):
        cand_syms = {c.get("symbol") for c in candidates}
        pairs = list(pairs)
        existing_rows = {self.tree.set(iid, "symbol"): iid for iid in self.tree.get_children()}
        pairs_sorted = sorted(pairs, key=lambda p: p.get("score", 0.0), reverse=True)
        for p in pairs_sorted:
            sym = p.get("symbol", "")
            cache = self._last_pair_values.setdefault(sym, {})

            def _nz(field: str) -> float:
                val = float(p.get(field, 0.0) or 0.0)
                if val != 0.0:
                    cache[field] = val
                else:
                    val = float(cache.get(field, 0.0))
                return val

            score = _nz('score')
            pct = _nz('pct_change_window')
            mid = _nz('mid')
            trend_w = _nz('trend_w')
            trend_d = _nz('trend_d')
            trend_h = _nz('trend_h')
            trend_m = _nz('trend_m')
            pressure = _nz('pressure')
            flow = _nz('flow')
            depth_buy = _nz('depth_buy')
            depth_sell = _nz('depth_sell')
            momentum = _nz('momentum')
            spread_abs = _nz('spread_abs')
            micro_vol = _nz('micro_volatility')

            values = (
                sym,
                f"{score:.1f}",
                f"{pct:+.2f}",
                self._fmt_sats(mid),
                f"{trend_w:+.2f}",
                f"{trend_d:+.2f}",
                f"{trend_h:+.2f}",
                f"{trend_m:+.2f}",
                f"{pressure:.2f}",
                f"{flow:.2f}",
                f"{depth_buy:.2f}",
                f"{depth_sell:.2f}",
                f"{momentum:.2f}",
                f"{spread_abs:.8f}",
                f"{micro_vol:.4f}",
            )
            item = existing_rows.pop(sym, None)
            if item:
                self.tree.item(item, values=values)
            else:
                item = self.tree.insert("", "end", values=values)
            try:
                sc = float(p.get('score', 0.0))
            except Exception:
                sc = 0.0

            tag = 'scoreLow'
            if sc >= 95:
                tag = 'score95'
            elif sc >= 90:
                tag = 'score90'
            elif sc >= 80:
                tag = 'score80'
            elif sc >= 70:
                tag = 'score70'
            elif sc >= 60:
                tag = 'score60'
            elif sc >= 50:
                tag = 'score50'
            elif sc >= 40:
                tag = 'score40'
            elif sc >= 30:
                tag = 'score30'

            tags = [tag]
            if sym in cand_syms:
                tags.append('candidate')
            self.tree.item(item, tags=tuple(tags))
        for iid in existing_rows.values():
            self.tree.delete(iid)
        if self._sort_col:
            self._sort_tree(self._sort_col, self._sort_reverse, preserve=True)

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
