from __future__ import annotations

import torch
import torch.nn as nn


class ActorCritic(nn.Module):
    """Simple MLP actor-critic network."""

    def __init__(self, obs_dim: int, action_dim: int, hidden: int = 128) -> None:
        super().__init__()
        self.body = nn.Sequential(
            nn.Linear(obs_dim, hidden),
            nn.Tanh(),
            nn.Linear(hidden, hidden),
            nn.Tanh(),
        )
        self.policy = nn.Linear(hidden, action_dim)
        self.value = nn.Linear(hidden, 1)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.body(x)
        return self.policy(h), self.value(h)
