import asyncio
import math
import os
import re
import shlex
import threading
import traceback
import weakref
from datetime import datetime
from pathlib import Path

from aworld.core.context.base import Context
from aworld.logs.util import logger
from aworld.memory.tool_call_compaction import (
    REPLAY_COMPACTED_ARGUMENT_FAILURE,
    compacted_replay_execution_error,
)
from aworld.sandbox.namespaces.base import (
    service_matches_logical_name,
)
from aworld.skills.execution_assets import build_skill_path_aliases


from aworld.utils.common import sync_exec

from aworld.events.util import send_message

from aworld.core.event.base import Message, Constants
from typing_extensions import Optional, List, Dict, Any, Mapping
from typing import TYPE_CHECKING

from aworld.mcp_client.utils import (
    _stdio_server_environment,
    call_api,
    call_function_tool,
    call_mcp_tool_with_exit_stack,
    call_mcp_tool_with_reuse,
    cleanup_server,
    get_server_instance,
    lower_mcp_call_result,
    mcp_tool_desc_transform_v2,
    mcp_tool_desc_transform_v2_reuse,
    mcp_tool_retry_safe,
    run as mcp_run,
)
from aworld.core.common import ActionResult
from aworld.output import Output
from aworld.sandbox.runtime import SandboxManager
from aworld.sandbox.task_budget import (
    DEFAULT_COMPLETION_RESERVE_SECONDS,
    FrameworkTaskBudget,
    ToolLeaseDecision,
    ToolLeaseStage,
    resolve_tool_lease,
    snapshot_task_budget,
)
from aworld.sandbox.declared_write import (
    DECLARED_PUBLIC_WRITE_CONTRACT_KEY,
    build_declared_public_write_contract,
)

# Import env_channel for subscription
# from env_channel import EnvChannelMessage, env_channel_sub

if TYPE_CHECKING:
    from aworld.sandbox.implementations.sandbox import Sandbox


_TERMINAL_EXECUTION_TOOL_NAMES = {"run_code", "execute_command", "mcp_execute_command"}
_TERMINAL_COMMAND_PARAMETER_KEYS = {"code", "command"}
_MCP_TRANSPORT_MIN_TIMEOUT_SECONDS = 120.0
_MCP_TRANSPORT_GRACE_SECONDS = 10.0
_MCP_TRANSPORT_MAX_TIMEOUT_SECONDS = 86410.0
_TERMINAL_DEFAULT_TIMEOUT_SECONDS = 300.0
_TERMINAL_MAX_TIMEOUT_SECONDS = 3600.0
_MAX_RETAINED_CANCELLED_PROVIDER_CALLS = 32
_PROVIDER_FORCE_CANCEL_ROUNDS = 3


class _ProviderCleanupState:
    __slots__ = ("calls", "worker")

    def __init__(self) -> None:
        self.calls: dict[asyncio.Future[Any], int] = {}
        self.worker: asyncio.Task[None] | None = None


_provider_cleanup_states: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, _ProviderCleanupState
] = weakref.WeakKeyDictionary()
_provider_cleanup_states_lock = threading.Lock()


class _TaskToolLeaseTimeout(TimeoutError):
    def __init__(self, *, timed_out: bool) -> None:
        super().__init__("authoritative task Tool lease elapsed")
        self.timed_out = timed_out


def _consume_provider_call(task: asyncio.Future[Any]) -> None:
    if task.cancelled() or not task.done():
        return
    try:
        task.exception()
    except Exception:
        pass


def _force_close_provider_call(task: asyncio.Future[Any]) -> None:
    if task.done():
        _consume_provider_call(task)
        return
    task.cancel()
    get_coro = getattr(task, "get_coro", None)
    coroutine = get_coro() if callable(get_coro) else None
    close = getattr(coroutine, "close", None)
    if callable(close):
        try:
            close()
        except (RuntimeError, ValueError):
            pass


def _provider_cleanup_state(
    loop: asyncio.AbstractEventLoop,
    *,
    create: bool,
) -> _ProviderCleanupState | None:
    with _provider_cleanup_states_lock:
        state = _provider_cleanup_states.get(loop)
        if state is None and create:
            state = _ProviderCleanupState()
            _provider_cleanup_states[loop] = state
            _install_provider_cleanup_close_hook(loop)
        return state


def _drop_provider_cleanup_state(
    loop: asyncio.AbstractEventLoop,
    state: _ProviderCleanupState,
) -> None:
    with _provider_cleanup_states_lock:
        if _provider_cleanup_states.get(loop) is state:
            _provider_cleanup_states.pop(loop, None)


def _provider_cleanup_snapshot(
    loop: asyncio.AbstractEventLoop,
) -> tuple[int, bool]:
    state = _provider_cleanup_state(loop, create=False)
    if state is None:
        return 0, False
    return len(state.calls), bool(state.worker is not None and not state.worker.done())


def _provider_cleanup_state_count() -> int:
    with _provider_cleanup_states_lock:
        return len(_provider_cleanup_states)


async def cleanup_provider_calls_for_loop(
    loop: asyncio.AbstractEventLoop | None = None,
) -> None:
    """Drain retained provider calls on their owning event loop."""

    owner_loop = asyncio.get_running_loop()
    if loop is not None and loop is not owner_loop:
        raise RuntimeError("provider cleanup must run on the owning event loop")
    state = _provider_cleanup_state(owner_loop, create=False)
    if state is None:
        return
    worker = state.worker
    if worker is not None and worker is not asyncio.current_task() and not worker.done():
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    retained = list(state.calls)
    for task in retained:
        _force_close_provider_call(task)
    state.calls.clear()
    await asyncio.sleep(0)
    for task in retained:
        _consume_provider_call(task)
    state.worker = None
    _drop_provider_cleanup_state(owner_loop, state)


def _close_provider_cleanup_before_loop_close(
    loop: asyncio.AbstractEventLoop,
) -> None:
    """Defensive synchronous hook for loops closed outside SandboxManager."""

    if loop.is_closed() or _provider_cleanup_state(loop, create=False) is None:
        return
    if loop.is_running():
        raise RuntimeError("cannot close an event loop while provider cleanup is active")
    loop.run_until_complete(cleanup_provider_calls_for_loop(loop))


def _install_provider_cleanup_close_hook(loop: asyncio.AbstractEventLoop) -> None:
    if getattr(loop, "_aworld_provider_cleanup_close_hook", False):
        return
    original_close = loop.close
    try:
        original_close_ref = weakref.WeakMethod(original_close)
    except TypeError:
        return
    loop_ref = weakref.ref(loop)

    def close_with_provider_cleanup(*args, **kwargs):
        owner_loop = loop_ref()
        original = original_close_ref()
        if owner_loop is None or original is None:
            return None
        _close_provider_cleanup_before_loop_close(owner_loop)
        return original(*args, **kwargs)

    try:
        setattr(loop, "close", close_with_provider_cleanup)
        setattr(loop, "_aworld_provider_cleanup_close_hook", True)
    except (AttributeError, TypeError):
        return


async def _cleanup_cancelled_provider_calls(
    state: _ProviderCleanupState,
) -> None:
    loop = asyncio.get_running_loop()
    worker = asyncio.current_task()
    try:
        while state.calls:
            for task, attempts in list(state.calls.items()):
                if task.done():
                    state.calls.pop(task, None)
                    _consume_provider_call(task)
                    continue
                if attempts >= _PROVIDER_FORCE_CANCEL_ROUNDS:
                    _force_close_provider_call(task)
                    state.calls.pop(task, None)
                    continue
                state.calls[task] = attempts + 1
                task.cancel()
            await asyncio.sleep(0)
    finally:
        # Worker cancellation or loop shutdown must not retain request-bound
        # provider coroutines indefinitely.
        for task in list(state.calls):
            _force_close_provider_call(task)
        state.calls.clear()
        if state.worker is worker:
            state.worker = None
        _drop_provider_cleanup_state(loop, state)


def _retain_cancelled_provider_call(task: asyncio.Future[Any]) -> None:
    get_loop = getattr(task, "get_loop", None)
    owner_loop = get_loop() if callable(get_loop) else None
    if owner_loop is None or owner_loop.is_closed():
        return
    try:
        running_loop = asyncio.get_running_loop()
    except RuntimeError:
        running_loop = None
    if running_loop is not owner_loop:
        try:
            owner_loop.call_soon_threadsafe(_retain_cancelled_provider_call, task)
        except RuntimeError:
            pass
        return
    if task.done():
        _consume_provider_call(task)
        return
    state = _provider_cleanup_state(owner_loop, create=True)
    assert state is not None
    if task in state.calls:
        return
    while len(state.calls) >= _MAX_RETAINED_CANCELLED_PROVIDER_CALLS:
        oldest = next(iter(state.calls))
        state.calls.pop(oldest, None)
        _force_close_provider_call(oldest)
    state.calls[task] = 0

    def finish_cancelled_call(done: asyncio.Future[Any]) -> None:
        state.calls.pop(done, None)
        _consume_provider_call(done)

    task.add_done_callback(finish_cancelled_call)
    if state.worker is None or state.worker.done():
        try:
            state.worker = asyncio.create_task(
                _cleanup_cancelled_provider_calls(state),
                name="aworld-task-lease-cleanup",
            )
        except RuntimeError:
            state.worker = None
            state.calls.pop(task, None)
            _force_close_provider_call(task)
            _drop_provider_cleanup_state(owner_loop, state)


