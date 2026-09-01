"""Pure Context lifecycle state machine and retention decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import re
from typing import Iterable

from .models import CacheBreakReason, ContextItem, Lifetime


class LifecycleAction(str, Enum):
    NEW_TASK = "new_task"
    NEXT_TURN = "next_turn"
    CHECKPOINT = "checkpoint"
    REWIND = "rewind"
    RESUME = "resume"
    BACKGROUND = "background"


@dataclass(frozen=True, slots=True)
class ContextLifecycleState:
    session_id: str
    session_epoch: int = 0
    task_epoch: int = 0
    turn_epoch: int = 0
    branch_id: str = "main"
    checkpoint_revision: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be a non-empty string")
        for name in (
            "session_epoch", "task_epoch", "turn_epoch", "checkpoint_revision"
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", self.branch_id):
            raise ValueError("branch_id must be a stable identifier")


@dataclass(frozen=True, slots=True)
class LifecycleItemDecision:
    item_id: str
    retained: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class ContextLifecycleEvent:
    action: LifecycleAction
    previous: ContextLifecycleState
    current: ContextLifecycleState
    source_offset: str | int | None
    item_decisions: tuple[LifecycleItemDecision, ...]
    cache_break_reason: CacheBreakReason | None
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", LifecycleAction(self.action))
        object.__setattr__(self, "item_decisions", tuple(self.item_decisions))
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")

    @property
    def retained_count(self) -> int:
        return sum(decision.retained for decision in self.item_decisions)

    @property
    def excluded_count(self) -> int:
        return len(self.item_decisions) - self.retained_count


def _retention_decisions(
    items: tuple[ContextItem, ...], action: LifecycleAction
) -> tuple[LifecycleItemDecision, ...]:
    decisions: list[LifecycleItemDecision] = []
    for item in items:
        if action in {LifecycleAction.NEW_TASK, LifecycleAction.BACKGROUND}:
            retained = item.lifetime in {
                Lifetime.INSTALLATION,
                Lifetime.WORKSPACE,
                Lifetime.SESSION,
            }
            reason = "retained_across_task" if retained else "expired_task_context"
        elif action is LifecycleAction.NEXT_TURN:
            retained = item.lifetime not in {Lifetime.TURN, Lifetime.SINGLE_CALL}
            reason = "retained_next_turn" if retained else "expired_turn_context"
        elif action is LifecycleAction.CHECKPOINT:
            retained = item.lifetime not in {Lifetime.TURN, Lifetime.SINGLE_CALL}
            reason = "checkpoint_retained" if retained else "checkpoint_compacted"
        elif action is LifecycleAction.REWIND:
            retained = item.lifetime is not Lifetime.SINGLE_CALL
            reason = "rewind_retained" if retained else "rewind_tail_excluded"
        else:
            retained = item.lifetime not in {Lifetime.TURN, Lifetime.SINGLE_CALL}
            reason = "resume_revalidated" if retained else "resume_expired"
        decisions.append(
            LifecycleItemDecision(
                item_id=item.id, retained=retained, reason_code=reason
            )
        )
    return tuple(decisions)


def transition_context_lifecycle(
    state: ContextLifecycleState,
    action: LifecycleAction,
    *,
    items: Iterable[ContextItem] = (),
    branch_id: str | None = None,
    source_offset: str | int | None = None,
    created_at: datetime | None = None,
) -> ContextLifecycleEvent:
    """Calculate one auditable transition without mutating a Context owner."""
    if not isinstance(state, ContextLifecycleState):
        raise TypeError("state must be a ContextLifecycleState")
    action = LifecycleAction(action)
    if action is LifecycleAction.NEW_TASK:
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch,
            task_epoch=state.task_epoch + 1,
        )
        cache_break = CacheBreakReason.TASK_RESET
    elif action is LifecycleAction.NEXT_TURN:
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch,
            task_epoch=state.task_epoch,
            turn_epoch=state.turn_epoch + 1,
            branch_id=state.branch_id,
            checkpoint_revision=state.checkpoint_revision,
        )
        cache_break = None
    elif action is LifecycleAction.BACKGROUND:
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch,
            task_epoch=state.task_epoch + 1,
            branch_id=branch_id or f"background-{state.task_epoch + 1}",
        )
        cache_break = CacheBreakReason.TASK_RESET
    elif action is LifecycleAction.CHECKPOINT:
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch,
            task_epoch=state.task_epoch,
            turn_epoch=state.turn_epoch,
            branch_id=state.branch_id,
            checkpoint_revision=state.checkpoint_revision + 1,
        )
        cache_break = CacheBreakReason.HISTORY_COMPACTION
    elif action is LifecycleAction.REWIND:
        if source_offset is None:
            raise ValueError("rewind requires a source_offset")
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch,
            task_epoch=state.task_epoch,
            turn_epoch=state.turn_epoch + 1,
            branch_id=branch_id or f"rewind-{state.turn_epoch + 1}",
            checkpoint_revision=state.checkpoint_revision,
        )
        cache_break = None
    else:
        current = ContextLifecycleState(
            session_id=state.session_id,
            session_epoch=state.session_epoch + 1,
            task_epoch=state.task_epoch,
            turn_epoch=state.turn_epoch,
            branch_id=state.branch_id,
            checkpoint_revision=state.checkpoint_revision,
        )
        cache_break = CacheBreakReason.PROVIDER_CACHE_UNKNOWN
    item_values = tuple(items)
    return ContextLifecycleEvent(
        action=action,
        previous=state,
        current=current,
        source_offset=source_offset,
        item_decisions=_retention_decisions(item_values, action),
        cache_break_reason=cache_break,
        created_at=created_at or datetime.now(timezone.utc),
    )


__all__ = [
    "ContextLifecycleEvent",
    "ContextLifecycleState",
    "LifecycleAction",
    "LifecycleItemDecision",
    "transition_context_lifecycle",
]
