"""Context-bound artifact workbench for the local task execution environment."""

from __future__ import annotations

import json

from aworld.core.common import ActionResult, ParamInfo, ToolActionInfo
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import AsyncTool, ToolFactory
from aworld.tools.utils import build_observation

WORKBENCH = "WORKBENCH"


def _param(name, kind, description, required=False, items=None):
    return ParamInfo(
        name=name, type=kind, desc=description, required=required, items=items
    )


class WorkbenchAction(ToolAction):
    INSPECT = ToolActionInfo(
        name="inspect",
        desc="Read the public delivery contract, protected inputs, candidate history, validation schema and current selection policy.",
    )
    PROTECT_INPUTS = ToolActionInfo(
        name="protect_inputs",
        desc="Snapshot source files before changing them. Existing original snapshots remain preserved. This does not make ordinary inputs immutable.",
        input_params={
            "paths": _param(
                "paths",
                "array",
                "Input file paths in the task workspace or explicitly declared public paths.",
                True,
                {"type": "string"},
            ),
        },
    )
    WORKING_COPY = ToolActionInfo(
        name="working_copy",
        desc="Create an isolated working copy from a protected input snapshot, including related database sidecars.",
        input_params={
            "snapshot_id": _param(
                "snapshot_id",
                "string",
                "Snapshot returned by inspect/protect_inputs.",
                True,
            ),
            "destination": _param(
                "destination",
                "string",
                "New working directory inside the task workspace.",
                True,
            ),
        },
    )
    RESTORE_INPUTS = ToolActionInfo(
        name="restore_inputs",
        desc="Restore missing original input files. Modified existing files are reported as conflicts and are never overwritten.",
        input_params={
            "snapshot_id": _param(
                "snapshot_id", "string", "Protected input snapshot ID.", True
            ),
        },
    )
    PROBE_API = ToolActionInfo(
        name="probe_api",
        desc="Inspect an installed Python API in the explicitly selected task interpreter, or execute a diagnostic argv. Returns version, signature, actual exceptions and optional minimal-call result; not a correctness verdict.",
        input_params={
            "interpreter": _param(
                "interpreter",
                "string",
                "Actual task Python executable (required for Python probes).",
            ),
            "module": _param("module", "string", "Installed Python module to inspect."),
            "object_path": _param(
                "object_path", "string", "Optional dotted attribute within the module."
            ),
            "call": _param(
                "call",
                "object",
                "Optional explicit {args:[], kwargs:{}, result:'structure'|'json'} minimal invocation.",
            ),
            "argv": _param(
                "argv",
                "array",
                "Alternative diagnostic command arguments.",
                items={"type": "string"},
            ),
            "cwd": _param(
                "cwd",
                "string",
                "Working directory within task authority; defaults to workspace.",
            ),
        },
    )
    SAVE_CANDIDATE = ToolActionInfo(
        name="save_candidate",
        desc="Preserve candidate bytes without replacing the current accepted deliverable. Candidate metrics must be obtained by validation.",
        input_params={
            "files": _param(
                "files",
                "object",
                "Mapping of final output paths to candidate source file paths.",
                True,
            ),
            "note": _param(
                "note",
                "string",
                "Short provenance description; never treated as validation evidence.",
            ),
            "provenance": _param(
                "provenance",
                "array",
                "Optional derivation records {artifact,start,end,sources:[{path,start,end}]}. Byte ranges must refer to protected input snapshots; declarations are not semantic proof.",
                items={"type": "object"},
            ),
        },
    )
    VALIDATE_CANDIDATE = ToolActionInfo(
        name="validate_candidate",
        desc="Execute mandatory public checks and optional additional self-checks against the saved bytes. Returns measured metrics and a content-bound receipt. Inspect exposes check schema. Additional checks cannot replace public checks.",
        input_params={
            "candidate_id": _param(
                "candidate_id", "string", "Saved candidate ID.", True
            ),
            "checks": _param(
                "checks",
                "array",
                "Additional semantic checks with unique IDs and supported kinds.",
                items={"type": "object"},
            ),
        },
    )
    REVISE_CHECKS = ToolActionInfo(
        name="revise_checks",
        desc="Correct or remove your own mistaken semantic self-checks with a concrete reason. Public/caller requirements cannot change. History is retained and previous receipts require revalidation.",
        input_params={
            "checks": _param(
                "checks",
                "array",
                "Replacement agent self-check definitions.",
                items={"type": "object"},
            ),
            "remove_ids": _param(
                "remove_ids",
                "array",
                "Agent self-check IDs to remove.",
                items={"type": "string"},
            ),
            "reason": _param(
                "reason",
                "string",
                "Evidence explaining why the previous self-check was mistaken.",
                True,
            ),
        },
    )
    PROMOTE_CANDIDATE = ToolActionInfo(
        name="promote_candidate",
        desc="Publish a freshly validated candidate if it satisfies public constraints and improves the retained selection objective. Publication is atomic per file and recoverable across multiple files.",
        input_params={
            "candidate_id": _param("candidate_id", "string", "Candidate ID.", True),
            "receipt_id": _param(
                "receipt_id", "string", "Executed validation receipt ID.", True
            ),
        },
    )
    READBACK = ToolActionInfo(
        name="readback",
        desc="Re-read current published bytes and protected immutable inputs. Hash mismatches invalidate previous claims; final completion executes fresh mandatory checks.",
    )


WORKBENCH_SCHEMA_IDS = tuple(
    f"{WORKBENCH}__{item.value.name}" for item in WorkbenchAction
)


@ToolFactory.register(
    name=WORKBENCH,
    desc="Task deliverables, validation, candidates and input recovery",
    supported_action=WorkbenchAction,
)
class WorkbenchTool(AsyncTool):
    async def reset(self, *, seed=None, options=None):
        await super().reset(seed=seed, options=options)
        return build_observation(observer=self.name(), ability="inspect"), {}

    async def close(self):
        pass

    async def finished(self):
        return True

    async def do_step(self, actions, message=None, **kwargs):
        from aworld.core.task_workspace.session import get_task_workspace

        results = []
        for action in actions:
            try:
                session = get_task_workspace(getattr(message, "context", None))
                result = await session.execute(action.action_name, action.params or {})
                results.append(
                    ActionResult(
                        action_name=action.action_name,
                        tool_name=self.name(),
                        success=True,
                        content=json.dumps(result, ensure_ascii=False, allow_nan=False),
                    )
                )
            except Exception as exc:
                results.append(
                    ActionResult(
                        action_name=action.action_name,
                        tool_name=self.name(),
                        success=False,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
        observation = build_observation(
            observer=self.name(),
            ability=actions[0].action_name if actions else "inspect",
            action_result=results,
        )
        # Operation errors are repairable tool results, not an end-of-task signal.
        return observation, 0.0, False, False, {}
