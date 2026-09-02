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
import re
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
    "You are solving a tool-using benchmark with an attached Docker container. "
    "Use the provided tools to inspect the real task environment, gather any required "
    "evidence, implement the solution when files must change, and perform focused "
    "verification before returning a final answer."
)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _write_json(path: Path, payload) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, default=str).encode(
        "utf-8"
    )
    path.write_bytes(encoded)
    return _sha256_bytes(encoded)


def _llm_calls_digest(calls: list) -> str:
    encoded = json.dumps(
        calls,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(encoded)


def _context_lifecycle_evidence(agent) -> dict:
    """Export typed, privacy-safe lifecycle evidence for canary verification."""
    from aworld.core.context.compiler import canonical_json_hash
    from aworld.core.context.compiler.lifecycle import ContextLifecycleState

    context = getattr(agent, "context", None)
    state = getattr(context, "context_lifecycle_state", None)
    if not isinstance(state, ContextLifecycleState):
        return {
            "schema_version": "aworld.context.lifecycle-evidence.v1",
            "status": "unavailable",
            "reason_code": "typed_lifecycle_state_unavailable",
        }
    projection = {
        "session_id_hash": canonical_json_hash({"session_id": state.session_id}),
        "session_epoch": state.session_epoch,
        "task_epoch": state.task_epoch,
        "turn_epoch": state.turn_epoch,
        "branch_id_hash": canonical_json_hash({"branch_id": state.branch_id}),
        "checkpoint_revision": state.checkpoint_revision,
    }
    return {
        "schema_version": "aworld.context.lifecycle-evidence.v1",
        "status": "available",
        "state": projection,
        "state_hash": canonical_json_hash(projection),
    }


def _export_context_tool_output_artifacts(agent, output_dir: Path) -> list[dict]:
    """Persist checksum-bound Context artifacts beside the raw trajectory."""
    context = getattr(agent, "context", None)
    if context is None:
        return []
    records = context.get_tool_output_records()
    destination = output_dir / "tool-output-artifacts"
    exported: dict[str, dict] = {}
    for record in records:
        artifact = getattr(record, "artifact", None)
        if artifact is None or artifact.ref in exported:
            continue
        data = context.read_tool_output_artifact(artifact.ref)
        digest = _sha256_bytes(data)
        content_hash = f"sha256:{digest}"
        if artifact.content_hash != content_hash or artifact.byte_count != len(data):
            raise RuntimeError("context_tool_output_artifact_mismatch")
        destination.mkdir(parents=True, exist_ok=True)
        target = destination / f"context-{digest}.bin"
        if target.exists() and target.read_bytes() != data:
            raise RuntimeError("context_tool_output_artifact_export_collision")
        target.write_bytes(data)
        exported[artifact.ref] = {
            "artifact_ref_hash": f"sha256:{_sha256_bytes(artifact.ref.encode('utf-8'))}",
            "content_hash": content_hash,
            "byte_count": len(data),
            "path": str(target.relative_to(output_dir)),
        }
    return [exported[key] for key in sorted(exported)]


def _resolve_llm_call_capture(response, agent) -> tuple[list, str, dict]:
    """Preserve blocked-call evidence when TaskResponse propagation is incomplete."""
    response_calls = list(getattr(response, "llm_calls", None) or [])
    live_context = getattr(agent, "context", None)
    live_getter = getattr(live_context, "get_llm_calls", None)
    live_calls = list(live_getter() or []) if callable(live_getter) else []
    continuity = {
        "task_response_count": len(response_calls),
        "live_context_count": len(live_calls),
        "counts_match": len(response_calls) == len(live_calls),
        "task_response_sha256": _llm_calls_digest(response_calls),
        "live_context_sha256": _llm_calls_digest(live_calls),
        "snapshots_match": _llm_calls_digest(response_calls)
        == _llm_calls_digest(live_calls),
    }
    if response_calls and live_calls:
        reconciled: list = []
        index_by_identity: dict[tuple[str, str], int] = {}

        def identity(call) -> tuple[str, str] | None:
            if not isinstance(call, dict):
                return None
            for field in ("request_id", "call_id"):
                value = call.get(field)
                if isinstance(value, str) and value:
                    return field, value
            return None

        for call in (*response_calls, *live_calls):
            call_identity = identity(call)
            if call_identity is not None and call_identity in index_by_identity:
                reconciled[index_by_identity[call_identity]] = call
            else:
                if call_identity is not None:
                    index_by_identity[call_identity] = len(reconciled)
                reconciled.append(call)
        continuity["reconciled_count"] = len(reconciled)
        continuity["reconciled_sha256"] = _llm_calls_digest(reconciled)
        if continuity["snapshots_match"]:
            return reconciled, "task_response", continuity
        return reconciled, "reconciled_task_response_live_context", continuity
    if response_calls:
        continuity["reconciled_count"] = len(response_calls)
        continuity["reconciled_sha256"] = _llm_calls_digest(response_calls)
        return response_calls, "task_response", continuity
    if live_calls:
        continuity["reconciled_count"] = len(live_calls)
        continuity["reconciled_sha256"] = _llm_calls_digest(live_calls)
        return live_calls, "live_context_fallback", continuity
    continuity["reconciled_count"] = 0
    continuity["reconciled_sha256"] = _llm_calls_digest([])
    return [], "unavailable", continuity


def _is_provider_bound_call(call) -> bool:
    if not isinstance(call, dict) or call.get("provider_invoked") is not True:
        return False
    provider_request = call.get("provider_request") or {}
    if (
        provider_request.get("capture_stage") == "provider_prepared"
        and provider_request.get("fidelity") == "provider_prepared"
        and isinstance(provider_request.get("payload"), dict)
    ):
        return True
    rollout = call.get("context_rollout") or {}
    lowering = rollout.get("provider_lowering") or {}
    provider_request = lowering.get("provider_request") or {}
    return (
        rollout.get("candidate_applied") is True
        and provider_request.get("capture_stage") == "provider_prepared"
        and provider_request.get("fidelity") == "provider_prepared"
    )


def _load_variant(path: Path | None) -> dict:
    if path is None:
        return {
            "schema_version": "aworld.context-eval-variant/v1",
            "name": "baseline",
            "agent_memory_config": {},
            "context_compiler": {},
            "docker_output_policy": {},
        }
    payload = json.loads(path.read_text(encoding="utf-8"))
    allowed = {
        "schema_version",
        "name",
        "agent_memory_config",
        "context_compiler",
        "docker_output_policy",
    }
    unexpected = sorted(set(payload) - allowed)
    if unexpected:
        raise ValueError(
            "Context evaluation variants may only change context/output policy; "
            f"unexpected fields: {', '.join(unexpected)}"
        )
    if not payload.get("name"):
        raise ValueError("variant config requires a non-empty name")
    payload.setdefault("agent_memory_config", {})
    payload.setdefault("context_compiler", {})
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
    unexpected_memory = sorted(
        set(payload["agent_memory_config"]) - allowed_memory_fields
    )
    if unexpected_memory:
        raise ValueError(
            "Variant agent_memory_config contains non-evaluation fields: "
            + ", ".join(unexpected_memory)
        )
    allowed_compiler_fields = {
        "mode",
        "compiler_version",
        "policy_version",
        "universal_final",
        "context_limit",
        "reserved_output_tokens",
        "provider_protocol_reserve",
        "safety_margin_tokens",
        "max_item_tokens",
        "require_proven_semantics_for_enforce",
        "scoped_instructions",
        "progressive_skills",
        "progressive_tools",
        "progressive_tool_base_tools",
        "progressive_tool_unmanaged_policy",
        "task_catalog_policy",
        "checkpoint_policy",
        "default_tool_output_inline_tokens",
        "artifact_offload",
        "context_inspector",
        "trace_level",
        "completion_contract",
    }
    unexpected_compiler = sorted(
        set(payload["context_compiler"]) - allowed_compiler_fields
    )
    if unexpected_compiler:
        raise ValueError(
            "Variant context_compiler contains unsupported fields: "
            + ", ".join(unexpected_compiler)
        )
    allowed_output_fields = {"max_inline_output_bytes", "output_head_bytes"}
    unexpected_output = sorted(
        set(payload["docker_output_policy"]) - allowed_output_fields
    )
    if unexpected_output:
        raise ValueError(
            "Variant docker_output_policy contains unsupported fields: "
            + ", ".join(unexpected_output)
        )
    return payload


def _load_task_skills(path: Path | None) -> dict[str, dict]:
    if path is None:
        return {}
    skills_directory = path.resolve()
    if not skills_directory.is_dir():
        raise ValueError(f"Skills directory does not exist: {skills_directory}")
    from aworld.skills.compat_provider import build_compat_registry

    registry = build_compat_registry(skills_directory)
    descriptors = registry.list_descriptors()
    if not descriptors:
        raise ValueError(f"Skills directory contains no Skills: {skills_directory}")
    return {
        descriptor.skill_name: registry.build_skill_config(descriptor.skill_id)
        for descriptor in descriptors
    }


def load_external_mcp_config(path: Path | None) -> tuple[dict, dict]:
    """Load an invariant external Tool profile without persisting its values."""
    payload: dict = {"mcpServers": {}}
    status = "disabled"
    if path is not None:
        resolved = path.resolve()
        if not resolved.is_file():
            raise ValueError(f"MCP config does not exist: {resolved}")
        loaded = json.loads(resolved.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or set(loaded) != {"mcpServers"}:
            raise ValueError(
                "MCP config must contain only a top-level mcpServers object"
            )
        servers = loaded.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            raise ValueError("MCP config requires at least one server")
        for name, config in servers.items():
            if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
                raise ValueError(f"Unsafe MCP server name: {name!r}")
            if name == "docker":
                raise ValueError(
                    "MCP server name 'docker' is reserved by DockerSandbox"
                )
            if not isinstance(config, dict):
                raise ValueError(f"MCP server {name!r} must be an object")
        payload = loaded
        status = "enabled"
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    evidence = {
        "status": status,
        "config_sha256": _sha256_bytes(canonical),
        "server_names": sorted(payload["mcpServers"]),
    }
    return payload, evidence


def _agent_loop_budget(max_steps: int) -> dict[str, int]:
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps <= 0:
        raise ValueError("--max-steps must be positive")
    # BaseAgent owns the actual loop guard through max_loop_steps. AgentConfig's
    # max_steps is retained for compatibility but does not bind that guard.
    return {"max_loop_steps": max_steps}


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
        "--skills-directory",
        type=Path,
        help=(
            "Task-provided Skills directory. The harness mounts the same directory in the "
            "task container at /aworld-skills."
        ),
    )
    parser.add_argument(
        "--mcp-config",
        type=Path,
        help=(
            "Invariant external MCP Tool profile shared by every paired variant. "
            "Only its checksum and server names are persisted."
        ),
    )
    parser.add_argument(
        "--allow-missing-provider-trace",
        action="store_true",
        help="Compatibility escape hatch; context evaluations should keep the provider trace hard gate enabled.",
    )
    parser.add_argument(
        "--deterministic-capture-provider",
        action="store_true",
        help=(
            "Use a local one-response provider for structural capture validation only. "
            "This mode requires --allow-missing-provider-trace and must not be used "
            "as benchmark quality or Reward evidence."
        ),
    )
    return parser.parse_args()


async def run(args: argparse.Namespace) -> None:
    started_at = time.time()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    log_dir = args.output_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    os.environ["AWORLD_LOG_PATH"] = str(log_dir.resolve())
    os.environ["AWORLD_TRAJECTORY_FORMAT"] = "dual"
    os.environ["AWORLD_LLM_CALL_JOURNAL_PATH"] = str(
        (args.output_dir / "llm_calls.journal.jsonl").resolve()
    )

    # Import after AWORLD_LOG_PATH is configured so trajectory.log is placed
    # beside the canonical TaskResponse artifacts.
    from aworld.agents.llm_agent import Agent
    from aworld.config.conf import AgentConfig, AgentMemoryConfig
    from aworld.runner import Runners
    from aworld.sandbox import DockerSandbox
    from aworld.utils.serialized_util import to_serializable

    if args.deterministic_capture_provider and not args.allow_missing_provider_trace:
        raise RuntimeError(
            "--deterministic-capture-provider requires --allow-missing-provider-trace"
        )
    if args.deterministic_capture_provider and args.variant_config is not None:
        raise RuntimeError(
            "deterministic capture validation must use the baseline/off variant"
        )

    model_name = (
        "aworld-deterministic-capture-v1"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_MODEL_NAME")
    )
    api_key = (
        "not-used"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_API_KEY")
    )
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")
    provider_name = (
        "deterministic_capture"
        if args.deterministic_capture_provider
        else os.environ.get("LLM_PROVIDER", "openai")
    )

    instruction = args.instruction.read_text(encoding="utf-8")
    variant = _load_variant(args.variant_config)
    skill_configs = _load_task_skills(args.skills_directory)
    external_mcp_config, external_mcp_evidence = load_external_mcp_config(
        args.mcp_config
    )
    output_policy = variant["docker_output_policy"]
    sandbox = DockerSandbox(
        container=args.container,
        workdir=args.workdir,
        allowed_directories=args.allowed_directories,
        max_inline_output_bytes=int(
            output_policy.get("max_inline_output_bytes", 1_048_576)
        ),
        output_head_bytes=output_policy.get("output_head_bytes"),
        artifact_directory=str((args.output_dir / "tool-output-artifacts").resolve()),
        mcp_config=external_mcp_config,
        reuse=True,
    )
    try:
        system_prompt = SYSTEM_PROMPT + (
            " Task-provided Skill assets are mounted read-only at /aworld-skills. "
            "Activate only relevant Skills and use the container paths documented there."
            if skill_configs
            else ""
        )
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
                context_compiler=variant["context_compiler"],
                skill_configs=skill_configs,
            ),
            sandbox=sandbox,
            feedback_tool_result=True,
            system_prompt=system_prompt,
            **_agent_loop_budget(args.max_steps),
        )
        if args.deterministic_capture_provider:
            from aworld.core.llm_provider import LLMProviderBase
            from aworld.models.llm import LLMModel
            from aworld.models.model_response import ModelResponse

            class DeterministicCaptureProvider(LLMProviderBase):
                """Local structural probe; deliberately has no benchmark ability."""

                def _init_provider(self):
                    return None

                def postprocess_response(self, response):
                    return response

                @staticmethod
                def _response() -> ModelResponse:
                    content = "Deterministic structural capture completed."
                    return ModelResponse(
                        id="deterministic-capture-response",
                        model="aworld-deterministic-capture-v1",
                        content=content,
                        usage={
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "total_tokens": 2,
                        },
                        message={"role": "assistant", "content": content},
                        finish_reason="stop",
                    )

                def completion(self, messages, **kwargs):
                    return self._response()

                async def acompletion(self, messages, **kwargs):
                    return self._response()

            agent._llm = LLMModel(
                conf=agent.conf.llm_config,
                custom_provider=DeterministicCaptureProvider(
                    model_name="aworld-deterministic-capture-v1"
                ),
            )
        response = await Runners.run(instruction, agent=agent)
        response_payload = to_serializable(response.to_dict())
        trajectory_payload = to_serializable(response.trajectory)
        captured_llm_calls, llm_capture_source, llm_capture_continuity = (
            _resolve_llm_call_capture(response, agent)
        )
        llm_calls = to_serializable(captured_llm_calls)
        provider_calls = [call for call in llm_calls if _is_provider_bound_call(call)]
        provider_capture_gate_passed = bool(provider_calls) and bool(
            llm_capture_continuity["snapshots_match"]
        )
        lifecycle_evidence = _context_lifecycle_evidence(agent)
        context_artifacts = _export_context_tool_output_artifacts(
            agent, args.output_dir
        )
        checksums = {
            "task_response.json": _write_json(
                args.output_dir / "task_response.json", response_payload
            ),
            "raw_trajectory.json": _write_json(
                args.output_dir / "raw_trajectory.json", trajectory_payload
            ),
            "llm_calls.json": _write_json(
                args.output_dir / "llm_calls.json", llm_calls
            ),
            "provider_calls.json": _write_json(
                args.output_dir / "provider_calls.json", provider_calls
            ),
            "context_lifecycle.json": _write_json(
                args.output_dir / "context_lifecycle.json", lifecycle_evidence
            ),
            "context_trace.json": _write_json(
                args.output_dir / "context_trace.json",
                [
                    {
                        "request_id": call.get("request_id"),
                        "status": call.get("status"),
                        "error_code": call.get("error_code"),
                        "provider_invoked": call.get("provider_invoked"),
                        "request_trace_match": call.get("request_trace_match"),
                        "assembly_observability": call.get("assembly_observability"),
                        "request_metrics": call.get("request_metrics"),
                        "context_rollout": call.get("context_rollout"),
                    }
                    for call in llm_calls
                    if isinstance(call, dict)
                ],
            ),
        }
        inspect = subprocess.run(
            [
                sandbox.docker_binary,
                "inspect",
                "--format",
                "{{json .Image}}",
                args.container,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        manifest = {
            "schema_version": "aworld.context-eval-run/v1",
            "variant": variant,
            "invariants": {
                "model": model_name,
                "provider": provider_name,
                "temperature": float(os.environ.get("LLM_TEMPERATURE", "0")),
                "max_steps": args.max_steps,
                "system_prompt_sha256": _sha256_bytes(system_prompt.encode("utf-8")),
                "instruction_sha256": _sha256_bytes(instruction.encode("utf-8")),
                "task_skill_count": len(skill_configs),
                "task_skill_catalog_sha256": _sha256_bytes(
                    json.dumps(
                        {
                            name: {
                                "description": config.get("description"),
                                "usage_sha256": _sha256_bytes(
                                    str(config.get("usage") or "").encode("utf-8")
                                ),
                            }
                            for name, config in sorted(skill_configs.items())
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ),
                "external_mcp": external_mcp_evidence,
                "structural_capture_only": args.deterministic_capture_provider,
            },
            "container": {
                "name": args.container,
                "image_id": inspect.stdout.strip().strip('"')
                if inspect.returncode == 0
                else None,
                "workdir": sandbox.container_workdir,
            },
            "capture": {
                "provider_call_count": len(provider_calls),
                "llm_call_count": len(llm_calls),
                "llm_call_source": llm_capture_source,
                "llm_call_continuity": llm_capture_continuity,
                "provider_capture_gate_passed": provider_capture_gate_passed,
                "trajectory_items": len(response.trajectory or []),
                "context_tool_output_artifacts": context_artifacts,
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
        if not provider_capture_gate_passed and not args.allow_missing_provider_trace:
            raise RuntimeError(
                "Provider-bound request capture is missing or TaskResponse/live Context continuity "
                "does not match; diagnostic artifacts were preserved, but reward cannot be "
                "attributed to a context-management variant"
            )
    finally:
        await sandbox.cleanup()


if __name__ == "__main__":
    asyncio.run(run(parse_args()))
