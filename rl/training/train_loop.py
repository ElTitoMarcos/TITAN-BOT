from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path
from typing import List

import numpy as np
import yaml
import torch

from rl.envs.market_env import MarketEnv
from rl.agents.ppo_agent import PPOAgent, PPOBatch


def rollout(env: MarketEnv, agent: PPOAgent, n_steps: int):
    obs_list: List[np.ndarray] = []
    act_list: List[int] = []
    logp_list: List[float] = []
    rew_list: List[float] = []
    val_list: List[float] = []
    obs = env.reset()
    for _ in range(n_steps):
        action, logp = agent.select_action(obs)
        next_obs, reward, done, info = env.step(action)
        obs_list.append(obs)
        act_list.append(action)
        logp_list.append(logp)
        rew_list.append(reward)
        obs = next_obs
        if done:
            obs = env.reset()
    return obs_list, act_list, logp_list, rew_list


def compute_returns_advantages(rewards: List[float], gamma: float) -> tuple[np.ndarray, np.ndarray]:
    returns = []
    g = 0.0
    for r in reversed(rewards):
        g = r + gamma * g
        returns.insert(0, g)
    returns = np.array(returns, dtype=np.float32)
    advantages = returns - returns.mean()
    return returns, advantages


def main() -> None:
    parser = argparse.ArgumentParser(description="Train PPO on MarketEnv")
    parser.add_argument("--config", default="rl/configs/rl_config.yaml")
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--rollout", type=int, default=128)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    env = MarketEnv()
    np.random.seed(args.seed)
    obs_dim = env.observation_space.shape[0]
    action_dim = env.action_space.n
    agent = PPOAgent(obs_dim, action_dim, lr=cfg.get("lr", 3e-4))

    log_dir = Path("logs/rl")
    log_dir.mkdir(parents=True, exist_ok=True)
    csv_path = log_dir / "train.csv"
    best_reward = -1e9
    writer = csv.writer(open(csv_path, "w", newline=""))
    writer.writerow(["step", "reward"])

    obs = env.reset(seed=args.seed)
    for step in range(0, args.steps, args.rollout):
        obs_list, act_list, logp_list, rew_list = rollout(env, agent, args.rollout)
        returns, adv = compute_returns_advantages(rew_list, agent.gamma)
        batch = PPOBatch(
            obs=torch.tensor(np.array(obs_list), dtype=torch.float32, device=agent.device),
            actions=torch.tensor(act_list, dtype=torch.int64, device=agent.device),
            logprobs=torch.tensor(logp_list, dtype=torch.float32, device=agent.device),
            returns=torch.tensor(returns, dtype=torch.float32, device=agent.device),
            advantages=torch.tensor(adv, dtype=torch.float32, device=agent.device),
        )
        stats = agent.update(batch)
        avg_reward = float(np.mean(rew_list))
        writer.writerow([step, avg_reward])
        if avg_reward > best_reward:
            best_reward = avg_reward
            agent.save("models/ppo/best_policy.pt")


if __name__ == "__main__":  # pragma: no cover
    main()
