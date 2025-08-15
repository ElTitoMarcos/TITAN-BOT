import threading, time, math, csv, os, uuid
from typing import Dict, Any, List, Callable, Optional
from config import Defaults, AppState
from exchange_utils import BinanceExchange
from scoring import compute_score
from llm_client import LLMClient

class Engine(threading.Thread):
    """
    Motor principal con modos SIM/LIVE.
    - Snapshot del universo completo con WS+REST
    - LLM (OpenAI si hay clave; heurística si no)
    - Validación dura y ejecución (SIM/LIVE)
    - Razones cuando no opera
    - Tracking de órdenes abiertas/cerradas
    """

    def __init__(self, ui_push_snapshot: Callable[[Dict[str, Any]], None], ui_log: Callable[[str], None] | None = None, exchange=None, name: str = "SIM"):
        super().__init__(daemon=True)
        self.cfg = Defaults()
        self.state = AppState()
        self.exchange = exchange if exchange is not None else BinanceExchange(rate_limit=True, sandbox=False)
        self.ui_log = ui_log or (lambda msg: None)
        self.name = name
        self.llm = LLMClient(model=self.cfg.llm_model, temperature_operativo=self.cfg.llm_temperature, api_key=self.cfg.openai_api_key)
        self.ui_push_snapshot = ui_push_snapshot
        self._stop = threading.Event()

        self.mode: str = "SIM"  # "SIM" | "LIVE"
        self._last_actions: List[Dict[str, Any]] = []
        self._open_orders: Dict[str, Dict[str, Any]] = {}
        self._closed_orders: List[Dict[str, Any]] = []
        self._positions: Dict[str, Dict[str, Any]] = {}
        self._last_loop_ts: float = 0.0
        self._last_reasons: List[str] = []
        self._first_call_done: bool = False
        self._last_auto_ts: float = 0.0

        self._patch_history: List[tuple[Dict[str, Any], str]] = []
        self._last_patch_code: str = ""

        os.makedirs(self.cfg.log_dir, exist_ok=True)
        self._audit_file = os.path.join(self.cfg.log_dir, "audit.csv")
        if not os.path.exists(self._audit_file):
            with open(self._audit_file, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["ts","event","symbol","detail"])

    def stop(self):
        self._stop.set()

    def is_stopped(self) -> bool:
        return self._stop.is_set()

    # --------------------- Patches LLM ---------------------
    def apply_llm_patch(self, code: str):
        backup: Dict[str, Any] = {}
        local_ns: Dict[str, Any] = {}
        try:
            exec(code, {}, local_ns)
            for k, v in local_ns.items():
                backup[k] = getattr(self, k, None)
                setattr(self, k, v)
            self._patch_history.append((backup, code))
            self._last_patch_code = code
            self.ui_log(f"[LLM PATCH] aplicado: {list(local_ns.keys())}")
        except Exception as e:
            self.ui_log(f"[LLM PATCH] error: {e}")

    def revert_last_patch(self):
        if not self._patch_history:
            return
        backup, _ = self._patch_history.pop()
        for k, v in backup.items():
            if v is None:
                try:
                    delattr(self, k)
                except Exception:
                    pass
            else:
                setattr(self, k, v)
        self.ui_log("[LLM PATCH] revertido")

    # --------------------- Helpers simulación ---------------------
    def _sim_queue_limit(self, sym: str, price: float, qty_usd: float, side: str) -> str:
        oid = f"SIM-{uuid.uuid4().hex[:8].upper()}"
        self._open_orders[oid] = {
            "id": oid, "symbol": sym, "price": price, "qty_usd": qty_usd,
            "side": side, "mode": "SIM", "ts": int(time.time()*1000)
        }
        return oid

    def _try_fill_sim_orders(self, snapshot: Dict[str, Any]):
        # Revisa órdenes SIM y llena si cruza best bid/ask
        pairs = snapshot.get("pairs", [])
        to_close = []
        for oid, o in list(self._open_orders.items()):
            if o.get("mode") != "SIM":
                continue
            sym = o["symbol"]
            par = next((p for p in pairs if p.get("symbol")==sym), None)
            if not par:
                continue
            best_ask = par.get("best_ask", 0.0)
            best_bid = par.get("best_bid", 0.0)
            if o["side"] == "buy":
                # Cancel if order price is not the nearest buy to best ask
                if best_bid and o["price"] < best_bid:
                    self._open_orders.pop(oid, None)
                    self._log_audit("CANCEL", sym, "buy not at top bid")
                    continue
                bid_qty = par.get("bid_top_qty", 0.0)
                ask_qty = par.get("ask_top_qty", 0.0)
                total_qty = bid_qty + ask_qty
                if total_qty > 0:
                    if bid_qty <= 0.1 * total_qty:
                        self._open_orders.pop(oid, None)
                        self._log_audit("CANCEL", sym, "bid support <=10%")
                        continue
                    # if bid_qty >=60% we simply continue monitoring
                if best_ask and o["price"] >= best_ask:
                    self._register_fill(o, fill_price=best_ask)
                    to_close.append(oid)
            elif o["side"] == "sell" and best_bid and o["price"] <= best_bid:
                self._register_fill(o, fill_price=best_bid)
                to_close.append(oid)
        for oid in to_close:
            self._open_orders.pop(oid, None)

    def _register_fill(self, order: Dict[str, Any], fill_price: float):
        sym = order["symbol"]
        side = order["side"]
        qty_usd = float(order["qty_usd"])
        qty_base = qty_usd / max(1e-12, fill_price)  # proxy
        pos = self._positions.setdefault(sym, {"qty": 0.0, "avg": 0.0})
        if side == "buy":
            new_qty = pos["qty"] + qty_base
            pos["avg"] = (pos["avg"]*pos["qty"] + qty_base*fill_price) / max(1e-12, new_qty)
            pos["qty"] = new_qty
        else:
            pos["qty"] = pos["qty"] - qty_base
        trade = {
            "id": order.get("id",""),
            "symbol": sym,
            "side": side,
            "price": fill_price,
            "qty_usd": qty_usd,
            "mode": order.get("mode","SIM"),
            "ts": int(time.time()*1000)
        }
        self._closed_orders.append(trade)
        self._log_audit("FILL", sym, f"{side.upper()} {qty_usd:.2f} USD @ {fill_price} ({order.get('mode')})")

    def _sim_mark_to_market(self, pairs: List[Dict[str, Any]]):
        pnl_usd = 0.0
        for p in pairs:
            sym = p["symbol"]
            mid = p.get("mid", 0.0)
            if sym in self._positions and mid:
                pos = self._positions[sym]
                qty = pos.get("qty", 0.0)
                avg = pos.get("avg", 0.0)
                pnl_usd += qty * (mid - avg)
        self.state.pnl_intraday_usd = pnl_usd
        notional = max(1.0, self.cfg.initial_balance_usd)
        self.state.pnl_intraday_percent = 100.0 * pnl_usd / notional

    # --------------------- Núcleo ---------------------
    def _find_candidates(self, snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Marca todos los pares BTC como candidatos."""
        cands: List[Dict[str, Any]] = []
        for p in snapshot.get("pairs", []):
            p["is_candidate"] = True
            cands.append(p)
        return cands

    def build_snapshot(self) -> Dict[str, Any]:
        universe = self.exchange.fetch_universe("BTC")
        universe = list(dict.fromkeys(universe))[:200]
        pairs = self.exchange.fetch_top_metrics(universe)
        try:
            self.exchange.ensure_collector([p['symbol'] for p in pairs], interval_ms=800)
        except Exception:
            pass

        trends = self.exchange.fetch_trend_metrics([p['symbol'] for p in pairs])
        store = self.exchange.market_summary_for([pp['symbol'] for pp in pairs])
        for p in pairs:
            ms = store.get(p['symbol'], {})
            mid = ms.get('mid', p.get('mid', 0.0))
            p['mid'] = mid
            p['spread_pct'] = float(ms.get('spread_pct', 0.0))
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
                "mid": p.get("mid", 0.0),
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
            p['best_bid'] = ms.get('best_bid', p.get('best_bid', 0.0))
            p['best_ask'] = ms.get('best_ask', p.get('best_ask', 0.0))
            p['bid_top_qty'] = features['best_bid_qty']
            p['ask_top_qty'] = features['best_ask_qty']
            p['imbalance'] = features['imbalance']
            p['depth'] = {"buy": features['depth_buy'], "sell": features['depth_sell']}
            p["score"] = compute_score(features)
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

        pairs.sort(key=lambda x: (-x.get("score", 0.0), -x.get("edge_est_bps", 0.0)))
        pairs = pairs[: self.cfg.topN]

        try:
            _b = self.exchange.fetch_balances_summary()
            if _b:
                self.state.balance_usd = _b.get('balance_usd', self.state.balance_usd)
                self.state.balance_btc = _b.get('balance_btc', self.state.balance_btc)
        except Exception:
            pass
        if not self.state.balance_usd:
            self.state.balance_usd = max(self.state.balance_usd, 1000.0)
        self._sim_mark_to_market(pairs)

        # ---- Selección de candidatos ----
        candidates = self._find_candidates({
            "pairs": pairs,
            "config": {"fee_per_side": self.cfg.fee_per_side},
        })
        self.ui_log(
            f"[ENGINE {self.name}] Evaluados {len(pairs)} pares; {len(candidates)} candidatos"
        )
        snap = {
            "ts": int(time.time()*1000),
            "global_state": {
                **self.state.global_state_dict(),
                "latency_ws_ms": self.exchange.ws_latency_ms(),
            },
            "config": {
                "fee_per_side": self.cfg.fee_per_side,
                "opportunity_threshold_percent": self.cfg.opportunity_threshold_percent,
                "size_usd": self.cfg.size_usd_live if self.mode=="LIVE" else self.cfg.size_usd_sim,
                "max_cycles_per_pair_per_min": self.cfg.llm_max_actions_per_cycle,
                "weights": self.cfg.weights,
            },
            "pairs": pairs,
            "candidates": candidates,
            "pairs_all": pairs,
            "mode": self.mode,
            "engine_name": self.name,
            "open_orders": list(self._open_orders.values()),
            "closed_orders": list(self._closed_orders[-200:]),
            "market_store_summary": store,
            "reasons": list(self._last_reasons),
        }
        return snap
    def validate_actions(self, actions: List[Dict[str, Any]], snapshot: Dict[str, Any]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        thr = float(snapshot["config"]["opportunity_threshold_percent"]) * 100.0
        size_usd = float(snapshot["config"]["size_usd"])
        for a in actions:
            sym = a.get("symbol","")
            t = a.get("type","")
            price = float(a.get("price", 0.0) or 0.0)
            qty_usd = float(a.get("qty_usd", 0.0) or 0.0)
            if qty_usd <= 0.0 or qty_usd > size_usd:
                continue
            if "PLACE_" in t and price <= 0.0:
                continue
            par = next((p for p in snapshot["pairs"] if p.get("symbol")==sym), None)
            if not par:
                continue
            edge = float(par.get("edge_est_bps", 0.0))
            if edge < thr:
                continue
            out.append(a)
        return out
    def execute_actions(self, actions: List[Dict[str, Any]], snapshot: Dict[str, Any]):
        for a in actions:
            sym = a.get("symbol", "")
            t = a.get("type", "")
            price = float(a.get("price", 0.0) or 0.0)
            qty_usd = float(a.get("qty_usd", 0.0) or 0.0)

            if t == "PLACE_LIMIT_BUY":
                if self.mode == "SIM":
                    oid = self._sim_queue_limit(sym, price, qty_usd, side="buy")
                    self._log_audit("NEW", sym, f"SIM LIMIT BUY {qty_usd:.2f} USD @ {price} (oid {oid})")
                elif self.mode == "LIVE":
                    if not (self.state.live_confirmed and self.exchange.is_live_ready()):
                        self._last_reasons.append("LIVE bloqueado: falta Confirm LIVE o API keys.")
                        continue
                    try:
                        base, quote = sym.split("/")
                        quote_usd = self.exchange._quote_to_usd(quote)
                        base_usd_price = price * max(1e-12, quote_usd)
                        amount = qty_usd / max(1e-12, base_usd_price)
                        order = self.exchange.exchange.create_order(sym, "limit", "buy", amount, price, {})
                        oid = order.get("id", f"LIVE-{uuid.uuid4().hex[:8]}")
                        self._open_orders[oid] = {"id": oid, "symbol": sym, "price": price, "qty_usd": qty_usd, "side": "buy", "mode": "LIVE", "ts": int(time.time()*1000)}
                        self._log_audit("NEW", sym, f"LIVE LIMIT BUY {qty_usd:.2f} USD @ {price} (oid {oid})")
                    except Exception as e:
                        self._last_reasons.append(f"Error al crear orden LIVE BUY: {e}")

            elif t == "PLACE_LIMIT_SELL":
                if self.mode == "SIM":
                    oid = self._sim_queue_limit(sym, price, qty_usd, side="sell")
                    self._log_audit("NEW", sym, f"SIM LIMIT SELL {qty_usd:.2f} USD @ {price} (oid {oid})")
                elif self.mode == "LIVE":
                    if not (self.state.live_confirmed and self.exchange.is_live_ready()):
                        self._last_reasons.append("LIVE bloqueado: falta Confirm LIVE o API keys.")
                        continue
                    try:
                        base, quote = sym.split("/")
                        quote_usd = self.exchange._quote_to_usd(quote)
                        base_usd_price = price * max(1e-12, quote_usd)
                        amount = qty_usd / max(1e-12, base_usd_price)
                        order = self.exchange.exchange.create_order(sym, "limit", "sell", amount, price, {})
                        oid = order.get("id", f"LIVE-{uuid.uuid4().hex[:8]}")
                        self._open_orders[oid] = {"id": oid, "symbol": sym, "price": price, "qty_usd": qty_usd, "side": "sell", "mode": "LIVE", "ts": int(time.time()*1000)}
                        self._log_audit("NEW", sym, f"LIVE LIMIT SELL {qty_usd:.2f} USD @ {price} (oid {oid})")
                    except Exception as e:
                        self._last_reasons.append(f"Error al crear orden LIVE SELL: {e}")

            elif t == "CANCEL_ORDER":
                ref = a.get("ref_order_id")
                if ref:
                    if ref in self._open_orders:
                        self._open_orders.pop(ref, None)
                        self._log_audit("CANCEL", sym, f"Cancelada {ref} (SIM/LIVE cache)")
                    try:
                        self.exchange.exchange.cancel_order(ref, sym)
                    except Exception:
                        pass

            elif t == "MODIFY_ORDER":
                ref = a.get("ref_order_id")
                new_price = float(a.get("price", 0.0) or 0.0)
                if ref and new_price > 0.0:
                    if ref in self._open_orders:
                        o = self._open_orders[ref]
                        o["price"] = new_price
                        o["ts"] = int(time.time()*1000)
                        self._log_audit("MODIFY", sym, f"Modificada {ref} -> precio {new_price}")
                    try:
                        self.exchange.exchange.cancel_order(ref, sym)
                    except Exception:
                        pass
                    try:
                        cached = self._open_orders.get(ref, {"qty_usd": qty_usd, "side": "buy"})
                        side = cached.get("side", "buy")
                        base, quote = sym.split("/")
                        quote_usd = self.exchange._quote_to_usd(quote)
                        base_usd_price = new_price * max(1e-12, quote_usd)
                        amount = cached.get("qty_usd", qty_usd) / max(1e-12, base_usd_price)
                        order = self.exchange.exchange.create_order(sym, "limit", side, amount, new_price, {})
                        oid = order.get("id", f"LIVE-{uuid.uuid4().hex[:8]}")
                        self._open_orders[oid] = {"id": oid, "symbol": sym, "price": new_price, "qty_usd": cached.get("qty_usd", qty_usd), "side": side, "mode": "LIVE", "ts": int(time.time()*1000)}
                        self._log_audit("NEW", sym, f"LIVE REPLACE {side.upper()} {cached.get('qty_usd', qty_usd):.2f} @ {new_price} (oid {oid})")
                    except Exception as e:
                        self._last_reasons.append(f"Error al modificar LIVE: {e}")

            elif t == "CLOSE_POSITION_MARKET":
                par = next((p for p in snapshot["pairs"] if p.get("symbol")==sym), None)
                if par:
                    mid = float(par.get("mid", 0.0) or 0.0)
                    pos = self._positions.get(sym, {"qty": 0.0, "avg": 0.0})
                    qty = abs(pos.get("qty", 0.0))
                    if qty > 0 and mid > 0:
                        qty_usd_close = qty * mid
                        side = "sell" if pos.get("qty",0.0) > 0 else "buy"
                        self._register_fill({"symbol": sym, "side": side, "qty_usd": qty_usd_close, "mode":"SIM"}, fill_price=mid)
                        self._log_audit("CLOSE", sym, f"Cierre mercado SIM {side} {qty_usd_close:.2f} USD @ {mid}")

def _log_audit(self, event: str, sym: str, detail: str):
    # Asegura carpeta y tolera archivos bloqueados (Excel/AV)
    path = self._audit_file
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    except Exception:
        pass
    row = [int(time.time()*1000), event, sym, detail]
    for i in range(3):
        try:
            with open(path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
            break
        except PermissionError:
            time.sleep(0.2 * (i+1))
            # fallback a archivo alternativo con timestamp si sigue bloqueado
            if i == 2:
                alt = os.path.splitext(path)[0] + f".{int(time.time()*1000)}.csv"
                try:
                    with open(alt, "a", newline="", encoding="utf-8") as f:
                        csv.writer(f).writerow(row)
                except Exception:
                    pass
                break
    

    def _compute_reasons(self, actions: List[Dict[str, Any]], snapshot: Dict[str, Any], candidates: List[Dict[str, Any]] = None, open_count: int = 0) -> List[str]:
        candidates = candidates or []
        reasons: List[str] = []
        if self.mode == "LIVE" and not self.state.live_confirmed:
            reasons.append("No LIVE: Confirm LIVE está en OFF.")
        if self.mode == "LIVE" and not self.exchange.is_live_ready():
            reasons.append("No LIVE: faltan API keys de Binance.")
        if not candidates and open_count == 0:
            reasons.append("Sin candidatos (1 sat ≤ 2× comisión) y sin órdenes abiertas; no llamo al LLM.")
        if not actions and (candidates or open_count):
            reasons.append("LLM no propuso acciones (tick/comisión insuficiente o timeout).")
        if snapshot.get("pairs") == []:
            reasons.append("No hay pares disponibles en el universo */BTC.")
        return reasons

    def _should_call_llm(self) -> bool:
        now = time.monotonic()
        if (now - self._last_loop_ts) * 1000.0 >= self.cfg.llm_call_interval_ms:
            self._last_loop_ts = now
            return True
        return False

    def run(self):
        try:
            greet_msg = self.llm.greet("hola")
            if greet_msg:
                self.ui_log(f"[LLM] {greet_msg}")
                self._last_reasons = [f"LLM: {greet_msg}"]
        except Exception:
            pass
        while not self.is_stopped():
            try:
                snapshot = self.build_snapshot()
                self.ui_push_snapshot(snapshot)
                self._try_fill_sim_orders(snapshot)

                open_count = len(snapshot.get("open_orders", []))
                candidates = snapshot.get("candidates", [])

                do_call = False
                if not self._first_call_done and (open_count > 0 or len(candidates) > 0):
                    do_call = True
                    self._first_call_done = True
                    self._last_loop_ts = time.monotonic()

                if not do_call and (open_count > 0 or len(candidates) > 0):
                    do_call = self._should_call_llm()

                if do_call:
                    self.ui_log(
                        f"[ENGINE {self.name}] Enviando snapshot al LLM ({len(candidates)} candidatos, {open_count} órdenes abiertas)"
                    )
                else:
                    if not candidates:
                        self.ui_log(f"[ENGINE {self.name}] Skip LLM: no hay pares buenos")
                    if open_count == 0:
                        self.ui_log(f"[ENGINE {self.name}] Skip LLM: no hay órdenes abiertas")

                                # Autotrade (sin LLM) si hay buenas condiciones
                now_ms = time.time()*1000
                if candidates and (now_ms - self._last_auto_ts) > 1500:
                    top = candidates[0]
                    sym = top.get('symbol')
                    price = float(top.get('best_ask') or top.get('mid') or 0.0)
                    if price > 0:
                        usd = self.cfg.size_usd_sim if self.mode=="SIM" else self.cfg.size_usd_live
                        # Coloca LIMIT BUY
                        self.execute_actions([{"symbol": sym, "type": "PLACE_LIMIT_BUY", "price": price, "qty_usd": usd}], snapshot)
                        self._last_auto_ts = now_ms

                actions: List[Dict[str, Any]] = []
                greet_msg = ""
                if do_call:
                    try:
                        greet_msg = self.llm.greet("hola")
                        if greet_msg:
                            self.ui_log(f"[LLM] {greet_msg}")
                    except Exception:
                        greet_msg = ""

                    llm_out = self.llm.propose_actions({
                        **snapshot,
                        "config": {**snapshot["config"], "max_actions_per_cycle": self.cfg.llm_max_actions_per_cycle},
                    })
                    patch_code = llm_out.get("patch") or llm_out.get("patch_code")
                    if patch_code:
                        self.apply_llm_patch(str(patch_code))
                    actions = llm_out.get("actions", [])

                valid = self.validate_actions(actions, snapshot)
                if valid:
                    self.execute_actions(valid, snapshot)
                    self._last_reasons = []
                else:
                    self._last_reasons = self._compute_reasons(actions, snapshot, candidates, open_count)
                    for r in self._last_reasons:
                        self.ui_log(f"[ENGINE {self.name}] {r}")

                if greet_msg:
                    self._last_reasons.append(f"LLM: {greet_msg}")

                # Empuja estado nuevo
                self.ui_push_snapshot(self.build_snapshot())

            except Exception as e:
                self._log_audit("ERROR", "-", str(e))

            time.sleep(0.25)


    def _ensure_logs_dir(self):
        import os
        self._logs_dir = getattr(self, "_logs_dir", None)
        if not self._logs_dir:
            self._logs_dir = os.path.join(os.getcwd(), "logs")
            os.makedirs(self._logs_dir, exist_ok=True)
        return self._logs_dir

    def _save_snapshot_jsonl(self, snapshot: dict):
        try:
            import json, time, os
            d = self._ensure_logs_dir()
            path = os.path.join(d, "snapshots.jsonl")
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(snapshot, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def _write_llm_context_files(self, snapshot: dict):
        """
        Escribe ficheros de contexto para el LLM: últimos candidatos, órdenes y resumen de market store.
        """
        try:
            import json, os, time
            d = self._ensure_logs_dir()
            ts = int(time.time()*1000)
            ctx = {
                "ts": ts,
                "engine": getattr(self, "name", ""),
                "candidates": snapshot.get("pairs", []),
                "orders_open": snapshot.get("open_orders", []),
                "orders_closed": snapshot.get("closed_orders", []),
                "market_store_summary": snapshot.get("market_store_summary", {}),
                "global_state": snapshot.get("global_state", {}),
            }
            with open(os.path.join(d, "llm_context_latest.json"), "w", encoding="utf-8") as f:
                json.dump(ctx, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
