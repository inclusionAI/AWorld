"""Lazy, model-selected advisory verification for the bundled AWorld agent.

The default CLI agent remains a single-agent swarm.  This module registers a
small Tool capability, but deliberately does not import or construct the
optional verifier Agent until the root model invokes the Tool.  The resulting
review is public-task-only, fresh-context, answer-only, read-only, and advisory.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import os
import re
import stat as stat_module
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import import_module
from typing import Any, Literal

from aworld.config import AgentConfig, ToolConfig
from aworld.core.agent.base import BaseAgent
from aworld.core.common import (
    ActionModel,
    Observation,
    ParamInfo,
    ToolActionInfo,
)
from aworld.core.context.generation_budget import GenerationBudgetPolicy
from aworld.core.context.compiler import (
    ChildResult,
    ChildStatus,
    DelegationSpec,
    MergePolicy,
    semantic_fingerprint,
)
from aworld.core.context.execution_state import state_context
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import AsyncTool, ToolFactory
from aworld.logs.util import logger


ADVISORY_VERIFIER_TOOL = "AWORLD_ADVISORY_VERIFIER"
ADVISORY_REVIEW_SCHEMA_VERSION = "aworld.advisory-review/v2"
_VERIFIER_MODULE = (
    "aworld_cli.builtin_agents.smllc.optional_agents.verifier.builder"
)
_MAX_TASK_CHARS = 8_192
_MAX_CANDIDATE_CHARS = 2_048
_MAX_EVIDENCE_CHARS = 1_024
_MAX_DELIVERABLES = 8
_MAX_DELIVERABLE_CHARS = 256
_MAX_REPORT_CHARS = 2_048
_MAX_RAW_REPORT_CHARS = 8_192
_REVIEW_INPUT_TOKENS_PER_TURN = 12_288
_REVIEW_OUTPUT_TOKENS_PER_TURN = 2_048
_REVIEW_MAX_TURNS = 2
_REVIEW_TOKEN_BUDGET = max(
    32_768,
    _REVIEW_MAX_TURNS
    * (_REVIEW_INPUT_TOKENS_PER_TURN + _REVIEW_OUTPUT_TOKENS_PER_TURN),
)
_REVIEW_OUTPUT_TOKEN_BUDGET = (
    _REVIEW_MAX_TURNS * _REVIEW_OUTPUT_TOKENS_PER_TURN
)
_REVIEW_WALL_CLOCK_SECONDS = 180.0
_REVIEW_MIN_START_SECONDS = 20.0
_REVIEW_MAX_ATTEMPTS = 1
_DECLARED_SNAPSHOT_SCHEMA = "aworld.declared-artifact-snapshot/v1"
_DECLARED_SNAPSHOT_TOTAL_BYTES = 12 * 1024
_DECLARED_SNAPSHOT_FILE_BYTES = 8 * 1024
_DIRECT_PUBLIC_CAPTURE_FILE_BYTES = 16 * 1024 * 1024
_DIRECT_PUBLIC_CAPTURE_TOTAL_BYTES = 32 * 1024 * 1024
_MUTATION_GATE_SCHEMA = "aworld.mutation-gate/v4"
_PUBLIC_TASK_CONTRACT_SCHEMA = "aworld.public-task-contract/v1"
_EXECUTABLE_OBLIGATION_KINDS = frozenset(
    {
        "function_signature",
        "import_contract",
        "process_liveness",
        "public_command",
        "service_port",
    }
)
_CANONICAL_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_READ_ONLY_FILESYSTEM_ACTIONS = frozenset(
    {
        "list_allowed_directories",
        "list_directory",
        "get_file_info",
        "read_file",
        "read_media_file",
        "search_content",
        "search_files",
    }
)
_DECISION_PATTERN = re.compile(
    r"(?im)^\s*-?\s*Decision:\s*`?(ready|repair|uncertain)`?\s*[.\s]*$"
)
_REVIEW_JSON_FIELDS = frozenset(
    {"decision", "evidence", "gaps", "recommended_next_action"}
)


def _parse_review_report(raw_report: str) -> tuple[str | None, str, str | None]:
    """Parse the strict JSON envelope, retaining one-release text compatibility."""

    candidate = raw_report.strip()
    if candidate.startswith("```") and candidate.endswith("```"):
        lines = candidate.splitlines()
        if len(lines) >= 3:
            candidate = "\n".join(lines[1:-1]).strip()
    candidates: list[Mapping[str, Any]] = []
    try:
        value = json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        value = None
    if isinstance(value, Mapping):
        candidates.append(value)
    else:
        decoder = json.JSONDecoder()
        for start, character in enumerate(raw_report):
            if character != "{":
                continue
            try:
                decoded, _end = decoder.raw_decode(raw_report[start:])
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(decoded, Mapping) and decoded not in candidates:
                candidates.append(decoded)

    valid: list[tuple[str, str]] = []
    for value in candidates:
        decision = value.get("decision")
        fields_valid = bool(
            set(value) == _REVIEW_JSON_FIELDS
            and decision in {"ready", "repair", "uncertain"}
            and all(
                isinstance(value.get(field), str)
                and bool(value[field].strip())
                and len(value[field].strip()) <= _MAX_REPORT_CHARS
                for field in _REVIEW_JSON_FIELDS - {"decision"}
            )
        )
        if fields_valid:
            normalized = json.dumps(
                {field: value[field].strip() for field in sorted(_REVIEW_JSON_FIELDS)},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            valid.append((str(decision), normalized))
    if len(valid) == 1:
        return valid[0][0], valid[0][1], None
    if len(valid) > 1:
        return None, raw_report, "decision_ambiguous"
    if candidates:
        return None, raw_report, "structured_decision_invalid"

    # Compatibility for reports produced by the immediately preceding wheel.
    # New prompts and tests use the JSON envelope; this path can be removed
    # after one release without reviving permissive prose inference.
    matches = list(_DECISION_PATTERN.finditer(raw_report))
    decisions = {match.group(1).lower() for match in matches}
    if len(decisions) == 1:
        return decisions.pop(), raw_report, None
    return (
        None,
        raw_report,
        "decision_unparseable" if not decisions else "decision_ambiguous",
    )


class AdvisoryVerifierAction(ToolAction):
    """The one explicit action exposed to the root model."""

    REVIEW_CANDIDATE = ToolActionInfo(
        name="review_candidate",
        desc=(
            "Explicitly request the task's only fresh-context, read-only "
            "review of the current candidate. Never retry an unavailable or "
            "inconclusive result; run one public task-derived validation "
            "instead. The report is advisory and never decides canonical "
            "reward or external verifier success."
        ),
        input_params={
            "candidate_claim": ParamInfo(
                name="candidate_claim",
                type="string",
                required=True,
                desc=(
                    "The bounded completion claim or candidate summary to cross-check."
                ),
            ),
            "deliverables": ParamInfo(
                name="deliverables",
                type="array",
                required=False,
                items={"type": "string"},
                desc=(
                    "Optional public workspace paths or artifact names relevant to the claim."
                ),
            ),
            "evidence_summary": ParamInfo(
                name="evidence_summary",
                type="string",
                required=False,
                desc=(
                    "Optional bounded summary of public checks already run. "
                    "The reviewer independently inspects the shared workspace."
                ),
            ),
        },
    )


@dataclass(frozen=True, slots=True)
class AdvisoryReviewRequest:
    """Bounded candidate input supplied by the root model.

    The public task is intentionally absent: only the caller Context may bind
    the user objective supplied to a fresh verifier.
    """

    candidate_claim: str
    deliverables: tuple[str, ...] = ()
    evidence_summary: str = ""

    @classmethod
    def from_params(cls, value: Mapping[str, Any]) -> "AdvisoryReviewRequest":
        if not isinstance(value, Mapping):
            raise ValueError("review_candidate parameters must be an object")
        unknown = set(value) - {
            "candidate_claim",
            "deliverables",
            "evidence_summary",
        }
        if unknown:
            raise ValueError(
                "review_candidate contains unknown parameters: "
                + ", ".join(sorted(str(item) for item in unknown))
            )

        candidate_claim = _bounded_required_text(
            value.get("candidate_claim"),
            "candidate_claim",
            _MAX_CANDIDATE_CHARS,
        )
        evidence_summary = _bounded_optional_text(
            value.get("evidence_summary"),
            "evidence_summary",
            _MAX_EVIDENCE_CHARS,
        )
        raw_deliverables = value.get("deliverables", ())
        if raw_deliverables is None:
            raw_deliverables = ()
        if (
            not isinstance(raw_deliverables, Sequence)
            or isinstance(raw_deliverables, (str, bytes))
        ):
            raise ValueError("deliverables must be an array of strings")
        if len(raw_deliverables) > _MAX_DELIVERABLES:
            raise ValueError(
                f"deliverables must contain at most {_MAX_DELIVERABLES} items"
            )
        deliverables = tuple(
            _bounded_required_text(
                item,
                f"deliverables[{index}]",
                _MAX_DELIVERABLE_CHARS,
            )
            for index, item in enumerate(raw_deliverables)
        )
        return cls(
            candidate_claim=candidate_claim,
            deliverables=deliverables,
            evidence_summary=evidence_summary,
        )

    def render_directive(
        self,
        *,
        public_task: str,
        authoritative_deliverables: Sequence[str] = (),
        authoritative_validation_receipts: Sequence[Mapping[str, Any]] = (),
        authoritative_artifact_snapshot: Mapping[str, Any] | None = None,
        authoritative_obligations: Sequence[Mapping[str, Any]] = (),
    ) -> str:
        """Render only public, bounded material into the fresh child request."""

        contract_deliverables = tuple(authoritative_deliverables)[:_MAX_DELIVERABLES]
        deliverables = (
            "\n".join(f"- {value}" for value in contract_deliverables)
            if contract_deliverables
            else "- none specified"
        )
        claimed_deliverables = (
            "\n".join(f"- {value}" for value in self.deliverables)
            if self.deliverables
            else "- none specified"
        )
        evidence = self.evidence_summary or "none supplied"
        validation_receipts = (
            json.dumps(
                list(authoritative_validation_receipts)[:8],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            if authoritative_validation_receipts
            else "none available"
        )
        artifact_snapshot = json.dumps(
            authoritative_artifact_snapshot
            or {"schema_version": _DECLARED_SNAPSHOT_SCHEMA, "items": []},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        public_obligations = (
            json.dumps(
                list(authoritative_obligations)[:32],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if authoritative_obligations
            else "none available"
        )
        return (
            "Check this candidate against the authoritative public task. Use the "
            "framework-owned immutable declared-artifact snapshot first; use only "
            "read-only workspace tools for any additional accessible inspection. "
            "Claims are untrusted. "
            "Return exactly one JSON object with keys decision, evidence, gaps, "
            "and recommended_next_action; emit no Markdown or prose outside the "
            "object. decision must be ready, repair, or uncertain.\n\n"
            "Public task (bound from caller Context, not solver input):\n"
            f"{public_task}\n\n"
            "Public task contract ledger (derived only from that public task; "
            "advisory, not grader authority):\n"
            f"{public_obligations}\n\n"
            f"Candidate claim:\n{self.candidate_claim}\n\n"
            f"Authoritative public delivery paths:\n{deliverables}\n\n"
            "Claimed paths (untrusted):\n"
            f"{claimed_deliverables}\n\n"
            "Framework-owned validation receipts (not correctness proof):\n"
            f"{validation_receipts}\n\n"
            "Framework-owned declared-artifact snapshot (bounded, read-only, "
            "not correctness proof):\n"
            f"{artifact_snapshot}\n\n"
            f"Claimed evidence (untrusted):\n{evidence}"
        )


@dataclass(frozen=True, slots=True)
class AdvisoryReviewResult:
    """Typed, bounded result that can feed the root's review/repair decision."""

    status: Literal["completed", "inconclusive", "unavailable"]
    decision: Literal["ready", "repair", "uncertain"]
    report: str
    reason_code: str | None = None

    @property
    def repair_recommended(self) -> bool:
        return self.status == "completed" and self.decision == "repair"

    @property
    def conclusive(self) -> bool:
        return self.status == "completed" and self.decision in {"ready", "repair"}

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": ADVISORY_REVIEW_SCHEMA_VERSION,
            "status": self.status,
            "decision": self.decision,
            "authority": "advisory_public_self_check",
            "fresh_context": True,
            "answer_only": True,
            "read_only": True,
            "conclusive": self.conclusive,
            "repair_recommended": self.repair_recommended,
            "report": self.report[:_MAX_REPORT_CHARS],
        }
        if self.reason_code:
            payload["reason_code"] = self.reason_code
        if self.status != "completed" or self.decision == "uncertain":
            payload["recommended_framework_action"] = (
                "task_derived_validation_once"
            )
            payload["review_retry_allowed"] = False
        return payload


