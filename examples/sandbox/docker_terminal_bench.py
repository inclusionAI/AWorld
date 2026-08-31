"""Run one instruction against an already-running Docker container with AWorld.

Example:
    docker run -d --rm --name terminal-task IMAGE sleep infinity
    LLM_MODEL_NAME=... LLM_API_KEY=... LLM_BASE_URL=... \
      python examples/sandbox/docker_terminal_bench.py \
        --container terminal-task \
        --instruction /path/to/instruction.md \
        --output-dir ./artifacts/terminal-task \
        --allowed-directory /workspace

The caller owns container startup, verifier execution, and container removal.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--container", required=True)
    parser.add_argument("--instruction", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--workdir")
    parser.add_argument(
        "--allowed-directory",
        action="append",
        dest="allowed_directories",
        help="Allowed absolute container path; may be specified more than once.",
    )
    parser.add_argument("--max-steps", type=int, default=10)
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AWORLD_LOG_PATH", str(log_dir.resolve()))

    # Import after AWORLD_LOG_PATH is configured so trajectory.log is placed
    # beside the canonical TaskResponse artifacts.
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig
    from aworld.runner import Runners
    from aworld.sandbox import DockerSandbox
    from aworld.utils.serialized_util import to_serializable

    model_name = os.environ.get("LLM_MODEL_NAME")
    api_key = os.environ.get("LLM_API_KEY")
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")

    instruction = args.instruction.read_text(encoding="utf-8")
    sandbox = DockerSandbox(
        container=args.container,
        workdir=args.workdir,
        allowed_directories=args.allowed_directories,
        reuse=True,
    )
    try:
        agent = Agent(
            name="terminal_bench_solver",
            conf=AgentConfig(
                llm_provider=os.environ.get("LLM_PROVIDER", "openai"),
                llm_model_name=model_name,
                llm_api_key=api_key,
                llm_base_url=os.environ.get("LLM_BASE_URL"),
                llm_temperature=float(os.environ.get("LLM_TEMPERATURE", "0")),
                max_steps=args.max_steps,
                use_vision=False,
            ),
            sandbox=sandbox,
            feedback_tool_result=True,
            system_prompt=(
                "You are solving a terminal benchmark inside the attached Docker container. "
                "Inspect and modify the actual container workspace with tools. Implement the "
                "solution and run focused verification before returning a final answer."
            ),
        )
        response = await Runners.run(instruction, agent=agent)
        response_payload = to_serializable(response.to_dict())
        trajectory_payload = to_serializable(response.trajectory)
        (args.output_dir / "task_response.json").write_text(
            json.dumps(response_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        (args.output_dir / "raw_trajectory.json").write_text(
            json.dumps(trajectory_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        print(
            json.dumps(
                {
                    "success": response.success,
                    "status": str(response.status),
                    "trajectory_items": len(response.trajectory or []),
                    "output_dir": str(args.output_dir.resolve()),
                },
                ensure_ascii=False,
            )
        )
    finally:
        await sandbox.cleanup()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