def _safe_context_task(context: Context | None) -> Any:
    getter = getattr(context, "get_task", None) if context is not None else None
    try:
        return getter() if callable(getter) else None
    except Exception:
        return None


def _tool_lease_stage(
    context: Context | None,
    event_message: Message | None,
    task: Any,
) -> ToolLeaseStage:
    """Derive the live Tool stage from Task time, never stale guidance."""

    source_context = context
    parent_task = getattr(task, "parent_task", None)
    if parent_task is not None and getattr(parent_task, "context", None) is not None:
        source_context = parent_task.context
    else:
        root = getattr(context, "root", None) if context is not None else None
        if root is not None:
            source_context = root
    agent_id = getattr(event_message, "sender", None)
    if not isinstance(agent_id, str) or not agent_id.strip():
        agent_info = getattr(source_context, "agent_info", None)
        try:
            agent_id = getattr(agent_info, "current_agent_id", None)
        except (AttributeError, KeyError, TypeError):
            agent_id = None
    deadline_stage = ToolLeaseStage.EXECUTE
    try:
        remaining = task.remaining_seconds() if task is not None else None
        total = getattr(task, "timeout", None) if task is not None else None
    except Exception:
        remaining = total = None
    if (
        isinstance(total, (int, float))
        and not isinstance(total, bool)
        and math.isfinite(float(total))
        and total > 0
        and isinstance(remaining, (int, float))
        and not isinstance(remaining, bool)
        and math.isfinite(float(remaining))
    ):
        bounded_remaining = min(float(total), max(0.0, float(remaining)))
        consumed = min(1.0, max(0.0, 1.0 - bounded_remaining / float(total)))
        if consumed >= 0.80:
            deadline_stage = ToolLeaseStage.DELIVERY_ONLY
        elif consumed >= 0.65:
            deadline_stage = ToolLeaseStage.VALIDATION_DUE
        elif consumed >= 0.40:
            deadline_stage = ToolLeaseStage.CANDIDATE_DUE
    if not isinstance(agent_id, str) or not agent_id.strip() or source_context is None:
        return deadline_stage
    try:
        from aworld.runners.execution_protocol import load_execution_protocol_state

        state = load_execution_protocol_state(source_context, agent_id.strip())
    except Exception:
        return deadline_stage
    phase = getattr(getattr(state, "phase", None), "value", None)
    if phase in {"finalize", "review", "complete"}:
        return ToolLeaseStage.DELIVERY_ONLY
    if phase == "repair" and deadline_stage is ToolLeaseStage.EXECUTE:
        return ToolLeaseStage.CONVERGENCE
    if deadline_stage is not ToolLeaseStage.EXECUTE:
        return deadline_stage
    if getattr(state, "convergence_constraint_active", False):
        return ToolLeaseStage.CONVERGENCE
    return deadline_stage


def _configured_protocol_reserve(
    context: Context | None,
    event_message: Message | None,
) -> float | None:
    """Load the exact policy reserve configured by the current LLMAgent."""

    task = _safe_context_task(context)
    candidates = [context]
    parent_context = getattr(getattr(task, "parent_task", None), "context", None)
    root = getattr(context, "root", None) if context is not None else None
    candidates.extend((parent_context, root))
    sender = getattr(event_message, "sender", None)
    seen_sources: set[int] = set()
    for source in candidates:
        if source is None:
            continue
        if id(source) in seen_sources:
            continue
        seen_sources.add(id(source))
        agent_id = sender
        if not isinstance(agent_id, str) or not agent_id.strip():
            try:
                agent_id = getattr(
                    getattr(source, "agent_info", None), "current_agent_id", None
                )
            except (AttributeError, KeyError, TypeError):
                agent_id = None
        if not isinstance(agent_id, str) or not agent_id.strip():
            continue
        value = None
        reader = getattr(source, "read_task_runtime_state", None)
        if callable(reader):
            try:
                value = reader(agent_id.strip(), "execution_protocol_policy")
            except Exception:
                value = None
        if not isinstance(value, Mapping):
            context_info = getattr(source, "context_info", None)
            if isinstance(context_info, Mapping):
                value = context_info.get(
                    f"execution_protocol_policy:{agent_id.strip()}"
                )
        if not isinstance(value, Mapping):
            continue
        try:
            from aworld.core.execution_protocol import ExecutionProtocolPolicy

            policy = ExecutionProtocolPolicy.from_dict(value)
        except (TypeError, ValueError, KeyError):
            continue
        reserve = float(policy.finalization_reserve_seconds)
        if source is not context:
            # A parent policy is only a fallback for a nested Task that has no
            # policy of its own.  Its reserve may already have been removed
            # when the child deadline was materialized.
            applied = getattr(task, "completion_reserve_applied_seconds", 0.0)
            if (
                isinstance(applied, (int, float))
                and not isinstance(applied, bool)
                and math.isfinite(float(applied))
                and applied > 0
            ):
                reserve = max(0.0, reserve - float(applied))
        return reserve
    return None


def _framework_task_budget(
    context: Context | None,
    event_message: Message | None = None,
) -> FrameworkTaskBudget:
    task = _safe_context_task(context)
    return snapshot_task_budget(
        task,
        stage=_tool_lease_stage(context, event_message, task),
        completion_reserve_seconds=_configured_protocol_reserve(
            context, event_message
        ),
    )


def _bind_env_content_tool_call_id(
    servers: Any,
    tool_key: str,
    parameter: Dict[str, Any],
    tool_call_id: Any,
) -> None:
    """Bind call authority without widening the legacy injection override API."""

    if not isinstance(tool_call_id, str) or not tool_call_id:
        return
    mapping = getattr(servers, "_env_content_param_mapping", None)
    if not isinstance(mapping, Mapping):
        return
    env_content_name = mapping.get(tool_key)
    if not isinstance(env_content_name, str) or not env_content_name:
        return
    env_content = parameter.get(env_content_name)
    if isinstance(env_content, dict):
        # This runs after the overridable compatibility hook. The framework
        # call identifier therefore remains authoritative even for subclasses
        # that still implement the historical four-argument method.
        env_content["tool_call_id"] = tool_call_id


def _transport_lease_decision(
    *,
    requested_timeout: float,
    budget: FrameworkTaskBudget,
    environ: Mapping[str, str] | None = None,
    now_epoch: float | None = None,
) -> ToolLeaseDecision:
    """Use the same task deadline at the provider transport boundary."""

    environment = environ or {}
    policy_override = None
    raw_terminal_override = environment.get("TERMINAL_TIMEOUT")
    try:
        terminal_override = float(raw_terminal_override)
    except (TypeError, ValueError):
        terminal_override = math.nan
    if math.isfinite(terminal_override) and terminal_override > 0:
        policy_override = "terminal_timeout"
    elif environment.get("AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS") is not None:
        policy_override = "terminal_maximum"
    raw_fraction = environment.get("AWORLD_TERMINAL_TASK_LEASE_FRACTION")
    try:
        configured_fraction = float(raw_fraction)
    except (TypeError, ValueError):
        configured_fraction = 0.25
        explicit_fraction = False
    else:
        explicit_fraction = (
            math.isfinite(configured_fraction) and 0 < configured_fraction <= 1
        )
        if not explicit_fraction:
            configured_fraction = 0.25
    raw_floor = environment.get("AWORLD_TERMINAL_TASK_LEASE_MIN_SECONDS")
    try:
        configured_floor = float(raw_floor)
    except (TypeError, ValueError):
        configured_floor = 15.0
    if not math.isfinite(configured_floor) or configured_floor < 0:
        configured_floor = 15.0
    raw_reserve = environment.get("AWORLD_TERMINAL_COMPLETION_RESERVE_SECONDS")
    try:
        configured_reserve = float(raw_reserve)
    except (TypeError, ValueError):
        configured_reserve = DEFAULT_COMPLETION_RESERVE_SECONDS
    if not math.isfinite(configured_reserve) or configured_reserve < 0:
        configured_reserve = DEFAULT_COMPLETION_RESERVE_SECONDS
    return resolve_tool_lease(
        requested_timeout,
        budget=budget,
        maximum_seconds=_MCP_TRANSPORT_MAX_TIMEOUT_SECONDS,
        policy_override=policy_override,
        now_epoch=now_epoch,
        constrained_fraction=configured_fraction,
        constrained_floor_seconds=configured_floor,
        explicit_fraction_policy=explicit_fraction,
        default_completion_reserve_seconds=configured_reserve,
    )


