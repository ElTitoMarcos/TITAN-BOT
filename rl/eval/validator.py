from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from rl.envs.market_env import MarketEnv
from rl.agents.ppo_agent import PPOAgent


def run_episode(env: MarketEnv, agent: PPOAgent) -> float:
    obs = env.reset()
    total_reward = 0.0
    done = False
    while not done:
        action, _ = agent.select_action(obs, deterministic=True)
        obs, reward, done, info = env.step(action)
        total_reward += reward
    return total_reward


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate PPO policy")
    parser.add_argument("--checkpoint", required=False, default="models/ppo/best_policy.pt")
    parser.add_argument("--episodes", type=int, default=5)
    parser.add_argument("--config", default="rl/configs/rl_config.yaml")
    args = parser.parse_args()

    env = MarketEnv()
    obs_dim = env.observation_space.shape[0]
    action_dim = env.action_space.n
    agent = PPOAgent(obs_dim, action_dim)
    if Path(args.checkpoint).exists():
        agent.load(args.checkpoint)

    rewards = [run_episode(env, agent) for _ in range(args.episodes)]
    mean_reward = float(np.mean(rewards))
    std_reward = float(np.std(rewards))
    Path("artifacts").mkdir(exist_ok=True)
    out = {"mean_reward": mean_reward, "std_reward": std_reward}
    with open("artifacts/validation.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