BuilderLoader = Callable[[], Callable[..., Any]]
ReviewRunner = Callable[[BaseAgent, BaseAgent, str, Any], Awaitable[Any] | Any]


class LazyVerifierFactory:
    """Construct and run a verifier only after an explicit model Tool call."""

    def __init__(
        self,
        *,
        parent_agent: BaseAgent,
        sandbox: Any,
        agent_config: AgentConfig,
        generation_budget_policy: GenerationBudgetPolicy | None,
        generation_budget_explicit_fields: Sequence[str] = (),
        max_loop_steps: int = 0,
        llm_max_attempts: int = 3,
        llm_retry_delay: float = 2.0,
        builder_loader: BuilderLoader | None = None,
        review_runner: ReviewRunner | None = None,
    ) -> None:
        self._parent_agent = parent_agent
        self._sandbox = sandbox
        self._agent_config = agent_config
        self._generation_budget_policy = generation_budget_policy
        self._generation_budget_explicit_fields = tuple(
            generation_budget_explicit_fields
        )
        self._max_loop_steps = max_loop_steps
        # Advisory review has one bounded provider opportunity. Retrying the
        # same terminal request consumes the finalization window without adding
        # independent evidence.
        self._llm_max_attempts = 1
        self._llm_retry_delay = 0.0
        self._builder_loader = builder_loader or self._load_builder
        self._review_runner = review_runner or self._run_with_subagent_manager
        self._construction_count = 0
        self._review_attempt_keys: set[str] = set()
        self._review_attempt_count = 0

    @property
    def construction_count(self) -> int:
        """Expose content-free lifecycle telemetry for tests and diagnostics."""

        return self._construction_count

    @property
    def parent_agent(self) -> BaseAgent:
        """Return the caller identity this factory is permanently bound to."""

        return self._parent_agent

    @staticmethod
    def _load_builder() -> Callable[..., Any]:
        module = import_module(_VERIFIER_MODULE)
        return getattr(module, "build_verifier_swarm")

    def _construct_verifier(self) -> BaseAgent:
        builder = self._builder_loader()
        swarm = builder(
            sandbox=self._sandbox,
            agent_config=self._agent_config,
            generation_budget_policy=self._generation_budget_policy,
            generation_budget_explicit_fields=(
                self._generation_budget_explicit_fields
            ),
            max_loop_steps=self._max_loop_steps,
            llm_max_attempts=self._llm_max_attempts,
            llm_retry_delay=self._llm_retry_delay,
        )
        agents = getattr(swarm, "agents", None)
        if not isinstance(agents, Mapping) or len(agents) != 1:
            raise RuntimeError("lazy verifier builder must return one Agent")
        verifier = next(iter(agents.values()))
        self._validate_verifier_boundary(verifier)
        self._construction_count += 1
        return verifier

    @staticmethod
    def _validate_verifier_boundary(verifier: BaseAgent) -> None:
        if getattr(verifier, "subagent_context_mode", None) != "fresh":
            raise RuntimeError("lazy verifier must use fresh context")
        if getattr(verifier, "subagent_merge_mode", None) != "answer_only":
            raise RuntimeError("lazy verifier must use answer-only merge")
        if getattr(verifier, "enable_subagent", None) is not False:
            raise RuntimeError("lazy verifier must not delegate")
        if list(getattr(verifier, "mcp_servers", ()) or ()) != ["filesystem"]:
            raise RuntimeError("lazy verifier must expose only filesystem MCP")
        allowlist = getattr(verifier, "mcp_tool_action_allowlist", None)
        filesystem_actions = (
            allowlist.get("filesystem") if isinstance(allowlist, Mapping) else None
        )
        if not filesystem_actions or not set(filesystem_actions).issubset(
            _READ_ONLY_FILESYSTEM_ACTIONS
        ):
            raise RuntimeError("lazy verifier filesystem surface must be read-only")

    async def review(
        self,
        request: AdvisoryReviewRequest,
        *,
        context: Any,
    ) -> AdvisoryReviewResult:
        """Run one deadline-bound review and fail open as unavailable."""

        try:
            public_task, request_error = _authoritative_public_task(context)
            if request_error is not None:
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=(
                        "The caller Context did not provide a usable authoritative "
                        "public task. The root agent retains completion authority."
                    ),
                    reason_code=request_error,
                )
            gate = _current_candidate_gate(
                context, agent_id=self._parent_agent.id()
            )
            candidate_fingerprint = (
                gate.get("candidate_fingerprint")
                if isinstance(gate, Mapping)
                else None
            )
            if not self._review_start_window_open(context):
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=(
                        "The remaining caller budget is reserved for validation, "
                        "persistence, and finalization."
                    ),
                    reason_code="review_start_window_closed",
                )
            authoritative_obligations = _authoritative_public_obligations(
                context,
                public_task=public_task,
            )
            authoritative_deliverables = _authoritative_delivery_paths(
                context,
                authoritative_obligations=authoritative_obligations,
            )
            artifact_snapshot_authentication = (
                _declared_snapshot_authentication_status(
                    context,
                    agent_id=self._parent_agent.id(),
                )
            )
            if artifact_snapshot_authentication.get("authenticated") is not True:
                artifact_snapshot_authentication = (
                    _direct_public_snapshot_authentication_status(
                        context,
                        authoritative_deliverables=authoritative_deliverables,
                    )
                )
            authoritative_artifact_snapshot = _authoritative_delivery_snapshot(
                context,
                authoritative_deliverables=authoritative_deliverables,
                authentication_status=artifact_snapshot_authentication,
            )
            post_snapshot_authentication = (
                _declared_snapshot_authentication_status(
                    context,
                    agent_id=self._parent_agent.id(),
                )
            )
            before_fingerprint = artifact_snapshot_authentication.get(
                "candidate_fingerprint"
            )
            if (
                isinstance(before_fingerprint, str)
                and (
                    post_snapshot_authentication.get("authenticated") is not True
                    or post_snapshot_authentication.get("candidate_fingerprint")
                    != before_fingerprint
                )
            ):
                authoritative_artifact_snapshot = {
                    "schema_version": _DECLARED_SNAPSHOT_SCHEMA,
                    "total_byte_limit": _DECLARED_SNAPSHOT_TOTAL_BYTES,
                    "authentication": "authentication_changed",
                    "items": [
                        {"path": path, "status": "authentication_changed"}
                        for path in authoritative_deliverables
                    ],
                }
            attempt_key = hashlib.sha256(
                json.dumps(
                    {
                        "public_task": public_task,
                        "candidate_claim": request.candidate_claim,
                        "deliverables": request.deliverables,
                        "evidence_summary": request.evidence_summary,
                        "candidate_fingerprint": candidate_fingerprint,
                        "artifact_snapshot": authoritative_artifact_snapshot,
                        "public_obligations": authoritative_obligations,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if attempt_key in self._review_attempt_keys:
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=(
                        "The current candidate has already received this bounded "
                        "advisory review attempt. The root agent should use the "
                        "existing result or continue with an explicitly revised "
                        "candidate."
                    ),
                    reason_code="duplicate_candidate_review",
                )
            if self._review_attempt_count >= _REVIEW_MAX_ATTEMPTS:
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=(
                        "The bounded candidate review has already been used. Run "
                        "one task-derived validation "
                        "and finalize the best current candidate."
                    ),
                    reason_code="review_attempt_limit",
                )
            self._review_attempt_keys.add(attempt_key)
            self._review_attempt_count += 1
            verifier = self._construct_verifier()
            authoritative_validation_receipts = _authoritative_validation_receipts(
                context,
                agent_id=self._parent_agent.id(),
            )
            directive = request.render_directive(
                public_task=public_task,
                authoritative_deliverables=authoritative_deliverables,
                authoritative_validation_receipts=(
                    authoritative_validation_receipts
                ),
                authoritative_artifact_snapshot=(
                    authoritative_artifact_snapshot
                ),
                authoritative_obligations=authoritative_obligations,
            )
            review_deadline = self._review_deadline(context)
            result = await self._invoke_review_runner(
                verifier=verifier,
                directive=directive,
                context=context,
                deadline=review_deadline,
                may_use_parent_finalization_reserve=(
                    _terminal_review_reserve_authorized(
                        context,
                        self._parent_agent,
                    )
                ),
            )
            child_failure = _child_failure_result(result)
            if child_failure is not None:
                return child_failure
            if isinstance(result, ChildResult):
                result = result.answer
            raw_report = str(result or "").strip()[:_MAX_RAW_REPORT_CHARS]
            if not raw_report:
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report="The advisory verifier returned no report.",
                    reason_code="empty_report",
                )
            if raw_report.startswith(("[Error]", "[Exception]")):
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=raw_report[:_MAX_REPORT_CHARS],
                    reason_code="reviewer_execution_failed",
                )
            decision, normalized_report, parse_reason = _parse_review_report(
                raw_report
            )
            if decision is None:
                return AdvisoryReviewResult(
                    status="inconclusive",
                    decision="uncertain",
                    report=raw_report[:_MAX_REPORT_CHARS],
                    reason_code=parse_reason,
                )
            executable_obligation_present = any(
                obligation.get("kind") in _EXECUTABLE_OBLIGATION_KINDS
                for obligation in authoritative_obligations
            )
            validation_evidence_present = any(
                receipt.get("exit_code") == 0
                for receipt in authoritative_validation_receipts
            ) or artifact_snapshot_authentication.get("reason") == (
                "current_stateful_probe"
            )
            captured_file_evidence_present = bool(
                artifact_snapshot_authentication.get("reason")
                == "current_public_capture"
                or any(
                    item.get("status") == "captured"
                    for item in (
                        authoritative_artifact_snapshot.get("items") or ()
                    )
                    if isinstance(item, Mapping)
                )
            )
            # Current file bytes can prove a static artifact requirement but
            # cannot prove executable behavior.  Behavioral contracts require
            # a fresh framework-bound public check before ``ready`` can be
            # promoted beyond advisory uncertainty.
            direct_ready_evidence = bool(
                validation_evidence_present
                or (
                    captured_file_evidence_present
                    and not executable_obligation_present
                )
            )
            if (
                decision == "ready"
                and getattr(context, "task_id", None) is not None
                and not direct_ready_evidence
            ):
                return AdvisoryReviewResult(
                    status="inconclusive",
                    decision="uncertain",
                    report=normalized_report[:_MAX_REPORT_CHARS],
                    reason_code="ready_evidence_missing",
                )
            return AdvisoryReviewResult(
                status=("inconclusive" if decision == "uncertain" else "completed"),
                decision=decision,
                report=normalized_report[:_MAX_REPORT_CHARS],
                reason_code=("reviewer_uncertain" if decision == "uncertain" else None),
            )
        except Exception as exc:
            logger.warning(
                "Lazy advisory verifier failed open; error_type=%s",
                type(exc).__name__,
            )
            return AdvisoryReviewResult(
                status="unavailable",
                decision="uncertain",
                report=(
                    "The advisory verifier was unavailable. The root agent "
                    "retains completion authority and should preserve material "
                    "uncertainty."
                ),
                reason_code=(
                    "deadline_exceeded"
                    if isinstance(exc, TimeoutError)
                    else "reviewer_unavailable"
                ),
            )

    async def _invoke_review_runner(
        self,
        *,
        verifier: BaseAgent,
        directive: str,
        context: Any,
        deadline: datetime,
        may_use_parent_finalization_reserve: bool,
    ) -> Any:
        """Bound default and injected runners by the same absolute deadline."""

        remaining = deadline.timestamp() - time.time()
        if remaining <= 0:
            raise asyncio.TimeoutError("advisory review deadline elapsed")

        async def invoke() -> Any:
            runner_identity = getattr(
                self._review_runner,
                "__func__",
                self._review_runner,
            )
            if inspect.iscoroutinefunction(self._review_runner):
                if runner_identity is LazyVerifierFactory._run_with_subagent_manager:
                    return await self._review_runner(
                        self._parent_agent,
                        verifier,
                        directive,
                        context,
                        may_use_parent_finalization_reserve=(
                            may_use_parent_finalization_reserve
                        ),
                    )
                return await self._review_runner(
                    self._parent_agent, verifier, directive, context
                )
            result = await asyncio.to_thread(
                self._review_runner,
                self._parent_agent,
                verifier,
                directive,
                context,
            )
            if inspect.isawaitable(result):
                return await result
            return result

        return await asyncio.wait_for(invoke(), timeout=remaining)

    @staticmethod
    def _review_start_window_open(context: Any) -> bool:
        get_task = getattr(context, "get_task", None)
        try:
            task = get_task() if callable(get_task) else None
            remaining = task.remaining_seconds() if task is not None else None
            reserve = (
                getattr(task, "completion_reserve_seconds", 0.0)
                if task is not None
                else 0.0
            )
        except Exception:
            return True
        if not isinstance(remaining, (int, float)) or isinstance(remaining, bool):
            return True
        safe_reserve = (
            float(reserve)
            if isinstance(reserve, (int, float))
            and not isinstance(reserve, bool)
            and reserve >= 0
            else 0.0
        )
        return float(remaining) - safe_reserve >= _REVIEW_MIN_START_SECONDS

    @staticmethod
    def _review_deadline(context: Any) -> datetime:
        """Return a finite child deadline without consuming caller persistence time."""

        available = _REVIEW_WALL_CLOCK_SECONDS
        get_task = getattr(context, "get_task", None)
        try:
            task = get_task() if callable(get_task) else None
            remaining = task.remaining_seconds() if task is not None else None
            reserve = (
                getattr(task, "completion_reserve_seconds", 0.0)
                if task is not None
                else 0.0
            )
        except Exception:
            remaining = None
            reserve = 0.0
        if isinstance(remaining, (int, float)) and not isinstance(remaining, bool):
            safe_reserve = (
                float(reserve)
                if isinstance(reserve, (int, float))
                and not isinstance(reserve, bool)
                and reserve >= 0
                else 0.0
            )
            available = min(available, max(0.001, float(remaining) - safe_reserve))
        return datetime.now(timezone.utc) + timedelta(seconds=available)

    @staticmethod
    async def _run_with_subagent_manager(
        parent_agent: BaseAgent,
        verifier: BaseAgent,
        directive: str,
        context: Any,
        *,
        may_use_parent_finalization_reserve: bool = False,
    ) -> ChildResult:
        """Use the existing fresh-child lifecycle and caller deadline plumbing."""

        from aworld.core.agent.subagent_manager import SubagentManager

        manager = SubagentManager(
            parent_agent,
            enable_agent_md_discovery=False,
        )
        # Registration only publishes this just-created verifier to the private
        # per-invocation manager.  It does not mutate the root swarm or enable
        # the generic spawn-subagent Tool.
        ephemeral_swarm = type(
            "LazyVerifierSwarm",
            (),
            {
                "agents": {
                    parent_agent.id(): parent_agent,
                    verifier.id(): verifier,
                }
            },
        )()
        await manager.register_team_members(ephemeral_swarm)
        return await manager.spawn(
            name="verifier",
            directive=directive,
            context=context,
            delegation_spec=DelegationSpec(
                objective=directive,
                context_item_ids=(),
                allowed_tools=(),
                token_budget=_REVIEW_TOKEN_BUDGET,
                max_output_tokens=_REVIEW_OUTPUT_TOKEN_BUDGET,
                max_turns=_REVIEW_MAX_TURNS,
                max_depth=1,
                deadline=LazyVerifierFactory._review_deadline(context),
                expected_output_schema={
                    "type": "string",
                    "minLength": 1,
                    "maxLength": _MAX_RAW_REPORT_CHARS,
                },
                inference_profile=None,
                stop_conditions=(),
                merge_policy=MergePolicy.ANSWER_ONLY,
                may_use_parent_finalization_reserve=(
                    may_use_parent_finalization_reserve
                ),
            ),
        )


