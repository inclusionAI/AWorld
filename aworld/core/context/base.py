# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import copy
import hashlib
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, TYPE_CHECKING, List, Literal, Optional

from aworld.checkpoint.inmemory import InMemoryCheckpointRepository
from aworld.config import ConfigDict, AgentMemoryConfig
from aworld.core.context.context_state import ContextState
from aworld.core.context.compiler.sidecar import ContextObservationSidecar
from aworld.core.context.compiler.lifecycle import (
    ContextLifecycleEvent,
    ContextLifecycleState,
    LifecycleAction,
    transition_context_lifecycle,
)
from aworld.core.context.compiler.completion import (
    ArtifactEvidence,
    CompletionAssessment,
    CompletionContract,
    CompletionMode,
    ExternalVerifierEvidence,
    ImmutableInputEvidence,
    SelfCheckEvidence,
    assess_completion,
)
from aworld.core.context.compiler.progressive import (
    CatalogChangeAction,
    CatalogTransition,
    TaskCatalogSnapshot,
    SkillActivation,
    transition_task_catalog,
)
from aworld.core.context.compiler.reducers import ReductionReceipt
from aworld.core.context.compiler.tool_output import ToolOutputPolicy, ToolOutputRecord
from aworld.core.context.session import Session
from aworld.logs.util import logger
from aworld.core.trajectory_update_registry import TrajectoryUpdateOutcome, TrajectoryUpdateRegistry
from aworld.utils.common import nest_dict_counter, nest_dict_diff

if TYPE_CHECKING:
    from aworld.core.task import Task, TaskResponse, TaskStatus, TaskStatusValue
    from aworld.events.manager import EventManager
    from aworld.core.agent import BaseAgent
    from aworld.core.context.amni import AgentContextConfig


@dataclass
class ContextUsage:
    total_context_length: int = 128000
    used_context_length: int = 0

    def __init__(self, total_context_length: int = 128000, used_context_length: int = 0):
        self.total_context_length = total_context_length
        self.used_context_length = used_context_length


