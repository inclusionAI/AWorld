"""
SubagentManager - Lightweight Subagent Orchestration

This module implements the subagent mechanism that allows LLM agents to autonomously
delegate subtasks to specialized subagents at runtime.

Key Features:
- Auto-discovery: TeamSwarm members + agent.md files
- Context inheritance: Share workspace/config, independent execution state
- Tool access control: Whitelist + blacklist for security
- Concurrent-safe: asyncio.Lock for registration, snapshot reads for spawn
- Zero-configuration: Add agent.md file and it's automatically available

Architecture:
- SubagentInfo: Metadata about available subagents
- SubagentManager: Core orchestration logic (register, spawn, filter)
- Integration: Hooks into Agent.async_run() for auto-registration

Design Document: docs/design/subagent-architecture.md
"""

import asyncio
import copy
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Literal, Mapping, Optional

from aworld.config.conf import ConfigDict
from aworld.logs.util import logger
from aworld.utils.runtime_state import get_runtime_state_root
from aworld.utils.skill_loader import extract_front_matter
from aworld.core.context.compiler import (
    AdapterResult,
    ChildResult,
    ChildStatus,
    ChildUsage,
    ContextEmissionIntent,
    ContextObservationSidecar,
    ContextPack,
    DelegationSpec,
    ModelResidency,
    freeze_json,
    merge_child_result,
    validate_delegation_output,
)


def _default_agent_md_search_paths() -> list[str]:
    control_root = get_runtime_state_root()
    if control_root is not None:
        return [str(control_root / "agents")]
    return ["./.aworld/agents", "~/.aworld/agents", "./agents"]


def _observed_child_request_evidence(
    child_calls: list[dict[str, Any]],
) -> dict[str, Any]:
    """Project content-free evidence from requests that reached the provider.

    The collaborator transcript remains private.  This projection retains only
    bounded model/routing/reasoning fields plus attempt state, which is enough
    to distinguish inherited configuration from an actually prepared or
    invoked provider request.
    """
    profiles: list[dict[str, Any]] = []
    provider_attempt_count = 0
    provider_invoked_count = 0
    for raw in child_calls[-32:]:
        if not isinstance(raw, Mapping) or raw.get("record_kind") != "model_attempt":
            continue
        provider_request = raw.get("provider_request")
        payload = (
            provider_request.get("payload")
            if isinstance(provider_request, Mapping)
            else None
        )
        payload = payload if isinstance(payload, Mapping) else {}
        attempt_status = raw.get("provider_attempt_status")
        if attempt_status in {"prepared", "attempted"}:
            provider_attempt_count += 1
        if raw.get("provider_invoked") is True:
            provider_invoked_count += 1
        profile = {
            "model": payload.get("model") or raw.get("model"),
            "provider": (
                provider_request.get("provider_name")
                if isinstance(provider_request, Mapping)
                else None
            )
            or raw.get("provider_name"),
            "reasoning_effort": payload.get("reasoning_effort"),
            "thinking": payload.get("thinking"),
            "max_completion_tokens": payload.get("max_completion_tokens"),
            "provider_attempt_status": attempt_status,
            "provider_invoked": raw.get("provider_invoked") is True,
            "status": raw.get("status"),
        }
        if profile not in profiles:
            profiles.append(profile)
    return {
        "model_attempt_count": len(
            [
                raw
                for raw in child_calls[-32:]
                if isinstance(raw, Mapping)
                and raw.get("record_kind") == "model_attempt"
            ]
        ),
        "provider_prepared_or_attempted_count": provider_attempt_count,
        "provider_invoked_count": provider_invoked_count,
        "profiles": profiles[-8:],
    }


@dataclass
class SubagentInfo:
    """
    Metadata about an available subagent.

    Attributes:
        name: Subagent identifier (unique within manager)
        description: Brief description of subagent capabilities
        source: Where this subagent comes from ('team_member' or 'agent_md')
        tools: List of tool names this subagent can use
        agent_instance: Reference to Agent object (for TeamSwarm members)
        config: Configuration dict (for agent.md loaded subagents)
        context_mode: ``fresh`` removes ambient parent task state before execution
        merge_mode: ``answer_only`` merges accounting, not mutable child state
    """
    name: str
    description: str
    source: Literal['team_member', 'agent_md']
    tools: List[str]
    agent_instance: Optional['Agent'] = None  # Forward reference to avoid circular import
    config: Optional[dict] = None
    context_mode: Literal['inherit', 'fresh'] = 'inherit'
    merge_mode: Literal['full', 'answer_only'] = 'full'

    def __post_init__(self) -> None:
        if self.context_mode not in {"inherit", "fresh"}:
            raise ValueError("context_mode must be inherit or fresh")
        if self.merge_mode not in {"full", "answer_only"}:
            raise ValueError("merge_mode must be full or answer_only")