@ToolFactory.register(
    name=ADVISORY_VERIFIER_TOOL,
    desc=(
        "Lazily request a fresh-context, read-only advisory review of a candidate"
    ),
    supported_action=AdvisoryVerifierAction,
)
class AdvisoryVerifierTool(AsyncTool):
    """Resolve the calling root's factory without constructing it eagerly."""

    def __init__(
        self,
        conf: ToolConfig | Mapping[str, Any] | None = None,
        *,
        factory: LazyVerifierFactory | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(conf=conf or {}, **kwargs)
        self._factory = factory

    async def reset(self, *, seed=None, options=None):
        return Observation(content="Lazy advisory verifier ready"), {}

    async def close(self) -> None:
        return None

    async def finished(self) -> bool:
        return True

    async def do_step(self, action: list[ActionModel], **kwargs):
        if len(action or ()) != 1:
            return self._invalid("review_candidate requires exactly one action")
        action_model = action[0]
        if action_model.action_name != "review_candidate":
            return self._invalid(
                f"unknown advisory verifier action: {action_model.action_name}"
            )
        try:
            request = AdvisoryReviewRequest.from_params(action_model.params or {})
        except ValueError as exc:
            return self._invalid(str(exc))

        message = kwargs.get("message")
        context = (
            getattr(message, "context", None)
            if message is not None
            else None
        )
        if context is None:
            context = kwargs.get("context")
        factory, resolution_error = self._resolve_bound_factory(
            action_model,
            context=context,
        )
        if resolution_error is not None:
            return self._invalid(
                f"advisory verifier caller identity rejected: {resolution_error}"
            )
        if not isinstance(factory, LazyVerifierFactory):
            result = AdvisoryReviewResult(
                status="unavailable",
                decision="uncertain",
                report=(
                    "The advisory verifier capability is unavailable. The root "
                    "agent retains completion authority."
                ),
                reason_code="factory_unavailable",
            )
        else:
            if context is None:
                context = BaseAgent._get_current_context()
            if context is None:
                result = AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=(
                        "The advisory verifier has no active caller context. "
                        "The root agent retains completion authority."
                    ),
                    reason_code="context_unavailable",
                )
            else:
                result = await factory.review(request, context=context)

        payload = result.to_payload()
        return (
            Observation(content=json.dumps(payload, ensure_ascii=False)),
            1.0 if result.conclusive else 0.0,
            False,
            False,
            {"advisory_review": {key: value for key, value in payload.items() if key != "report"}},
        )

    @staticmethod
    def _agent_info_value(agent_info: Any, key: str) -> Any:
        if isinstance(agent_info, Mapping):
            return agent_info.get(key)
        return getattr(agent_info, key, None)

    @staticmethod
    def _agent_id(agent: Any) -> str | None:
        getter = getattr(agent, "id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            return None
        return str(value) if value else None

    def _resolve_bound_factory(
        self,
        action_model: ActionModel,
        *,
        context: Any,
    ) -> tuple[LazyVerifierFactory | None, str | None]:
        """Bind a factory to the authoritative caller in the message Context."""

        swarm = getattr(context, "swarm", None) if context is not None else None
        agents = getattr(swarm, "agents", None)
        caller = None
        if isinstance(agents, Mapping) and agents:
            action_agent_id = str(action_model.agent_name or "").strip()
            current_agent_id = str(
                self._agent_info_value(
                    getattr(context, "agent_info", None),
                    "current_agent_id",
                )
                or ""
            ).strip()
            caller_ids = tuple(
                dict.fromkeys(
                    value for value in (action_agent_id, current_agent_id) if value
                )
            )
            resolved = []
            for caller_id in caller_ids:
                candidate = agents.get(caller_id)
                if candidate is None:
                    return None, f"caller_not_in_context_swarm:{caller_id}"
                resolved.append(candidate)
            if resolved and any(candidate is not resolved[0] for candidate in resolved):
                return None, "action_and_context_caller_mismatch"
            if resolved:
                caller = resolved[0]
            else:
                # Only when the message carries no caller id may the active
                # contextvar identify a member of this exact authoritative swarm.
                fallback = BaseAgent._get_current_agent()
                fallback_id = self._agent_id(fallback)
                if fallback_id and agents.get(fallback_id) is fallback:
                    caller = fallback
                else:
                    return None, "caller_identity_unavailable"
        else:
            # Compatibility path for direct Tool calls without a message swarm.
            caller = BaseAgent._get_current_agent()
            if caller is None and self._factory is not None:
                caller = self._factory.parent_agent

        factory = self._factory or getattr(caller, "lazy_verifier_factory", None)
        if isinstance(factory, LazyVerifierFactory):
            if caller is None or factory.parent_agent is not caller:
                return None, "factory_parent_mismatch"
        return factory, None

    @staticmethod
    def _invalid(message: str):
        return (
            Observation(content=message),
            0.0,
            False,
            False,
            {"error": message},
        )


def _bounded_required_text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ValueError(f"{name} must not exceed {limit} characters")
    return normalized


def _bounded_optional_text(value: Any, name: str, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise ValueError(f"{name} must not exceed {limit} characters")
    return normalized


def _terminal_review_reserve_authorized(
    context: Any,
    parent_agent: BaseAgent,
) -> bool:
    """Grant reserve use only to a canonical terminal review in this scope."""

    parent_id_getter = getattr(parent_agent, "id", None)
    try:
        parent_id = parent_id_getter() if callable(parent_id_getter) else None
    except Exception:
        return False
    if not isinstance(parent_id, str) or not parent_id:
        return False

    swarm = getattr(context, "swarm", None)
    agents = getattr(swarm, "agents", None)
    if not isinstance(agents, Mapping) or agents.get(parent_id) is not parent_agent:
        return False
    agent_info = getattr(context, "agent_info", None)
    current_agent_id = (
        agent_info.get("current_agent_id")
        if isinstance(agent_info, Mapping)
        else getattr(agent_info, "current_agent_id", None)
    )
    if current_agent_id != parent_id:
        return False

    try:
        from aworld.core.execution_protocol import (
            ExecutionProtocolStore,
            ProtocolPhase,
        )
        from aworld.runners.execution_protocol import (
            execution_protocol_policy,
            load_candidate_fallback,
        )

        policy = execution_protocol_policy(context, parent_id)
        if not policy.terminal_review_gate_enabled:
            return False
        state = ExecutionProtocolStore(context, parent_id, policy).load()
        if state.phase not in {ProtocolPhase.REVIEW, ProtocolPhase.FINALIZE}:
            return False
        fallback = load_candidate_fallback(context, parent_id)
    except Exception:
        return False
    return bool(
        fallback
        and all(
            getattr(action, "agent_name", None) == parent_id
            and bool(str(getattr(action, "policy_info", "") or "").strip())
            for action in fallback
        )
    )


def _child_failure_result(value: Any) -> AdvisoryReviewResult | None:
    """Translate structured child lifecycle status without parsing error text."""

    if not isinstance(value, ChildResult):
        return None
    if (
        value.status is ChildStatus.SUCCEEDED
        and value.schema_validated
        and isinstance(value.answer, str)
    ):
        return None
    reason_codes = {
        ChildStatus.DEADLINE_EXCEEDED: "deadline_exceeded",
        ChildStatus.BUDGET_EXCEEDED: "reviewer_budget_exceeded",
        ChildStatus.CANCELLED: "reviewer_cancelled",
        ChildStatus.DEPTH_EXCEEDED: "reviewer_depth_exceeded",
        ChildStatus.PARTIAL: "reviewer_output_invalid",
        ChildStatus.FAILED: "reviewer_execution_failed",
        ChildStatus.SUCCEEDED: "reviewer_output_invalid",
    }
    reason_code = reason_codes[value.status]
    return AdvisoryReviewResult(
        status="unavailable",
        decision="uncertain",
        report=(
            "The advisory verifier did not produce a usable bounded report "
            f"({reason_code}). The root agent retains completion authority."
        ),
        reason_code=reason_code,
    )


def _authoritative_public_task(context: Any) -> tuple[str, str | None]:
    """Resolve public task text only from caller-owned Context state.

    ``origin_user_input`` wins because ``task_input`` may contain a continuation
    receipt or hook-expanded working prompt. Missing or oversized Context input
    fails open instead of falling back to a solver-provided value or silently
    truncating the objective.
    """

    candidates = [
        getattr(context, "origin_user_input", None),
        getattr(context, "task_input", None),
    ]
    task_state_input = getattr(
        getattr(getattr(context, "task_state", None), "task_input", None),
        "origin_user_input",
        None,
    )
    candidates.append(task_state_input)

    for value in candidates:
        text = _authoritative_text(value)
        if not text:
            continue
        if len(text) > _MAX_TASK_CHARS:
            return "", "authoritative_task_too_large"
        return text, None
    return "", "authoritative_task_unavailable"


def _authoritative_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (Mapping, list, tuple)):
        try:
            return json.dumps(value, ensure_ascii=False, separators=(",", ":")).strip()
        except (TypeError, ValueError):
            return ""
    return ""


def _trusted_context_info_value(context: Any, key: str) -> Any:
    """Read framework-owned task state through a transport Context copy.

    Tool execution may receive a child/transport Context whose EventManager
    points at the task owner.  Public contracts are authored on that owner;
    solver claims never populate this lookup.
    """

    candidates = (context, state_context(context))
    seen: set[int] = set()
    for candidate in candidates:
        identity = id(candidate)
        if identity in seen:
            continue
        seen.add(identity)
        context_info = getattr(candidate, "context_info", None)
        if isinstance(context_info, Mapping) and key in context_info:
            return context_info.get(key)
    return None


def _authoritative_delivery_paths(
    context: Any,
    *,
    authoritative_obligations: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, ...]:
    """Read the generic public-task delivery contract without trusting claims."""

    value = _trusted_context_info_value(context, "public_deliverable_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != "aworld.public-deliverables/v1"
        or value.get("authority") != "public_task_advisory"
        or value.get("source") != "public_task_text"
    ):
        paths = []
        for obligation in authoritative_obligations:
            expectation = obligation.get("expectation")
            path = (
                expectation.get("path")
                if obligation.get("kind") == "deliverable_file"
                and isinstance(expectation, Mapping)
                else None
            )
            if isinstance(path, str) and path.strip():
                paths.append(path.strip()[:_MAX_DELIVERABLE_CHARS])
        return tuple(dict.fromkeys(paths[:_MAX_DELIVERABLES]))
    artifacts = value.get("artifacts")
    if not isinstance(artifacts, list) or len(artifacts) > _MAX_DELIVERABLES:
        return ()
    paths: list[str] = []
    for artifact in artifacts:
        if (
            not isinstance(artifact, Mapping)
            or artifact.get("kind") != "file"
            or artifact.get("authority") != "public_task_advisory"
        ):
            return ()
        path = artifact.get("path")
        display_path = artifact.get("display_path")
        if (
            not isinstance(path, str)
            or not path.strip()
            or not isinstance(display_path, str)
            or not display_path.strip()
        ):
            return ()
        paths.append(path.strip()[:_MAX_DELIVERABLE_CHARS])
    return tuple(dict.fromkeys(paths))


def _authoritative_public_obligations(
    context: Any,
    *,
    public_task: str,
) -> tuple[dict[str, Any], ...]:
    """Project only the bounded ledger created from authoritative public text."""

    value = _trusted_context_info_value(context, "public_task_contract")
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != _PUBLIC_TASK_CONTRACT_SCHEMA
        or value.get("authority") != "public_task_advisory"
        or value.get("source") != "public_task_text"
        or value.get("request_fingerprint")
        != "sha256:" + hashlib.sha256(public_task.encode("utf-8")).hexdigest()
    ):
        return _derive_public_obligations(public_task)
    rows = value.get("obligations")
    if not isinstance(rows, list) or len(rows) > 32:
        return ()
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        if (
            not isinstance(row, Mapping)
            or row.get("authority") != "public_task_advisory"
            or row.get("source") != "public_task_text"
        ):
            return ()
        obligation_id = row.get("obligation_id")
        kind = row.get("kind")
        subject = row.get("subject")
        source_fingerprint = row.get("source_fingerprint")
        if (
            not isinstance(obligation_id, str)
            or not 0 < len(obligation_id) <= 128
            or obligation_id in seen
            or not isinstance(kind, str)
            or not 0 < len(kind) <= 64
            or not isinstance(subject, str)
            or not 0 < len(subject) <= 512
            or not isinstance(source_fingerprint, str)
            or _CANONICAL_SHA256.fullmatch(source_fingerprint) is None
        ):
            return ()
        expectation = row.get("expectation")
        try:
            json.dumps(expectation, ensure_ascii=False, sort_keys=True)
        except (TypeError, ValueError):
            return ()
        seen.add(obligation_id)
        result.append(
            {
                "obligation_id": obligation_id,
                "kind": kind,
                "subject": subject,
                "expectation": expectation,
                "source_fingerprint": source_fingerprint,
            }
        )
    return tuple(result)


