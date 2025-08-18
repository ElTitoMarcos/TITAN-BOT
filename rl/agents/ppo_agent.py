from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.optim as optim
from torch.distributions import Categorical

from rl.policies.actor_critic import ActorCritic


@dataclass
class PPOBatch:
    obs: torch.Tensor
    actions: torch.Tensor
    logprobs: torch.Tensor
    returns: torch.Tensor
    advantages: torch.Tensor


class PPOAgent:
    """Minimal PPO agent."""

    def __init__(
        self,
        obs_dim: int,
        action_dim: int,
        lr: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_ratio: float = 0.2,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
    ) -> None:
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_ratio = clip_ratio
        self.entropy_coef = entropy_coef
        self.value_coef = value_coef
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.net = ActorCritic(obs_dim, action_dim).to(self.device)
        self.opt = optim.Adam(self.net.parameters(), lr=lr)
        self.obs_mean = np.zeros(obs_dim, dtype=np.float32)
        self.obs_std = np.ones(obs_dim, dtype=np.float32)
        self.count = 1e-4

    # ------------------------------------------------------------------
    def _norm_obs(self, obs: np.ndarray) -> torch.Tensor:
        self.obs_mean = 0.99 * self.obs_mean + 0.01 * obs
        self.obs_std = 0.99 * self.obs_std + 0.01 * (obs - self.obs_mean) ** 2
        norm = (obs - self.obs_mean) / (np.sqrt(self.obs_std) + 1e-8)
        return torch.as_tensor(norm, dtype=torch.float32, device=self.device)

    # ------------------------------------------------------------------
    def select_action(self, obs: np.ndarray, deterministic: bool = False) -> Tuple[int, float]:
        with torch.no_grad():
            o = self._norm_obs(obs)
            logits, value = self.net(o)
            dist = Categorical(logits=logits)
            if deterministic:
                action = torch.argmax(logits).item()
                logprob = dist.log_prob(torch.tensor(action)).item()
            else:
                action = dist.sample()
                logprob = dist.log_prob(action).item()
                action = action.item()
        return action, logprob

    # ------------------------------------------------------------------
    def update(self, batch: PPOBatch, epochs: int = 4) -> Dict[str, float]:
        stats: Dict[str, float] = {}
        for _ in range(epochs):
            logits, values = self.net(batch.obs)
            dist = Categorical(logits=logits)
            entropy = dist.entropy().mean()
            new_logprob = dist.log_prob(batch.actions)
            ratio = (new_logprob - batch.logprobs).exp()
            surr1 = ratio * batch.advantages
            surr2 = torch.clamp(ratio, 1.0 - self.clip_ratio, 1.0 + self.clip_ratio) * batch.advantages
            policy_loss = -torch.min(surr1, surr2).mean()
            value_loss = nn.functional.mse_loss(values.squeeze(-1), batch.returns)
            loss = policy_loss + self.value_coef * value_loss - self.entropy_coef * entropy
            self.opt.zero_grad()
            loss.backward()
            self.opt.step()
            stats = {
                "policy_loss": policy_loss.item(),
                "value_loss": value_loss.item(),
                "entropy": entropy.item(),
            }
        return stats

    # ------------------------------------------------------------------
    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        torch.save(self.net.state_dict(), path)

    # ------------------------------------------------------------------
    def load(self, path: str) -> None:
        state = torch.load(path, map_location=self.device)
        self.net.load_state_dict(state)
