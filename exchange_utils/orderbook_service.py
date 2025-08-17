import json
import random
import threading
import time
from typing import Any, Dict, Optional

import requests
from websocket import WebSocketApp

from .rate_limiter import RateLimiter
from .subscription_manager import SubscriptionManager

STREAM_URL = "wss://stream.binance.com:9443/stream?streams={streams}"
REST_DEPTH = "https://api.binance.com/api/v3/depth"


class MarketDataHub:
    """Servicio que mantiene libros de órdenes usando snapshot + diffs."""

    def __init__(self, max_depth_symbols: int = 20) -> None:
      
        self._lock = threading.RLock()
        self._books: Dict[str, Dict[str, Any]] = {}
        self._streams: Dict[str, str] = {}
        self._ws: Optional[WebSocketApp] = None
        self._running = True
        self._rate_limiter = RateLimiter(6000)
        self._sub_mgr = SubscriptionManager(max_depth_symbols, self.unsubscribe_depth)
        self._th = threading.Thread(target=self._run, daemon=True)
        self._th.start()

    # --------------------- Gestión WS ---------------------
    def _build_url(self) -> str:
        with self._lock:
            streams = ["!bookTicker"] + [
                f"{s.lower()}@depth@{spd}" for s, spd in self._streams.items()
            ]
        return STREAM_URL.format(streams="/".join(streams))

    def _run(self) -> None:
        while self._running:
            url = self._build_url()

            def on_message(ws, msg):
                self._handle_message(msg)

            def on_error(ws, err):
                pass

            def on_close(ws, code, msg):
                with self._lock:
                    self._ws = None

            ws = WebSocketApp(url, on_message=on_message, on_error=on_error, on_close=on_close)
            with self._lock:
                self._ws = ws
            try:
                ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception:
                pass
            if not self._running:
                break
            time.sleep(random.uniform(1, 2))

    def _reconnect(self) -> None:
        with self._lock:
            if self._ws:
                try:
                    self._ws.close()
                except Exception:
                    pass

    # ------------------ Snapshot + diffs ------------------
    def _fetch_snapshot(self, symbol: str) -> None:
        def worker():
            try:
                self._rate_limiter.acquire(50)  # depth1000 weight
                r = requests.get(
                    REST_DEPTH, params={"symbol": symbol.upper(), "limit": 1000}, timeout=10
                )
                data = r.json()
                bids = {float(p): float(q) for p, q in data.get("bids", []) if float(q) > 0}
                asks = {float(p): float(q) for p, q in data.get("asks", []) if float(q) > 0}
                with self._lock:
                    self._books[symbol] = {
                        "bids": bids,
                        "asks": asks,
                        "lastUpdateId": int(data.get("lastUpdateId", 0)),
                        "ts": time.time(),
                    }
            except Exception:
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _handle_message(self, msg: str) -> None:
        try:
            payload = json.loads(msg)
            stream = payload.get("stream", "")
            data = payload.get("data", {})
            if "@depth" not in stream:
                return
            symbol = data.get("s")
            if not symbol:
                return
            U = int(data.get("U", 0))
            u = int(data.get("u", 0))
            bids = data.get("b", [])
            asks = data.get("a", [])
            with self._lock:
                book = self._books.get(symbol)
                if not book:
                    return
                last_id = int(book.get("lastUpdateId", 0))
            if u <= last_id:
                return
            if U > last_id + 1:
                self._fetch_snapshot(symbol)
                return
            with self._lock:
                bb = book["bids"]
                for p, q in bids:
                    price = float(p)
                    qty = float(q)
                    if qty == 0:
                        bb.pop(price, None)
                    else:
                        bb[price] = qty
                aa = book["asks"]
                for p, q in asks:
                    price = float(p)
                    qty = float(q)
                    if qty == 0:
                        aa.pop(price, None)
                    else:
                        aa[price] = qty
                book["lastUpdateId"] = u
                book["ts"] = time.time()
        except Exception:
            pass

    # ----------------------- API pública -----------------------
    def subscribe_depth(self, symbol: str, speed: str = "100ms") -> None:
        symbol = symbol.upper()
        if not self._sub_mgr.request_symbol(symbol):
            return

        with self._lock:
            if symbol in self._streams:
                return
            self._streams[symbol] = speed
        self._fetch_snapshot(symbol)
        self._reconnect()

    def unsubscribe_depth(self, symbol: str) -> None:
        symbol = symbol.upper()
        with self._lock:
            self._streams.pop(symbol, None)
            self._books.pop(symbol, None)
        self._sub_mgr.remove(symbol)
        self._reconnect()

    def get_order_book(self, symbol: str, top: int = 5) -> Optional[Dict[str, Any]]:
        symbol = symbol.upper()
        with self._lock:
            book = self._books.get(symbol)
            if not book:
                return None
            bids = sorted(book["bids"].items(), key=lambda x: x[0], reverse=True)[:top]
            asks = sorted(book["asks"].items(), key=lambda x: x[0])[:top]
            return {
                "bids": bids,
                "asks": asks,
                "ts": book.get("ts", 0.0),
                "lastUpdateId": book.get("lastUpdateId", 0),
            }

    def close(self) -> None:
        self._running = False
        self._reconnect()


market_data_hub = MarketDataHub()