def _derive_public_obligations(public_task: str) -> tuple[dict[str, Any], ...]:
    """Rebuild the advisory ledger when a transport Context lost projections.

    The extractor receives only the already bound public task and current
    public workspace root.  It has no verifier, reward, or solver-claim input.
    """

    try:
        from aworld_cli.core.runtime_completion import (
            infer_public_obligation_hints,
        )

        hints = infer_public_obligation_hints(
            public_task,
            workspace_path=os.getcwd(),
        )
    except Exception:
        return ()
    return tuple(
        {
            "obligation_id": hint.obligation_id,
            "kind": hint.kind,
            "subject": hint.subject,
            "expectation": hint.expectation,
            "source_fingerprint": hint.source_fingerprint,
        }
        for hint in hints[:32]
    )


def _direct_public_snapshot_authentication_status(
    context: Any,
    *,
    authoritative_deliverables: Sequence[str],
) -> Mapping[str, Any]:
    """Bind an in-progress review to current declared public files.

    This is advisory evidence only.  It neither creates a protocol candidate
    checkpoint nor authorizes external success.  Paths must already belong to
    the public task contract and pass the same openat/O_NOFOLLOW confinement as
    terminal snapshots.
    """

    hashes: dict[str, str] = {}
    remaining = _DIRECT_PUBLIC_CAPTURE_TOTAL_BYTES
    for path in tuple(authoritative_deliverables)[:_MAX_DELIVERABLES]:
        trusted_root = _trusted_declared_snapshot_root(context, path)
        if trusted_root is None or remaining <= 0:
            continue
        try:
            descriptor = _open_confined_snapshot_file(
                trusted_root=trusted_root,
                path=path,
            )
            with os.fdopen(descriptor, "rb") as handle:
                before = os.fstat(handle.fileno())
                if (
                    not stat_module.S_ISREG(before.st_mode)
                    or before.st_size > _DIRECT_PUBLIC_CAPTURE_FILE_BYTES
                    or before.st_size > remaining
                ):
                    continue
                digest = hashlib.sha256()
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
                after = os.fstat(handle.fileno())
            def identity(item):
                return (
                    item.st_dev,
                    item.st_ino,
                    item.st_size,
                    item.st_mtime_ns,
                )
            if identity(before) != identity(after):
                continue
            hashes[path] = "sha256:" + digest.hexdigest()
            remaining -= before.st_size
        except (OSError, TypeError, ValueError):
            continue
    return {
        "authenticated": bool(hashes),
        "reason": "current_public_capture" if hashes else "public_candidate_unavailable",
        "artifact_hashes": hashes,
    }


