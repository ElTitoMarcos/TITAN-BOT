
import os, time, json, threading
from typing import List, Dict, Any, Optional
import ccxt
from websocket import WebSocketApp

BINANCE_KEY = os.getenv("BINANCE_KEY") or ""
BINANCE_SECRET = os.getenv("BINANCE_SECRET") or ""

STREAM_URL = "wss://stream.binance.com:9443/stream?streams={streams}"

def sym_to_stream(symbol: str) -> str:
    return symbol.replace("/", "").lower()

class _WSState:
    def __init__(self):
        self.lock = threading.Lock()
        self.books: Dict[str, Dict[str, Any]] = {}
        self.flow: Dict[str, Dict[str, Any]] = {}
        self.last_ms: float = 0.0
        self.symbols: List[str] = []
        self.ws = None
        self.running = False

class BinanceWS:
    def __init__(self):
        self.s = _WSState()
        self.th: Optional[threading.Thread] = None

    def _url(self, symbols: List[str]) -> str:
        parts = []
        for s in sorted(set(symbols)):
            ss = sym_to_stream(s)
            parts += [f"{ss}@depth5@100ms", f"{ss}@aggTrade"]
        return STREAM_URL.format(streams="/".join(parts))

    def start(self, symbols: List[str]):
        url = self._url(symbols)

        def on_message(ws, msg):
            try:
                now = time.time()*1000.0
                self.s.last_ms = now
                payload = json.loads(msg)
                stream = payload.get("stream","")
                data = payload.get("data", {})
                if "@depth5" in stream:
                    bids = [(float(p), float(q)) for p,q in data.get("b",[]) if float(q)>0]
                    asks = [(float(p), float(q)) for p,q in data.get("a",[]) if float(q)>0]
                    key = stream.split("@")[0]
                    with self.s.lock:
                        self.s.books[key] = {"bids": bids, "asks": asks, "ts": now}
                elif "@aggTrade" in stream:
                    key = stream.split("@")[0]
                    q = float(data.get("q",0.0) or 0.0)
                    is_buyer_maker = bool(data.get("m", False))
                    side = "sell" if is_buyer_maker else "buy"
                    with self.s.lock:
                        f = self.s.flow.get(key, {"buy":0,"sell":0,"qty_buy":0.0,"qty_sell":0.0,"streak":0,"last":None})
                        f[side] += 1
                        if side == "buy": f["qty_buy"] += q
                        else: f["qty_sell"] += q
                        if f["last"] == side: f["streak"] += (1 if side=="buy" else -1)
                        else: f["streak"] = 1 if side=="buy" else -1
                        f["last"] = side
                        f["ts"] = now
                        self.s.flow[key] = f
            except Exception:
                pass

        def on_error(ws, err): time.sleep(1.0)
        def on_close(ws, code, msg): 
            with self.s.lock: self.s.running = False

        def runner():
            while True:
                try:
                    ws = WebSocketApp(url, on_message=on_message, on_close=on_close, on_error=on_error)
                    with self.s.lock: self.s.ws = ws; self.s.running = True
                    ws.run_forever(ping_interval=20, ping_timeout=10)
                except Exception:
                    time.sleep(1.5)
                with self.s.lock:
                    if not self.s.running: break

        if self.th and self.th.is_alive():
            try: self.s.ws and self.s.ws.close()
            except Exception: pass
        self.th = threading.Thread(target=runner, daemon=True)
        self.th.start()

    def snapshot_for(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        out: Dict[str, Dict[str, Any]] = {}
        with self.s.lock:
            for sym in symbols:
                k = sym_to_stream(sym)
                book = self.s.books.get(k, {})
                flow = self.s.flow.get(k, {})
                bids = book.get("bids", []); asks = book.get("asks", [])
                bb = bids[0][0] if bids else 0.0
                ba = asks[0][0] if asks else 0.0
                mid = (bb+ba)/2.0 if bb and ba else (bb or ba or 0.0)
                volb = sum(q for _,q in bids[:5]); vola = sum(q for _,q in asks[:5])
                spread = (ba-bb) if (bb and ba) else 0.0
                imb = (volb/(volb+vola)) if (volb+vola)>0 else 0.5
                tf_buy = float(flow.get("buy",0)); tf_sell = float(flow.get("sell",0))
                buy_ratio = (tf_buy/(tf_buy+tf_sell)) if (tf_buy+tf_sell)>0 else 0.5
                out[sym] = {
                    "best_bid": bb, "best_ask": ba, "mid": mid,
                    "spread_abs": spread, "spread_pct": (spread/mid*100.0) if mid else 0.0,
                    "depth_buy": volb, "depth_sell": vola, "imbalance": imb,
                    "trade_flow": {"buy_ratio": buy_ratio, "streak": int(flow.get("streak",0))},
                }
        return out

    def latency_ms(self) -> float:
        return max(0.0, (time.time()*1000.0) - (self.s.last_ms or 0.0))

class BinanceExchange:
    _cached_universe: List[str] = []

    def __init__(self, rate_limit=True, sandbox=False):
        self.exchange = ccxt.binance({
            "enableRateLimit": rate_limit,
            "apiKey": BINANCE_KEY,
            "secret": BINANCE_SECRET,
            "options": {"defaultType": "spot"},
        })
        if sandbox and hasattr(self.exchange, "set_sandbox_mode"):
            self.exchange.set_sandbox_mode(True)
        self._markets_loaded = False
        self.ws = BinanceWS()
        self._usd_cache: Dict[str, float] = {}

    def set_api_keys(self, key: str, secret: str):
        self.exchange = ccxt.binance({
            "enableRateLimit": True,
            "apiKey": key,
            "secret": secret,
            "options": {"defaultType": "spot"},
        })
        self._markets_loaded = False

    def load_markets(self) -> int:
        if not self._markets_loaded:
            self.exchange.load_markets()
            self._markets_loaded = True
        return len(self.exchange.markets)

    def is_live_ready(self) -> bool:
        k = getattr(self.exchange, "apiKey", "") or ""
        s = getattr(self.exchange, "secret", "") or ""
        return bool(k and s)

    # ---------- WS helpers ----------
    def ensure_collector(self, symbols: List[str], interval_ms: int = 800) -> None:
        try:
            self.ws.start(symbols or [])
        except Exception:
            pass

    def ws_latency_ms(self) -> float:
        try:
            return float(self.ws.latency_ms())
        except Exception:
            return 0.0

    def market_summary_for(self, symbols: List[str]) -> Dict[str, Dict[str, Any]]:
        return self.ws.snapshot_for(symbols)

    # ---------- Universe + metrics ----------
    def fetch_universe(self, quote="BTC") -> List[str]:
        if self._cached_universe:
            return self._cached_universe
        self.load_markets()
        syms = []
        for s, m in self.exchange.markets.items():
            try:
                if not m.get("active"): continue
                if m.get("spot") is False: continue
                if (m.get("quote") or "").upper() != quote.upper(): continue
                syms.append(m.get("symbol") or s)
            except Exception:
                continue
        self._cached_universe = sorted(list(set(syms)))
        return self._cached_universe

    def fetch_top_metrics(self, symbols: List[str], limit: int = 200) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        if not symbols: return out
        try:
            tickers = self.exchange.fetch_tickers(symbols)
        except Exception:
            tickers = {}
        try:
            ws_snap = self.ws.snapshot_for(symbols)
        except Exception:
            ws_snap = {}

        for sym in symbols[:limit]:
            t = (tickers or {}).get(sym, {})
            last = float(t.get("last") or t.get("close") or 0.0) if t else 0.0
            ws = (ws_snap or {}).get(sym, {})
            bb = ws.get("best_bid", t.get("bid", last) if t else 0.0) or 0.0
            ba = ws.get("best_ask", t.get("ask", last) if t else 0.0) or 0.0
            mid = ws.get("mid") or ((bb+ba)/2.0 if (bb and ba) else last)
            spread_abs = abs(ba - bb) if (bb and ba) else 0.0
            volb = (ws.get("depth_buy", 0.0) or 0.0); vola = (ws.get("depth_sell", 0.0) or 0.0)
            imb = (volb / (volb + vola)) if (volb + vola) > 0 else 0.5

            mkt = (self.exchange.markets or {}).get(sym, {})
            precision = (mkt.get("precision") or {}).get("price")
            tick_size = None
            if precision is not None:
                try:
                    tick_size = 10 ** (-int(precision))
                except Exception:
                    tick_size = None
            if tick_size is None:
                info = mkt.get("info") or {}
                for f in info.get("filters", []):
                    if f.get("filterType") == "PRICE_FILTER":
                        ts = float(f.get("tickSize") or 0.0)
                        if ts > 0:
                            tick_size = ts
                            break
            tick_size = tick_size or 1e-8
            out.append({
                "symbol": sym,
                "price_last": last or mid or 0.0,
                "mid": mid or 0.0,
                "best_bid": bb, "best_ask": ba,
                "spread_abs": spread_abs,
                "pct_change_window": float(t.get("percentage") or 0.0) if t else 0.0,
                "depth": {"buy": volb, "sell": vola},
                "imbalance": imb,
                "trade_flow": ws.get("trade_flow", {"buy_ratio":0.5,"streak":0}),
                "micro_volatility": (spread_abs / (mid or 1.0)) if mid else 0.0,
                "tick_size": tick_size,
                "edge_est_bps": 0.0,
                "score": 0.0,
            })
        return out

    # ---------- Quotes -> USD ----------
    def _quote_to_usd(self, quote: str) -> float:
        q = (quote or "").upper()
        if q in ("USDT","USD","FDUSD","TUSD","BUSD"): return 1.0
        px = self._usd_cache.get(q)
        if px: return px
        try:
            t = self.exchange.fetch_ticker(f"{q}/USDT")
            px = float(t.get("last") or t.get("close") or 0.0)
            if px: self._usd_cache[q] = px; return px
        except Exception:
            pass
        return 0.0

    # ---------- Balances ----------
    def fetch_balances_summary(self) -> Dict[str, float]:
        try:
            bal = self.exchange.fetch_balance()
        except Exception:
            return {}
        total = bal.get("total") or {}
        usd = 0.0; btc = float(total.get("BTC") or 0.0)
        for coin, amt in total.items():
            a = float(amt or 0.0)
            if a <= 0: continue
            if coin.upper() in ("USDT","USD","FDUSD","TUSD","BUSD"):
                usd += a
            elif coin.upper() == "BTC":
                pass
            else:
                px = self._quote_to_usd(coin)
                if px: usd += a * px
        try:
            pbtc = float(self.exchange.fetch_ticker("BTC/USDT").get("last") or 0.0)
        except Exception:
            pbtc = 0.0
        usd += btc * (pbtc or 0.0)
        return {"balance_usd": float(usd), "balance_btc": float(btc)}

    # ---------- Mínimos ----------
    def global_min_order_btc(self) -> float:
        """Devuelve el mínimo en BTC calculado a partir del notional en USD."""
        usd = self.global_min_notional_usd()
        try:
            pbtc = float(self.exchange.fetch_ticker("BTC/USDT").get("last") or 0.0)
        except Exception:
            pbtc = 0.0
        return float(usd) / (pbtc or 1.0)

    def global_min_notional_usd(self) -> float:
        """Calcula el mayor ``MIN_NOTIONAL`` entre todos los pares */BTC y lo
        convierte a USD para obtener el mínimo absoluto con el que cumplir en
        cualquier mercado BTC.

<<<<<< codex/fix-binance-minimum-order-and-api-calls-9nb9vg
        No se aplica margen adicional; el pequeño colchón (+0.1 USDT) se añade
        al momento de enviar órdenes si así se configura desde la UI."""
=======
<<<<<< codex/fix-binance-minimum-order-and-api-calls-63gexs
        No se aplica margen adicional; el pequeño colchón (+0.01 USDT) se añade
        al momento de enviar órdenes si así se configura desde la UI."""
=======
<<<<<< codex/fix-binance-minimum-order-and-api-calls-4tzxux
        No se aplica margen adicional; el pequeño colchón (+0.1 USDT) se añade
        al momento de enviar órdenes si así se configura desde la UI."""
=======
        Se añade un margen de 0.01 USDT para asegurar que las órdenes
        cumplen con el mínimo requerido por Binance."""
        default = 5.0
        buffer = 0.01
>>>>>> main
>>>>>> main
>>>>>> main

        default_usd = 5.0

        # Recorre todos los pares BTC para encontrar el mayor MIN_NOTIONAL en BTC
        try:
            self.load_markets()
        except Exception:
            return float(default_usd)

        max_btc = 0.0
        for m in self.exchange.markets.values():
            try:
                if not m.get("active"): continue
                if m.get("spot") is False: continue
                if (m.get("quote") or "").upper() != "BTC":
                    continue
                val = None
                lim = ((m.get("limits") or {}).get("cost") or {}).get("min")
                if isinstance(lim, (int, float)) and lim and lim > 0:
                    val = float(lim)
                else:
                    info = m.get("info") or {}
                    for f in info.get("filters", []):
                        if f.get("filterType") in ("MIN_NOTIONAL", "NOTIONAL"):
                            v = float(f.get("minNotional") or f.get("notional") or 0.0)
                            if v > 0:
                                val = v
                                break
                if val is None:
                    continue
                if val > max_btc:
                    max_btc = val
            except Exception:
                continue

        if max_btc <= 0:
            return float(default_usd)

        try:
            pbtc = float(self.exchange.fetch_ticker("BTC/USDT").get("last") or 0.0)
        except Exception:
            pbtc = 0.0

        return float(max_btc * (pbtc or 0.0)) or float(default_usd)
