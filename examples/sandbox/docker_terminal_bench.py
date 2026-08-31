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
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


# Example scripts must exercise the worktree that contains them, not whichever
# editable AWorld installation happens to be first in the user's environment.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


SYSTEM_PROMPT = (
    "You are solving a terminal benchmark inside the attached Docker container. "
    "Inspect and modify the actual container workspace with tools. Implement the "
    "solution and run focused verification before returning a final answer."
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode("utf-8")
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _load_variant(path: Path | None) -> dict:
    if path is None:
        return {
            "schema_version": "aworld.context-eval-variant/v1",
            "name": "baseline",
            "agent_memory_config": {},
            "docker_output_policy": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {"schema_version", "name", "agent_memory_config", "docker_output_policy"}
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(
            "Context evaluation variants may only change context/output policy; "
            f"unexpected fields: {', '.join(unexpected)}"
        )
    if not payload.get("name"):
        raise ValueError("variant config requires a non-empty name")
    payload.setdefault("agent_memory_config", {})
    payload.setdefault("docker_output_policy", {})
    allowed_memory_fields = {
        "history_scope",
        "enable_summary",
        "summary_rounds",
        "summary_context_length",
        "summary_summaried",
        "tool_result_offload",
        "tool_action_white_list",
        "tool_result_length_threshold",
        "tool_result_preview_chars",
    }
    unexpected_memory = sorted(set(payload["agent_memory_config"]) - allowed_memory_fields)
    if unexpected_memory:
        raise ValueError(
            "Variant agent_memory_config contains non-evaluation fields: "
            + ", ".join(unexpected_memory)
        )
    allowed_output_fields = {"max_inline_output_bytes", "output_head_bytes"}
    unexpected_output = sorted(set(payload["docker_output_policy"]) - allowed_output_fields)
    if unexpected_output:
        raise ValueError(
            "Variant docker_output_policy contains unsupported fields: "
            + ", ".join(unexpected_output)
        )
    return payload


def _git_snapshot() -> dict:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    status_text = status.stdout if status.returncode == 0 else ""
    return {
        "source_root": str(REPO_ROOT),
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status_text.strip()),
        "status_sha256": _sha256_bytes(status_text.encode("utf-8")),
    }


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
    parser.add_argument(
        "--variant-config",
        type=Path,
        help="JSON variant that may change context/output policy only, never task prompts or answers.",
    )
    parser.add_argument(
        "--allow-missing-provider-trace",
        action="store_true",
        help="Compatibility escape hatch; context evaluations should keep the provider trace hard gate enabled.",
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    started_at = time.time()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AWORLD_LOG_PATH", str(log_dir.resolve()))

    # Import after AWORLD_LOG_PATH is configured so trajectory.log is placed
    # beside the canonical TaskResponse artifacts.
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig, AgentMemoryConfig
    from aworld.runner import Runners
    from aworld.sandbox import DockerSandbox
    from aworld.utils.serialized_util import to_serializable

    model_name = os.environ.get("LLM_MODEL_NAME")
    api_key = os.environ.get("LLM_API_KEY")
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")

    instruction = args.instruction.read_text(encoding="utf-8")
    variant = _load_variant(args.variant_config)
    output_policy = variant["docker_output_policy"]
    sandbox = DockerSandbox(
        container=args.container,
        workdir=args.workdir,
        allowed_directories=args.allowed_directories,
        max_inline_output_bytes=int(output_policy.get("max_inline_output_bytes", 1_048_576)),
        output_head_bytes=output_policy.get("output_head_bytes"),
        artifact_directory=str((args.output_dir / "tool-output-artifacts").resolve()),
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
                memory_config=AgentMemoryConfig(**variant["agent_memory_config"]),
            ),
            sandbox=sandbox,
            feedback_tool_result=True,
            system_prompt=SYSTEM_PROMPT,
        )
        response = await Runners.run(instruction, agent=agent)
        response_payload = to_serializable(response.to_dict())
        trajectory_payload = to_serializable(response.trajectory)
        llm_calls = to_serializable(response.llm_calls or [])
        provider_calls = [
            call for call in llm_calls
            if isinstance(call, dict) and call.get("capture_stage") == "provider_bound"
        ]
        if not provider_calls and not args.allow_missing_provider_trace:
            raise RuntimeError(
                "No provider-bound request snapshots were captured; reward cannot be attributed "
                "to a context-management variant"
            )
        checksums = {
            "task_response.json": _write_json(args.output_dir / "task_response.json", response_payload),
            "raw_trajectory.json": _write_json(args.output_dir / "raw_trajectory.json", trajectory_payload),
            "provider_calls.json": _write_json(args.output_dir / "provider_calls.json", provider_calls),
            "context_trace.json": _write_json(
                args.output_dir / "context_trace.json",
                [
                    {
                        "request_id": call.get("request_id"),
                        "request_trace_match": call.get("request_trace_match"),
                        "assembly_observability": call.get("assembly_observability"),
                        "request_metrics": call.get("request_metrics"),
                    }
                    for call in provider_calls
                ],
            ),
        }
        inspect = subprocess.run(
            [sandbox.docker_binary, "inspect", "--format", "{{json .Image}}", args.container],
            capture_output=True,
            text=True,
            check=False,
        )
        manifest = {
            "schema_version": "aworld.context-eval-run/v1",
            "variant": variant,
            "invariants": {
                "model": model_name,
                "provider": os.environ.get("LLM_PROVIDER", "openai"),
                "temperature": float(os.environ.get("LLM_TEMPERATURE", "0")),
                "max_steps": args.max_steps,
                "system_prompt_sha256": _sha256_bytes(SYSTEM_PROMPT.encode("utf-8")),
                "instruction_sha256": _sha256_bytes(instruction.encode("utf-8")),
            },
            "container": {
                "name": args.container,
                "image_id": inspect.stdout.strip().strip('"') if inspect.returncode == 0 else None,
                "workdir": sandbox.container_workdir,
            },
            "capture": {
                "provider_call_count": len(provider_calls),
                "trajectory_items": len(response.trajectory or []),
                "checksums": checksums,
            },
            "started_at_epoch": started_at,
            "finished_at_epoch": time.time(),
            "aworld_source": _git_snapshot(),
            "python": sys.version,
        }
        _write_json(args.output_dir / "run_manifest.json", manifest)
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