def _authoritative_delivery_snapshot(
    context: Any,
    *,
    authoritative_deliverables: Sequence[str] | None = None,
    authentication_status: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture a bounded immutable value projection of declared regular files.

    The verifier keeps its original read-only filesystem authority.  This
    projection bridges declared outputs outside its mounted roots without
    broadening those roots, following symlinks, or exposing undeclared files.
    """

    paths = tuple(
        authoritative_deliverables
        if authoritative_deliverables is not None
        else _authoritative_delivery_paths(context)
    )[:_MAX_DELIVERABLES]
    authenticated = bool(
        isinstance(authentication_status, Mapping)
        and authentication_status.get("authenticated") is True
    )
    authentication_reason = (
        authentication_status.get("reason")
        if isinstance(authentication_status, Mapping)
        and isinstance(authentication_status.get("reason"), str)
        else "authentication_unavailable"
    )
    if not authenticated:
        return {
            "schema_version": _DECLARED_SNAPSHOT_SCHEMA,
            "total_byte_limit": _DECLARED_SNAPSHOT_TOTAL_BYTES,
            "authentication": authentication_reason,
            "items": [
                {"path": path, "status": "unauthenticated"} for path in paths
            ],
        }
    remaining = _DECLARED_SNAPSHOT_TOTAL_BYTES
    expected_hashes = (
        authentication_status.get("artifact_hashes")
        if isinstance(authentication_status, Mapping)
        else None
    )
    if not isinstance(expected_hashes, Mapping):
        expected_hashes = {}
    items: list[dict[str, Any]] = []
    for path in paths:
        item: dict[str, Any] = {"path": path, "status": "unavailable"}
        trusted_root = _trusted_declared_snapshot_root(context, path)
        if trusted_root is None:
            item["status"] = "untrusted_path"
            items.append(item)
            continue
        expected_hash = expected_hashes.get(path)
        if not isinstance(expected_hash, str) or _CANONICAL_SHA256.fullmatch(
            expected_hash
        ) is None:
            item["status"] = "unauthenticated"
            items.append(item)
            continue
        try:
            limit = min(_DECLARED_SNAPSHOT_FILE_BYTES, remaining)
            if limit <= 0:
                item["status"] = "budget_exhausted"
                items.append(item)
                continue
            descriptor = _open_confined_snapshot_file(
                trusted_root=trusted_root,
                path=path,
            )
            with os.fdopen(descriptor, "rb") as handle:
                before = os.fstat(handle.fileno())
                if not stat_module.S_ISREG(before.st_mode):
                    items.append(item)
                    continue
                digest = hashlib.sha256()
                captured = bytearray()
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
                    if len(captured) < limit:
                        captured.extend(chunk[: limit - len(captured)])
                after = os.fstat(handle.fileno())
            def identity(value):
                return (
                    value.st_dev,
                    value.st_ino,
                    value.st_size,
                    value.st_mtime_ns,
                )
            if identity(before) != identity(after):
                item["status"] = "unstable"
                items.append(item)
                continue
            if "sha256:" + digest.hexdigest() != expected_hash:
                item["status"] = "authentication_changed"
                items.append(item)
                continue
            truncated = before.st_size > limit
            captured_bytes = bytes(captured)
            remaining -= len(captured_bytes)
            item.update(
                {
                    "status": "captured",
                    "size": before.st_size,
                    "truncated": truncated,
                    "captured_bytes": len(captured_bytes),
                    "captured_sha256": "sha256:"
                    + hashlib.sha256(captured_bytes).hexdigest(),
                }
            )
            try:
                item["content_utf8"] = captured_bytes.decode("utf-8-sig")
            except UnicodeDecodeError:
                item["encoding"] = "binary"
        except (OSError, TypeError, ValueError):
            pass
        items.append(item)
    return {
        "schema_version": _DECLARED_SNAPSHOT_SCHEMA,
        "total_byte_limit": _DECLARED_SNAPSHOT_TOTAL_BYTES,
        "authentication": authentication_reason,
        "items": items,
    }


def _trusted_declared_snapshot_root(context: Any, path: str) -> str | None:
    """Return a non-symlink root for a workspace or exact completion artifact."""

    normalized = os.path.abspath(os.path.expanduser(path))
    completion_contract = getattr(context, "completion_contract", None)
    exact_completion_paths = {
        os.path.abspath(os.path.expanduser(requirement.path))
        for requirement in getattr(completion_contract, "required_artifacts", ()) or ()
        if isinstance(getattr(requirement, "path", None), str)
    }
    if normalized in exact_completion_paths:
        root = os.path.dirname(normalized)
        return root if os.path.realpath(root) == root else None
    roots = [os.getcwd()]
    artifact_root = os.environ.get("ARTIFACT_DIRECTORY")
    if isinstance(artifact_root, str) and artifact_root.strip():
        roots.append(artifact_root.strip())
    for root in roots:
        trusted_root = os.path.abspath(os.path.expanduser(root))
        if trusted_root == os.path.sep:
            continue
        if os.path.realpath(trusted_root) != trusted_root:
            continue
        try:
            if os.path.commonpath((normalized, trusted_root)) == trusted_root:
                return trusted_root
        except ValueError:
            continue
    return None


def _open_confined_snapshot_file(*, trusted_root: str, path: str) -> int:
    """Open every descendant component with openat/O_NOFOLLOW confinement."""

    normalized = os.path.abspath(os.path.expanduser(path))
    relative = os.path.relpath(normalized, trusted_root)
    parts = tuple(part for part in relative.split(os.sep) if part not in {"", "."})
    if not parts or any(part == ".." for part in parts):
        raise OSError("snapshot path escapes trusted root")
    directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    directory_flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(trusted_root, directory_flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, directory_flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        file_flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)
        file_flags |= getattr(os, "O_NOFOLLOW", 0)
        result = os.open(parts[-1], file_flags, dir_fd=descriptor)
    finally:
        os.close(descriptor)
    return result


def _declared_snapshot_authentication_status(
    context: Any, *, agent_id: str
) -> Mapping[str, Any]:
    """Fail closed unless current declared bytes match a protocol checkpoint."""

    try:
        from aworld.runners.execution_protocol import (
            authenticated_declared_candidate_status,
        )

        return authenticated_declared_candidate_status(context, agent_id)
    except Exception:
        return {
            "authenticated": False,
            "reason": "authentication_unavailable",
        }


def _authoritative_validation_receipts(
    context: Any,
    *,
    agent_id: str,
) -> tuple[dict[str, Any], ...]:
    """Project only framework-recorded completion checks from scoped work state."""

    gate = _current_candidate_gate(context, agent_id=agent_id)
    candidate_fingerprint = (
        gate.get("candidate_fingerprint")
        if isinstance(gate, Mapping)
        else None
    )
    if not isinstance(candidate_fingerprint, str):
        return ()

    state = None
    reader = getattr(context, "read_task_runtime_state", None)
    if callable(reader):
        try:
            state = reader(agent_id, "adaptive_work_state")
        except Exception:
            state = None
    if not isinstance(state, Mapping):
        context_info = getattr(context, "context_info", None)
        state = (
            context_info.get(f"adaptive_work_state:{agent_id}")
            if isinstance(context_info, Mapping)
            else None
        )
    expected_scope = {
        "task_id": getattr(context, "task_id", None),
        "task_epoch": getattr(context, "task_epoch", None),
    }
    receipts: list[dict[str, Any]] = []
    raw_receipts = (
        state.get("validation_evidence")
        if isinstance(state, Mapping) and state.get("scope") == expected_scope
        else ()
    )
    for raw in raw_receipts[-8:] if isinstance(raw_receipts, list) else ():
        if (
            not isinstance(raw, Mapping)
            or raw.get("source") != "runtime_self_check"
            or raw.get("historical") is True
            or raw.get("candidate_fingerprint") != candidate_fingerprint
        ):
            continue
        command_id = raw.get("command_id")
        exit_code = raw.get("exit_code")
        output_hash = raw.get("output_hash")
        if (
            not isinstance(command_id, str)
            or not command_id.strip()
            or isinstance(exit_code, bool)
            or not isinstance(exit_code, int)
            or not isinstance(output_hash, str)
            or not output_hash.startswith("sha256:")
        ):
            continue
        receipts.append(
            {
                "command_id": command_id[:128],
                "exit_code": exit_code,
                "output_hash": output_hash[:128],
                "observed_at": str(raw.get("observed_at") or "")[:64],
                "candidate_fingerprint": candidate_fingerprint,
                "source": "runtime_self_check",
            }
        )
    try:
        from aworld.runners.execution_protocol import load_public_probe_receipts

        for raw in load_public_probe_receipts(context, agent_id)[-4:]:
            if (
                raw.get("tool_execution_succeeded") is not True
                or raw.get("stale") is not False
                or not isinstance(raw.get("receipt_id"), str)
                or not isinstance(raw.get("result_hash"), str)
            ):
                continue
            receipts.append(
                {
                    "command_id": str(raw.get("tool_identity") or "public_probe")[:128],
                    "exit_code": 0,
                    "output_hash": raw["result_hash"][:128],
                    "observed_at": "",
                    "candidate_fingerprint": candidate_fingerprint,
                    "source": "public_probe",
                    "hypothesis_id": str(raw.get("hypothesis_id") or "")[:128],
                    "probe_kind": str(raw.get("probe_kind") or "")[:64],
                    "receipt_id": raw["receipt_id"][:128],
                }
            )
    except Exception:
        pass
    return tuple(receipts)


def _current_candidate_gate(
    context: Any,
    *,
    agent_id: str,
) -> Mapping[str, Any] | None:
    """Return only this parent agent's exact, current v4 mutation gate."""

    owner = state_context(context)
    gate = None
    reader = getattr(owner, "read_task_runtime_state", None)
    if callable(reader):
        try:
            gate = reader(agent_id, "execution_protocol_mutation_gate")
        except Exception:
            gate = None
    if (
        not isinstance(gate, Mapping)
        or gate.get("schema_version") != _MUTATION_GATE_SCHEMA
    ):
        context_info = getattr(owner, "context_info", None)
        gate = (
            context_info.get(f"execution_protocol_mutation_gate:{agent_id}")
            if isinstance(context_info, Mapping)
            else None
        )
    expected_scope_hash = semantic_fingerprint(
        {
            "task_id": getattr(owner, "task_id", None),
            "task_epoch": getattr(owner, "task_epoch", None),
            "agent_id": agent_id,
        }
    )
    candidate_fingerprint = (
        gate.get("candidate_fingerprint")
        if isinstance(gate, Mapping)
        else None
    )
    if (
        not isinstance(gate, Mapping)
        or gate.get("schema_version") != _MUTATION_GATE_SCHEMA
        or gate.get("agent_id") != agent_id
        or gate.get("scope_hash") != expected_scope_hash
        or not isinstance(candidate_fingerprint, str)
        or _CANONICAL_SHA256.fullmatch(candidate_fingerprint) is None
    ):
        return None
    return gate


__all__ = [
    "ADVISORY_REVIEW_SCHEMA_VERSION",
    "ADVISORY_VERIFIER_TOOL",
    "AdvisoryReviewRequest",
    "AdvisoryReviewResult",
    "AdvisoryVerifierAction",
    "AdvisoryVerifierTool",
    "LazyVerifierFactory",
]