class SubagentManager:
    """
    Manages available subagents for an agent instance.

    Responsibilities:
    - Register TeamSwarm members as available subagents
    - Scan and load agent.md files for dynamic extension
    - Generate system prompt sections listing available subagents
    - Execute subagent spawning with proper isolation and merging
    - Apply tool access control (whitelist + blacklist)

    Thread Safety:
    - Registration operations (register_team_members, scan_agent_md_files) use asyncio.Lock
    - Read operations (spawn, generate_system_prompt_section) use snapshot reads
    - Idempotent: Multiple registrations are safe (checked via _registered flag)

    Usage:
        manager = SubagentManager(parent_agent)
        await manager.register_team_members(swarm)
        result = await manager.spawn('researcher', 'Search for X')
    """

    def __init__(
        self,
        agent: 'Agent',
        agent_md_search_paths: List[str] = None,
        *,
        enable_agent_md_discovery: bool = True,
    ):
        """
        Initialize SubagentManager for a parent agent.

        Args:
            agent: The parent Agent instance that owns this manager
            agent_md_search_paths: Optional search paths for agent.md files.
                                   If provided, will be used for lazy scanning on first spawn.
            enable_agent_md_discovery: Explicitly disable filesystem discovery
                                       for private, pre-registered managers.
        """
        self.agent = agent
        self._available_subagents: Dict[str, SubagentInfo] = {}
        self._registry_lock = asyncio.Lock()  # Protects registration operations
        self._registered = False  # Idempotency flag for team member registration
        self._spawn_tool_instance = None  # Lazy-initialized spawn_subagent tool (per-agent instance)

        # Lazy initialization support for agent.md scanning
        if not isinstance(enable_agent_md_discovery, bool):
            raise TypeError("enable_agent_md_discovery must be a boolean")
        self._agent_md_discovery_enabled = enable_agent_md_discovery
        self._scanned_agent_md_files = not enable_agent_md_discovery
        self._agent_md_search_paths = agent_md_search_paths  # Paths to scan (deferred until first spawn)
        self._agent_md_scan_task: Optional[asyncio.Task] = None  # Shared in-flight lazy scan task

        logger.debug(
            f"SubagentManager initialized for agent: {agent.name() if hasattr(agent, 'name') else 'unknown'}"
        )

    async def register_team_members(self, swarm: 'Swarm'):
        """
        Register TeamSwarm members as available subagents (thread-safe, idempotent).

        This method is automatically called when an agent with enable_subagent=True
        runs in a TeamSwarm context. All team members (except self) are registered
        as callable subagents.

        Args:
            swarm: The Swarm instance containing team members

        Thread Safety:
            - Uses asyncio.Lock to prevent concurrent registration
            - Idempotent: Multiple calls are safe (checked via _registered flag)

        Design Note:
            Only registers team members once per agent lifetime to avoid overhead.
            If the swarm composition changes at runtime, create a new agent instance.
        """
        async with self._registry_lock:
            # Idempotency check: only register once
            if self._registered:
                logger.debug(
                    f"SubagentManager.register_team_members: "
                    f"Already registered for agent {self.agent.name()}, skipping"
                )
                return

            if not swarm or not hasattr(swarm, 'agents'):
                logger.warning(
                    f"SubagentManager.register_team_members: "
                    f"Invalid swarm provided (no agents dict), skipping"
                )
                return

            # Register all team members except self
            registered_count = 0
            for agent_id, agent in swarm.agents.items():
                # Skip self to avoid recursion
                if agent.id() == self.agent.id():
                    continue

                # Create SubagentInfo
                subagent_info = SubagentInfo(
                    name=agent.name(),
                    description=agent.desc() if hasattr(agent, 'desc') else "",
                    source='team_member',
                    tools=agent.tool_names if hasattr(agent, 'tool_names') else [],
                    agent_instance=agent,
                    config=None,
                    context_mode=getattr(
                        agent, "subagent_context_mode", "inherit"
                    ),
                    merge_mode=getattr(agent, "subagent_merge_mode", "full"),
                )

                # Store in registry (may overwrite if name collision, which is intentional)
                self._available_subagents[agent.name()] = subagent_info
                registered_count += 1

            self._registered = True

            logger.info(
                f"SubagentManager.register_team_members: "
                f"Registered {registered_count} team members as subagents for agent {self.agent.name()}. "
                f"Available subagents: {list(self._available_subagents.keys())}"
            )

    async def scan_agent_md_files(self, search_paths: List[str] = None):
        """
        Scan and load agent.md files as available subagents (thread-safe).

        Searches specified directories for *.md files with valid agent configuration
        frontmatter. Invalid files are logged and skipped (non-fatal errors).

        Args:
            search_paths: List of directories to search. Defaults to:
                          ['./.aworld/agents', '~/.aworld/agents', './agents']

        Thread Safety:
            - Uses asyncio.Lock to prevent concurrent scanning
            - Safe to call multiple times with different paths

        Error Handling:
            - Missing directories: Logged as debug, continue scanning other paths
            - Parse failures: Logged as error, continue scanning other files
            - Invalid config: Logged as warning, file is skipped

        Design Note:
            User can add agent.md files at runtime and call this method to refresh.
            Consider caching parsed configs if performance becomes an issue.
        """
        if not search_paths:
            search_paths = _default_agent_md_search_paths()

        async with self._registry_lock:
            scanned_count = 0
            registered_count = 0

            for path_str in search_paths:
                path_obj = Path(path_str).expanduser().resolve()

                # Skip missing directories (not an error)
                if not path_obj.exists():
                    logger.debug(
                        f"SubagentManager.scan_agent_md_files: "
                        f"Search path not found: {path_str}, skipping"
                    )
                    continue

                if not path_obj.is_dir():
                    logger.debug(
                        f"SubagentManager.scan_agent_md_files: "
                        f"Path is not a directory: {path_str}, skipping"
                    )
                    continue

                # Scan all .md files in this directory
                for md_file in path_obj.glob('*.md'):
                    scanned_count += 1

                    try:
                        # Read file content
                        with open(md_file, 'r', encoding='utf-8') as f:
                            content_lines = f.readlines()

                        # Extract front matter
                        front_matter, body_start = extract_front_matter(content_lines)

                        # Validate required fields
                        name = front_matter.get('name')
                        if not name:
                            logger.warning(
                                f"SubagentManager.scan_agent_md_files: "
                                f"Missing 'name' field in {md_file}, skipping"
                            )
                            continue

                        # Extract configuration
                        description = front_matter.get('description', "")
                        tool_names = front_matter.get('tool_names', [])
                        mcp_servers = front_matter.get('mcp_servers', [])
                        disallowed_tools = front_matter.get('disallowedTools', [])
                        model = front_matter.get('model', 'inherit')

                        # Create SubagentInfo
                        subagent_info = SubagentInfo(
                            name=name,
                            description=description,
                            source='agent_md',
                            tools=tool_names,
                            agent_instance=None,
                            config={
                                'name': name,
                                'description': description,
                                'tool_names': tool_names,
                                'mcp_servers': mcp_servers,
                                'disallowedTools': disallowed_tools,
                                'model': model,
                                'file_path': str(md_file)
                            }
                        )

                        existing = self._available_subagents.get(name)
                        if existing is not None and existing.source == "team_member":
                            logger.warning(
                                "SubagentManager.scan_agent_md_files: "
                                f"Ignoring agent.md collision with team member: {name}"
                            )
                            continue

                        # Explicit agent.md sources may refresh another agent.md,
                        # but can never replace a wheel/team-owned collaborator.
                        self._available_subagents[name] = subagent_info
                        registered_count += 1

                        logger.info(
                            f"SubagentManager.scan_agent_md_files: "
                            f"Registered subagent from {md_file.name}: {name}"
                        )

                    except Exception as e:
                        logger.error(
                            f"SubagentManager.scan_agent_md_files: "
                            f"Failed to parse {md_file}: {e}",
                            exc_info=True
                        )
                        # Continue scanning other files

            logger.info(
                f"SubagentManager.scan_agent_md_files: "
                f"Scanned {scanned_count} files, registered {registered_count} subagents. "
                f"Total available: {len(self._available_subagents)}"
            )

    async def _ensure_agent_md_scanned(self):
        """
        Ensure agent.md files have been scanned (lazy initialization).

        This method implements lazy scanning to avoid sync_exec in __init__.
        Scanning is deferred until the first spawn() call, which runs in async context.

        Thread Safety:
            - Uses _registry_lock to prevent concurrent scans
            - Idempotent: Multiple calls are safe (checked via _scanned_agent_md_files flag)

        Design Rationale:
            - Avoids sync_exec in __init__ which can cause nested event loop issues
            - Scanning happens in async context where it belongs
            - Only pays scanning cost when subagent capability is actually used
            - Compatible with both sync and async initialization patterns
        """
        # Fast path: already scanned
        if self._scanned_agent_md_files:
            return
        if not self._agent_md_discovery_enabled:
            # Defensive against callers restoring older serialized state or
            # tests toggling the compatibility marker directly.
            self._scanned_agent_md_files = True
            return

        async with self._registry_lock:
            # Double-check after acquiring lock (race condition guard)
            if self._scanned_agent_md_files:
                return

            # Reuse any in-flight scan so concurrent callers wait on the same work.
            if self._agent_md_scan_task is None:
                search_paths = self._agent_md_search_paths
                if search_paths is None:
                    search_paths = _default_agent_md_search_paths()

                logger.debug(
                    f"SubagentManager._ensure_agent_md_scanned: "
                    f"Performing lazy scan of agent.md files for agent '{self.agent.name()}'"
                )
                self._agent_md_scan_task = asyncio.create_task(
                    self.scan_agent_md_files(search_paths=search_paths)
                )

            scan_task = self._agent_md_scan_task

        try:
            await scan_task
        except Exception:
            async with self._registry_lock:
                if self._agent_md_scan_task is scan_task:
                    self._agent_md_scan_task = None
            raise

        async with self._registry_lock:
            if self._agent_md_scan_task is scan_task:
                self._scanned_agent_md_files = True
                self._agent_md_scan_task = None

                logger.debug(
                    f"SubagentManager._ensure_agent_md_scanned: "
                    f"Lazy scan completed, {len(self._available_subagents)} subagents available"
                )

    def generate_system_prompt_section(self, max_subagents: int = 10) -> str:
        """
        Generate system prompt section listing available subagents (concurrent-safe).

        Creates a formatted markdown section that LLM can understand, listing
        each subagent's name, description, tools, and usage example.

        Args:
            max_subagents: Maximum number of subagents to include in prompt.
                           Prevents prompt explosion. Defaults to 10.

        Returns:
            Formatted markdown string for inclusion in agent system prompt.
            Empty string if no subagents are available.

        Thread Safety:
            - Uses snapshot read (_available_subagents copy) to avoid lock contention
            - Safe to call concurrently with registration operations

        Design Note:
            If more than max_subagents exist, shows first N and indicates total count.
            Future enhancement: Sort by usage frequency (most used first).
        """
        # Concurrent-safe: snapshot read
        subagents_snapshot = dict(self._available_subagents)

        if not subagents_snapshot:
            return ""

        prompt = "\n## Available Subagents\n\n"
        prompt += "You can delegate subtasks using the spawn_subagent tool:\n\n"

        # Limit to max_subagents to prevent prompt explosion
        subagents_to_show = list(subagents_snapshot.items())[:max_subagents]

        for name, info in subagents_to_show:
            prompt += f"- **{name}**: {info.description}\n"

            # Show first 5 tools (abbreviated to save tokens)
            tools_display = ', '.join(info.tools[:5]) if info.tools else 'No tools'
            if len(info.tools) > 5:
                tools_display += f" (+{len(info.tools) - 5} more)"
            prompt += f"  - Tools: {tools_display}\n"

            prompt += f"  - Usage: `spawn_subagent(name='{name}', directive='...')`\n\n"

        # Indicate if more subagents are available
        if len(subagents_snapshot) > max_subagents:
            remaining = len(subagents_snapshot) - max_subagents
            prompt += f"*({remaining} more subagents available. Use spawn_subagent with exact name.)*\n"

        return prompt

    async def spawn(self, name: str, directive: str, task_type: str = 'normal', **kwargs) -> str | ChildResult:
        """
        Execute a subagent to handle a subtask (core orchestration method).

        This is the main entry point for subagent invocation. It:
        1. Creates an isolated sub_context (via context.build_sub_context)
        2. Executes the subagent with the directive
        3. Merges results back (via context.merge_sub_context)
        4. Returns the subagent's output

        Args:
            name: Name of the subagent to invoke (must be registered)
            directive: Clear task instruction for the subagent
            task_type: Task type: 'normal' or 'background' (default: 'normal')
            **kwargs: Optional overrides (model, tools, disallowedTools, etc.)

        Returns:
            String result from subagent execution

        Raises:
            ValueError: If subagent name not found
            RuntimeError: If no active context available

        Thread Safety:
            - spawn() is concurrent-safe (only reads from _available_subagents)
            - Each spawn creates independent sub_context (contextvars isolation)
            - Merge operations are atomic (guaranteed by ApplicationContext)

        Design Note:
            Audit logs are emitted at start/success/failure for observability.
            Trajectory merging is NOT implemented (deferred per user request).
        """
        from aworld.core.task import Task
        from aworld.runner import Runners
        from aworld.core.agent.base import BaseAgent
        from aworld.core.agent.swarm import Swarm

        start_time = time.time()

        # Step 0: Ensure agent.md files have been scanned (lazy initialization)
        await self._ensure_agent_md_scanned()

        # Step 1: Validate subagent exists
        if name not in self._available_subagents:
            available = ', '.join(self._available_subagents.keys())
            raise ValueError(
                f"Subagent '{name}' not found. Available subagents: {available}"
            )

        subagent_info = self._available_subagents[name]
        fresh_context = subagent_info.context_mode == "fresh"
        answer_only_merge = subagent_info.merge_mode == "answer_only"
        logger.info(
            f"SubagentManager.spawn: Starting subagent '{name}' "
            f"(source={subagent_info.source}) with directive: {directive[:100]}..."
        )

        # Step 2: Get parent context.
        # Prefer an explicit context passed by the caller (e.g. tool execution),
        # then fall back to the current agent's contextvar-based execution context.
        current_context = kwargs.pop('context', None) or kwargs.pop('parent_context', None)
        delegation_spec = kwargs.pop('delegation_spec', None)
        if delegation_spec is not None and not isinstance(
            delegation_spec, DelegationSpec
        ):
            raise TypeError("delegation_spec must be a DelegationSpec or None")
        if delegation_spec is not None and directive != delegation_spec.objective:
            raise ValueError("directive must match DelegationSpec.objective")
        if current_context is None:
            current_context = BaseAgent._get_current_context()
        if current_context is None:
            raise RuntimeError(
                f"SubagentManager.spawn: No active context found. "
                f"spawn() must be called within an agent execution context."
            )

        # Step 3: Create isolated sub_context for subtask
        # build_sub_context creates a deep copy with isolated token tracking
        # Allow overriding sub_task_id (e.g., for background tasks to ensure ID consistency)
        sub_task_id = kwargs.get('sub_task_id', f"{name}_{uuid.uuid4().hex}")
        sub_context = await current_context.build_sub_context(
            sub_task_content=directive,
            sub_task_id=sub_task_id,
            task_type=task_type
        )

        context_pack = None
        child_depth = kwargs.pop(
            "child_depth",
            getattr(current_context, "_delegation_depth", 0) + 1,
        )
        if delegation_spec is not None:
            if child_depth > delegation_spec.max_depth:
                return ChildResult(
                    status=ChildStatus.DEPTH_EXCEEDED,
                    answer=None,
                    evidence=(),
                    artifacts=(),
                    context_delta=(),
                    usage=ChildUsage(input_tokens=0, output_tokens=0, turns=0),
                    reason_code="delegation_depth_exceeded",
                )
        if delegation_spec is not None or fresh_context:
            sub_context.prepare_delegated_child(delegation_depth=child_depth)

            if fresh_context:
                # ApplicationContext builds a useful shared-workspace child but
                # historically copied task memory/kv state as well.  A verifier
                # must not inherit the solver transcript or private scratch state.
                task_state_service = getattr(sub_context, "task_state_service", None)
                get_working_state = getattr(
                    task_state_service, "get_working_state", None
                )
                working_state = (
                    get_working_state() if callable(get_working_state) else None
                )
                if working_state is not None:
                    if hasattr(working_state, "history_messages"):
                        working_state.history_messages = []
                    if hasattr(working_state, "user_profiles"):
                        working_state.user_profiles = []
                    if hasattr(working_state, "kv_store"):
                        working_state.kv_store = {}

            # Manager-owned answer/delegation merges must happen exactly once.
            # Detaching prevents ApplicationContext.update_task_after_run from
            # importing the child's full mutable state before the bounded merge.
            if delegation_spec is not None or answer_only_merge:
                if hasattr(sub_context, "_parent"):
                    sub_context._parent = None
                elif "parent" in getattr(sub_context, "__dict__", {}):
                    sub_context.parent = None

        if delegation_spec is not None:
            available_items = tuple(
                item
                for sidecar in current_context.get_context_observations()
                for item in sidecar.result.items
            )
            context_pack = ContextPack.build(
                spec=delegation_spec,
                available_items=available_items,
                parent_allowed_tools=self.agent.tool_names,
                child_declared_tools=subagent_info.tools,
                parent_task_epoch=current_context.task_epoch,
                child_depth=child_depth,
                child_task_id=sub_task_id,
                child_task_epoch=sub_context.task_epoch,
            )
            sub_context.publish_context_observation(
                ContextObservationSidecar.from_adapter_result(
                    owner="delegation.context_pack",
                    namespace=sub_task_id,
                    source_identity=f"context-pack:{context_pack.pack_hash}",
                    result=AdapterResult(items=context_pack.items, diagnostics=()),
                    model_residency=ModelResidency.NOT_RESIDENT,
                    emission_intent=ContextEmissionIntent.MESSAGE,
                )
            )

        logger.debug(
            f"SubagentManager.spawn: Created sub_context {sub_task_id} "
            f"for subagent '{name}'"
        )

        try:
            # Step 4: Prepare subagent execution based on source type
            if subagent_info.source == 'team_member':
                # TeamSwarm member: Clone agent instance with filtered tools
                original_agent = subagent_info.agent_instance

                # Extract disallowed tools from kwargs
                disallowed_tools = kwargs.get('disallowedTools', [])
                if isinstance(disallowed_tools, str):
                    disallowed_tools = [disallowed_tools]

                # Apply tool filtering (whitelist + blacklist)
                filtered_tools = self._filter_tools(
                    parent_tools=self.agent.tool_names,
                    subagent_tools=subagent_info.tools,
                    disallowed=disallowed_tools
                )
                if context_pack is not None:
                    filtered_tools = [
                        tool for tool in filtered_tools
                        if tool in context_pack.allowed_tools
                    ]

                # Clone agent to avoid state pollution
                cloned_agent = self._clone_agent_instance(original_agent, filtered_tools)

                logger.debug(
                    f"SubagentManager.spawn: Cloned team member '{name}', "
                    f"tools: {len(filtered_tools)}"
                )

            elif subagent_info.source == 'agent_md':
                # Agent.md subagent: Create temporary agent instance
                cloned_agent = self._create_temp_agent(
                    name=name,
                    info=subagent_info,
                    **kwargs
                )
                if context_pack is not None:
                    cloned_agent.tool_names = [
                        tool for tool in cloned_agent.tool_names
                        if tool in context_pack.allowed_tools
                    ]

                logger.debug(
                    f"SubagentManager.spawn: Created temp agent '{name}' from agent.md"
                )
            else:
                raise ValueError(
                    f"Unknown subagent source: {subagent_info.source}"
                )

            if delegation_spec is not None:
                if hasattr(cloned_agent, "conf"):
                    cloned_agent.conf.max_steps = min(
                        getattr(cloned_agent.conf, "max_steps", delegation_spec.max_turns),
                        delegation_spec.max_turns,
                    )
                    cloned_agent.conf.max_input_tokens = min(
                        getattr(
                            cloned_agent.conf,
                            "max_input_tokens",
                            delegation_spec.token_budget,
                        ) or delegation_spec.token_budget,
                        delegation_spec.token_budget,
                    )

            # Step 5: Execute subagent with sub_context
            # Create a Task with the sub_context
            get_parent_task = getattr(current_context, "get_task", None)
            parent_task = get_parent_task() if callable(get_parent_task) else None
            parent_remaining = None
            parent_reserve_applied = 0.0
            if parent_task is not None:
                try:
                    value = parent_task.remaining_seconds()
                except Exception:
                    value = None
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    parent_reserve = getattr(
                        parent_task, "completion_reserve_seconds", None
                    )
                    parent_reserve = (
                        float(parent_reserve)
                        if isinstance(parent_reserve, (int, float))
                        and not isinstance(parent_reserve, bool)
                        else 0.0
                    )
                    resolve_protocol = getattr(
                        self.agent, "_resolve_execution_protocol_policy", None
                    )
                    try:
                        protocol_policy = (
                            resolve_protocol(current_context)
                            if callable(resolve_protocol)
                            else None
                        )
                    except Exception:
                        protocol_policy = None
                    protocol_mode = getattr(
                        getattr(protocol_policy, "mode", None), "value", None
                    )
                    protocol_reserve = getattr(
                        protocol_policy, "finalization_reserve_seconds", None
                    )
                    if (
                        protocol_mode != "off"
                        and isinstance(protocol_reserve, (int, float))
                        and not isinstance(protocol_reserve, bool)
                    ):
                        parent_reserve = max(
                            parent_reserve, float(protocol_reserve)
                        )
                    parent_remaining = max(
                        0.0, float(value) - max(0.0, parent_reserve)
                    )
                    parent_reserve_applied = max(0.0, parent_reserve)
            explicit_remaining = None
            if delegation_spec is not None and delegation_spec.deadline is not None:
                explicit_remaining = max(
                    0.0,
                    delegation_spec.deadline.timestamp() - time.time(),
                )
            bounded_deadlines = [
                value
                for value in (parent_remaining, explicit_remaining)
                if value is not None
            ]
            execution_timeout = min(bounded_deadlines) if bounded_deadlines else None
            if execution_timeout is not None and execution_timeout <= 0:
                raise asyncio.TimeoutError("delegation deadline elapsed")
            task = Task(
                input=directive,
                swarm=Swarm(cloned_agent),
                session_id=sub_context.session_id if hasattr(sub_context, 'session_id') else None,
                context=sub_context,
                parent_task=parent_task,
                timeout=execution_timeout,
                completion_reserve_applied_seconds=(
                    parent_reserve_applied
                    if parent_remaining is not None
                    else 0.0
                ),
            )

            # Execute via Runners
            run = Runners.run_task(task)
            if execution_timeout is not None:
                result_dict = await asyncio.wait_for(
                    run,
                    timeout=execution_timeout,
                )
            else:
                result_dict = await run
            task_response = result_dict.get(task.id)

            # Extract result string
            if task_response and task_response.success:
                result_str = str(task_response.answer) if task_response.answer else ""

                # Step 6: Merge sub_context back to parent context. Fresh
                # collaborators return only their answer plus attributed usage;
                # ordinary legacy collaborators retain full merge behavior.
                if delegation_spec is not None:
                    try:
                        frozen_answer = freeze_json(task_response.answer)
                    except TypeError:
                        frozen_answer = freeze_json(result_str)
                    child_usage = ChildUsage(
                        input_tokens=int(sub_context.token_usage.get("prompt_tokens", 0)),
                        output_tokens=int(sub_context.token_usage.get("completion_tokens", 0)),
                        turns=len(sub_context.get_llm_calls()),
                    )
                    budget_exceeded = (
                        child_usage.input_tokens + child_usage.output_tokens
                        > delegation_spec.token_budget
                        or child_usage.output_tokens
                        > delegation_spec.max_output_tokens
                    )
                    schema_validated = (
                        not budget_exceeded
                        and validate_delegation_output(
                            frozen_answer,
                            delegation_spec.expected_output_schema,
                        )
                    )
                    child_result = ChildResult(
                        status=(
                            ChildStatus.BUDGET_EXCEEDED
                            if budget_exceeded
                            else (
                                ChildStatus.SUCCEEDED
                                if schema_validated
                                else ChildStatus.PARTIAL
                            )
                        ),
                        answer=(frozen_answer if schema_validated else None),
                        evidence=(),
                        artifacts=tuple(
                            value for value in (
                                getattr(task_response, "trajectory_ref", None),
                            ) if value
                        ),
                        context_delta=(),
                        usage=child_usage,
                        reason_code=(
                            "delegation_budget_exceeded"
                            if budget_exceeded
                            else (
                                None
                                if schema_validated
                                else "delegation_output_schema_invalid"
                            )
                        ),
                        schema_validated=schema_validated,
                    )
                    merged_result = merge_child_result(
                        delegation_spec, child_result
                    )
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "context_pack_hash": context_pack.pack_hash,
                            "child_task_id": sub_task_id,
                            "status": child_result.status.value,
                            "input_tokens": child_result.usage.input_tokens,
                            "output_tokens": child_result.usage.output_tokens,
                            "schema_validated": merged_result.schema_validated,
                            "merged_context_delta_ids": [
                                item.id for item in merged_result.context_delta
                            ],
                            "rejected_context_delta_ids": list(
                                merged_result.rejected_delta_ids
                            ),
                        },
                    )
                elif answer_only_merge:
                    child_config = getattr(
                        getattr(cloned_agent, "conf", None), "llm_config", None
                    )
                    child_params = getattr(child_config, "params", None)
                    child_params = (
                        dict(child_params) if isinstance(child_params, dict) else {}
                    )
                    template_kwargs = child_params.get("chat_template_kwargs")
                    template_kwargs = (
                        dict(template_kwargs)
                        if isinstance(template_kwargs, dict)
                        else {}
                    )
                    child_calls = sub_context.get_llm_calls()
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "child_task_id": sub_task_id,
                            "subagent": name,
                            "status": ChildStatus.SUCCEEDED.value,
                            "context_mode": subagent_info.context_mode,
                            "merge_mode": subagent_info.merge_mode,
                            "result_length": len(result_str),
                            "request_profile": {
                                "model": getattr(
                                    child_config, "llm_model_name", None
                                ),
                                "provider": getattr(
                                    child_config, "llm_provider", None
                                ),
                                "reasoning_effort": child_params.get(
                                    "reasoning_effort",
                                    template_kwargs.get("reasoning_effort"),
                                ),
                                "thinking": child_params.get(
                                    "thinking", template_kwargs.get("thinking")
                                ),
                                "max_completion_tokens": child_params.get(
                                    "max_completion_tokens"
                                ),
                            },
                            "usage": {
                                "input_tokens": int(
                                    sub_context.token_usage.get(
                                        "prompt_tokens", 0
                                    )
                                ),
                                "output_tokens": int(
                                    sub_context.token_usage.get(
                                        "completion_tokens", 0
                                    )
                                ),
                                "turns": len(child_calls),
                            },
                            "observed_request_evidence": (
                                _observed_child_request_evidence(child_calls)
                            ),
                        },
                        include_llm_calls=False,
                    )
                else:
                    current_context.merge_sub_context(sub_context)

                elapsed = time.time() - start_time
                logger.info(
                    f"SubagentManager.spawn: Subagent '{name}' succeeded "
                    f"in {elapsed:.2f}s, result length: {len(result_str)}"
                )

                return child_result if delegation_spec is not None else result_str
            else:
                # Execution failed
                error_msg = task_response.msg if task_response else "Unknown error"
                elapsed = time.time() - start_time
                logger.error(
                    f"SubagentManager.spawn: Subagent '{name}' failed "
                    f"after {elapsed:.2f}s: {error_msg}"
                )

                # Still merge context to capture token usage
                if delegation_spec is not None:
                    child_result = ChildResult(
                        status=ChildStatus.FAILED,
                        answer=None,
                        evidence=(),
                        artifacts=(),
                        context_delta=(),
                        usage=ChildUsage(
                            input_tokens=int(sub_context.token_usage.get("prompt_tokens", 0)),
                            output_tokens=int(sub_context.token_usage.get("completion_tokens", 0)),
                            turns=len(sub_context.get_llm_calls()),
                        ),
                        reason_code="child_execution_failed",
                    )
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "context_pack_hash": context_pack.pack_hash,
                            "child_task_id": sub_task_id,
                            "status": child_result.status.value,
                        },
                    )
                    return child_result
                if answer_only_merge:
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "child_task_id": sub_task_id,
                            "subagent": name,
                            "status": ChildStatus.FAILED.value,
                            "context_mode": subagent_info.context_mode,
                            "merge_mode": subagent_info.merge_mode,
                        },
                        include_llm_calls=False,
                    )
                else:
                    current_context.merge_sub_context(sub_context)

                return f"[Error] Subagent '{name}' failed: {error_msg}"

        except asyncio.CancelledError:
            if delegation_spec is not None:
                child_result = ChildResult(
                    status=ChildStatus.CANCELLED,
                    answer=None,
                    evidence=(),
                    artifacts=(),
                    context_delta=(),
                    usage=ChildUsage(
                        input_tokens=int(sub_context.token_usage.get("prompt_tokens", 0)),
                        output_tokens=int(sub_context.token_usage.get("completion_tokens", 0)),
                        turns=len(sub_context.get_llm_calls()),
                    ),
                    reason_code="parent_cancelled",
                )
                current_context.merge_delegation_context(
                    sub_context,
                    delegation_record={
                        "context_pack_hash": context_pack.pack_hash,
                        "child_task_id": sub_task_id,
                        "status": child_result.status.value,
                    },
                )
            elif answer_only_merge:
                current_context.merge_delegation_context(
                    sub_context,
                    delegation_record={
                        "child_task_id": sub_task_id,
                        "subagent": name,
                        "status": ChildStatus.CANCELLED.value,
                        "context_mode": subagent_info.context_mode,
                        "merge_mode": subagent_info.merge_mode,
                    },
                    include_llm_calls=False,
                )
            raise
        except Exception as e:
            elapsed = time.time() - start_time
            logger.exception(
                f"SubagentManager.spawn: Exception during subagent '{name}' "
                f"execution after {elapsed:.2f}s: {e}"
            )

            # Attempt to merge context even on exception
            try:
                if delegation_spec is not None:
                    status = (
                        ChildStatus.DEADLINE_EXCEEDED
                        if isinstance(e, asyncio.TimeoutError)
                        else ChildStatus.FAILED
                    )
                    child_result = ChildResult(
                        status=status,
                        answer=None,
                        evidence=(),
                        artifacts=(),
                        context_delta=(),
                        usage=ChildUsage(
                            input_tokens=int(sub_context.token_usage.get("prompt_tokens", 0)),
                            output_tokens=int(sub_context.token_usage.get("completion_tokens", 0)),
                            turns=len(sub_context.get_llm_calls()),
                        ),
                        reason_code=(
                            "deadline_exceeded"
                            if status is ChildStatus.DEADLINE_EXCEEDED
                            else "child_exception"
                        ),
                    )
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "context_pack_hash": context_pack.pack_hash,
                            "child_task_id": sub_task_id,
                            "status": child_result.status.value,
                        },
                    )
                elif answer_only_merge:
                    status = (
                        ChildStatus.DEADLINE_EXCEEDED
                        if isinstance(e, asyncio.TimeoutError)
                        else ChildStatus.FAILED
                    )
                    current_context.merge_delegation_context(
                        sub_context,
                        delegation_record={
                            "child_task_id": sub_task_id,
                            "subagent": name,
                            "status": status.value,
                            "context_mode": subagent_info.context_mode,
                            "merge_mode": subagent_info.merge_mode,
                        },
                        include_llm_calls=False,
                    )
                else:
                    current_context.merge_sub_context(sub_context)
            except Exception as merge_error:
                logger.error(
                    f"SubagentManager.spawn: Failed to merge sub_context "
                    f"after exception: {merge_error}"
                )

            if delegation_spec is not None:
                return child_result
            return f"[Exception] Subagent '{name}' crashed: {str(e)}"

    def _filter_tools(
        self,
        parent_tools: List[str],
        subagent_tools: List[str],
        disallowed: List[str]
    ) -> List[str]:
        """
        Apply tool access control (whitelist + blacklist).

        Implements Principle of Least Privilege: Subagent can only use a subset
        of parent agent's tools, with explicit deny list.

        Args:
            parent_tools: Tools available to parent agent
            subagent_tools: Tools requested by subagent config
                            Use ['*'] to inherit all parent tools
            disallowed: Explicit deny list (e.g., ['terminal', 'write_file'])

        Returns:
            Filtered list of tool names (intersection after applying blacklist)

        Design Note:
            Whitelist is applied first (intersection), then blacklist (removal).
            This ensures security: even if subagent requests '*', dangerous tools
            can still be blocked via disallowed list.
        """
        # Step 1: Apply whitelist (intersection)
        if subagent_tools == ['*'] or '*' in subagent_tools:
            # Inherit all parent tools
            allowed = list(parent_tools)
        else:
            # Take intersection: only tools that both parent has and subagent requests
            allowed = [tool for tool in subagent_tools if tool in parent_tools]

        # Step 2: Apply blacklist (removal)
        filtered = [tool for tool in allowed if tool not in disallowed]

        # Log filtering for audit trail
        if len(filtered) != len(subagent_tools) and subagent_tools != ['*']:
            removed_count = len(subagent_tools) - len(filtered)
            logger.debug(
                f"SubagentManager._filter_tools: "
                f"Filtered {removed_count} tools. "
                f"Requested: {subagent_tools}, Allowed: {filtered}"
            )

        return filtered

    def _clone_agent_instance(self, original: 'Agent', filtered_tools: List[str]) -> 'Agent':
        """
        Clone agent instance for per-spawn execution (avoid state pollution).

        CRITICAL FIX (from Codex Review):
        BaseAgent stores mutable state (trajectory, tools, state, loop_step, _finished)
        at instance level. Directly reusing the same instance across concurrent spawns
        causes state pollution (race conditions).

        Solution: Clone agent instance for each spawn, with independent runtime state.

        Args:
            original: Original TeamSwarm member agent
            filtered_tools: Tools allowed for this spawn (after access control)

        Returns:
            Cloned agent instance with fresh state

        Design Note:
            - Copies immutable config (name, desc, conf, tool_names)
            - Does NOT copy mutable state (trajectory, loop_step, _finished)
            - Sandbox is shared (stateless component, safe to reuse)
            - Cloning overhead: ~1ms, negligible vs execution time

        Compatible Agent Types:
            - Agent subclasses that accept BaseAgent's standard constructor signature
            - For custom subclasses with additional required arguments, cloning will
              fall back to shallow copy + manual attribute reset
        """
        # Import Agent here to avoid circular dependency
        from aworld.core.agent.base import BaseAgent

        # Strategy 1: Try constructor-based cloning (preferred, creates fresh instance)
        # This works for standard Agent subclasses (LLMAgent, Agent, etc.)
        try:
            # Safe attribute access with getattr for all fields
            agent_names = getattr(original, 'handoffs', None)
            if agent_names is None:
                # Fallback: check for agent_names attribute directly
                agent_names = getattr(original, 'agent_names', [])

            # Create a new instance with the same behavior-defining configuration
            # but fresh mutable execution state.  In particular, collaborator
            # prompts and budget policies are part of capability semantics; losing
            # them turns a verifier/developer clone into an unscoped generic Agent.
            original_conf = getattr(original, "conf", None)

            def clone_config_value(value):
                if isinstance(value, dict):
                    return ConfigDict(
                        {key: clone_config_value(item) for key, item in value.items()}
                    )
                if isinstance(value, list):
                    return [clone_config_value(item) for item in value]
                if isinstance(value, tuple):
                    return tuple(clone_config_value(item) for item in value)
                try:
                    return copy.deepcopy(value)
                except Exception:
                    # Provider/parser extensions may deliberately retain live
                    # objects. Sharing an immutable extension is safer than
                    # falling back to a shallow copy of the whole config tree.
                    return value

            cloned_conf = clone_config_value(original_conf)
            cloned = original.__class__(
                name=original.name(),
                conf=cloned_conf,
                desc=getattr(original, 'desc', lambda: None)() if callable(getattr(original, 'desc', None)) else getattr(original, '_desc', None),
                tool_names=filtered_tools,  # ✅ Apply tool filtering
                agent_names=agent_names.copy() if isinstance(agent_names, list) else [],
                mcp_servers=getattr(original, 'mcp_servers', []).copy() if hasattr(getattr(original, 'mcp_servers', []), 'copy') else [],
                black_tool_actions=getattr(original, 'black_tool_actions', {}).copy() if hasattr(getattr(original, 'black_tool_actions', {}), 'copy') else {},
                feedback_tool_result=getattr(original, 'feedback_tool_result', True),
                wait_tool_result=getattr(original, 'wait_tool_result', False),
                sandbox=getattr(original, 'sandbox', None),  # ✅ Shared authority boundary
                system_prompt=getattr(original, 'system_prompt', None),
                need_reset=getattr(original, 'need_reset', True),
                step_reset=getattr(original, 'step_reset', True),
                use_tools_in_prompt=getattr(original, 'use_tools_in_prompt', False),
                event_driven=getattr(original, 'event_driven', True),
                llm_max_attempts=getattr(original, 'llm_max_attempts', 2),
                llm_retry_delay=getattr(original, 'llm_retry_delay', 10.0),
                generation_budget_policy=getattr(
                    original, '_explicit_generation_budget_policy', None
                ),
                execution_protocol_policy=getattr(
                    original, '_explicit_execution_protocol_policy', None
                ),
                tool_surface_specs=getattr(original, '_tool_surface_specs', ()),
                tool_surface_profile=getattr(original, '_tool_surface_profile', None),
                tool_surface_probes=getattr(original, '_tool_surface_probes', ()),
                max_loop_steps=getattr(original, 'max_loop_steps', 0),
                _generation_budget_explicit_fields=getattr(
                    original, '_generation_budget_explicit_fields', ()
                ),
            )

            logger.debug(
                f"SubagentManager._clone_agent_instance: "
                f"Successfully cloned agent {original.name()} via constructor, "
                f"original_tools={len(getattr(original, 'tool_names', []))}, "
                f"cloned_tools={len(filtered_tools)}"
            )

            return cloned

        except TypeError as e:
            # Strategy 2: Fallback to shallow copy + manual attribute reset
            # This handles custom Agent subclasses with non-standard constructors
            logger.warning(
                f"SubagentManager._clone_agent_instance: "
                f"Constructor-based cloning failed for {original.__class__.__name__}: {e}. "
                f"Falling back to copy-based cloning."
            )

            # Create shallow copy
            cloned = copy.copy(original)

            # Reset mutable state to avoid pollution
            cloned.trajectory = [] if hasattr(cloned, 'trajectory') else None
            cloned.state = None  # Will be initialized on first run
            cloned._finished = True  # Reset finished flag

            # Apply tool filtering (critical for security)
            cloned.tool_names = list(filtered_tools)  # Create new list

            # Rebuild tools from filtered tool_names
            # This ensures the cloned agent only has access to allowed tools
            if hasattr(cloned, '_init_tools') and callable(cloned._init_tools):
                try:
                    cloned._init_tools()
                except Exception as init_error:
                    logger.error(
                        f"SubagentManager._clone_agent_instance: "
                        f"Failed to reinitialize tools after cloning: {init_error}"
                    )

            logger.debug(
                f"SubagentManager._clone_agent_instance: "
                f"Cloned agent {original.name()} via copy fallback, "
                f"original_tools={len(getattr(original, 'tool_names', []))}, "
                f"cloned_tools={len(filtered_tools)}"
            )

            return cloned

    def _create_temp_agent(self, name: str, info: SubagentInfo, **kwargs) -> 'Agent':
        """
        Create temporary agent instance from agent.md config.

        Constructs a new Agent instance dynamically from parsed agent.md configuration.
        Applies tool filtering and model inheritance.

        Args:
            name: Subagent name
            info: SubagentInfo containing configuration
            **kwargs: Runtime overrides (model, tools, disallowedTools, etc.)

        Returns:
            Newly created Agent instance

        Design Note:
            Used for agent.md loaded subagents. TeamSwarm members use _clone_agent_instance.
            Model inheritance: 'inherit' means use parent agent's model.
        """
        from aworld.agents.llm_agent import Agent
        from aworld.config.conf import AgentConfig

        # Extract config from SubagentInfo
        config = info.config or {}

        # Handle model inheritance
        model_name = kwargs.get('model', config.get('model', 'inherit'))

        # Access parent agent's llm config (conf is AgentConfig, a Pydantic model)
        # Try to get from llm_config first, fallback to root level
        parent_llm_config = getattr(self.agent.conf, 'llm_config', None)
        if parent_llm_config and hasattr(parent_llm_config, 'llm_model_name'):
            # llm_config is a ModelConfig object
            parent_model = parent_llm_config.llm_model_name
            parent_provider = parent_llm_config.llm_provider
            parent_api_key = parent_llm_config.llm_api_key
            parent_base_url = parent_llm_config.llm_base_url
        else:
            # Fallback: try root level fields (backward compatibility)
            # AgentConfig has properties for llm_model_name and llm_provider
            parent_model = getattr(self.agent.conf, 'llm_model_name', 'gpt-4o')
            parent_provider = getattr(self.agent.conf, 'llm_provider', 'openai')
            parent_api_key = getattr(parent_llm_config, 'llm_api_key', None) if parent_llm_config else None
            parent_base_url = getattr(parent_llm_config, 'llm_base_url', None) if parent_llm_config else None

        if model_name == 'inherit':
            # Use parent agent's model configuration
            model_name = parent_model
            llm_provider = parent_provider
            llm_api_key = parent_api_key
            llm_base_url = parent_base_url
        else:
            # Use specified model (inherit provider/api_key from parent)
            llm_provider = parent_provider
            llm_api_key = parent_api_key
            llm_base_url = parent_base_url

        # Apply tool filtering
        disallowed_tools = kwargs.get('disallowedTools', [])
        if isinstance(disallowed_tools, str):
            disallowed_tools = [disallowed_tools]

        filtered_tools = self._filter_tools(
            parent_tools=self.agent.tool_names,
            subagent_tools=info.tools,
            disallowed=disallowed_tools
        )

        # Extract system prompt from config
        system_prompt = config.get('system_prompt', info.description)

        # Create agent configuration
        agent_conf = AgentConfig(
            llm_model_name=model_name,
            llm_provider=llm_provider,
            llm_api_key=llm_api_key,
            llm_base_url=llm_base_url
        )

        # Create temporary agent instance
        temp_agent = Agent(
            name=name,
            conf=agent_conf,
            desc=info.description,
            system_prompt=system_prompt,
            tool_names=filtered_tools,
            sandbox=self.agent.sandbox  # Share sandbox (stateless)
        )

        logger.debug(
            f"SubagentManager._create_temp_agent: Created temp agent '{name}', "
            f"model={model_name}, tools={len(filtered_tools)}"
        )

        return temp_agent

    def create_spawn_tool(self):
        """
        Create the spawn_subagent tool that LLM can use to delegate subtasks.

        Returns a SpawnSubagentTool instance. This tool is NOT registered to the
        global ToolFactory, but instead held as an instance variable. The parent
        agent will provide this tool through its custom tool resolution mechanism.

        Returns:
            SpawnSubagentTool: Tool instance for this agent's subagent delegation

        Design Note:
            - Uses pre-defined SpawnSubagentTool class from builtin tools
            - No global registration (avoids multi-agent conflicts)
            - Each agent has its own spawn_subagent tool instance
            - Tool discovery happens via Agent._get_tool_instance()
        """
        from aworld.core.tool.builtin import SpawnSubagentTool

        # Create tool instance with this SubagentManager (singleton per agent)
        if self._spawn_tool_instance is None:
            self._spawn_tool_instance = SpawnSubagentTool(
                subagent_manager=self,
                conf=self.agent.conf
            )
            logger.info(
                f"SubagentManager.create_spawn_tool: SpawnSubagentTool created for "
                f"agent '{self.agent.name()}' (not globally registered)"
            )

        return self._spawn_tool_instance

    def get_spawn_tool(self):
        """
        Get the spawn_subagent tool instance for this agent.

        Returns the cached tool instance, creating it if necessary.

        Returns:
            SpawnSubagentTool or None: Tool instance, or None if not created yet
        """
        if self._spawn_tool_instance is None:
            return self.create_spawn_tool()
        return self._spawn_tool_instance
