import threading
import time
from typing import Any, Dict, Optional

import requests

EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"


class ExchangeMeta:
    """Cache for Binance exchange metadata (symbol filters)."""

    def __init__(self, ttl: float = 600.0, session: Optional[requests.Session] = None) -> None:
        self.ttl = ttl
        self._session = session or requests.Session()
        self._lock = threading.RLock()
        self._cache: Dict[str, tuple[Dict[str, Any], float]] = {}

    def get_symbol_filters(self, symbol: str) -> Dict[str, Any]:
        symbol = symbol.upper()
        now = time.time()
        with self._lock:
            data = self._cache.get(symbol)
            if data and data[1] > now:
                return data[0]
        result: Dict[str, Any] = {}
        try:
            r = self._session.get(EXCHANGE_INFO_URL, params={"symbol": symbol}, timeout=10)
            payload = r.json()
            info = payload.get("symbols", [{}])[0]
            filters = {f.get("filterType"): f for f in info.get("filters", [])}
            pf = filters.get("PRICE_FILTER")
            if pf:
                result["priceIncrement"] = float(pf.get("tickSize", 0))
            lf = filters.get("LOT_SIZE")
            if lf:
                result["stepSize"] = float(lf.get("stepSize", 0))
            nf = filters.get("MIN_NOTIONAL")
            if nf:
                val = nf.get("notional") or nf.get("minNotional") or 0
                result["minNotional"] = float(val)
        except Exception:
            pass
        with self._lock:
            self._cache[symbol] = (result, now + self.ttl)
        return result


exchange_meta = ExchangeMeta()
