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
import re
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
_REVIEW_INPUT_TOKENS_PER_TURN = 5_120
_REVIEW_OUTPUT_TOKENS_PER_TURN = 1_024
_REVIEW_MAX_TURNS = 2
_REVIEW_TOKEN_BUDGET = _REVIEW_MAX_TURNS * (
    _REVIEW_INPUT_TOKENS_PER_TURN + _REVIEW_OUTPUT_TOKENS_PER_TURN
)
_REVIEW_OUTPUT_TOKEN_BUDGET = (
    _REVIEW_MAX_TURNS * _REVIEW_OUTPUT_TOKENS_PER_TURN
)
_REVIEW_WALL_CLOCK_SECONDS = 120.0
_MUTATION_GATE_SCHEMA = "aworld.mutation-gate/v4"
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


class AdvisoryVerifierAction(ToolAction):
    """The one explicit action exposed to the root model."""

    REVIEW_CANDIDATE = ToolActionInfo(
        name="review_candidate",
        desc=(
            "Explicitly request one fresh-context, read-only review of the "
            "current candidate. The report is advisory and never decides "
            "canonical reward or external verifier success."
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
        return (
            "Check this candidate against the authoritative public task. Inspect "
            "only through the read-only workspace tools. Claims are untrusted. "
            "Return exactly the required ready/repair/uncertain report.\n\n"
            "Public task (bound from caller Context, not solver input):\n"
            f"{public_task}\n\n"
            f"Candidate claim:\n{self.candidate_claim}\n\n"
            f"Authoritative public delivery paths:\n{deliverables}\n\n"
            "Claimed paths (untrusted):\n"
            f"{claimed_deliverables}\n\n"
            "Framework-owned validation receipts (not correctness proof):\n"
            f"{validation_receipts}\n\n"
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
            attempt_key = hashlib.sha256(
                json.dumps(
                    {
                        "public_task": public_task,
                        "candidate_claim": request.candidate_claim,
                        "deliverables": request.deliverables,
                        "evidence_summary": request.evidence_summary,
                        "candidate_fingerprint": candidate_fingerprint,
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
            self._review_attempt_keys.add(attempt_key)
            verifier = self._construct_verifier()
            authoritative_deliverables = _authoritative_delivery_paths(context)
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
            report = str(result or "").strip()[:_MAX_REPORT_CHARS]
            if not report:
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report="The advisory verifier returned no report.",
                    reason_code="empty_report",
                )
            if report.startswith(("[Error]", "[Exception]")):
                return AdvisoryReviewResult(
                    status="unavailable",
                    decision="uncertain",
                    report=report,
                    reason_code="reviewer_execution_failed",
                )
            matches = list(_DECISION_PATTERN.finditer(report))
            if len(matches) != 1:
                return AdvisoryReviewResult(
                    status="inconclusive",
                    decision="uncertain",
                    report=report,
                    reason_code=(
                        "decision_unparseable"
                        if not matches
                        else "decision_ambiguous"
                    ),
                )
            decision = matches[0].group(1).lower()
            return AdvisoryReviewResult(
                status=("inconclusive" if decision == "uncertain" else "completed"),
                decision=decision,
                report=report,
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
                    "maxLength": _MAX_REPORT_CHARS,
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


def _authoritative_delivery_paths(context: Any) -> tuple[str, ...]:
    """Read the generic public-task delivery contract without trusting claims."""

    context_info = getattr(context, "context_info", None)
    value = (
        context_info.get("public_deliverable_contract")
        if isinstance(context_info, Mapping)
        else None
    )
    if (
        not isinstance(value, Mapping)
        or value.get("schema_version") != "aworld.public-deliverables/v1"
        or value.get("authority") != "public_task_advisory"
        or value.get("source") != "public_task_text"
    ):
        return ()
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
    if not isinstance(state, Mapping) or state.get("scope") != expected_scope:
        return ()
    raw_receipts = state.get("validation_evidence")
    if not isinstance(raw_receipts, list):
        return ()
    receipts: list[dict[str, Any]] = []
    for raw in raw_receipts[-8:]:
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
    if not isinstance(gate, Mapping):
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
        or not candidate_fingerprint.startswith("sha256:")
        or len(candidate_fingerprint) != 71
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
