import abc
import asyncio
import json
import logging
import os
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional

from aworld.logs.util import logger
from aworld.sandbox.api.setup import SandboxSetup
from aworld.sandbox.models import SandboxStatus, SandboxEnvType, SandboxInfo
from aworld.sandbox.run.mcp_servers import McpServers
from aworld.sandbox.runtime import SandboxManager
from aworld.core.tool_action_journal import (
    append_tool_action_event,
    tool_action_batch_id,
)
from aworld.core.common import ActionResult
from aworld.sandbox.tool_observation import (
    SandboxToolObservationRuntime,
    canonical_tool_identity,
    classify_tool_effect,
)


class BaseSandbox(SandboxSetup):
    """
    Abstract base class for sandbox implementations.
    Defines minimal attributes and interface; concrete behavior is in Sandbox (implementations/sandbox.py).
    """

    default_sandbox_timeout = 3000

    @property
    def sandbox_id(self) -> str:
        """Unique identifier of the sandbox."""
        return self._sandbox_id

    @property
    def status(self) -> SandboxStatus:
        """Current status of the sandbox."""
        return self._status

    @property
    def timeout(self) -> int:
        """Timeout value for sandbox operations."""
        return self._timeout

    @property
    def metadata(self) -> Dict[str, Any]:
        """Sandbox metadata."""
        return self._metadata

    @property
    def env_type(self) -> SandboxEnvType:
        """Environment type of the sandbox."""
        return self._env_type

    @property
    @abc.abstractmethod
    def mcpservers(self) -> McpServers:
        """MCP servers instance. Implemented by Sandbox."""
        pass

    def __init__(
        self,
        sandbox_id: Optional[str] = None,
        env_type: Optional[int] = None,
        metadata: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ):
        """
        Initialize minimal sandbox state. Only base attributes are set here.
        Subclasses (e.g. Sandbox) set MCP/workspace/agents etc. in their own __init__.
        """
        self._sandbox_id = sandbox_id or str(uuid.uuid4())
        self._status = SandboxStatus.INIT
        self._timeout = timeout or self.default_sandbox_timeout
        self._metadata = metadata or {}
        self._env_type = env_type or SandboxEnvType.LOCAL
        # Tool execution state belongs to the Sandbox control plane, not to an
        # individual terminal/filesystem provider or transport implementation.
        self._tool_observation_runtime = SandboxToolObservationRuntime()
        # create = Sandbox object constructed; bound (in manager) = first time this sandbox_id gets a worker/loop
        logger.info(
            f"[sandbox create] sandbox_id={self._sandbox_id} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        if self._sandbox_id:
            SandboxManager.get_instance().register_sandbox(self._sandbox_id, self)

    @abc.abstractmethod
    def get_info(self) -> SandboxInfo:
        """Returns information about the sandbox."""
        pass

    @abc.abstractmethod
    async def remove(self) -> bool:
        """Remove the sandbox and clean up all resources."""
        pass

    @abc.abstractmethod
    def get_skill_list(self) -> Optional[Any]:
        """Get the skill configurations."""
        pass

    @abc.abstractmethod
    async def cleanup(self) -> bool:
        """Clean up the sandbox resources."""
        pass

    async def __aenter__(self):
        """Async context manager entry. Returns self for use in `async with sandbox:`."""
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> bool:
        """
        Async context manager exit. Ensures cleanup runs when leaving the `async with` block.
        Caller does not need to manually call await sandbox.cleanup() when using async with.
        """
        try:
            await self.cleanup()
        except Exception as e:
            logger.warning(f"Sandbox cleanup during async with exit failed: {e}")
        return False

    async def list_tools(
        self,
        context: Any = None,
        *,
        server_names: List[str] | None = None,
        black_tool_actions: Dict[str, List[str]] | None = None,
    ) -> List[Dict[str, Any]]:
        """Return one Sandbox-owned capability catalog.

        ``server_names`` is a compatibility spelling for an Agent capability
        allowlist. Transport discovery remains private to the Sandbox.
        """
        if hasattr(self, "mcpservers") and self.mcpservers is not None:
            tools = await self.mcpservers.list_tools(context=context)
            if server_names is not None:
                from aworld.mcp_client.utils import filter_mcp_tools_by_servers

                tools = filter_mcp_tools_by_servers(
                    tools,
                    allowed_servers=server_names,
                )
            if black_tool_actions:
                filtered = []
                for tool in tools:
                    identity = tool.get("function", {}).get("name", "")
                    if "__" in identity:
                        server, action = identity.split("__", 1)
                        if action in black_tool_actions.get(server, ()):
                            continue
                    filtered.append(tool)
                tools = filtered
            return tools
        return []

    def _sandbox_tool_observations(self) -> SandboxToolObservationRuntime:
        runtime = getattr(self, "_tool_observation_runtime", None)
        if not isinstance(runtime, SandboxToolObservationRuntime):
            runtime = SandboxToolObservationRuntime()
            self._tool_observation_runtime = runtime
        return runtime

    async def call_tool(
        self,
        action_list: List[Dict[str, Any]] = None,
        task_id: str = None,
        session_id: str = None,
        context: Any = None,
        event_message: Any = None,
        allowed_servers: List[str] | None = None,
        black_tool_actions: Dict[str, List[str]] | None = None,
    ) -> List[Any]:
        """Call a tool on MCP servers. Delegates to mcpservers.call_tool()."""
        actions = action_list or []
        batch_id = tool_action_batch_id(actions)

        def journal(event_type: str, status: str, results=None, metadata=None) -> None:
            if context is None:
                return
            try:
                append_tool_action_event(
                    context=context,
                    event_type=event_type,
                    actions=actions,
                    results=results,
                    status=status,
                    batch_id=batch_id,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning(
                    "Tool action journal append failed open; "
                    f"event_type={event_type} error_type={type(exc).__name__}"
                )

        journal(
            "sandbox_call_started",
            "in_progress",
            metadata={
                "sandbox_id": self.sandbox_id,
                "env_type": str(self.env_type),
                "execution_boundaries": self._tool_execution_boundaries(actions),
            },
        )
        try:
            results = []
            interception = (
                event_message.headers.get("tool_interception")
                if event_message is not None
                and isinstance(getattr(event_message, "headers", None), dict)
                else None
            )
            blocked_call_ids = set(
                interception.get("tool_call_ids") or ()
                if isinstance(interception, dict)
                and interception.get("schema_version")
                == "aworld.tool-interception/v1"
                and interception.get("kind") == "block"
                else ()
            )
            # Preserve one canonical execution boundary for every capability.
            # Existing MCP/local/docker details remain private transports below
            # this method. Sequential execution also makes workspace generation
            # ordering deterministic for a model-emitted action batch.
            for action in actions:
                action_value = (
                    action if isinstance(action, dict) else vars(action)
                )
                server_name = action_value.get("tool_name") or ""
                action_name = action_value.get("action_name") or ""
                tool_call_id = str(action_value.get("tool_call_id") or "")
                if tool_call_id and tool_call_id in blocked_call_ids:
                    effect = classify_tool_effect(action)
                    canonical_tool, canonical_action = canonical_tool_identity(action)
                    error_code = str(
                        interception.get("error_code") or "tool_call_intercepted"
                    )
                    content_type = str(
                        interception.get("content_type") or "tool_call_intercepted"
                    )
                    message = str(
                        interception.get("message") or "Tool call intercepted by Hook"
                    )
                    receipt = {
                        "schema_version": "aworld.sandbox-tool-observation/v1",
                        "canonical_tool": effect.identity,
                        "effect": (
                            "blocked_read_only"
                            if effect.effect == "read_only"
                            else "blocked"
                        ),
                        "cache_hit": False,
                        "changed": False,
                        "workspace_mutated": False,
                        "workspace_generation": self._sandbox_tool_observations().current_generation(context),
                        "operation_hash": effect.operation_hash,
                        "hook_interception": dict(interception),
                    }
                    results.append(
                        ActionResult(
                            success=False,
                            tool_name=canonical_tool,
                            action_name=canonical_action,
                            content=json.dumps(
                                {
                                    "type": content_type,
                                    "message": message,
                                },
                                ensure_ascii=False,
                            ),
                            error=error_code,
                            keep=True,
                            metadata={"sandbox_observation": receipt},
                            parameter=action_value.get("params") or {},
                        )
                    )
                    continue
                if allowed_servers is not None and server_name not in set(
                    allowed_servers
                ):
                    results.append(
                        ActionResult(
                            success=False,
                            tool_name=server_name,
                            action_name=action_name,
                            content="Sandbox capability is not available to this Agent",
                            error="sandbox_capability_denied",
                            keep=True,
                            parameter=action_value.get("params") or {},
                        )
                    )
                    continue
                if action_name in (black_tool_actions or {}).get(
                    server_name, ()
                ):
                    results.append(
                        ActionResult(
                            success=False,
                            tool_name=server_name,
                            action_name=action_name,
                            content="Sandbox capability action is denied",
                            error="sandbox_capability_action_denied",
                            keep=True,
                            parameter=action_value.get("params") or {},
                        )
                    )
                    continue
                cached = (
                    self._sandbox_tool_observations().lookup(
                        action, context=context
                    )
                    if context is not None
                    else None
                )
                if cached is not None:
                    results.append(cached)
                    continue
                if hasattr(self, "mcpservers") and self.mcpservers is not None:
                    observed = await self.mcpservers.call_tool(
                        action_list=[action],
                        task_id=task_id,
                        session_id=session_id,
                        context=context,
                        event_message=event_message,
                    )
                else:
                    observed = []
                for result in observed or []:
                    if context is not None:
                        result = self._sandbox_tool_observations().record(
                            action,
                            result,
                            context=context,
                        )
                    results.append(result)
        except BaseException as exc:
            journal(
                "sandbox_call_failed",
                "failed",
                metadata={"error_type": type(exc).__name__},
            )
            raise
        journal("sandbox_call_completed", "completed", results=results or [])
        return results

    def _tool_execution_boundaries(self, actions: List[Any]) -> list[dict[str, Any]]:
        """Return de-duplicated, redacted execution-boundary receipts."""

        resolver = getattr(self, "get_tool_execution_boundary", None)
        if not callable(resolver):
            return []
        server_names: list[str] = []
        for action in actions:
            server_name = (
                action.get("tool_name")
                if isinstance(action, dict)
                else getattr(action, "tool_name", None)
            )
            if (
                isinstance(server_name, str)
                and server_name
                and server_name not in server_names
            ):
                server_names.append(server_name)
        receipts: list[dict[str, Any]] = []
        for server_name in server_names:
            try:
                receipt = resolver(server_name)
                to_dict = getattr(receipt, "to_dict", None)
                if callable(to_dict):
                    receipts.append(to_dict())
            except Exception as exc:
                logger.warning(
                    "Tool execution-boundary observation failed open; "
                    f"server={server_name} error_type={type(exc).__name__}"
                )
        return receipts

    def __del__(self):
        """Ensure resources are cleaned up when the object is garbage collected."""
        try:
            try:
                asyncio.get_running_loop()
                logging.warning("Cannot clean up sandbox in __del__ when event loop is already running")
                return
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(self.cleanup())
                loop.close()
        except Exception as e:
            logging.debug(f"Failed to cleanup sandbox resources during garbage collection: {e}")
