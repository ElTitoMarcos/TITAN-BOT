from __future__ import annotations

import numpy as np
from typing import Dict, Tuple, Optional

try:
    import gym
    from gym import spaces
except Exception:  # pragma: no cover - gym not installed
    class _BaseEnv:
        def reset(self, seed: Optional[int] = None):
            raise NotImplementedError

        def step(self, action):
            raise NotImplementedError

    class spaces:  # minimal stub
        class Box:
            def __init__(self, low, high, shape, dtype):
                self.low = low
                self.high = high
                self.shape = shape
                self.dtype = dtype

        class Discrete:
            def __init__(self, n):
                self.n = n

    gym = _BaseEnv  # type: ignore


class MarketEnv(getattr(gym, "Env", object)):
    """Minimal market environment with synthetic dynamics.

    Observation: 64 random floats plus position qty and average price.
    Actions: 0 hold, 1 buy_limit, 2 sell_limit, 3 cancel_all (no-op).
    """

    def __init__(self, obs_size: int = 64) -> None:
        self.obs_size = obs_size
        self.position_qty = 0.0
        self.position_price = 0.0
        self.rng = np.random.default_rng()
        low = np.full(obs_size + 2, -np.inf, dtype=np.float32)
        high = np.full(obs_size + 2, np.inf, dtype=np.float32)
        self.observation_space = spaces.Box(low, high, shape=(obs_size + 2,), dtype=np.float32)
        self.action_space = spaces.Discrete(4)
        self._last_price = 100.0
        self.step_count = 0

    # ------------------------------------------------------------------
    def reset(self, seed: Optional[int] = None) -> np.ndarray:
        if seed is not None:
            self.rng = np.random.default_rng(seed)
        self.position_qty = 0.0
        self.position_price = 0.0
        self._last_price = 100.0
        self.step_count = 0
        return self._get_obs()

    # ------------------------------------------------------------------
    def step(self, action: int) -> Tuple[np.ndarray, float, bool, Dict[str, float]]:
        self.step_count += 1
        price_change = self.rng.normal(0, 1)
        price = max(0.1, self._last_price + price_change)
        reward = 0.0

        if action == 1:  # buy
            self.position_qty += 1.0
            self.position_price = (
                (self.position_price * (self.position_qty - 1) + price) / self.position_qty
            )
        elif action == 2 and self.position_qty > 0:  # sell
            reward += (price - self.position_price) * 1.0
            self.position_qty -= 1.0
            if self.position_qty <= 0:
                self.position_qty = 0.0
                self.position_price = 0.0
        elif action == 3:  # cancel all -> no effect in stub
            pass

        risk_penalty = 0.001 * (self.position_qty ** 2)
        reward -= risk_penalty
        self._last_price = price
        done = self.step_count >= 100
        obs = self._get_obs()
        info = {"price": price, "pnl": reward}
        return obs, reward, done, info

    # ------------------------------------------------------------------
    def _get_obs(self) -> np.ndarray:
        market = self.rng.normal(0, 1, size=self.obs_size).astype(np.float32)
        pos = np.array([self.position_qty, self.position_price], dtype=np.float32)
        return np.concatenate([market, pos])

    # ------------------------------------------------------------------
    def render(self) -> None:  # pragma: no cover - visualisation not needed
        print(f"step={self.step_count} price={self._last_price:.2f} position={self.position_qty}")
