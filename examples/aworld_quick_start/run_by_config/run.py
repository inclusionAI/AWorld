# coding: utf-8
import os

from aworld.config.agent_loader import load_swarm_from_yaml
from aworld.runner import Runners

# Example: load agents and swarm from a YAML file and run
if __name__ == "__main__":
    # You can change the config path as needed
    swarm, agents = load_swarm_from_yaml(f"{os.path.dirname(__file__)}/agents.yaml")

    # Access a specific agent if needed
    summarizer = agents["summarizer"]

    # Run with the constructed swarm
    result = Runners.sync_run(
        input="hello who are you?",
        swarm=swarm,
    )

    print("Result:", result)

