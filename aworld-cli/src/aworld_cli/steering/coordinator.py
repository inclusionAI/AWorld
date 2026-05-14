from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock


def _utcnow() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


@dataclass
class SteeringInput:
    sequence: int
    text: str
    created_at: str


@dataclass
class SteeringSessionState:
    active_task_id: str | None = None
    steerable: bool = False
    interrupt_requested: bool = False
    next_sequence: int = 1
    pending_inputs: list[SteeringInput] = field(default_factory=list)


class SteeringCoordinator:
    def __init__(self) -> None:
        self._lock = RLock()
        self._sessions: dict[str, SteeringSessionState] = {}

    def begin_task(self, session_id: str, task_id: str, *, steerable: bool = True) -> None:
        with self._lock:
            state = self._sessions.setdefault(session_id, SteeringSessionState())
            state.active_task_id = task_id
            state.steerable = steerable
            state.interrupt_requested = False

    def enqueue_text(self, session_id: str, text: str) -> SteeringInput:
        normalized = str(text).strip()
        if not normalized:
            raise ValueError("steering text must not be empty")

        with self._lock:
            state = self._sessions.setdefault(session_id, SteeringSessionState())
            item = SteeringInput(
                sequence=state.next_sequence,
                text=normalized,
                created_at=_utcnow(),
            )
            state.next_sequence += 1
            state.pending_inputs.append(item)
            return item

    def request_interrupt(self, session_id: str) -> bool:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.steerable or not state.active_task_id:
                return False
            state.interrupt_requested = True
            return True

    def end_task(self, session_id: str, *, clear_pending: bool = False) -> None:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return

            state.active_task_id = None
            state.steerable = False
            state.interrupt_requested = False
            if clear_pending:
                state.pending_inputs.clear()

    def snapshot(self, session_id: str) -> dict[str, object]:
        with self._lock:
            state = self._sessions.get(session_id) or SteeringSessionState()
            excerpt = state.pending_inputs[-1].text if state.pending_inputs else None
            return {
                "active": bool(state.steerable and state.active_task_id),
                "task_id": state.active_task_id,
                "pending_count": len(state.pending_inputs),
                "interrupt_requested": state.interrupt_requested,
                "last_steer_excerpt": excerpt,
            }

    def drain_for_checkpoint(self, session_id: str) -> list[SteeringInput]:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None or not state.pending_inputs:
                return []

            drained = list(state.pending_inputs)
            state.pending_inputs.clear()
            return drained

    def consume_terminal_fallback(self, session_id: str) -> tuple[str | None, list[SteeringInput], bool]:
        with self._lock:
            state = self._sessions.get(session_id)
            if state is None:
                return None, [], False

            drained = list(state.pending_inputs)
            state.pending_inputs.clear()
            interrupt_requested = state.interrupt_requested
            state.interrupt_requested = False

        if not drained and not interrupt_requested:
            return None, [], False

        lines = [
            "Continue the current task with this additional operator steering:",
            "",
        ]
        if interrupt_requested:
            lines.append(
                "Interrupt requested by operator. Pause at the next safe checkpoint before continuing."
            )
            if drained:
                lines.append("")

        for index, item in enumerate(drained, start=1):
            lines.append(f"{index}. {item.text}")

        return "\n".join(lines).strip(), drained, interrupt_requested

    def consume_terminal_fallback_prompt(self, session_id: str) -> str | None:
        prompt, _drained, _interrupt_requested = self.consume_terminal_fallback(session_id)
        return prompt
