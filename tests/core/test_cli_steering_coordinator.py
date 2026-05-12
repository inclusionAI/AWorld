import threading

from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.steering.coordinator import SteeringCoordinator


class DummyRuntime(BaseCliRuntime):
    def __init__(self):
        super().__init__(agent_name="Aworld")
        self.plugin_dirs = []

    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"


def test_enqueue_and_drain_fifo():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")

    coordinator.enqueue_text("sess-1", "Focus on failing tests first.")
    coordinator.enqueue_text("sess-1", "Avoid refactoring unrelated files.")

    drained = coordinator.drain_for_checkpoint("sess-1")

    assert [item.text for item in drained] == [
        "Focus on failing tests first.",
        "Avoid refactoring unrelated files.",
    ]
    assert coordinator.snapshot("sess-1")["pending_count"] == 0


def test_interrupt_flag_and_terminal_fallback_prompt_reset():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")
    coordinator.enqueue_text("sess-1", "Re-run the failing test before editing code.")
    coordinator.request_interrupt("sess-1")

    prompt = coordinator.consume_terminal_fallback_prompt("sess-1")

    assert "Re-run the failing test before editing code." in prompt
    assert coordinator.snapshot("sess-1")["interrupt_requested"] is False
    assert coordinator.snapshot("sess-1")["pending_count"] == 0


def test_interrupt_and_text_are_both_rendered_in_fallback_prompt():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")
    coordinator.enqueue_text("sess-1", "Re-run the failing test before editing code.")
    coordinator.request_interrupt("sess-1")

    prompt = coordinator.consume_terminal_fallback_prompt("sess-1")

    assert "Re-run the failing test before editing code." in prompt
    assert "interrupt" in prompt.lower()
    assert coordinator.snapshot("sess-1")["interrupt_requested"] is False


def test_interrupt_only_consumption_returns_prompt_and_clears_flag():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")
    coordinator.request_interrupt("sess-1")

    prompt = coordinator.consume_terminal_fallback_prompt("sess-1")

    assert prompt is not None
    assert "interrupt" in prompt.lower()
    assert coordinator.snapshot("sess-1")["interrupt_requested"] is False
    assert coordinator.snapshot("sess-1")["pending_count"] == 0


def test_concurrent_interrupt_after_drain_is_not_lost():
    coordinator = SteeringCoordinator()
    coordinator.begin_task(session_id="sess-1", task_id="task-1")
    coordinator.enqueue_text("sess-1", "Re-run the failing test before editing code.")

    class CoordinatedLock:
        def __init__(self):
            self._lock = threading.RLock()
            self._main_thread_id = threading.get_ident()
            self._first_section_finished = threading.Event()
            self._interrupt_done = threading.Event()
            self._local = threading.local()
            self._main_top_level_entries = 0

        def __enter__(self):
            depth = getattr(self._local, "depth", 0)
            if (
                threading.get_ident() == self._main_thread_id
                and depth == 0
                and self._first_section_finished.is_set()
                and not self._interrupt_done.is_set()
            ):
                assert self._interrupt_done.wait(timeout=1)

            self._lock.acquire()
            self._local.depth = depth + 1
            if threading.get_ident() == self._main_thread_id and depth == 0:
                self._main_top_level_entries += 1
            return self

        def __exit__(self, exc_type, exc, tb):
            depth = getattr(self._local, "depth", 1) - 1
            self._local.depth = depth
            main_thread = threading.get_ident() == self._main_thread_id
            top_level = depth == 0
            if main_thread and top_level and self._main_top_level_entries == 1:
                self._first_section_finished.set()
            self._lock.release()
            return False

    coordinated_lock = CoordinatedLock()
    coordinator._lock = coordinated_lock

    interrupt_thread = threading.Thread(
        target=lambda: (
            coordinated_lock._first_section_finished.wait(timeout=1),
            coordinator.request_interrupt("sess-1"),
            coordinated_lock._interrupt_done.set(),
        )
    )
    interrupt_thread.start()

    prompt = coordinator.consume_terminal_fallback_prompt("sess-1")
    interrupt_thread.join(timeout=1)

    assert "Re-run the failing test before editing code." in prompt
    assert coordinator.snapshot("sess-1")["interrupt_requested"] is True


def test_runtime_steering_accessors_delegate_to_coordinator():
    runtime = DummyRuntime()

    assert runtime.steering_snapshot(None) == {}
    assert runtime.request_session_interrupt(None) is False

    runtime._steering.begin_task("sess-1", "task-1")
    runtime._steering.enqueue_text("sess-1", "Focus on failing tests first.")

    assert runtime.steering_snapshot("sess-1") == {
        "active": True,
        "task_id": "task-1",
        "pending_count": 1,
        "interrupt_requested": False,
        "last_steer_excerpt": "Focus on failing tests first.",
    }
    assert runtime.request_session_interrupt("sess-1") is True
    assert runtime.steering_snapshot("sess-1")["interrupt_requested"] is True