@dataclass
class AgentTokenIdStep:
    step: int
    tool_call_ids: List[str] = field(default_factory=list)
    # Prompt token ids of the current llm call, including historical messages.
    prompt_token_ids: List[int] = field(default_factory=list)
    # Input token ids of the step, without tokens of previous steps.
    input_token_ids: List[int] = field(default_factory=list)
    output_token_ids: List[int] = field(default_factory=list)
    output_logprobs: List[float] = field(default_factory=list)
    output_versions: List[int] = field(default_factory=list)
    tool_resp_token_ids: List[int] = field(default_factory=list)
    finish_reason: Literal["length", "stop", "interrupt"] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the AgentTokenIdStep to a dictionary."""
        return asdict(self)


@dataclass
class AgentTokenIdTrajectory:
    agent_id: str
    tool_call_id: str = None
    all_token_id_seq: List[int] = field(default_factory=list)
    token_id_steps: List[AgentTokenIdStep] = field(default_factory=list)

    def new_step(self):
        """Add a new step to the trajectory."""
        current_step = self.get_current_step()
        step = AgentTokenIdStep(step=(current_step.step if current_step else 0) + 1)
        self.token_id_steps.append(step)

    def get_current_step(self) -> AgentTokenIdStep:
        """Get the current step of the trajectory."""
        return self.token_id_steps[-1] if self.token_id_steps else None

    def to_dict(self) -> Dict[str, Any]:
        """Convert the AgentTokenIdTrajectory to a dictionary."""
        return asdict(self)


@dataclass
class StepLifecycleRecord:
    step_id: str
    name: str
    step_num: int
    alias_name: Optional[str] = None
    namespace: str = "default"
    parent_step_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Context:
    """Context is the core context management class in the AWorld architecture, used to store and manage
    the complete state information of an Agent, including configuration data and runtime state.

    Context serves as both a session-level context manager and agent-level context manager, providing:

    1. **State Restoration**: Save all state information during Agent execution, supporting Agent state restoration and recovery
    2. **Configuration Management**: Store Agent's immutable configuration information (such as agent_id, system_prompt, etc.)
    3. **Runtime State Tracking**: Manage Agent's mutable state during execution (such as messages, step, tools, etc.)
    4. **LLM Prompt Management**: Manage and maintain the complete prompt context required for LLM calls, including system prompts, historical messages, etc.
    5. **LLM Call Intervention**: Provide complete control over the LLM call process through Hook and ContextProcessor
    6. **Multi-task State Management**: Support fork_new_task and context merging for complex multi-task scenarios

    ## Lifecycle
    The lifecycle of Context is completely consistent with the Agent instance:
    - **Creation**: Created during Agent initialization, containing initial configuration
    - **Runtime**: Continuously update runtime state during Agent execution
    - **Destruction**: Destroyed along with Agent instance destruction
    ```
    ┌─────────────────────── AWorld Runner ─────────────────────────┐
    |  ┌──────────────────── Agent Execution ────────────────────┐  │
    │  │  ┌────────────── Step 1 ─────────────┐ ┌── Step 2 ──┐   │  │
    │  │  │  [LLM Call]     [Tool Call(s)]    │
    │  │  │  [       Context Update      ]    │
    ```

    ## Field Classification
    - **Immutable Configuration Fields**: agent_id, agent_name, agent_desc, system_prompt, 
       tool_names, context_rule
    - **Mutable Runtime Fields**: tools, step, messages, context_usage, llm_output, trajectories

    ## LLM Call Intervention Mechanism
    Context implements complete control over LLM calls through the following mechanisms:

    1. **Hook System**:
       - pre_llm_call_hook: Context preprocessing before LLM call
       - post_llm_call_hook: Result post-processing after LLM call
       - pre_tool_call_hook: Context adjustment before tool call
       - post_tool_call_hook: State update after tool call

    2. **PromptProcessor**:
       - Prompt Optimization: Optimize prompt content based on context length limitations
       - Message Compression: Intelligently compress historical messages to fit model context window
       - Context Rules: Apply context_rule for customized context processing

    ## Usage Scenarios
    1. **Agent Initialization**: Create Context containing configuration information
    2. **LLM Call Control**: Pass as info parameter in policy(), async_policy() methods to control LLM behavior
    3. **Hook Callbacks**: Access and modify LLM call context in various Hooks, use PromptProcessor for prompt optimization and context processing
    4. **State Recovery**: Recover Agent's complete state from persistent storage
    5. **Multi-task Management**: Use fork_new_task to create child contexts and merge_context to consolidate results

    Examples:
        >>> context = Context()
        >>> context.set_state("key", "value")
        >>> child_context = context.deep_copy()
        >>> context.merge_context(child_context)
    """

    def __init__(self,
                 user: str = None,
                 task_id: str = None,
                 trace_id: str = None,
                 session: Session = None,
                 **kwargs):
        self._user = user
        self._init(task_id=task_id, trace_id=trace_id,
                   session=session, **kwargs)

    def _init(self, *, task_id: str = None, trace_id: str = None, session: Session = None, **kwargs):
        self._task_id = task_id
        self._task = None
        self._trace_id = trace_id
        self._session: Session = session
        self.context_info = ContextState()
        self.agent_info = ConfigDict()
        self.trajectories = OrderedDict()
        self._token_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        # Workspace path for CLI/hook system (set by CLI on initialization)
        self._workspace_path: str = kwargs.get('workspace_path', None)
        self._merge_token_baseline = copy.deepcopy(self._token_usage)
        # TODO workspace
        self._event_manager = None
        # checkpoint repository for saving/restoring context state
        self._checkpoint_repository = kwargs.get('checkpoint_repository', InMemoryCheckpointRepository())
        self._start = time.time()
        # agent_id -> token_id trajectory
        self._agent_token_id_traj: Dict[str, List[AgentTokenIdTrajectory]] = {}

        self._task_graph: Dict[str, Dict[str, Any]] = {}
        self.trajectory_dataset = None
        self._trajectory_update_registry = TrajectoryUpdateRegistry()
        self._context_observations: Dict[
            tuple[str, str], ContextObservationSidecar
        ] = {}
        lifecycle_session_id = (
            session.session_id if session is not None and session.session_id else "unbound"
        )
        self._context_lifecycle_state = ContextLifecycleState(
            session_id=lifecycle_session_id,
            task_epoch=kwargs.get("task_epoch", 0),
        )
        self._context_lifecycle_events: List[ContextLifecycleEvent] = []
        self._completion_contract: CompletionContract | None = None
        self._completion_mode = CompletionMode.OFF
        self._completion_artifact_evidence: List[ArtifactEvidence] = []
        self._completion_immutable_input_evidence: List[ImmutableInputEvidence] = []
        self._completion_self_checks: List[SelfCheckEvidence] = []
        self._completion_final_evidence_codes: set[str] = set()
        self._completion_external_verifier: ExternalVerifierEvidence | None = None
        self._completion_repair_attempt = 0
        self._completion_assessment: CompletionAssessment | None = None
        self._task_tool_catalogs: Dict[str, TaskCatalogSnapshot] = {}
        self._tool_catalog_transitions: List[CatalogTransition] = []
        self._task_skill_sets: Dict[str, tuple[str, ...]] = {}
        self._skill_activations: Dict[str, tuple[SkillActivation, ...]] = {}
        self._context_reduction_receipts: Dict[str, ReductionReceipt] = {}
        # Delegation depth belongs to the isolated execution Context.  It is
        # never inferred from transcript text and is advanced only by the
        # structured SubagentManager boundary.
        self._delegation_depth = 0
        self._tool_output_policy: ToolOutputPolicy | None = None
        self._tool_output_artifact_offload = True
        self._tool_output_records: Dict[str, ToolOutputRecord] = {}
        self._tool_output_artifact_paths: Dict[str, str] = {}
        # Provider cache identity is runtime evidence, never checkpoint payload.
        # It may be carried across an in-process task reset so the next exact
        # provider serialization can explain why the prefix cache changed.
        self._provider_cache_identity = kwargs.get(
            "provider_cache_identity"
        )
        self._pending_cache_break_reasons = set(
            kwargs.get("pending_cache_break_reasons", ())
        )

    @property
    def start_time(self) -> float:
        return self._start

    def add_token(self, usage: Dict[str, int]):
        self._token_usage = nest_dict_counter(self._token_usage, usage)

    def reset(self, **kwargs):
        previous_state = getattr(self, "_context_lifecycle_state", None)
        next_task_epoch = (
            previous_state.task_epoch + 1
            if isinstance(previous_state, ContextLifecycleState)
            else 0
        )
        kwargs.setdefault("task_epoch", next_task_epoch)
        kwargs.setdefault("session", getattr(self, "_session", None))
        kwargs.setdefault("workspace_path", getattr(self, "_workspace_path", None))
        kwargs.setdefault(
            "checkpoint_repository", getattr(self, "_checkpoint_repository", None)
        )
        kwargs.setdefault(
            "provider_cache_identity",
            getattr(self, "_provider_cache_identity", None),
        )
        pending_cache_break_reasons = set(
            getattr(self, "_pending_cache_break_reasons", ())
        )
        from aworld.core.context.compiler.models import CacheBreakReason

        pending_cache_break_reasons.add(CacheBreakReason.TASK_RESET)
        kwargs.setdefault(
            "pending_cache_break_reasons", pending_cache_break_reasons
        )
        self._init(**kwargs)

    def set_task(self, task: 'Task'):
        self._task = task

    def get_task(self) -> 'Task':
        return self._task

    @property
    def token_id_traj(self):
        return self._agent_token_id_traj

    @property
    def trace_id(self):
        return self._trace_id

    @trace_id.setter
    def trace_id(self, trace_id):
        self._trace_id = trace_id

    @property
    def token_usage(self):
        return self._token_usage

    @property
    def user(self):
        return self._user

    @user.setter
    def user(self, user):
        if user is not None:
            self._user = user

    @property
    def task_id(self):
        return self._task_id

    @task_id.setter
    def task_id(self, task_id):
        if task_id is not None:
            changed = (
                getattr(self, "_task_id", None) is not None
                and self._task_id != task_id
            )
            if changed:
                self.advance_context_lifecycle(LifecycleAction.NEW_TASK)
            self._task_id = task_id

    @property
    def task_epoch(self) -> int:
        return self._context_lifecycle_state.task_epoch

    @property
    def context_lifecycle_state(self) -> ContextLifecycleState:
        return self._context_lifecycle_state

    def get_context_lifecycle_events(self) -> tuple[ContextLifecycleEvent, ...]:
        return tuple(self._context_lifecycle_events)

    def configure_completion_contract(
        self, contract: CompletionContract | None, *, mode: CompletionMode = CompletionMode.OFF
    ) -> None:
        if contract is not None and not isinstance(contract, CompletionContract):
            raise TypeError("contract must be CompletionContract or None")
        self._completion_contract = contract
        self._completion_mode = CompletionMode(mode)
        self._completion_artifact_evidence = []
        self._completion_immutable_input_evidence = []
        self._completion_self_checks = []
        self._completion_final_evidence_codes = set()
        self._completion_external_verifier = None
        self._completion_repair_attempt = 0
        self._completion_assessment = None

    def configure_tool_output_boundary(
        self,
        policy: ToolOutputPolicy | None,
        *,
        artifact_offload: bool = True,
    ) -> None:
        if policy is not None and not isinstance(policy, ToolOutputPolicy):
            raise TypeError("policy must be a ToolOutputPolicy or None")
        if not isinstance(artifact_offload, bool):
            raise TypeError("artifact_offload must be a boolean")
        self._tool_output_policy = policy
        self._tool_output_artifact_offload = artifact_offload

    def record_tool_output(
        self,
        record: ToolOutputRecord,
        *,
        artifact_path: str | None,
    ) -> None:
        if not isinstance(record, ToolOutputRecord):
            raise TypeError("record must be a ToolOutputRecord")
        existing = self._tool_output_records.get(record.tool_call_id)
        if existing is not None and existing != record:
            raise ValueError("conflicting Tool output record for tool_call_id")
        self._tool_output_records[record.tool_call_id] = record
        if record.artifact is not None:
            if not artifact_path:
                raise ValueError("artifact Tool output requires a local path")
            self._tool_output_artifact_paths[record.artifact.ref] = artifact_path

    def get_tool_output_records(self) -> tuple[ToolOutputRecord, ...]:
        return tuple(self._tool_output_records.values())

    def read_tool_output_artifact(self, artifact_ref: str) -> bytes:
        path = self._tool_output_artifact_paths.get(artifact_ref)
        if path is None:
            raise KeyError("unknown Tool output artifact ref")
        record = next(
            (
                value
                for value in self._tool_output_records.values()
                if value.artifact is not None and value.artifact.ref == artifact_ref
            ),
            None,
        )
        if record is None or record.artifact is None:
            raise KeyError("Tool output artifact receipt is unavailable")
        with open(path, "rb") as stream:
            payload = stream.read()
        actual = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        if actual != record.artifact.content_hash:
            raise ValueError("Tool output artifact checksum mismatch")
        return payload

    def record_completion_artifact(self, evidence: ArtifactEvidence) -> None:
        if not isinstance(evidence, ArtifactEvidence):
            raise TypeError("evidence must be ArtifactEvidence")
        self._completion_artifact_evidence.append(evidence)

    def record_completion_self_check(self, evidence: SelfCheckEvidence) -> None:
        if not isinstance(evidence, SelfCheckEvidence):
            raise TypeError("evidence must be SelfCheckEvidence")
        self._completion_self_checks.append(evidence)

    def record_completion_immutable_input(
        self, evidence: ImmutableInputEvidence
    ) -> None:
        if not isinstance(evidence, ImmutableInputEvidence):
            raise TypeError("evidence must be ImmutableInputEvidence")
        self._completion_immutable_input_evidence.append(evidence)

    def record_completion_final_evidence(self, code: str) -> None:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("completion evidence code must be non-empty")
        self._completion_final_evidence_codes.add(code)

    def record_external_verifier(
        self, evidence: ExternalVerifierEvidence
    ) -> None:
        if not isinstance(evidence, ExternalVerifierEvidence):
            raise TypeError("evidence must be ExternalVerifierEvidence")
        self._completion_external_verifier = evidence

    def assess_completion_contract(
        self, *, agent_claimed_finished: bool
    ) -> CompletionAssessment | None:
        if self._completion_contract is None:
            return None
        assessment = assess_completion(
            self._completion_contract,
            mode=self._completion_mode,
            artifact_evidence=self._completion_artifact_evidence,
            immutable_input_evidence=self._completion_immutable_input_evidence,
            self_checks=self._completion_self_checks,
            final_evidence_codes=self._completion_final_evidence_codes,
            agent_claimed_finished=agent_claimed_finished,
            repair_attempt=self._completion_repair_attempt,
            external_verifier=self._completion_external_verifier,
        )
        self._completion_assessment = assessment
        return assessment

    def bind_task_tool_catalog(
        self,
        namespace: str,
        candidate: TaskCatalogSnapshot,
        *,
        action: CatalogChangeAction = CatalogChangeAction.ACCEPT_CURRENT_EPOCH,
    ) -> CatalogTransition:
        if candidate.task_epoch != self.task_epoch:
            raise ValueError("Tool Catalog task epoch does not match Context")
        previous = self._task_tool_catalogs.get(namespace)
        transition = transition_task_catalog(previous, candidate, action=action)
        self._task_tool_catalogs[namespace] = transition.snapshot
        self._tool_catalog_transitions.append(transition)
        return transition

    def bind_task_skill_set(
        self,
        namespace: str,
        requested_skill_ids: tuple[str, ...],
        *,
        sticky: bool,
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        requested = tuple(dict.fromkeys(requested_skill_ids))
        previous = self._task_skill_sets.get(namespace)
        if previous is None or not sticky:
            active = requested
            deferred = ()
        else:
            previous_ids = set(previous)
            # Contractions apply immediately; additions wait for a new epoch.
            active = tuple(value for value in requested if value in previous_ids)
            deferred = tuple(value for value in requested if value not in previous_ids)
        self._task_skill_sets[namespace] = active
        return active, deferred

    def record_skill_activations(
        self, namespace: str, activations: tuple[SkillActivation, ...]
    ) -> None:
        values = tuple(activations)
        if not all(isinstance(value, SkillActivation) for value in values):
            raise TypeError("activations must contain SkillActivation values")
        self._skill_activations[namespace] = values

    def get_skill_activations(self) -> dict[str, tuple[SkillActivation, ...]]:
        return dict(self._skill_activations)

    def publish_context_reduction(self, receipt: ReductionReceipt) -> None:
        """Publish one owner-created, hash-bound reducer/offload receipt."""
        if not isinstance(receipt, ReductionReceipt):
            raise TypeError("receipt must be a ReductionReceipt")
        existing = self._context_reduction_receipts.get(receipt.item_id)
        if existing is not None and existing != receipt:
            raise ValueError("Context item already has a different reduction receipt")
        self._context_reduction_receipts[receipt.item_id] = receipt

    def get_context_reduction_receipts(self) -> tuple[ReductionReceipt, ...]:
        return tuple(self._context_reduction_receipts.values())

    def advance_context_lifecycle(
        self,
        action: LifecycleAction,
        *,
        branch_id: str | None = None,
        source_offset: str | int | None = None,
    ) -> ContextLifecycleEvent:
        items = tuple(
            item
            for sidecar in self.get_context_observations()
            for item in sidecar.result.items
        )
        event = transition_context_lifecycle(
            self._context_lifecycle_state,
            action,
            items=items,
            branch_id=branch_id,
            source_offset=source_offset,
        )
        self._context_lifecycle_state = event.current
        self._context_lifecycle_events.append(event)
        if event.cache_break_reason is not None:
            self._pending_cache_break_reasons.add(event.cache_break_reason)
        if action in {
            LifecycleAction.NEW_TASK,
            LifecycleAction.NEXT_TURN,
            LifecycleAction.BACKGROUND,
            LifecycleAction.CHECKPOINT,
            LifecycleAction.REWIND,
            LifecycleAction.RESUME,
        }:
            retained_ids = {
                decision.item_id
                for decision in event.item_decisions
                if decision.retained
            }
            self._context_observations = {
                key: sidecar
                for key, sidecar in self._context_observations.items()
                if all(item.id in retained_ids for item in sidecar.result.items)
            }
            self._context_reduction_receipts = {}
        if action in {LifecycleAction.NEW_TASK, LifecycleAction.BACKGROUND}:
            self._completion_artifact_evidence = []
            self._completion_immutable_input_evidence = []
            self._completion_self_checks = []
            self._completion_final_evidence_codes = set()
            self._completion_external_verifier = None
            self._completion_repair_attempt = 0
            self._completion_assessment = None
            self._task_tool_catalogs = {}
            self._tool_catalog_transitions = []
            self._task_skill_sets = {}
            self._skill_activations = {}
            self._context_reduction_receipts = {}
            self._tool_output_records = {}
            self._tool_output_artifact_paths = {}
        return event

    def commit_provider_cache_identity(self, verified_identity: Any) -> Dict[str, Any]:
        """Commit exact provider-wire cache evidence and explain continuity.

        Logical prefix hashes are intentionally insufficient here.  This API
        accepts only ``ProviderVerifiedCacheIdentity`` and consumes lifecycle
        invalidations exactly when a provider-owned serialized request exists.
        """
        from aworld.core.context.compiler.cache import ProviderVerifiedCacheIdentity

        if not isinstance(verified_identity, ProviderVerifiedCacheIdentity):
            raise TypeError(
                "verified_identity must be ProviderVerifiedCacheIdentity"
            )
        result = self.preview_provider_cache_identity(verified_identity)
        current = verified_identity.identity
        self._provider_cache_identity = current
        self._pending_cache_break_reasons.clear()
        return result

    def preview_provider_cache_identity(self, verified_identity: Any) -> Dict[str, Any]:
        """Compute cache continuity without mutating cache/lifecycle state."""
        from aworld.core.context.compiler.cache import (
            ProviderVerifiedCacheIdentity,
            cache_break_reasons,
        )
        from aworld.core.context.compiler.models import CacheBreakReason

        if not isinstance(verified_identity, ProviderVerifiedCacheIdentity):
            raise TypeError(
                "verified_identity must be ProviderVerifiedCacheIdentity"
            )
        current = verified_identity.identity
        previous = self._provider_cache_identity
        pending = set(self._pending_cache_break_reasons)
        reasons = cache_break_reasons(
            previous,
            current,
            history_compaction=(
                CacheBreakReason.HISTORY_COMPACTION in pending
            ),
            task_reset=CacheBreakReason.TASK_RESET in pending,
            resume_cache_expired=(
                CacheBreakReason.RESUME_CACHE_EXPIRED in pending
            ),
            provider_cache_unknown=(
                CacheBreakReason.PROVIDER_CACHE_UNKNOWN in pending
            ),
        )
        return {
            "status": (
                "initialized"
                if previous is None
                else "continued" if not reasons else "broken"
            ),
            "previous_present": previous is not None,
            "break_reasons": [reason.value for reason in reasons],
        }

    def prepare_delegated_child(self, *, delegation_depth: int) -> None:
        """Remove ambient parent runtime state before applying a Context Pack."""
        if isinstance(delegation_depth, bool) or not isinstance(
            delegation_depth, int
        ) or delegation_depth <= 0:
            raise ValueError("delegation_depth must be a positive integer")
        self._delegation_depth = delegation_depth
        self.context_info = ContextState()
        self.trajectories = OrderedDict()
        self._token_usage = {
            "completion_tokens": 0,
            "prompt_tokens": 0,
            "total_tokens": 0,
        }
        self._merge_token_baseline = copy.deepcopy(self._token_usage)
        self._merge_llm_calls_baseline = 0
        self.configure_completion_contract(None, mode=CompletionMode.OFF)
        self._task_tool_catalogs = {}
        self._tool_catalog_transitions = []
        self._task_skill_sets = {}
        self._skill_activations = {}
        self._context_reduction_receipts = {}
        self._tool_output_records = {}
        self._tool_output_artifact_paths = {}

    def _fence_context_observations_for_task_transition(
        self,
        *,
        current_task_id: str | None,
        next_task_id: str,
    ) -> None:
        """Discard request observations when a Context changes task identity."""
        if current_task_id is not None and current_task_id != next_task_id:
            self._context_observations = {}

    @property
    def session_id(self):
        if self.session:
            return self.session.session_id
        else:
            return None

    @property
    def workspace_path(self):
        """Get workspace path set by CLI."""
        return self._workspace_path

    @workspace_path.setter
    def workspace_path(self, path: str):
        """Set workspace path (typically set by CLI on initialization)."""
        self._workspace_path = path

    @property
    def session(self):
        return self._session

    @session.setter
    def session(self, session: Session):
        self._session = session
        state = getattr(self, "_context_lifecycle_state", None)
        if (
            isinstance(state, ContextLifecycleState)
            and state.session_id == "unbound"
            and session is not None
            and session.session_id
        ):
            self._context_lifecycle_state = ContextLifecycleState(
                session_id=session.session_id,
                session_epoch=state.session_epoch,
                task_epoch=state.task_epoch,
                turn_epoch=state.turn_epoch,
                branch_id=state.branch_id,
                checkpoint_revision=state.checkpoint_revision,
            )

    @property
    def swarm(self):
        return self._task.swarm

    @property
    def event_manager(self):
        return self._event_manager

    @event_manager.setter
    def event_manager(self, event_manager: 'EventManager'):
        self._event_manager = event_manager

    @property
    def checkpoint_repository(self):
        """Get checkpoint repository.

        Returns:
            The checkpoint repository if set, otherwise None
        """
        return self._checkpoint_repository

    @checkpoint_repository.setter
    def checkpoint_repository(self, repository: 'BaseCheckpointRepository'):
        """Set checkpoint repository.

        Args:
            repository: BaseCheckpointRepository instance for checkpoint storage
        """
        self._checkpoint_repository = repository

    @property
    def task_input(self):
        return self._task.input

    @task_input.setter
    def task_input(self, task_input):
        if self._task:
            self._task.input = task_input

    @property
    def outputs(self):
        return self._task.outputs

    @property
    def task_graph(self):
        return self._task_graph

    @task_graph.setter
    def task_graph(self, task_graph):
        self._task_graph = task_graph

    def get_state(self, key: str, default: Any = None) -> Any:
        return self.context_info.get(key, default)

    def set_state(self, key: str, value: Any):
        self.context_info[key] = value

    def get_llm_calls(self) -> List[Dict[str, Any]]:
        if isinstance(self.context_info, ContextState):
            local = self.context_info.local_dict()
            if "llm_calls" not in local:
                inherited = self.context_info.get("llm_calls")
                llm_calls = copy.deepcopy(inherited) if isinstance(inherited, list) else []
                self.context_info["llm_calls"] = llm_calls
                return llm_calls
        llm_calls = self.context_info.get("llm_calls")
        if not isinstance(llm_calls, list):
            llm_calls = []
            self.context_info["llm_calls"] = llm_calls
        return llm_calls

    def append_llm_call(self, llm_call: Dict[str, Any]) -> None:
        self.get_llm_calls().append(llm_call)

    @staticmethod
    def _llm_call_identity(llm_call: Dict[str, Any]) -> tuple[str, str] | None:
        """Return the strongest task-local identity available for one LLM call."""
        if not isinstance(llm_call, dict):
            return None
        for field in ("call_id", "request_id"):
            value = llm_call.get(field)
            if isinstance(value, str) and value:
                return field, value
        return None

    def _merge_llm_call(self, llm_call: Dict[str, Any]) -> None:
        """Reconcile a transported call snapshot without duplicating the call.

        A call can reach the runner root through an intermediate control message
        before the terminal task message arrives.  The terminal copy is normally
        more complete, so replace the existing snapshot in place when both carry
        the same stable identity; otherwise append it as a new call.
        """
        incoming = copy.deepcopy(llm_call)
        identity = self._llm_call_identity(incoming)
        calls = self.get_llm_calls()
        if identity is not None:
            for index, existing in enumerate(calls):
                if self._llm_call_identity(existing) == identity:
                    calls[index] = incoming
                    return
        calls.append(incoming)

    def get_context_inspector(self) -> Dict[str, Any] | None:
        """Return the latest redacted compiler projection for CLI/ACP consumers."""
        for record in reversed(self.get_llm_calls()):
            rollout = record.get("context_rollout") if isinstance(record, dict) else None
            projection = rollout.get("final_compile") if isinstance(rollout, dict) else None
            if isinstance(projection, dict):
                inspector = copy.deepcopy(projection)
                lowering = rollout.get("provider_lowering")
                if isinstance(lowering, dict):
                    inspector["provider_lowering"] = copy.deepcopy(lowering)
                inspector["runtime"] = {
                    "tool_outputs": [
                        {
                            "tool_call_id_hash": (
                                "sha256:"
                                + hashlib.sha256(
                                    record.tool_call_id.encode("utf-8")
                                ).hexdigest()
                            ),
                            "policy_version": record.policy_version,
                            "reason_code": record.reason_code,
                            "raw_byte_count": record.raw_byte_count,
                            "inline_tokens": record.inline_tokens,
                            "offloaded_tokens": record.offloaded_tokens,
                            "artifact_present": record.artifact is not None,
                            "upstream_artifact_count": len(
                                record.upstream_artifacts
                            ),
                        }
                        for record in self.get_tool_output_records()
                    ],
                    "skill_activations": {
                        namespace: [
                            {
                                "skill_id": activation.skill_id,
                                "level": activation.level.value,
                                "activated": activation.activated,
                                "reason_code": activation.reason_code,
                                "loaded_tokens": activation.loaded_tokens,
                                "requested_tool_count": len(
                                    activation.requested_tools
                                ),
                                "unavailable_tool_count": len(
                                    activation.unavailable_tools
                                ),
                            }
                            for activation in activations
                        ]
                        for namespace, activations in sorted(
                            self._skill_activations.items()
                        )
                    },
                    "tool_catalog_transitions": [
                        {
                            "catalog_hash": transition.snapshot.catalog_hash,
                            "added": list(transition.added),
                            "removed": list(transition.removed),
                            "applied_added": list(transition.applied_added),
                            "applied_removed": list(transition.applied_removed),
                            "deferred_added": list(transition.deferred_added),
                            "action": transition.action.value,
                            "cache_break_reason": (
                                transition.cache_break_reason.value
                                if transition.cache_break_reason is not None
                                else None
                            ),
                        }
                        for transition in self._tool_catalog_transitions
                    ],
                }
                return inspector
        return None

    def publish_context_observation(
        self, sidecar: ContextObservationSidecar
    ) -> None:
        """Publish the latest immutable owner sidecar outside ContextState."""
        if not isinstance(sidecar, ContextObservationSidecar):
            raise TypeError("sidecar must be a ContextObservationSidecar")
        observations = getattr(self, "_context_observations", None)
        if not isinstance(observations, dict):
            observations = {}
            self._context_observations = observations
        observations[(sidecar.owner, sidecar.namespace)] = sidecar

    def get_context_observations(
        self,
        *,
        owner: str | None = None,
        namespace: str | None = None,
    ) -> tuple[ContextObservationSidecar, ...]:
        """Read immutable sidecars in deterministic publication order."""
        return tuple(
            sidecar
            for sidecar in getattr(self, "_context_observations", {}).values()
            if (owner is None or sidecar.owner == owner)
            and (namespace is None or sidecar.namespace == namespace)
        )

    def refresh_nested_instruction_observation(
        self, *, active_path: str | None = None
    ) -> ContextObservationSidecar | None:
        """Load nested instruction files at their filesystem owner boundary."""
        if not self.workspace_path:
            return None
        from aworld.core.context.compiler import (
            AdapterResult,
            Authority,
            ContextEmissionIntent,
            ContextObservationSidecar,
            ModelResidency,
        )
        from aworld.core.context.instructions import ScopedInstructionLoader

        result = ScopedInstructionLoader().load(
            workspace=self.workspace_path,
            active_path=active_path or self.workspace_path,
            task_epoch=self.task_epoch,
        )
        # Root/workspace layers remain owned by the compatibility prompt path.
        # This bridge publishes only newly supported nested-directory layers,
        # avoiding a duplicate copy of legacy AWORLD.md content.
        nested_items = tuple(
            item for item in result.items if item.authority is Authority.DIRECTORY
        )
        sidecar = ContextObservationSidecar.from_adapter_result(
            owner="workspace.nested_instructions",
            namespace=str(self.workspace_path),
            source_identity=f"workspace-instructions:{self.workspace_path}",
            result=AdapterResult(
                items=nested_items,
                diagnostics=result.diagnostics,
            ),
            model_residency=ModelResidency.NOT_RESIDENT,
            emission_intent=ContextEmissionIntent.MESSAGE,
        )
        self.publish_context_observation(sidecar)
        return sidecar

    async def build_sub_context(self, sub_task_content: Any, sub_task_id: str = None, **kwargs):
        # Create a new Context instance without calling __init__ to avoid singleton issues
        new_context = object.__new__(Context)
        self._deep_copy(new_context)
        # Owner observations describe an already-built request in this task.
        # They may be copied for same-task isolation, but must never inherit
        # across a task boundary without explicit epoch/provenance evidence.
        new_context._context_observations = {}
        new_context.task_id = sub_task_id
        new_context.task_input = sub_task_content
        new_context._merge_llm_calls_baseline = len(new_context.get_llm_calls())
        self.add_task_node(sub_task_id, self.task_id, caller_agent_info=self.agent_info, **kwargs)
        return new_context

    def merge_sub_context(self, sub_task_context: 'ApplicationContext', **kwargs):
        self.merge_context(sub_task_context)

    def deep_copy(self, preserve_merge_baseline: bool = False) -> 'Context':
        # Create a new Context instance without calling __init__ to avoid singleton issues
        new_context = object.__new__(Context)
        return self._deep_copy(
            new_context,
            preserve_merge_baseline=preserve_merge_baseline,
        )

    def _deep_copy(self, new_context, preserve_merge_baseline: bool = False) -> 'Context':
        """Create a deep copy of this Context instance with all attributes copied.

        Returns:
            Context: A new Context instance with deeply copied attributes
        """

        # Manually copy all important instance attributes
        # Basic attributes
        new_context._user = self._user
        new_context._task_id = self._task_id
        new_context._trace_id = self._trace_id
        new_context._start = self._start
        new_context._workspace_path = self._workspace_path
        new_context._checkpoint_repository = self._checkpoint_repository
        # Session - shallow copy to maintain reference
        new_context._session = self._session

        # Task - set to None to avoid circular references
        new_context._task = None

        new_context._task_graph = self._task_graph
        new_context.trajectory_dataset = self.trajectory_dataset
        new_context._trajectory_update_registry = self._trajectory_update_registry
        new_context._context_observations = dict(
            getattr(self, "_context_observations", {})
        )
        new_context._context_lifecycle_state = self._context_lifecycle_state
        new_context._context_lifecycle_events = list(
            getattr(self, "_context_lifecycle_events", ())
        )
        new_context._completion_contract = self._completion_contract
        new_context._completion_mode = self._completion_mode
        new_context._completion_artifact_evidence = list(
            self._completion_artifact_evidence
        )
        new_context._completion_immutable_input_evidence = list(
            self._completion_immutable_input_evidence
        )
        new_context._completion_self_checks = list(self._completion_self_checks)
        new_context._completion_final_evidence_codes = set(
            self._completion_final_evidence_codes
        )
        new_context._completion_external_verifier = self._completion_external_verifier
        new_context._completion_repair_attempt = self._completion_repair_attempt
        new_context._completion_assessment = self._completion_assessment
        new_context._task_tool_catalogs = dict(self._task_tool_catalogs)
        new_context._tool_catalog_transitions = list(self._tool_catalog_transitions)
        new_context._task_skill_sets = dict(self._task_skill_sets)
        new_context._skill_activations = dict(self._skill_activations)
        new_context._context_reduction_receipts = dict(
            self._context_reduction_receipts
        )
        new_context._delegation_depth = getattr(self, "_delegation_depth", 0)
        new_context._tool_output_policy = self._tool_output_policy
        new_context._tool_output_artifact_offload = (
            self._tool_output_artifact_offload
        )
        new_context._tool_output_records = dict(self._tool_output_records)
        new_context._tool_output_artifact_paths = dict(
            self._tool_output_artifact_paths
        )
        new_context._provider_cache_identity = getattr(
            self, "_provider_cache_identity", None
        )
        new_context._pending_cache_break_reasons = set(
            getattr(self, "_pending_cache_break_reasons", ())
        )

        # Deep copy complex state objects
        try:
            new_context.context_info = copy.deepcopy(self.context_info)
        except Exception:
            new_context.context_info = copy.copy(self.context_info)

        try:
            # Use standard deep copy and then convert to ConfigDict if needed
            new_context.agent_info = copy.deepcopy(self.agent_info)
            # If the result is not ConfigDict but original was, convert it
            if isinstance(self.agent_info, ConfigDict) and not isinstance(new_context.agent_info, ConfigDict):
                new_context.agent_info = ConfigDict(new_context.agent_info)
        except Exception:
            # Fallback: manual deep copy for ConfigDict
            if isinstance(self.agent_info, ConfigDict):
                import json
                # Use JSON serialization for deep copy (if data is JSON-serializable)
                try:
                    serialized = json.dumps(dict(self.agent_info))
                    deserialized = json.loads(serialized)
                    new_context.agent_info = ConfigDict(deserialized)
                except Exception:
                    # Final fallback to shallow copy
                    new_context.agent_info = copy.copy(self.agent_info)
            else:
                new_context.agent_info = copy.copy(self.agent_info)

        try:
            new_context.trajectories = copy.deepcopy(self.trajectories)
        except Exception:
            new_context.trajectories = copy.copy(self.trajectories)

        try:
            new_context._token_usage = copy.deepcopy(self._token_usage)
        except Exception:
            new_context._token_usage = copy.copy(self._token_usage)
        try:
            baseline_source = (
                getattr(self, '_merge_token_baseline', new_context._token_usage)
                if preserve_merge_baseline
                else new_context._token_usage
            )
            new_context._merge_token_baseline = copy.deepcopy(baseline_source)
        except Exception:
            baseline_source = (
                getattr(self, '_merge_token_baseline', new_context._token_usage)
                if preserve_merge_baseline
                else new_context._token_usage
            )
            new_context._merge_token_baseline = copy.copy(baseline_source)

        # Copy other attributes if they exist
        if hasattr(self, '_event_manager'):
            new_context._event_manager = self._event_manager  # Shallow copy for complex objects

        if hasattr(self, '_agent_token_id_traj'):
            try:
                new_context._agent_token_id_traj = copy.deepcopy(self._agent_token_id_traj)
            except Exception:
                new_context._agent_token_id_traj = copy.copy(self._agent_token_id_traj)

        llm_calls = new_context.get_llm_calls()
        if preserve_merge_baseline:
            inherited_baseline = getattr(
                self,
                "_merge_llm_calls_baseline",
                len(llm_calls),
            )
            new_context._merge_llm_calls_baseline = max(
                0,
                min(inherited_baseline, len(llm_calls)),
            )
        else:
            new_context._merge_llm_calls_baseline = len(llm_calls)

        return new_context

    def merge_context(self, other_context: 'Context') -> None:
        if not other_context:
            return

        # 1. Merge context_info state
        if hasattr(other_context, 'context_info') and other_context.context_info:
            try:
                # Get local state from child context (excluding inherited parent state)
                if hasattr(other_context.context_info, 'local_dict'):
                    local_state = other_context.context_info.local_dict()
                    if local_state:
                        child_llm_calls = local_state.pop("llm_calls", None)
                        self.context_info.update(local_state)
                        if isinstance(child_llm_calls, list):
                            baseline = max(0, min(getattr(other_context, "_merge_llm_calls_baseline", 0), len(child_llm_calls)))
                            for llm_call in child_llm_calls[baseline:]:
                                self._merge_llm_call(llm_call)
                            other_context._merge_llm_calls_baseline = len(child_llm_calls)
                else:
                    # If no local_dict method, directly update all states
                    merged_state = other_context.context_info.to_dict()
                    child_llm_calls = merged_state.pop("llm_calls", None)
                    self.context_info.update(merged_state)
                    if isinstance(child_llm_calls, list):
                        baseline = max(0, min(getattr(other_context, "_merge_llm_calls_baseline", 0), len(child_llm_calls)))
                        for llm_call in child_llm_calls[baseline:]:
                            self._merge_llm_call(llm_call)
                        other_context._merge_llm_calls_baseline = len(child_llm_calls)
            except Exception as e:
                logger.warning(f"Failed to merge context_info: {e}")

        # 2. Merge trajectories
        if hasattr(other_context, 'trajectories') and other_context.trajectories:
            try:
                # Use timestamp or step number to avoid key conflicts
                for key, value in other_context.trajectories.items():
                    # If key already exists, add suffix to avoid overwriting
                    merge_key = key
                    counter = 1
                    while merge_key in self.trajectories:
                        merge_key = f"{key}_merged_{counter}"
                        counter += 1
                    self.trajectories[merge_key] = value
            except Exception as e:
                logger.warning(f"Failed to merge trajectories: {e}")

        # 3. Merge token usage statistics
        if hasattr(other_context, '_token_usage') and other_context._token_usage:
            try:
                child_tokens = other_context._token_usage.copy()
                baseline_tokens = getattr(other_context, '_merge_token_baseline', None) or {}

                # Calculate net increment relative to the child context's baseline.
                # This supports both:
                # 1. deep-copied child contexts that inherit parent token totals
                # 2. freshly created child contexts that start from zero
                net_tokens = nest_dict_diff(child_tokens, baseline_tokens)

                # Add net increment to parent context
                if net_tokens:
                    self.add_token(net_tokens)

                try:
                    other_context._merge_token_baseline = copy.deepcopy(child_tokens)
                except Exception:
                    other_context._merge_token_baseline = child_tokens.copy()
            except Exception as e:
                logger.warning(f"Failed to merge token usage: {e}")
                # If calculating net increment fails, directly add child context's tokens (may result in double counting)
                try:
                    self.add_token(other_context._token_usage)
                except Exception:
                    pass

        # 4. Merge agent_info configuration (only merge new configuration items)
        if hasattr(other_context, 'agent_info') and other_context.agent_info:
            try:
                # Only merge configuration items that don't exist in parent context
                for key, value in other_context.agent_info.items():
                    if key not in self.agent_info:
                        self.agent_info[key] = value
            except Exception as e:
                logger.warning(f"Failed to merge agent_info: {e}")

        # Record merge operation
        try:
            merge_info = {
                "merged_at": datetime.now().isoformat(),
                "merged_from_task_id": getattr(other_context, '_task_id', 'unknown'),
                "merged_trajectories_count": len(other_context.trajectories) if hasattr(other_context,
                                                                                        'trajectories') else 0,
                "merged_token_usage": other_context._token_usage if hasattr(other_context, '_token_usage') else {},
            }
            self.context_info.set('last_merge_info', merge_info)
        except Exception as e:
            logger.warning(f"Failed to record merge info: {e}")

    def merge_delegation_context(
        self,
        other_context: 'Context',
        *,
        delegation_record: Dict[str, Any],
    ) -> None:
        """Merge child accounting/evidence without importing its mutable state."""
        child_tokens = copy.deepcopy(getattr(other_context, '_token_usage', {}) or {})
        baseline_tokens = copy.deepcopy(
            getattr(other_context, '_merge_token_baseline', {}) or {}
        )
        net_tokens = nest_dict_diff(child_tokens, baseline_tokens)
        if net_tokens:
            self.add_token(net_tokens)
        baseline = max(
            0,
            min(
                getattr(other_context, "_merge_llm_calls_baseline", 0),
                len(other_context.get_llm_calls()),
            ),
        )
        for llm_call in other_context.get_llm_calls()[baseline:]:
            attributed = copy.deepcopy(llm_call)
            attributed["delegation"] = copy.deepcopy(delegation_record)
            self.append_llm_call(attributed)
        records = self.context_info.get("delegation_records")
        if not isinstance(records, list):
            records = []
        records.append(copy.deepcopy(delegation_record))
        self.context_info["delegation_records"] = records
        other_context._merge_token_baseline = child_tokens

    def save_action_trajectory(self,
                               step,
                               result: Any,
                               agent_name: str = None,
                               tool_name: str = None,
                               params: str = None):
        step_key = f"step_{step}"
        step_data = {
            "step": step,
            "params": params,
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_name,
            "tool_name": tool_name
        }
        self.trajectories[step_key] = step_data

    def add_background_task(self, task_id: str, agent_id: str, agent_name: str, parent_task_id: str = None):
        """Add a background task to the context."""
        if 'background_tasks' not in self.context_info:
            self.context_info['background_tasks'] = {}
        
        self.context_info['background_tasks'][task_id] = {
            'bg_task_id': task_id,
            'agent_id': agent_id,
            'agent_name': agent_name,
            'parent_task_id': parent_task_id,
            'status': 'running',
            'start_time': time.time()
        }
        logger.info(f"Added background task {task_id} for agent {agent_name}({agent_id}) in task {parent_task_id}")

    def mark_background_task_completed(self, task_id: str):
        """Mark a background task as completed in the context."""
        if 'background_tasks' in self.context_info and task_id in self.context_info['background_tasks']:
            self.context_info['background_tasks'][task_id]['status'] = 'completed'
            self.context_info['background_tasks'][task_id]['end_time'] = time.time()
            logger.info(f"Marked background task {task_id} as completed")

    def has_pending_background_tasks(self, agent_id: str, parent_task_id: str = None) -> bool:
        """Check if an agent has any pending background tasks for a specific parent task."""
        if 'background_tasks' not in self.context_info:
            return False
        
        for task_id, task_info in self.context_info['background_tasks'].items():
            if (task_info.get('agent_id') == agent_id and 
                task_info.get('status') == 'running' and 
                (parent_task_id is None or task_info.get('parent_task_id') == parent_task_id)):
                return True
        return False

    async def update_task_after_run(self, task_response: 'TaskResponse'):
        pass

    def update_agent_step(self, agent_id: str):
        self.agent_info.current_agent_id = agent_id
        if agent_id not in self.agent_info:
            self.agent_info[agent_id] = {}
        if self.task_id not in self.agent_info[agent_id]:
            self.agent_info[agent_id][self.task_id] = {}
        agent_task_info = self.agent_info[agent_id][self.task_id]
        agent_task_info['step'] = agent_task_info.get('step', 0) + 1

    def get_agent_step(self, agent_id: str, task_id: str = None, agent_info: dict = None):
        if not agent_info:
            agent_info = self.agent_info
        if not task_id:
            task_id = self.task_id
        if not agent_id or not agent_info.get(agent_id, {}).get(task_id):
            return 0
        return agent_info[agent_id][task_id].get('step', 0)

    def open_step(
        self,
        *,
        name: str,
        step_num: int,
        alias_name: str | None = None,
        namespace: str | None = None,
        parent_step_id: str | None = None,
        step_id: str | None = None,
    ) -> Dict[str, Any]:
        resolved_namespace = self._resolve_step_namespace(namespace)
        active_steps = self._get_active_steps_map()
        stack = active_steps.setdefault(resolved_namespace, [])
        if parent_step_id is None and stack:
            parent_step_id = stack[-1].step_id
        if parent_step_id is None:
            parent_step_id = self._current_step_lineage_id() or self._get_inherited_step_id()
        record = StepLifecycleRecord(
            step_id=step_id or uuid.uuid4().hex,
            name=name,
            step_num=step_num,
            alias_name=alias_name,
            namespace=resolved_namespace,
            parent_step_id=parent_step_id,
        )
        stack.append(record)
        self.context_info["active_steps"] = active_steps
        self._push_step_lineage(record)
        return record.to_dict()

    def close_step(
        self,
        *,
        namespace: str | None = None,
        step_id: str | None = None,
        expected_name: str | None = None,
        expected_step_num: int | None = None,
    ) -> Dict[str, Any] | None:
        resolved_namespace = self._resolve_step_namespace(namespace)
        active_steps = self._get_active_steps_map()
        stack = active_steps.get(resolved_namespace)
        if not stack:
            return None

        if step_id is not None:
            for index in range(len(stack) - 1, -1, -1):
                record = stack[index]
                if record.step_id == step_id:
                    stack.pop(index)
                    self.context_info["active_steps"] = active_steps
                    self._remove_step_lineage(record.step_id)
                    return record.to_dict()
            return None

        for index in range(len(stack) - 1, -1, -1):
            record = stack[index]
            if expected_name is not None and record.name != expected_name:
                continue
            if expected_step_num is not None and record.step_num != expected_step_num:
                continue
            stack.pop(index)
            self.context_info["active_steps"] = active_steps
            self._remove_step_lineage(record.step_id)
            return record.to_dict()

        return None

    def current_step_id(self, namespace: str | None = None) -> str | None:
        resolved_namespace = self._resolve_step_namespace(namespace)
        active_steps = self._get_active_steps_map()
        stack = active_steps.get(resolved_namespace) or []
        if not stack:
            if namespace is not None:
                return None
            return self._current_step_lineage_id() or self._get_inherited_step_id()
        return stack[-1].step_id

    def inherit_step_parent(self, parent_step_id: str | None) -> None:
        if isinstance(parent_step_id, str) and parent_step_id:
            self.context_info["inherited_step_id"] = parent_step_id
        else:
            self.context_info.pop("inherited_step_id", None)

    def _resolve_step_namespace(self, namespace: str | None = None) -> str:
        if namespace:
            return namespace
        current_agent_id = getattr(self.agent_info, "current_agent_id", None) if self.agent_info else None
        if isinstance(current_agent_id, str) and current_agent_id:
            return current_agent_id
        return "default"

    def _get_active_steps_map(self) -> Dict[str, List[StepLifecycleRecord]]:
        active_steps = self.context_info.get("active_steps", {})
        if not isinstance(active_steps, dict):
            return {}
        return active_steps

    def _get_step_lineage(self) -> List[Dict[str, str]]:
        lineage = self.context_info.get("step_lineage", [])
        if not isinstance(lineage, list):
            return []
        return lineage

    def _push_step_lineage(self, record: StepLifecycleRecord) -> None:
        lineage = self._get_step_lineage()
        lineage.append({"step_id": record.step_id, "namespace": record.namespace})
        self.context_info["step_lineage"] = lineage

    def _remove_step_lineage(self, step_id: str) -> None:
        lineage = self._get_step_lineage()
        for index in range(len(lineage) - 1, -1, -1):
            item = lineage[index]
            if isinstance(item, dict) and item.get("step_id") == step_id:
                lineage.pop(index)
                break
        self.context_info["step_lineage"] = lineage

    def _current_step_lineage_id(self) -> str | None:
        lineage = self._get_step_lineage()
        for item in reversed(lineage):
            if isinstance(item, dict):
                step_id = item.get("step_id")
                if isinstance(step_id, str) and step_id:
                    return step_id
        return None

    def _get_inherited_step_id(self) -> str | None:
        inherited_step_id = self.context_info.get("inherited_step_id")
        if isinstance(inherited_step_id, str) and inherited_step_id:
            return inherited_step_id
        return None

    """
    Agent Skills Support
    """
    async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str):
        """
        init skill list from agent
        """

    async def active_skill(self, skill_name: str, namespace: str) -> str:
        """
        activate a skill help agent to perform a task
        """
        pass

    async def offload_skill(self, skill_name: str, namespace: str) -> str:
        """
        offload a skill help agent to perform a task
        """
        pass

    async def get_active_skills(self, namespace: str) -> list[str]:
        """
        get skills from context
        """
        pass

    async def get_skill_list(self, namespace: str) -> Dict[str, Any]:
        pass

    def get_agent_token_id_traj(self, agent_id: str = None, tool_call_id: str = None) -> AgentTokenIdTrajectory:
        """Get the token id trajectory of the agent.

        Args:
            agent_id: Agent id.
            tool_call_id: Tool call id when agent as tool.

        Returns:
            AgentTokenIdTrajectory: Token id trajectory of the agent.
        """
        if not agent_id and 'current_agent_id' in self.agent_info:
            agent_id = self.agent_info.current_agent_id
        if not tool_call_id and 'current_tool_call_id' in self.agent_info:
            tool_call_id = self.agent_info.current_tool_call_id
        if not agent_id:
            logger.error("No current agent id found in context.")
            raise Exception("No current agent id found in context.")

        if agent_id not in self._agent_token_id_traj:
            self._agent_token_id_traj[agent_id] = []
        trajectories = self._agent_token_id_traj[agent_id]
        if tool_call_id:
            for traj in trajectories:
                if traj.tool_call_id == tool_call_id:
                    return traj
                traj = AgentTokenIdTrajectory(agent_id=agent_id, tool_call_id=tool_call_id)
                trajectories.append(traj)
                return traj
        else:
            if trajectories:
                return trajectories[0]
            else:
                traj = AgentTokenIdTrajectory(agent_id=agent_id, tool_call_id=tool_call_id)
                trajectories.append(traj)
                return traj

    def add_llm_resp_token_ids(self,
                               input_token_ids: List[int],
                               prompt_token_ids: List[int],
                               response: "TokenIdModelResponse",
                               agent_id: str = None,
                               tool_call_id: str = None):
        """Add the token ids of the current step input to the context.

        Args:
            agent_id: Agent id.
            input_token_ids: Input token ids of the current step.
            prompt_token_ids: Prompt token ids of the current llm call.
            response: Token id model response.
            tool_call_id: Tool call id when agent as tool.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        step = token_id_traj.get_current_step()
        if not step:
            logger.error(f"No current step found in context. agent_id: {agent_id}, tool_call_id: {tool_call_id}")
            raise Exception("No current step found in context.")

        step.prompt_token_ids = prompt_token_ids
        step.input_token_ids = input_token_ids
        step.output_token_ids = response.output_token_ids
        step.output_logprobs = response.output_logprobs
        step.output_versions = response.output_versions
        step.finish_reason = response.finish_reason
        token_id_traj.all_token_id_seq.extend(step.input_token_ids + step.output_token_ids)

    def add_tool_resp_token_ids(self,
                                tool_resp_token_ids: List[int],
                                resp_tool_call_ids: List[str],
                                agent_id: str = None,
                                tool_call_id: str = None):
        """Add the token ids of the current step tool response to the context.

        Args:
            agent_id: Agent id.
            tool_resp_token_ids: Tool response token ids of the current step.
            tool_call_id: Tool call id when agent as tool.
        """
        if not tool_resp_token_ids:
            return
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        step = token_id_traj.get_current_step()
        if not step:
            logger.error("No current step found in context.")
            raise Exception("No current step found in context.")
        step.tool_call_ids = resp_tool_call_ids
        step.tool_resp_token_ids = tool_resp_token_ids
        step.output_token_ids.extend(tool_resp_token_ids)
        step.output_logprobs.extend([0.0] * len(tool_resp_token_ids))
        step.output_versions.extend([-1] * len(tool_resp_token_ids))
        token_id_traj.all_token_id_seq.extend(step.tool_resp_token_ids)

    def new_trajectory_step(self, agent_id: str = None, tool_call_id: str = None):
        """Add a new trajectory step to the context.

        Args:
            agent_id: Agent id.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        token_id_traj.new_step()

    def get_current_step_of_trajectory(self, agent_id: str = None, tool_call_id: str = None) -> AgentTokenIdStep:
        """Get the current step of the trajectory.

        Args:
            agent_id: Agent id.
            tool_call_id: Tool call id when agent as tool.

        Returns:
            AgentTokenIdStep: Current step of the trajectory.
        """
        token_id_traj = self.get_agent_token_id_traj(agent_id, tool_call_id)
        return token_id_traj.get_current_step()

    def merge_sub_task_token_ids(self, sub_task_context: 'Context'):
        """Merge sub task token ids to context"""
        for agent_id, token_id_trajs in sub_task_context._agent_token_id_traj.items():
            for traj in token_id_trajs:
                self._agent_token_id_traj[agent_id].append(traj)


    """
        Context Checkpoint Support
    """
    def _create_checkpoint_values(self) -> Dict[str, Any]:
        """Extract key state information from context for checkpoint.

        Returns:
            Dict containing context state values for checkpoint.
        """
        return {
            # Context state information
            'context_info': self.context_info.to_dict() if self.context_info else {},

            # Agent configuration
            'agent_info': dict(self.agent_info) if self.agent_info else {},

            # Execution trajectories
            'trajectories': dict(self.trajectories) if self.trajectories else {},

            # Token usage statistics
            'token_usage': copy.deepcopy(self._token_usage) if self._token_usage else {},

            # Basic identifiers
            'user': self._user,
            'task_id': self._task_id,
            'trace_id': self._trace_id,
            'context_lifecycle': {
                'session_id': self._context_lifecycle_state.session_id,
                'session_epoch': self._context_lifecycle_state.session_epoch,
                'task_epoch': self._context_lifecycle_state.task_epoch,
                'turn_epoch': self._context_lifecycle_state.turn_epoch,
                'branch_id': self._context_lifecycle_state.branch_id,
                'checkpoint_revision': self._context_lifecycle_state.checkpoint_revision,
            },

            # Timestamp for checkpoint creation
            'checkpoint_created_at': datetime.now().isoformat(),
        }

    def _create_checkpoint_metadata(self, metadata_extra: Optional[Dict[str, Any]] = None) -> 'CheckpointMetadata':
        """Create checkpoint metadata.

        Args:
            metadata_extra: Extra metadata to include.

        Returns:
            CheckpointMetadata object.
        """
        from aworld.checkpoint import CheckpointMetadata

        metadata_dict = {
            'session_id': self.session_id or 'unknown',
            'task_id': self._task_id or 'unknown',
        }

        # Add extra metadata if provided
        if metadata_extra:
            metadata_dict.update(metadata_extra)

        return CheckpointMetadata(**metadata_dict)

    async def snapshot(self):
        """Save current context state to a checkpoint.

        This method serializes the current context state into a Checkpoint object,
        which will be automatically saved to the internal checkpoint_repository
        if one has been set via `context.checkpoint_repository = repo`.
        """
        from aworld.checkpoint import create_checkpoint, VersionUtils

        # A checkpoint is a logical context rewrite/cache boundary even when
        # the repository later reports a persistence failure. Record that
        # transition before freezing the values so the revision is restorable.
        self.advance_context_lifecycle(LifecycleAction.CHECKPOINT)

        # Extract checkpoint values
        checkpoint_values = self._create_checkpoint_values()

        # Create checkpoint metadata
        from aworld.checkpoint import CheckpointMetadata

        checkpoint_metadata = CheckpointMetadata(
            session_id=self.session_id,
            task_id=self._task_id
        )

        # Get version for the checkpoint
        version = 1
        if self._checkpoint_repository:
            try:
                # Try to get last checkpoint for this session to determine next version
                last_checkpoint = await self._checkpoint_repository.aget_by_session(self.session_id)
                if last_checkpoint:
                    version = VersionUtils.get_next_version(last_checkpoint.version)
            except Exception as e:
                logger.warning(f"Failed to get last checkpoint version: {e}")

        # Create the checkpoint
        checkpoint = create_checkpoint(
            values=checkpoint_values,
            metadata=checkpoint_metadata,
            version=version
        )

        # Save asynchronously if repository available
        if self._checkpoint_repository:
            try:
                await self._checkpoint_repository.aput(checkpoint)
                logger.info(f"Checkpoint {checkpoint.id} saved asynchronously for task {self._task_id}")
            except Exception as e:
                logger.error(f"Failed to save checkpoint asynchronously: {e}")

        return checkpoint

    async def get_task_status(self):
        from aworld.core.common import TaskStatusValue
        return TaskStatusValue.SUCCESS

    async def update_task_status(self, task_id: str, status: 'TaskStatus'):
        pass

    async def post_init(self):
        pass

    def get_agent_context_config(self, namespace: str) -> 'AgentContextConfig':
        pass

    def get_agent_memory_config(self, namespace: str) -> 'AgentMemoryConfig':
        pass



    """
        Sub Task Trajectory Support
    """

    async def add_task_trajectory(self, task_id: str, task_trajectory: List[Dict[str, Any]], **kwargs):
        """Add trajectory data for a task.

        Args:
            task_id: The task id.
            task_trajectory: The list of trajectory steps.
        """
        if self.trajectory_dataset is None:
            return TrajectoryUpdateOutcome(False, False, error="trajectory dataset is unavailable")

        registry = self.trajectory_update_registry
        finalized_import = bool(kwargs.get("finalized_import", False))
        if registry.state(task_id) is None and finalized_import:
            # A remote/separate child finalized outside this root dataset. Open
            # an explicit import scope so persistence acknowledgement and
            # fencing are still observable rather than falling through to the
            # exception-swallowing legacy save path.
            registry.open(task_id)

        if registry.state(task_id) is not None:
            step_ids = [
                str(step.get("id", index)) if isinstance(step, dict) else str(getattr(step, "id", index))
                for index, step in enumerate(task_trajectory)
            ]
            batch_digest = hashlib.sha256("\0".join(step_ids).encode("utf-8")).hexdigest()
            logical_batch_id = f"batch:{batch_digest}"
            revision = int(kwargs.get("revision", 2))
            entry = registry.schedule(
                task_id=task_id,
                logical_step_id=logical_batch_id,
                revision=revision,
                update_factory=lambda: self.trajectory_dataset.save_task_trajectory_batch_tracked(
                    task_id, task_trajectory, revision=revision
                ),
            )
            outcome = await entry.task
            if finalized_import:
                registry.seal(task_id)
                await registry.drain(
                    task_id,
                    timeout=float(kwargs.get("finalize_timeout", 10) or 10),
                )
                fence = getattr(self.trajectory_dataset, "fence_task_updates", None)
                if callable(fence):
                    fence(task_id)
                registry.release(task_id)
            return outcome

        # Compatibility for contexts that are not runner-managed.
        await self.trajectory_dataset.save_task_trajectory(task_id, task_trajectory)
        return TrajectoryUpdateOutcome(True, True, persisted=bool(task_trajectory))


    @property
    def trajectory_update_registry(self) -> TrajectoryUpdateRegistry:
        return self._trajectory_update_registry

    async def update_task_trajectory(self, message: Any, task_id: str = None, **kwargs):
        """
        Generate trajectory item from message (or other source) and append to dataset.

        Args:
            message: Source message or data
            task_id: Optional task id
        """
        if not task_id:
            logger.error("update_task_trajectory#task_id is required")
            raise Exception("update_task_trajectory#task_id is required")

        if self.trajectory_dataset is None:
            return TrajectoryUpdateOutcome(
                build_succeeded=False,
                storage_acknowledged=False,
                error="trajectory dataset is unavailable",
            )

        logical_step_id = str(kwargs.get("logical_step_id") or getattr(message, "id", ""))
        revision = int(kwargs.get("revision", 1))
        registry_managed = bool(kwargs.get("_registry_managed", False))
        registry = self.trajectory_update_registry

        if not registry_managed and registry.state(task_id) is not None:
            entry = registry.schedule(
                task_id=task_id,
                logical_step_id=logical_step_id,
                revision=revision,
                update_factory=lambda: self.update_task_trajectory(
                    message,
                    task_id,
                    logical_step_id=logical_step_id,
                    revision=revision,
                    _registry_managed=True,
                ),
            )
            return await entry.task

        if registry_managed:
            return await self.trajectory_dataset.append_trajectory_tracked(
                message,
                task_id=task_id,
                logical_step_id=logical_step_id,
                revision=revision,
            )

        # Compatibility for callers outside a runner-managed lifecycle.
        item = await self.trajectory_dataset.append_trajectory(message, task_id=task_id)
        return TrajectoryUpdateOutcome(
            build_succeeded=item is not None,
            storage_acknowledged=item is not None,
            persisted=item is not None,
            item=item,
            error=None if item is not None else "trajectory update produced no item",
        )

    async def get_task_trajectory(self, task_id: str, **kwargs) -> List['TrajectoryItem']:
        """Get trajectory data for a task.

        Args:
            task_id: The task id.

        Returns:
            List[Dict[str, Any]]: The list of trajectory steps.
        """
        # Try to get from storage first
        if self.trajectory_dataset is not None:
            trajectory = await self.trajectory_dataset.get_task_trajectory(task_id, **kwargs)
            return trajectory
        return []

    def add_task_node(self, child_task_id: str, parent_task_id: str, caller_agent_info: dict = None, **kwargs):
        """Add a task node and its relationship to the task graph.

        Args:
            child_task_id: Child task id.
            parent_task_id: Parent task id.
        """
        if child_task_id not in self._task_graph:
            self._task_graph[child_task_id] = {}
        child_task_node = self._task_graph[child_task_id]

        agent_info = caller_agent_info
        if not agent_info:
            agent_info = self.agent_info
        caller_id = agent_info.current_agent_id if agent_info and hasattr(agent_info, 'current_agent_id') else None
        caller_info = child_task_node.get("caller_info", {})
        caller_info.update({
            "agent_id": caller_id,
            "agent_step": self.get_agent_step(caller_id, task_id=parent_task_id, agent_info=agent_info)
        })

        self._task_graph[child_task_id].update({
            "parent_task": parent_task_id,
            "caller_info": caller_info,
            **kwargs
        })
        logger.info(f"{self.task_id}#Task graph: {self._task_graph}")

    def get_task_graph(self) -> Dict[str, Any]:
        """Get the task execution graph structure.

        Returns:
            Dict containing nodes and edges representing the task execution flow.
            Format:
            {
                "nodes": [{"id": "task_id", "data": {...}}],
                "edges": [{"source": "parent_id", "target": "child_id", "relation": "..."}]
            }
        """
        nodes = []
        edges = []

        # Collect all unique task IDs
        task_ids = set(self._task_graph.keys())
        for child_data in self._task_graph.values():
            if "parent_task" in child_data and child_data["parent_task"] is not None:
                task_ids.add(child_data["parent_task"])

        # Build nodes
        for tid in task_ids:
            nodes.append({"id": tid})

        # Build edges
        for child_id, data in self._task_graph.items():
            parent_id = data.get("parent_task")
            if parent_id:
                edges.append({
                    "source": parent_id,
                    "target": child_id,
                    "metadata": data
                })

        return {
            "nodes": nodes,
            "edges": edges
        }
