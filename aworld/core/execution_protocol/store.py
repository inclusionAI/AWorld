"""Task-scoped persistence for execution protocol state.

The runtime registry fans updates in across transported Context copies.  The
normal WorkingState key/value surface supplies checkpoint/resume persistence.
Neither surface writes into the user's task workspace.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from aworld.core.context.execution_state import state_context

from .controller import safe_transition_execution_protocol
from .models import (
    ControllerAction,
    ControllerDecision,
    DecisionReason,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ProtocolScope,
    ProtocolTransition,
)


EXECUTION_PROTOCOL_POLICY_KEY = "execution_protocol_policy"
EXECUTION_PROTOCOL_STATE_KEY = "execution_protocol_state"


class ExecutionProtocolStore:
    """Persist one agent's protocol state under the current task scope."""

    def __init__(self, context: Any, agent_id: str, policy: ExecutionProtocolPolicy):
        if not isinstance(policy, ExecutionProtocolPolicy):
            raise TypeError("policy must be ExecutionProtocolPolicy")
        self._context = state_context(context)
        self._agent_id = agent_id
        self._policy = policy
        self._scope = ProtocolScope(
            task_id=getattr(self._context, "task_id", None),
            task_epoch=getattr(self._context, "task_epoch", None),
            agent_id=agent_id,
        )

    @property
    def context_key(self) -> str:
        return f"{EXECUTION_PROTOCOL_STATE_KEY}:{self._agent_id}"

    def _decode(self, value: Any) -> ExecutionProtocolState | None:
        if not isinstance(value, Mapping):
            return None
        try:
            state = ExecutionProtocolState.from_dict(value)
        except (TypeError, ValueError, KeyError):
            return None
        if state.scope != self._scope:
            return None
        return state.bounded(self._policy.history_limit)

    def _local_value(self) -> Any:
        context_info = getattr(self._context, "context_info", None)
        if isinstance(context_info, Mapping):
            value = context_info.get(self.context_key)
            if value is not None:
                return value
        get = getattr(self._context, "get", None)
        if callable(get):
            try:
                return get(self.context_key)
            except Exception:
                return None
        return None

    def load(self) -> ExecutionProtocolState:
        value = None
        reader = getattr(self._context, "read_task_runtime_state", None)
        if callable(reader):
            try:
                value = reader(self._agent_id, EXECUTION_PROTOCOL_STATE_KEY)
            except Exception:
                value = None
        state = self._decode(value) or self._decode(self._local_value())
        return state or ExecutionProtocolState.initial(self._scope)

    def _project(self, state: ExecutionProtocolState) -> None:
        payload = state.bounded(self._policy.history_limit).to_dict()
        context_info = getattr(self._context, "context_info", None)
        if isinstance(context_info, dict):
            context_info[self.context_key] = deepcopy(payload)
        put = getattr(self._context, "put", None)
        if callable(put):
            try:
                put(self.context_key, deepcopy(payload))
            except Exception:
                pass

    def save(self, state: ExecutionProtocolState) -> ExecutionProtocolState:
        if not isinstance(state, ExecutionProtocolState):
            raise TypeError("state must be ExecutionProtocolState")
        if state.scope != self._scope:
            raise ValueError("execution protocol state belongs to another task scope")
        state = state.bounded(self._policy.history_limit)
        payload = state.to_dict()
        writer = getattr(self._context, "write_task_runtime_state", None)
        if callable(writer):
            try:
                writer(self._agent_id, EXECUTION_PROTOCOL_STATE_KEY, payload)
            except Exception:
                pass
        self._project(state)
        return state

    def apply(self, event: ExecutionProtocolEvent) -> ProtocolTransition:
        """Atomically fan in an event when the Context supports runtime updates."""
        updater = getattr(self._context, "update_task_runtime_state", None)
        captured: list[ProtocolTransition] = []

        if callable(updater):

            def update(current):
                state = self._decode(current) or self.load()
                transition = safe_transition_execution_protocol(
                    state, event, self._policy
                )
                captured.append(transition)
                return transition.state.bounded(self._policy.history_limit).to_dict()

            try:
                payload = updater(self._agent_id, EXECUTION_PROTOCOL_STATE_KEY, update)
                if captured:
                    state = self._decode(payload) or captured[-1].state
                    transition = ProtocolTransition(state, captured[-1].decision)
                    self._project(state)
                    return transition
            except Exception:
                captured.clear()

        state = self.load()
        transition = safe_transition_execution_protocol(state, event, self._policy)
        try:
            saved = self.save(transition.state)
        except Exception:
            # Control-state persistence must not fail the user's execution.
            return ProtocolTransition(
                state=transition.state,
                decision=ControllerDecision(
                    action=(
                        ControllerAction.SUBMIT_CURRENT_RESULT
                        if transition.decision.action
                        is ControllerAction.SUBMIT_CURRENT_RESULT
                        else ControllerAction.CONTINUE
                    ),
                    reason=DecisionReason.CONTROLLER_ERROR,
                ),
            )
        return ProtocolTransition(saved, transition.decision)


__all__ = [
    "EXECUTION_PROTOCOL_POLICY_KEY",
    "EXECUTION_PROTOCOL_STATE_KEY",
    "ExecutionProtocolStore",
]
