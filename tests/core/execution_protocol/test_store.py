from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from threading import Event
from types import SimpleNamespace

from aworld.core.execution_protocol import (
    ControllerAction,
    EventKind,
    ExecutionProtocolEvent,
    ExecutionProtocolPolicy,
    ExecutionProtocolState,
    ExecutionProtocolStore,
    ModelPlanUpdate,
    ProtocolScope,
)
from aworld.core.context.base import Context
from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.state import (
    ApplicationTaskContextState,
    TaskInput,
    TaskOutput,
    TaskWorkingState,
)


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


def _application_context() -> ApplicationContext:
    return ApplicationContext(
        task_state=ApplicationTaskContextState(
            task_input=TaskInput(
                session_id="protocol-session",
                task_id="protocol-checkpoint",
                content="complete the public task",
            ),
            working_state=TaskWorkingState(messages=[], user_profiles=[], kv_store={}),
            task_output=TaskOutput(),
        )
    )


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


def test_store_atomically_projects_concurrent_transport_events_to_checkpoint(
    monkeypatch,
):
    context = _application_context()
    policy = ExecutionProtocolPolicy(mode="observe")
    copies = [context.deep_copy(), context.deep_copy()]
    for copy in copies:
        copy._event_manager = SimpleNamespace(context=context)
    stores = [ExecutionProtocolStore(copy, "agent", policy) for copy in copies]
    first_projection_entered = Event()
    release_first_projection = Event()
    original_project = ExecutionProtocolStore._project

    def delayed_project(store, state, *, context=None):
        if state.event_count == 1 and not first_projection_entered.is_set():
            first_projection_entered.set()
            assert release_first_projection.wait(timeout=5)
        return original_project(store, state, context=context)

    monkeypatch.setattr(ExecutionProtocolStore, "_project", delayed_project)

    def observe(store: ExecutionProtocolStore, step: int):
        return store.apply(
            ExecutionProtocolEvent(
                kind=EventKind.TOOL_OBSERVATION,
                current_step=step,
                evidence_advanced=True,
            )
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(observe, stores[0], 1)
        assert first_projection_entered.wait(timeout=5)
        second = pool.submit(observe, stores[1], 2)
        assert not second.done()
        release_first_projection.set()
        assert first.result(timeout=5).state.event_count == 1
        assert second.result(timeout=5).state.event_count == 2

    restored = ApplicationContext.from_dict(context.to_dict())
    restored_state = ExecutionProtocolStore(restored, "agent", policy).load()
    assert restored_state.event_count == 2
    assert [item.current_step for item in restored_state.history] == [1, 2]


def test_model_plan_update_survives_core_only_checkpoint_round_trip():
    context = _application_context()
    policy = ExecutionProtocolPolicy(mode="guide")
    update = ModelPlanUpdate.from_model_mapping(
        {
            "decision": "continue",
            "horizon": "long",
            "milestone": "validate candidate",
            "next_action": "run a public probe",
            "next_action_tool": "terminal__execute",
            "next_action_arguments": '{"command":"private exact probe"}',
            "verification_plan": "inspect the observed result",
            "completion_assessment": "in_progress",
            "delivery_intent": "validate_candidate",
            "delivery_rationale": "the candidate needs a fresh public check",
            "assumptions": [],
            "retired_approaches": [],
            "evidence_refs": [],
            "selected_candidate_id": "candidate-2",
        }
    )
    ExecutionProtocolStore(context, "agent", policy).apply(
        ExecutionProtocolEvent(
            kind=EventKind.MODEL_PLAN_UPDATE,
            model_plan_update=update,
        )
    )

    checkpoint = json.dumps(context.to_dict())
    assert "next_action_arguments" not in checkpoint
    assert "private exact probe" not in checkpoint

    restored = ApplicationContext.from_dict(context.to_dict())
    restored_state = ExecutionProtocolStore(restored, "agent", policy).load()

    assert restored_state.model_plan_update == update
    assert restored_state.long_horizon_armed is True
