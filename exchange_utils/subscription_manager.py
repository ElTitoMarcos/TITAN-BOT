import threading
import time
from typing import Callable, Dict, List


class SubscriptionManager:
    """Manage a limited set of depth subscriptions."""

    def __init__(self, max_depth_symbols: int, on_evict: Callable[[str], None]):
        self.max_depth = max_depth_symbols
        self.on_evict = on_evict
        self.lock = threading.RLock()
        self.keep_alive: Dict[str, float] = {}
        self.active: Dict[str, float] = {}

    def request_symbol(self, symbol: str) -> bool:
        """Mark *symbol* as requested and ensure it has depth if within quota."""
        symbol = symbol.upper()
        evicted = None
        with self.lock:
            now = time.time()
            self.keep_alive[symbol] = now
            if symbol in self.active:
                self.active[symbol] = now
                return True
            if self.max_depth <= 0:
                return False
            if len(self.active) >= self.max_depth:
                evicted, _ = min(self.active.items(), key=lambda kv: kv[1])
                self.active.pop(evicted, None)
            self.active[symbol] = now
        if evicted:
            try:
                self.on_evict(evicted)
            except Exception:
                pass
        return True

    def remove(self, symbol: str) -> None:
        symbol = symbol.upper()
        with self.lock:
            self.keep_alive.pop(symbol, None)
            self.active.pop(symbol, None)

    def get_active(self) -> List[str]:
        with self.lock:
            return list(self.active.keys())