def _finite_positive_timeout(value: Any) -> float:
    """Accept provider numeric strings, never bool/NaN/infinity or no deadline."""
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ValueError("timeout must be a positive finite number")
    try:
        seconds = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("timeout must be a positive finite number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("timeout must be a positive finite number")
    return seconds


def _resolve_mcp_transport_timeout(
    *,
    server_name: str,
    tool_name: str,
    parameter: Dict[str, Any],
    tool_list: List[Dict[str, Any]],
    environ: Dict[str, str] | None = None,
) -> float:
    """Wait beyond the tool's declared execution budget, with finite bounds.

    Schema defaults are annotations, not values that MCP automatically inserts
    into the arguments. Older schema projections also discard ``default``;
    retain the packaged terminal's public default for that canonical route.
    Task/process deadlines continue to bound this transport wait externally.
    """
    terminal_run = server_name == "terminal" and tool_name == "run_code"
    fallback = _TERMINAL_DEFAULT_TIMEOUT_SECONDS if terminal_run else 30.0
    if "timeout" in parameter:
        seconds = _finite_positive_timeout(parameter["timeout"])
        parameter["timeout"] = seconds
    else:
        seconds = fallback
        identifier = f"{server_name}__{tool_name}"
        for tool in tool_list or ():
            function = tool.get("function", {})
            if tool.get("type") != "function" or function.get("name") != identifier:
                continue
            timeout_schema = function.get("parameters", {}).get("properties", {}).get(
                "timeout", {}
            )
            if "default" in timeout_schema:
                try:
                    seconds = _finite_positive_timeout(timeout_schema["default"])
                except ValueError:
                    # A malformed schema must not create an unbounded wait.
                    seconds = fallback
            break
    if terminal_run:
        # Only the resolved child environment is authoritative. Most host
        # variables are deliberately not inherited by MCP subprocesses.
        environment = {} if environ is None else environ
        override = environment.get("TERMINAL_TIMEOUT")
        if override is not None:
            try:
                seconds = _finite_positive_timeout(override)
            except ValueError:
                pass
        maximum = _TERMINAL_MAX_TIMEOUT_SECONDS
        configured_maximum = environment.get("AWORLD_TERMINAL_MAX_TIMEOUT_SECONDS")
        if configured_maximum is not None:
            try:
                maximum = min(maximum, max(1.0, _finite_positive_timeout(configured_maximum)))
            except ValueError:
                pass
        seconds = min(seconds, maximum)
    return min(
        _MCP_TRANSPORT_MAX_TIMEOUT_SECONDS,
        max(_MCP_TRANSPORT_MIN_TIMEOUT_SECONDS, seconds + _MCP_TRANSPORT_GRACE_SECONDS),
    )


def _coalesce_tool_result_content(content_items: List[str]) -> Any:
    """Keep single text outputs as plain strings instead of JSON array strings."""
    if not content_items:
        return ""
    if len(content_items) == 1:
        return content_items[0]
    return content_items


def _summarize_tool_parameters(parameter: Dict[str, Any] | None, *, max_items: int = 4) -> str:
    if not isinstance(parameter, dict) or not parameter:
        return ""
    items = []
    for idx, (key, value) in enumerate(parameter.items()):
        if idx >= max_items:
            items.append(f"+{len(parameter) - max_items} more")
            break
        text = str(value).replace("\n", "\\n")
        if len(text) > 120:
            text = text[:117] + "..."
        items.append(f"{key}={text}")
    return ", ".join(items)


def _build_tool_call_failure_result(
    *,
    server_name: str,
    tool_name: str,
    parameter: Dict[str, Any] | None,
    error: BaseException | None,
) -> ActionResult:
    error_text = "Unknown error"
    if error is not None:
        error_text = f"{type(error).__name__}: {error}"
    content = f"Error calling tool {server_name}__{tool_name}: {error_text}"
    parameter_summary = _summarize_tool_parameters(parameter)
    if parameter_summary:
        content += f". Arguments: {parameter_summary}"
    metadata = {
        key: value
        for key, value in (
            ("failure_category", getattr(error, "failure_category", None)),
            ("failure_code", getattr(error, "failure_code", None)),
        )
        if isinstance(value, str) and value
    }
    return ActionResult(
        success=False,
        tool_name=server_name,
        action_name=tool_name,
        content=content,
        error=error_text,
        keep=True,
        metadata=metadata,
        parameter=parameter,
    )


def _requested_timeout_for_receipt(
    parameter: Dict[str, Any], transport_timeout: float
) -> float:
    raw = parameter.get("timeout")
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        return float(transport_timeout)
    if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
        return float(transport_timeout)
    return value


def _build_task_lease_failure_result(
    *,
    server_name: str,
    tool_name: str,
    parameter: Dict[str, Any],
    transport_timeout: float,
    decision: ToolLeaseDecision,
    budget: FrameworkTaskBudget,
    timed_out: bool,
) -> ActionResult:
    failure_type = (
        "task_tool_lease_timeout" if timed_out else "task_tool_lease_exhausted"
    )
    message = (
        "Tool execution exceeded the authoritative AWorld task lease"
        if timed_out
        else "Task execution deadline is reserved for completion"
    )
    return ActionResult(
        success=False,
        tool_name=server_name,
        action_name=tool_name,
        content=message,
        error=failure_type,
        keep=True,
        parameter=parameter,
        metadata={
            "failure_type": failure_type,
            "requested_timeout_seconds": _requested_timeout_for_receipt(
                parameter, transport_timeout
            ),
            "requested_transport_timeout_seconds": float(transport_timeout),
            "effective_timeout_seconds": decision.effective_seconds,
            "remaining_task_seconds": decision.remaining_task_seconds,
            "timeout_limited_by": decision.limited_by,
            "timeout_policy_override": decision.policy_override,
            "task_budget_stage": budget.stage.value,
        },
    )


async def _await_with_task_lease(awaitable, decision: ToolLeaseDecision):
    if decision.effective_seconds <= 0:
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()
        raise _TaskToolLeaseTimeout(timed_out=False)
    provider_task = asyncio.ensure_future(awaitable)
    try:
        done, _ = await asyncio.wait(
            {provider_task}, timeout=decision.effective_seconds
        )
    except asyncio.CancelledError:
        provider_task.cancel()
        _retain_cancelled_provider_call(provider_task)
        raise
    if provider_task in done:
        return await provider_task
    provider_task.cancel()
    await asyncio.sleep(0)
    # Cancellation is cooperative. Keep a strong reference and consume any
    # eventual exception without letting a provider that suppresses
    # cancellation hold the caller beyond the authoritative lease.
    _retain_cancelled_provider_call(provider_task)
    raise _TaskToolLeaseTimeout(timed_out=True)


class McpServers:

    def __init__(
            self,
            mcp_servers: Optional[List[str]] = None,
            mcp_config: Dict[str, Any] = None,
            sandbox: Optional["Sandbox"] = None,
            black_tool_actions: Dict[str, List[str]] = None,
            skill_configs: Dict[str, Any] = None,
            tool_actions: Optional[List[str]] = None,
    ) -> None:
        self.mcp_servers = mcp_servers
        self.mcp_config = mcp_config
        self.skill_configs = skill_configs or {}
        self.sandbox = sandbox
        # Dictionary to store server instances {server_name: server_instance}
        self.server_instances = {}
        self.server_instances_session = {}
        self.tool_list = None
        self.black_tool_actions = black_tool_actions or {}
        self.map_tool_list = {}
        self.tool_actions = tool_actions or []
        # Mapping from tool_key to env_content parameter name
        # Format: {"server_name__tool_name": "env_content"}
        self._env_content_param_mapping: Dict[str, str] = {}

    def _should_reuse(self) -> bool:
        """Check if server connections should be reused based on sandbox.reuse."""
        return bool(self.sandbox and hasattr(self.sandbox, 'reuse') and self.sandbox.reuse)

    async def list_tools(self, context: Context = None) -> List[Dict[str, Any]]:
        """
        Public entry point for listing tools.

        When reuse=True, ensures MCP operations run on the sandbox-affined
        event loop (SandboxManager) so that connect/cleanup stay in the same task.
        When reuse=False, no manager; run directly on current loop.
        """
        if self.tool_list:
            return self.tool_list
        sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None
        if not sandbox_id or not self._should_reuse():
            return await self._list_tools_impl(context=context)

        manager = SandboxManager.get_instance()
        # When reuse: one worker per server (sandbox_id:server_name) to avoid cleanup hang / no DELETE when multiple servers share the same endpoint
        if not self.mcp_servers or not self.mcp_config:
            return []
        self.tool_list = []
        for server_name in self.mcp_servers:
            tools = await manager.run_on_sandbox(
                sandbox_id,
                self._connect_and_get_tools_one_server,
                server_name,
                context,
                server_name=server_name,
            )
            if tools:
                self.tool_list.extend(tools)
        if self.sandbox and self.tool_list:
            self._process_and_save_env_content_mapping()
        logger.info(
            f"[sandbox list_tools] done connections={len(self.server_instances)} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        return self.tool_list

    async def _list_tools_impl(self, context: Context = None) -> List[Dict[str, Any]]:
        """
        Actual implementation of list_tools that assumes it is running on
        the correct event loop for this sandbox.
        """
        if self.tool_list:
            return self.tool_list
        if not self.mcp_servers or not self.mcp_config:
            return []
        try:
            sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None
            if self._should_reuse():
                self.tool_list = await mcp_tool_desc_transform_v2_reuse(
                    tools=self.mcp_servers,
                    mcp_config=self.mcp_config,
                    context=context,
                    server_instances=self.server_instances,
                    black_tool_actions=self.black_tool_actions,
                    sandbox_id=sandbox_id,
                    tool_actions=self.tool_actions,
                    server_instances_session=self.server_instances_session
                )
            else:
                self.tool_list = await mcp_tool_desc_transform_v2(
                    tools=self.mcp_servers,
                    mcp_config=self.mcp_config,
                    context=context,
                    server_instances=self.server_instances,
                    black_tool_actions=self.black_tool_actions,
                    sandbox_id=sandbox_id,
                    tool_actions=self.tool_actions
                )
            if self.sandbox and self.tool_list:
                self._process_and_save_env_content_mapping()

            logger.info(
                f"[sandbox list_tools] done connections={len(self.server_instances)} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
            )
            return self.tool_list
        except Exception:
            logger.warning(f"Failed to list tools: {traceback.format_exc()}")
            return []

    async def _connect_and_get_tools_one_server(
        self, server_name: str, context: Context = None
    ) -> List[Dict[str, Any]]:
        """
        Run on (sandbox_id, server_name) worker: connect one server and return its tools.

        In reuse mode this MUST prefer an existing cached instance from
        self.server_instances to avoid dropping an older MCP connection
        while its async generators / cancel scopes are still live.
        Otherwise the old instance can be garbage-collected and its
        async cleanup may run on a different task/loop, triggering
        errors like:
        "Attempted to exit cancel scope in a different task than it was entered in".
        """
        sandbox_id = self.sandbox.sandbox_id if self.sandbox else None

        # Reuse existing server instance for this server_name if available.
        # This mirrors the reuse logic in mcp_tool_desc_transform_v2_reuse,
        # but scoped to a single (sandbox_id, server_name) worker.
        server = self.server_instances.get(server_name)

        if not server:
            server, _ = await get_server_instance(
                server_name,
                mcp_config=self.mcp_config,
                context=context,
                sandbox_id=sandbox_id,
            )
            if server is None:
                return []
            self.server_instances[server_name] = server

        return await mcp_run(
            mcp_servers=[server],
            black_tool_actions=self.black_tool_actions,
            tool_actions=self.tool_actions,
        )

    async def check_tool_params(self, context: Context, server_name: str, tool_name: str,
                                parameter: Dict[str, Any]) -> Any:
        """
        Check tool parameters and automatically supplement session_id, task_id and other parameters from context
        
        Args:
            context: Context object containing session_id, task_id and other information
            server_name: Server name
            tool_name: Tool name
            parameter: Parameter dictionary, will be modified
            
        Returns:
            bool: Whether parameter check passed
        """
        # Ensure tool_list is loaded
        if not self.tool_list:
            return False

        if not self.mcp_servers or not self.mcp_config:
            return False

        # Build unique identifier for the tool
        tool_identifier = f"{server_name}__{tool_name}"

        # Find corresponding tool in tool_list
        target_tool = None
        for tool in self.tool_list:
            if tool.get("type") == "function" and tool.get("function", {}).get("name") == tool_identifier:
                target_tool = tool
                break

        if not target_tool:
            logger.warning(f"Tool not found: {tool_identifier}")
            return False

        # Get tool parameter definitions
        function_info = target_tool.get("function", {})
        tool_parameters = function_info.get("parameters", {})
        properties = tool_parameters.get("properties", {})

        if "session_id" in properties and context:
            if hasattr(context, 'session_id') and context.session_id:
                parameter["session_id"] = context.session_id
                logger.info(f"Auto-added session_id: {context.session_id}")

        if "task_id" in properties and context:
            if hasattr(context, 'task_id') and context.task_id:
                parameter["task_id"] = context.task_id
                logger.info(f"Auto-added task_id: {context.task_id}")

        await self._prepare_remote_skill_execution_params(
            context=context,
            server_name=server_name,
            tool_name=tool_name,
            parameter=parameter,
        )

        return True

    async def _prepare_remote_skill_execution_params(
        self,
        *,
        context: Context | None,
        server_name: str,
        tool_name: str,
        parameter: Dict[str, Any],
    ) -> None:
        if (
            not isinstance(parameter, dict)
            or not self.sandbox
            or getattr(self.sandbox, "mode", "local") != "remote"
            or not self._is_terminal_service(server_name)
            or tool_name not in _TERMINAL_EXECUTION_TOOL_NAMES
        ):
            return

        for key in _TERMINAL_COMMAND_PARAMETER_KEYS:
            raw_value = parameter.get(key)
            if not isinstance(raw_value, str) or not raw_value.strip():
                continue
            parameter[key] = await self._rewrite_remote_skill_paths(
                raw_value,
                context=context,
            )

    def _is_terminal_service(self, server_name: str) -> bool:
        return service_matches_logical_name(self.mcp_config, server_name, "terminal")

    async def _rewrite_remote_skill_paths(
        self,
        command_text: str,
        *,
        context: Context | None,
    ) -> str:
        rewritten = command_text
        active_skill_names = await self._get_active_skill_names(context)
        candidate_skill_names = self._resolve_candidate_skill_names(
            command_text=command_text,
            active_skill_names=active_skill_names,
        )
        relative_path_owners = self._build_relative_path_owners(candidate_skill_names)
        relative_directory_owners = self._build_relative_directory_owners(candidate_skill_names)

        for skill_name in candidate_skill_names:
            skill_config = (self.skill_configs or {}).get(skill_name)
            if not isinstance(skill_config, dict):
                continue
            execution_assets = dict(skill_config.get("execution_assets", {}) or {})
            if not execution_assets.get("enabled"):
                continue

            asset_root_value = str(skill_config.get("asset_root", "") or "").strip()
            if not asset_root_value:
                continue
            asset_root = str(Path(asset_root_value).resolve())
            relative_paths = [
                str(path).strip()
                for path in execution_assets.get("relative_paths", []) or []
                if str(path).strip()
            ]
            if not relative_paths:
                continue

            host_file_paths = {
                str((Path(asset_root) / relative_path).resolve()): relative_path
                for relative_path in relative_paths
            }
            path_aliases = self._get_skill_path_aliases(
                skill_name=skill_name,
                skill_config=skill_config,
            )
            relative_path_matches = [
                relative_path
                for relative_path in relative_paths
                if self._should_rewrite_relative_path(
                    command_text=rewritten,
                    relative_path=relative_path,
                    skill_name=skill_name,
                    active_skill_names=active_skill_names,
                    relative_path_owners=relative_path_owners,
                )
            ]
            relative_directory_matches = [
                relative_dir
                for relative_dir in self._build_relative_directories(relative_paths)
                if self._should_rewrite_relative_directory(
                    command_text=rewritten,
                    relative_dir=relative_dir,
                    skill_name=skill_name,
                    active_skill_names=active_skill_names,
                    relative_directory_owners=relative_directory_owners,
                )
            ]
            root_referenced = asset_root in rewritten or any(
                self._skill_root_occurs_in_command(rewritten, alias)
                for alias in path_aliases
            )
            file_referenced = any(host_path in rewritten for host_path in host_file_paths)
            if (
                not root_referenced
                and not file_referenced
                and not relative_path_matches
                and not relative_directory_matches
            ):
                continue

            remote_root = await self.sandbox.ensure_skill_execution_assets_ready(
                skill_name,
                skill_config,
            )

            for host_path, relative_path in sorted(
                host_file_paths.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                remote_path = str(Path(remote_root) / relative_path)
                rewritten = self._rewrite_path_reference(
                    rewritten,
                    host_path,
                    remote_path,
                )

            rewritten = self._rewrite_virtual_skill_root(
                rewritten,
                path_aliases,
                remote_root,
            )
            rewritten = self._rewrite_cd_command_root(
                rewritten,
                asset_root,
                remote_root,
            )
            for relative_dir in relative_directory_matches:
                rewritten = self._rewrite_relative_cd_directory(
                    rewritten,
                    relative_dir,
                    str(Path(remote_root) / relative_dir),
                )
            for relative_path in relative_path_matches:
                remote_path = str(Path(remote_root) / relative_path)
                rewritten = self._rewrite_relative_path_reference(
                    rewritten,
                    relative_path,
                    remote_path,
                )

        return rewritten

    async def _get_active_skill_names(self, context: Context | None) -> list[str]:
        if not context or not hasattr(context, "get_active_skills"):
            return []
        getter = getattr(context, "get_active_skills")
        if not callable(getter):
            return []
        namespace = self._resolve_skill_namespace(context)
        try:
            skills = await getter(namespace=namespace)
        except TypeError:
            try:
                skills = await getter(namespace)
            except TypeError:
                skills = await getter()
        except Exception:
            return []
        if not isinstance(skills, list):
            return []
        return [str(skill).strip() for skill in skills if str(skill).strip()]

    @staticmethod
    def _resolve_skill_namespace(context: Context | None) -> str | None:
        if not context:
            return None
        agent_info = getattr(context, "agent_info", None)
        current_agent_id = getattr(agent_info, "current_agent_id", None)
        if isinstance(current_agent_id, str) and current_agent_id.strip():
            return current_agent_id.strip()
        return None

    def _resolve_candidate_skill_names(
        self,
        *,
        command_text: str,
        active_skill_names: list[str],
    ) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()

        for match in re.finditer(r"/skills/(?P<skill_name>[A-Za-z0-9_.-]+)/", command_text):
            skill_name = match.group("skill_name")
            if skill_name in self.skill_configs and skill_name not in seen:
                ordered.append(skill_name)
                seen.add(skill_name)

        for skill_name in active_skill_names:
            if skill_name in self.skill_configs and skill_name not in seen:
                ordered.append(skill_name)
                seen.add(skill_name)

        for skill_name in self.skill_configs:
            if skill_name not in seen:
                ordered.append(skill_name)
                seen.add(skill_name)

        return ordered

    @classmethod
    def _build_relative_directories(cls, relative_paths: list[str]) -> list[str]:
        directories: list[str] = []
        seen: set[str] = set()
        for relative_path in relative_paths:
            path = Path(str(relative_path).strip())
            parents = list(path.parents)
            parents.reverse()
            for parent in parents:
                candidate = str(parent).strip()
                if not candidate or candidate == "." or candidate in seen:
                    continue
                directories.append(candidate)
                seen.add(candidate)
        return directories

    def _build_relative_path_owners(
        self,
        candidate_skill_names: list[str],
    ) -> dict[str, list[str]]:
        owners: dict[str, list[str]] = {}
        for skill_name in candidate_skill_names:
            skill_config = (self.skill_configs or {}).get(skill_name)
            if not isinstance(skill_config, dict):
                continue
            execution_assets = dict(skill_config.get("execution_assets", {}) or {})
            for relative_path in execution_assets.get("relative_paths", []) or []:
                normalized = str(relative_path).strip()
                if not normalized:
                    continue
                owners.setdefault(normalized, []).append(skill_name)
        return owners

    def _build_relative_directory_owners(
        self,
        candidate_skill_names: list[str],
    ) -> dict[str, list[str]]:
        owners: dict[str, list[str]] = {}
        for skill_name in candidate_skill_names:
            skill_config = (self.skill_configs or {}).get(skill_name)
            if not isinstance(skill_config, dict):
                continue
            execution_assets = dict(skill_config.get("execution_assets", {}) or {})
            relative_paths = [
                str(relative_path).strip()
                for relative_path in execution_assets.get("relative_paths", []) or []
                if str(relative_path).strip()
            ]
            for relative_dir in self._build_relative_directories(relative_paths):
                owners.setdefault(relative_dir, []).append(skill_name)
        return owners

    def _should_rewrite_relative_path(
        self,
        *,
        command_text: str,
        relative_path: str,
        skill_name: str,
        active_skill_names: list[str],
        relative_path_owners: dict[str, list[str]],
    ) -> bool:
        if not self._path_occurs_in_command(command_text, relative_path) and not self._path_occurs_in_command(
            command_text,
            f"./{relative_path}",
        ):
            return False
        if skill_name not in active_skill_names:
            return False

        owners = relative_path_owners.get(relative_path, [])
        if not owners:
            return False
        if len(owners) <= 1:
            return True

        active_owners = [owner for owner in owners if owner in active_skill_names]
        return len(active_owners) == 1 and active_owners[0] == skill_name

    def _should_rewrite_relative_directory(
        self,
        *,
        command_text: str,
        relative_dir: str,
        skill_name: str,
        active_skill_names: list[str],
        relative_directory_owners: dict[str, list[str]],
    ) -> bool:
        if not self._cd_command_targets_path(command_text, relative_dir):
            return False
        if skill_name not in active_skill_names:
            return False

        owners = relative_directory_owners.get(relative_dir, [])
        if not owners:
            return False
        if len(owners) <= 1:
            return True

        active_owners = [owner for owner in owners if owner in active_skill_names]
        return len(active_owners) == 1 and active_owners[0] == skill_name

    @staticmethod
    def _get_skill_path_aliases(
        *,
        skill_name: str,
        skill_config: dict[str, Any],
    ) -> list[str]:
        merged = build_skill_path_aliases(skill_name=skill_name)
        aliases = skill_config.get("path_aliases")
        if isinstance(aliases, list):
            for alias in aliases:
                candidate = str(alias).strip()
                if candidate and candidate not in merged:
                    merged.append(candidate)
        return merged

    @staticmethod
    def _path_occurs_in_command(command_text: str, path_text: str) -> bool:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_./-]){re.escape(path_text)}(?![A-Za-z0-9_./-])"
        )
        return bool(pattern.search(command_text))

    @staticmethod
    def _skill_root_occurs_in_command(command_text: str, path_text: str) -> bool:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_./-]){re.escape(path_text)}(?=(?:/|[^A-Za-z0-9_.-]|$))"
        )
        return bool(pattern.search(command_text))

    @staticmethod
    def _cd_command_targets_path(command_text: str, path_text: str) -> bool:
        for candidate in (path_text, f"./{path_text}"):
            pattern = re.compile(
                rf"(?P<prefix>\bcd\s+)(?P<quote>['\"]?)(?P<path>{re.escape(candidate)})(?P=quote)"
                rf"(?=(?:\s|&&|;|\|\||$))"
            )
            if pattern.search(command_text):
                return True
        return False

    @classmethod
    def _rewrite_relative_path_reference(
        cls,
        command_text: str,
        relative_path: str,
        remote_path: str,
    ) -> str:
        rewritten = command_text
        for candidate in (f"./{relative_path}", relative_path):
            rewritten = cls._rewrite_path_reference(
                rewritten,
                candidate,
                remote_path,
            )
        return rewritten

    @classmethod
    def _rewrite_cd_command_root(cls, command_text: str, asset_root: str, remote_root: str) -> str:
        return cls._rewrite_cd_command_path(
            command_text,
            asset_root,
            remote_root,
        )

    @classmethod
    def _rewrite_relative_cd_directory(
        cls,
        command_text: str,
        relative_dir: str,
        remote_path: str,
    ) -> str:
        rewritten = command_text
        for candidate in (relative_dir, f"./{relative_dir}"):
            rewritten = cls._rewrite_cd_command_path(
                rewritten,
                candidate,
                remote_path,
            )
        return rewritten

    @classmethod
    def _rewrite_cd_command_path(
        cls,
        command_text: str,
        original_path: str,
        replacement_path: str,
    ) -> str:
        pattern = re.compile(
            rf"(?P<prefix>\bcd\s+)(?P<quote>['\"]?)(?P<path>{re.escape(original_path)})(?P=quote)"
            rf"(?=(?:\s|&&|;|\|\||$))"
        )
        return pattern.sub(
            lambda match: (
                f"{match.group('prefix')}"
                f"{match.group('quote')}"
                f"{cls._format_shell_path_replacement(command_text, match.start('path'), replacement_path)}"
                f"{match.group('quote')}"
            ),
            command_text,
        )

    @classmethod
    def _rewrite_virtual_skill_root(
        cls,
        command_text: str,
        path_aliases: list[str],
        remote_root: str,
    ) -> str:
        rewritten = command_text
        for virtual_root in path_aliases:
            rewritten = cls._rewrite_path_reference(
                rewritten,
                virtual_root,
                remote_root,
                allow_suffix=True,
            )
        return rewritten

    @classmethod
    def _rewrite_path_reference(
        cls,
        command_text: str,
        path_text: str,
        replacement_path: str,
        *,
        allow_suffix: bool = False,
    ) -> str:
        suffix_pattern = r"(?=(?:/|[^A-Za-z0-9_.-]|$))" if allow_suffix else r"(?![A-Za-z0-9_./-])"
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_./-])(?P<path>{re.escape(path_text)}){suffix_pattern}"
        )
        return pattern.sub(
            lambda match: cls._format_shell_path_replacement(
                command_text,
                match.start("path"),
                replacement_path,
            ),
            command_text,
        )

    @classmethod
    def _format_shell_path_replacement(
        cls,
        command_text: str,
        start_index: int,
        replacement_path: str,
    ) -> str:
        if cls._shell_quote_context(command_text, start_index):
            return replacement_path
        if cls._looks_like_windows_path(replacement_path):
            if any(char.isspace() for char in replacement_path):
                return f'"{replacement_path}"'
            return replacement_path
        return shlex.quote(replacement_path)

    @staticmethod
    def _shell_quote_context(command_text: str, position: int) -> str | None:
        quote: str | None = None
        escaped = False
        for char in command_text[:position]:
            if quote == "'":
                if char == "'":
                    quote = None
                continue
            if quote == '"':
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    quote = None
                continue
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char in {"'", '"'}:
                quote = char
        return quote

    @staticmethod
    def _looks_like_windows_path(path_text: str) -> bool:
        candidate = str(path_text or "").strip()
        if not candidate:
            return False
        return bool(re.match(r"^(?:[A-Za-z]:[\\/]|\\\\)", candidate))

    def _resolve_mcp_timeout(self, tool_identifier: str, parameter: Dict[str, Any]) -> float:
        """Allow the tool's declared execution time plus MCP transport overhead."""
        tool_timeout = parameter.get("timeout")
        if "timeout" not in parameter:
            for tool in self.tool_list or []:
                function = tool.get("function", {})
                if function.get("name") != tool_identifier:
                    continue
                schema = function.get("parameters", {})
                properties = schema.get("properties", {})
                timeout_schema = properties.get("timeout", {})
                if isinstance(timeout_schema, dict):
                    tool_timeout = timeout_schema.get("default")
                break

        if isinstance(tool_timeout, bool) or not isinstance(tool_timeout, (int, float)):
            return 120.0
        try:
            seconds = float(tool_timeout)
        except OverflowError:
            return 120.0
        if not math.isfinite(seconds) or seconds <= 0:
            return 120.0
        return max(seconds + 10, 120.0)

    async def call_tool(
            self,
            action_list: List[Dict[str, Any]] = None,
            task_id: str = None,
            session_id: str = None,
            context: Context = None,
            event_message: Message = None
    ) -> List[ActionResult]:
        """
        Public entry point for calling tools.

        When reuse=True, runs on the sandbox-affined loop (SandboxManager).
        When reuse=False, runs directly on current loop.
        """
        sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None
        if not sandbox_id or not self._should_reuse():
            return await self._call_tool_impl(
                action_list=action_list,
                task_id=task_id,
                session_id=session_id,
                context=context,
                event_message=event_message,
            )

        manager = SandboxManager.get_instance()
        if action_list:
            # Group by server, run on each server's worker, then merge results back in original action_list order
            from collections import defaultdict
            by_server = defaultdict(list)
            invalid_results = {}
            for i, action in enumerate(action_list):
                ad = action if isinstance(action, dict) else vars(action)
                sn = ad.get("tool_name") or ad.get("server_name")
                if sn:
                    by_server[sn].append((i, action))
                else:
                    invalid_results[i] = _build_tool_call_failure_result(
                        server_name="",
                        tool_name=ad.get("action_name") or "",
                        parameter=ad.get("params", {}),
                        error=ValueError("Missing tool_name"),
                    )
            results_by_server = {}
            for server_name, indexed_actions in by_server.items():
                filtered = [a for _, a in indexed_actions]
                part = await manager.run_on_sandbox(
                    sandbox_id,
                    self._call_tool_impl,
                    filtered,
                    task_id,
                    session_id,
                    context,
                    event_message,
                    server_name=server_name,
                )
                if part:
                    results_by_server[server_name] = part
            indices = {sn: 0 for sn in results_by_server}
            merged = [None] * len(action_list)
            for index, invalid_result in invalid_results.items():
                merged[index] = invalid_result
            for i, action in enumerate(action_list):
                ad = action if isinstance(action, dict) else vars(action)
                sn = ad.get("tool_name") or ad.get("server_name")
                if sn and sn in results_by_server and indices[sn] < len(results_by_server[sn]):
                    merged[i] = results_by_server[sn][indices[sn]]
                    indices[sn] += 1
            for index, result in enumerate(merged):
                if result is None:
                    action = action_list[index]
                    ad = action if isinstance(action, dict) else vars(action)
                    merged[index] = _build_tool_call_failure_result(
                        server_name=ad.get("tool_name") or ad.get("server_name") or "",
                        tool_name=ad.get("action_name") or "",
                        parameter=ad.get("params", {}),
                        error=RuntimeError("MCP execution returned no result"),
                    )
            return merged
        return await manager.run_on_sandbox(
            sandbox_id,
            self._call_tool_impl,
            action_list,
            task_id,
            session_id,
            context,
            event_message,
        )

    async def _call_tool_impl(
            self,
            action_list: List[Dict[str, Any]] = None,
            task_id: str = None,
            session_id: str = None,
            context: Context = None,
            event_message: Message = None
    ) -> List[ActionResult]:
        results = []
        if not action_list:
            return None

        # Lazy initialization: ensure tool_list is loaded before calling tools
        if not self.tool_list:
            await self.list_tools(context=context)

        try:
            for action in action_list:
                if not isinstance(action, dict):
                    action_dict = vars(action)
                else:
                    action_dict = action

                # Get values from dictionary
                server_name = action_dict.get("tool_name")
                tool_name = action_dict.get("action_name")
                parameter = action_dict.get("params", {})
                result_key = f"{server_name}__{tool_name}"

                operation_info = {
                    "server_name": server_name,
                    "tool_name": tool_name,
                    "params": parameter
                }

                if not server_name or not tool_name:
                    missing = "tool_name" if not server_name else "action_name"
                    results.append(
                        _build_tool_call_failure_result(
                            server_name=server_name or "",
                            tool_name=tool_name or "",
                            parameter=parameter,
                            error=ValueError(f"Missing {missing}"),
                        )
                    )
                    continue

                replay_error = compacted_replay_execution_error(
                    parameter,
                    tool_name=f"{server_name}.{tool_name}",
                )
                if replay_error:
                    logger.warning(
                        f"Blocking replay-compacted sandbox MCP tool call: {replay_error}"
                    )
                    results.append(
                        ActionResult(
                            tool_name=server_name,
                            action_name=tool_name,
                            content=f"{REPLAY_COMPACTED_ARGUMENT_FAILURE}: {replay_error}",
                            keep=True,
                            is_done=True,
                            success=False,
                            error=REPLAY_COMPACTED_ARGUMENT_FAILURE,
                            metadata={
                                "failure_type": REPLAY_COMPACTED_ARGUMENT_FAILURE,
                            },
                            parameter=parameter,
                        )
                    )
                    continue

                # Inject env_content parameter if needed (before other processing)
                self._inject_env_content_parameter(
                    result_key,
                    parameter,
                    context,
                    event_message,
                )
                _bind_env_content_tool_call_id(
                    self,
                    result_key,
                    parameter,
                    action_dict.get("tool_call_id"),
                )

                # Check server type
                server_type = None
                server_config = {}
                if self.mcp_config and self.mcp_config.get("mcpServers"):
                    server_config = self.mcp_config.get("mcpServers").get(server_name, {})
                    server_type = server_config.get("type", "")

                task_budget = _framework_task_budget(context, event_message)

                if server_type == "function_tool":
                    try:
                        transport_timeout = _resolve_mcp_transport_timeout(
                            server_name=server_name,
                            tool_name=tool_name,
                            parameter=parameter,
                            tool_list=self.tool_list,
                        )
                        lease_decision = _transport_lease_decision(
                            requested_timeout=transport_timeout,
                            budget=task_budget,
                        )
                        call_result = await _await_with_task_lease(
                            call_function_tool(
                                server_name, tool_name, parameter, self.mcp_config
                            ),
                            lease_decision,
                        )
                        results.append(call_result)

                        self._update_metadata(result_key, call_result, operation_info)
                    except _TaskToolLeaseTimeout as exc:
                        call_result = _build_task_lease_failure_result(
                            server_name=server_name,
                            tool_name=tool_name,
                            parameter=parameter,
                            transport_timeout=transport_timeout,
                            decision=lease_decision,
                            budget=task_budget,
                            timed_out=exc.timed_out,
                        )
                        results.append(call_result)
                        self._update_metadata(result_key, call_result, operation_info)
                    except Exception as e:
                        logger.warning(f"Error calling function_tool tool: {e}")
                        results.append(
                            _build_tool_call_failure_result(
                                server_name=server_name,
                                tool_name=tool_name,
                                parameter=parameter,
                                error=e,
                            )
                        )
                        self._update_metadata(result_key, {"error": str(e)}, operation_info)
                    continue

                # For API type servers, use call_api function directly
                if server_type == "api":
                    try:
                        transport_timeout = _resolve_mcp_transport_timeout(
                            server_name=server_name,
                            tool_name=tool_name,
                            parameter=parameter,
                            tool_list=self.tool_list,
                        )
                        lease_decision = _transport_lease_decision(
                            requested_timeout=transport_timeout,
                            budget=task_budget,
                        )
                        call_result = await _await_with_task_lease(
                            call_api(
                                server_name, tool_name, parameter, self.mcp_config
                            ),
                            lease_decision,
                        )
                        results.append(call_result)

                        self._update_metadata(result_key, call_result, operation_info)
                    except _TaskToolLeaseTimeout as exc:
                        call_result = _build_task_lease_failure_result(
                            server_name=server_name,
                            tool_name=tool_name,
                            parameter=parameter,
                            transport_timeout=transport_timeout,
                            decision=lease_decision,
                            budget=task_budget,
                            timed_out=exc.timed_out,
                        )
                        results.append(call_result)
                        self._update_metadata(result_key, call_result, operation_info)
                    except Exception as e:
                        logger.warning(f"Error calling API tool: {e}")
                        results.append(
                            _build_tool_call_failure_result(
                                server_name=server_name,
                                tool_name=tool_name,
                                parameter=parameter,
                                error=e,
                            )
                        )
                        self._update_metadata(result_key, {"error": str(e)}, operation_info)
                    continue

                # Define progress callback for this tool call
                async def progress_callback(
                        progress: float, total: float | None, message: str | None
                ):
                    if not context:
                        return
                    # for debug vnc
                    message_str = message.replace('\n', '\\n') if message else message
                    logger.info(f"McpServers|progress_callback|{progress}|{total}|{message_str}")
                    try:
                        output = Output()
                        output.data = message
                        tool_output_message = Message(
                            category=Constants.OUTPUT,
                            payload=output,
                            sender=f"{server_name}__{tool_name}",
                            session_id=context.session_id if context else "",
                            headers={"context": context}
                        )
                        sync_exec(send_message, tool_output_message)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        logger.warning(f"Error calling progress callback: {e}")

                # Check and supplement tool parameters
                try:
                    await self.check_tool_params(
                        context=context,
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter
                    )
                    server_environment = (
                        _stdio_server_environment(server_config)
                        if server_type == "stdio" or server_config.get("command")
                        else {}
                    )
                    mcp_timeout = _resolve_mcp_transport_timeout(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        tool_list=self.tool_list,
                        environ=server_environment,
                    )
                    lease_decision = _transport_lease_decision(
                        requested_timeout=mcp_timeout,
                        budget=task_budget,
                        environ=server_environment,
                    )
                except Exception as e:
                    logger.warning(f"Error checking tool parameters: {e}")
                    action_result = _build_tool_call_failure_result(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        error=e,
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, {"error": str(e)}, operation_info)
                    continue

                call_result_raw = None
                action_result = ActionResult(
                    tool_name=server_name,
                    action_name=tool_name,
                    content="",
                    keep=True
                )
                call_mcp_e = None

                sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None

                logger.debug(f"MCP timeout for {result_key}: {mcp_timeout}s")
                retry_safe = mcp_tool_retry_safe(
                    self.mcp_config,
                    server_name,
                    tool_name,
                )

                if self._should_reuse():
                    # Reuse mode: use cached server instances (delegated to utils.py)
                    provider_call = call_mcp_tool_with_reuse(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        server_instances=self.server_instances,
                        mcp_config=self.mcp_config,
                        context=context,
                        sandbox_id=sandbox_id,
                        progress_callback=progress_callback,
                        max_retry=3,
                        timeout=mcp_timeout,
                        retry_safe=retry_safe,
                    )
                else:
                    # Non-reuse mode: use AsyncExitStack (delegated to utils.py)
                    provider_call = call_mcp_tool_with_exit_stack(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        mcp_config=self.mcp_config,
                        context=context,
                        sandbox_id=sandbox_id,
                        progress_callback=progress_callback,
                        max_retry=3,
                        timeout=mcp_timeout,
                        retry_safe=retry_safe,
                    )
                try:
                    call_result_raw = await _await_with_task_lease(
                        provider_call, lease_decision
                    )
                except _TaskToolLeaseTimeout as exc:
                    action_result = _build_task_lease_failure_result(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        transport_timeout=mcp_timeout,
                        decision=lease_decision,
                        budget=task_budget,
                        timed_out=exc.timed_out,
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, action_result, operation_info)
                    continue

                if not call_result_raw:
                    call_mcp_e = Exception("Failed to call tool after all retry attempts")

                logger.debug(f"tool_name:{server_name},action_name:{tool_name} finished.")
                content_types = [
                    getattr(block, "type", type(block).__name__)
                    for block in (getattr(call_result_raw, "content", None) or ())
                ]
                logger.debug(
                    "MCP Tool result received: "
                    f"server={server_name} action={tool_name} "
                    f"content_types={content_types} "
                    f"is_error={bool(getattr(call_result_raw, 'isError', False))}"
                )

                if not call_result_raw:
                    logger.warning(f"Error calling tool: {server_name}__{tool_name}")
                    action_result = _build_tool_call_failure_result(
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        error=call_mcp_e,
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, {"error": call_mcp_e}, operation_info)
                else:
                    action_result = lower_mcp_call_result(
                        call_result_raw,
                        server_name=server_name,
                        tool_name=tool_name,
                        parameter=parameter,
                        trusted_artifact_observation=(
                            tool_name == "observe_artifact"
                            and self._is_terminal_service(server_name)
                        ),
                    )
                    results.append(action_result)
                    self._update_metadata(result_key, action_result, operation_info)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning(
                f"Failed to call_tool: {e}.Extra info: session_id = {session_id}, action_list = {action_list}, traceback = {traceback.format_exc()}")
            while len(results) < len(action_list):
                action = action_list[len(results)]
                action_dict = action if isinstance(action, dict) else vars(action)
                results.append(
                    _build_tool_call_failure_result(
                        server_name=action_dict.get("tool_name") or action_dict.get("server_name") or "",
                        tool_name=action_dict.get("action_name") or "",
                        parameter=action_dict.get("params", {}),
                        error=e,
                    )
                )

        # Log first action's server+action for clarity (action_list from call_tool)
        first_server = first_action = ""
        if action_list and isinstance(action_list[0], dict):
            first_server = action_list[0].get("tool_name") or action_list[0].get("server_name") or ""
            first_action = action_list[0].get("action_name") or ""
        elif action_list and hasattr(action_list[0], "tool_name"):
            first_server = getattr(action_list[0], "tool_name", "") or getattr(action_list[0], "server_name", "")
            first_action = getattr(action_list[0], "action_name", "")
        logger.info(
            f"[sandbox call_tool] server={first_server} action={first_action} pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        return results

    def _process_and_save_env_content_mapping(self):
        """
        Process env_content parameters in tool schemas.
        Removes env_content parameters from tool schemas and saves mapping relationships.
        This ensures LLM doesn't see these parameters, but they will be injected during tool calls.

        This method should be called immediately after mcp_tool_desc_transform_v2 generates tool_list
        to ensure the mapping is saved before the schema is returned.
        """
        if not self.sandbox or not self.tool_list:
            return

        env_content_name = self.sandbox.env_content_name
        if not env_content_name:
            return

        # Clear previous mapping
        self._env_content_param_mapping = {}

        for tool in self.tool_list:
            if tool.get("type") != "function":
                continue

            function = tool.get("function", {})
            tool_key = function.get("name", "")  # Format: "server_name__tool_name"
            if not tool_key:
                continue

            parameters = function.get("parameters", {})
            if not isinstance(parameters, dict):
                continue

            properties = parameters.get("properties", {})
            required = parameters.get("required", [])

            # Check if env_content_name parameter exists in this tool
            if env_content_name in properties:
                # Save mapping relationship (must save before removing)
                self._env_content_param_mapping[tool_key] = env_content_name

                # Remove from schema (so LLM doesn't see it)
                del properties[env_content_name]

                # Remove from required list if present
                if isinstance(required, list) and env_content_name in required:
                    required.remove(env_content_name)

                logger.debug(
                    f"Removed env_content parameter '{env_content_name}' from tool '{tool_key}' schema and saved mapping")

    def _inject_env_content_parameter(
        self,
        tool_key: str,
        parameter: Dict[str, Any],
        context: Context = None,
        event_message: Message = None,
        *,
        tool_call_id: str | None = None,
    ):
        """
        Inject env_content parameter into tool call parameters.

        This method:
        1. Checks if the tool needs env_content injection (based on mapping)
        2. Builds env_content value from sandbox.env_content (user-defined)
        3. Dynamically adds task_id and session_id from context
        4. Merges ordinary values while keeping framework authority fields final

        Args:
            tool_key: Tool identifier in format "server_name__tool_name"
            parameter: Tool call parameters dictionary (will be modified)
            context: Context object containing task_id and session_id
            event_message: Optional message object that may contain additional context
        """
        # Check if this tool needs env_content injection
        if tool_key not in self._env_content_param_mapping:
            return

        if not self.sandbox:
            return

        env_content_name = self._env_content_param_mapping[tool_key]

        # Build env_content value
        env_content_value = {}
        framework_authority_keys = {
            DECLARED_PUBLIC_WRITE_CONTRACT_KEY,
            "task_budget",
            "task_id",
            "session_id",
            "task_epoch",
            "session_epoch",
            "branch_id",
            "checkpoint_revision",
            "agent_id",
            "prompt_namespace",
            "sandbox_id",
            "tool_call_id",
        }

        # 1. Copy user-defined context from sandbox.env_content. Framework
        # authority fields are never inherited from caller-owned dictionaries.
        if hasattr(self.sandbox, 'env_content') and isinstance(
            self.sandbox.env_content, dict
        ):
            env_content_value.update(
                {
                    key: value
                    for key, value in self.sandbox.env_content.items()
                    if key not in framework_authority_keys
                }
            )

        # 2. Dynamically add task_id and session_id from context
        if context:
            if hasattr(context, 'task_id') and context.task_id:
                env_content_value["task_id"] = context.task_id
            if hasattr(context, 'session_id') and context.session_id:
                env_content_value["session_id"] = context.session_id
            if hasattr(context, 'task_epoch') and context.task_epoch is not None:
                env_content_value["task_epoch"] = context.task_epoch
            lifecycle = getattr(context, "context_lifecycle_state", None)
            session_epoch = (
                lifecycle.get("session_epoch")
                if isinstance(lifecycle, Mapping)
                else getattr(lifecycle, "session_epoch", None)
            )
            branch_id = (
                lifecycle.get("branch_id")
                if isinstance(lifecycle, Mapping)
                else getattr(lifecycle, "branch_id", None)
            )
            checkpoint_revision = (
                lifecycle.get("checkpoint_revision")
                if isinstance(lifecycle, Mapping)
                else getattr(lifecycle, "checkpoint_revision", 0)
            )
            if (
                isinstance(session_epoch, int)
                and not isinstance(session_epoch, bool)
                and session_epoch >= 0
            ):
                env_content_value["session_epoch"] = session_epoch
            if isinstance(branch_id, str) and branch_id.strip():
                env_content_value["branch_id"] = branch_id.strip()
            if (
                isinstance(checkpoint_revision, int)
                and not isinstance(checkpoint_revision, bool)
                and checkpoint_revision >= 0
            ):
                env_content_value["checkpoint_revision"] = checkpoint_revision
        sandbox_id = getattr(self.sandbox, "sandbox_id", None)
        if isinstance(sandbox_id, str) and sandbox_id.strip():
            env_content_value["sandbox_id"] = sandbox_id.strip()

        # 3. Dynamically add additional context from event_message
        agent_id = None
        if event_message:
            if hasattr(event_message, 'sender') and event_message.sender:
                agent_id = event_message.sender
        if not agent_id and context is not None:
            agent_info = getattr(context, "agent_info", None)
            try:
                agent_id = (
                    agent_info.get("current_agent_id")
                    if isinstance(agent_info, Mapping)
                    else getattr(agent_info, "current_agent_id", None)
                )
            except (AttributeError, KeyError, TypeError):
                agent_id = None
            if not agent_id:
                try:
                    agent_id = getattr(context, "agent_id", None)
                except (AttributeError, KeyError, TypeError):
                    agent_id = None
        if isinstance(agent_id, str) and agent_id.strip():
            env_content_value["agent_id"] = agent_id.strip()
            env_content_value["prompt_namespace"] = agent_id.strip()
        if isinstance(tool_call_id, str) and tool_call_id:
            env_content_value["tool_call_id"] = tool_call_id
        if (
            tool_key.rsplit("__", 1)[-1] == "run_code"
            and parameter.get("declared_write_paths") is not None
            and context is not None
        ):
            declared_contract = build_declared_public_write_contract(context)
            if declared_contract is not None:
                env_content_value[DECLARED_PUBLIC_WRITE_CONTRACT_KEY] = (
                    declared_contract
                )

        # 4. Merge into parameter
        # If a stale caller supplied the hidden parameter, merge ordinary
        # values while keeping framework task/session/budget state authoritative.
        if env_content_name not in parameter:
            parameter[env_content_name] = env_content_value
        else:
            user_value = parameter[env_content_name]
            if isinstance(user_value, dict):
                parameter[env_content_name] = {
                    **{
                        key: value
                        for key, value in user_value.items()
                        if key not in framework_authority_keys
                    },
                    **env_content_value,
                }
            else:
                parameter[env_content_name] = env_content_value

        # The sentinel is always present, including when no Task exists. This
        # makes absence authoritative instead of allowing a stale model,
        # Sandbox dictionary or process environment to fabricate a deadline.
        parameter[env_content_name]["task_budget"] = _framework_task_budget(
            context, event_message
        ).to_hidden_dict()

        logger.debug(f"Injected env_content parameter '{env_content_name}' for tool '{tool_key}'")

    def _update_metadata(self, result_key: str, result: Any, operation_info: Dict[str, Any]):
        """
        Update sandbox metadata with a single tool call result

        Args:
            result_key: The key name in metadata
            result: Tool call result
            operation_info: Operation information
        """
        if not self.sandbox or not hasattr(self.sandbox, '_metadata'):
            return

        try:
            metadata = self.sandbox._metadata.get("mcp_metadata", {})
            tmp_data = {
                "input": operation_info,
                "output": result
            }
            if not metadata:
                metadata["mcp_metadata"] = {}
                metadata["mcp_metadata"][result_key] = [tmp_data]
                self.sandbox._metadata["mcp_metadata"] = metadata
                return

            _metadata = metadata.get(result_key, [])
            if not _metadata:
                _metadata[result_key] = [_metadata]
            else:
                _metadata[result_key].append(tmp_data)
            metadata[result_key] = _metadata
            self.sandbox._metadata["mcp_metadata"] = metadata
            return

        except Exception as e:
            logger.debug(f"Failed to update sandbox metadata: {e}")

    # def _init_tool_result_subscription(self, env_session_id: Optional[str] = None, context: Context = None, result_key: Optional[str] = None):
    #     """Initialize subscription for tool results.
    #
    #     Args:
    #         env_session_id: Environment session ID for WebSocket connection
    #         context: Context object containing task_id and session_id
    #         result_key: Tool identifier in format "server_name__tool_name"
    #     """
    #     # Only initialize once
    #     if hasattr(self, '_tool_result_handler'):
    #         return
    #     if not env_session_id:
    #         logger.warning("env_session_id is not provided")
    #         return
    #
    #     try:
    #         token = os.getenv("ENV_CHANNEL_TOKEN", "")
    #         _ws_headers = {"Authorization": f"Bearer {token}"}
    #
    #         server_url = f"ws://mcp.aworldagents.com/vpc-pre/stream/{env_session_id}/channel"
    #
    #         @env_channel_sub(
    #             server_url=server_url,
    #             topics=["env-tool-message-topic"],
    #             auto_connect=True,
    #             auto_reconnect=True,
    #             reconnect_interval=10.0,
    #             headers=_ws_headers,
    #             auto_start=True
    #         )
    #         async def handle_tool_result(msg: EnvChannelMessage):
    #             parent_task_id = None
    #             if context and hasattr(context, 'task_id') and context.task_id:
    #                 parent_task_id = context.task_id
    #
    #             bg_msg = BackgroundTaskMessage(
    #                     background_task_id=f"bg_{uuid.uuid4().hex}",
    #                     parent_task_id=parent_task_id,
    #                     payload=msg.message,
    #                     sender=result_key,
    #                     topic=TopicType.BACKGROUND_TOOL_COMPLETE,
    #                     headers={"context": context}
    #                 )
    #             await send_message(bg_msg)
    #             logger.debug(f"tool:sender: {result_key},result-logging: {msg.message}")
    #
    #         # Store the handler to keep reference
    #         self._tool_result_handler = handle_tool_result
    #         logger.info(
    #             f"Initialized tool result subscription for env_session_id: {env_session_id}, server_url: {server_url}")
    #     except Exception as e:
    #         logger.warning(f"Failed to initialize tool result subscription: {e}")

    # Add cleanup method, called when Sandbox is destroyed
    async def cleanup(self):
        """
        Public cleanup entry point.

        Delegates actual server cleanup to the sandbox-affined event loop
        so that any async generators / cancel scopes are exited from the
        same task/loop that created them.
        """
        if not self._should_reuse():
            return

        sandbox_id = self.sandbox.sandbox_id if self.sandbox is not None else None
        if not sandbox_id:
            return await self._cleanup_impl()

        manager = SandboxManager.get_instance()
        return await self._cleanup_impl_with_server_affinity(sandbox_id, manager)

    async def _cleanup_one_server(self, server_name: str) -> None:
        """Clean up one server; run on (sandbox_id, server_name) worker."""
        server = self.server_instances.get(server_name)
        if server is None:
            return
        try:
            await cleanup_server(server)
        except Exception as e:
            logger.warning(f"Failed to cleanup server {server_name}: {e}")
        finally:
            self.server_instances.pop(server_name, None)
            self.server_instances_session.pop(server_name, None)

    async def _cleanup_impl_with_server_affinity(
        self, sandbox_id: str, manager: SandboxManager
    ) -> None:
        """Cleanup with one worker per server."""
        if not self._should_reuse():
            return
        logger.info(
            f"[sandbox cleanup] start pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        for server_name in list(self.server_instances.keys()):
            try:
                await manager.run_on_sandbox(
                    sandbox_id,
                    self._cleanup_one_server,
                    server_name,
                    server_name=server_name,
                )
            except Exception as e:
                logger.warning(f"Failed to cleanup server {server_name}: {e}")

    async def _cleanup_impl(self):
        """Actual cleanup logic; assumes running on the correct loop."""
        if not self._should_reuse():
            return

        logger.info(
            f"[sandbox cleanup] start pid={os.getpid()} tid={threading.get_ident()} at={datetime.now().isoformat(timespec='milliseconds')}"
        )
        for server_name, server in list(self.server_instances.items()):
            try:
                await cleanup_server(server)
                del self.server_instances[server_name]
                if server_name in self.server_instances_session:
                    del self.server_instances_session[server_name]
            except Exception as e:
                logger.warning(f"Failed to cleanup server {server_name}: {e}")
