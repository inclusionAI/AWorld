# coding: utf-8
# Copyright (c) 2026 inclusionAI.
import asyncio
from pathlib import Path

from aworld.agents.llm_agent import Agent
from aworld.core.task import Task
from aworld.runner import Runners
from examples.aworld_quick_start.common import agent_config
from examples.aworld_quick_start.ralph_runner.example_setup import (
    RalphExamplePaths,
    build_ralph_runner_example_config,
    build_ralph_runner_example_criteria,
    ensure_ralph_runner_example_workspace,
)


async def main() -> None:
    paths = RalphExamplePaths.from_root(Path(__file__).resolve().parent / ".workdir")
    ensure_ralph_runner_example_workspace(paths, reset=True)

    builder = Agent(
        conf=agent_config,
        name="pricing_builder",
        system_prompt=(
            "You are implementing a small Python business module. "
            "Read the seeded workspace files, edit code conservatively, and use verification results to repair failures."
        ),
    )

    task = Task(
        input=(
            "Implement src/order_pricing.py so the seeded tests pass. "
            "Read business_rules.md first. "
            "When you finish an iteration, explain what changed and what verification result you expect."
        ),
        agent=builder,
        conf=build_ralph_runner_example_config(
            workspace=str(paths.root),
            model_config=agent_config.llm_config,
        ),
    )

    result = await Runners.ralph_run(
        task=task,
        completion_criteria=build_ralph_runner_example_criteria(task_id=task.id),
    )

    print("Final answer:")
    print(result.answer)
    print(f"Workspace: {paths.root}")


if __name__ == "__main__":
    asyncio.run(main())
