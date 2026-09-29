from copy import deepcopy

from aworld.core.execution_protocol import (
    ControllerAction,
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ExecutionProtocolStore,
    ProtocolScope,
)
from aworld.core.context.base import Context


class FakeContext:
    def __init__(self, *, task_id="task", task_epoch=1):
        self.task_id = task_id
        self.task_epoch = task_epoch
        self.context_info = {}
        self.working_state = {}
        self.runtime = {}

    def read_task_runtime_state(self, namespace, key):
        return deepcopy(self.runtime.get((namespace, key)))

    def write_task_runtime_state(self, namespace, key, value):
        self.runtime[(namespace, key)] = deepcopy(value)

    def update_task_runtime_state(self, namespace, key, updater):
        current = deepcopy(self.runtime.get((namespace, key)))
        updated = updater(current)
        self.runtime[(namespace, key)] = deepcopy(updated)
        return deepcopy(updated)

    def put(self, key, value):
        self.working_state[key] = deepcopy(value)

    def get(self, key):
        return deepcopy(self.working_state.get(key))


def test_store_discards_state_from_a_stale_task_scope():
    context = FakeContext(task_id="new", task_epoch=2)
    stale = ExecutionProtocolState.initial(
        ProtocolScope(task_id="old", task_epoch=1, agent_id="agent")
    ).to_dict()
    context.context_info["execution_protocol_state:agent"] = stale

    state = ExecutionProtocolStore(
        context, "agent", ExecutionProtocolPolicy(mode="guide")
    ).load()

    assert state.scope.task_id == "new"
    assert state.scope.task_epoch == 2
    assert state.revision == 0


def test_store_fans_in_transport_copies_through_runtime_state():
    context = FakeContext()
    policy = ExecutionProtocolPolicy(mode="guide", history_limit=4)
    first = ExecutionProtocolStore(context, "agent", policy)
    second = ExecutionProtocolStore(context, "agent", policy)

    first.apply(
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            current_step=1,
            evidence_advanced=True,
        )
    )
    transition = second.apply(
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            current_step=2,
            repetition_count=3,
        )
    )

    assert transition.decision.action is ControllerAction.REQUEST_REPLAN
    assert transition.state.event_count == 2
    assert [item.current_step for item in transition.state.history] == [1, 2]
    assert context.working_state[second.context_key]["event_count"] == 2


def test_store_persists_only_bounded_history():
    context = FakeContext()
    policy = ExecutionProtocolPolicy(mode="observe", history_limit=2)
    store = ExecutionProtocolStore(context, "agent", policy)

    for step in range(5):
        store.apply(
            ExecutionProtocolEvent(
                kind=EventKind.TOOL_OBSERVATION,
                current_step=step,
                evidence_advanced=True,
            )
        )

    assert store.load().event_count == 5
    assert len(context.context_info[store.context_key]["history"]) == 2
    assert len(context.runtime[("agent", "execution_protocol_state")]["history"]) == 2


def test_store_uses_context_runtime_fan_in_across_transport_copy():
    context = Context(task_id="transported-task")
    policy = ExecutionProtocolPolicy(mode="observe")
    original = ExecutionProtocolStore(context, "agent", policy)
    transported = ExecutionProtocolStore(context.deep_copy(), "agent", policy)

    original.apply(
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            current_step=1,
            evidence_advanced=True,
        )
    )
    transported.apply(
        ExecutionProtocolEvent(
            kind=EventKind.TOOL_OBSERVATION,
            current_step=2,
            evidence_advanced=True,
        )
    )

    restored = original.load()
    assert restored.event_count == 2
    assert [item.current_step for item in restored.history] == [1, 2]
