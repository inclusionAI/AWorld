import pytest

from aworld.memory.db.filesystem import FileSystemMemoryStore
from aworld.memory.models import MemoryHumanMessage, MessageMetadata


def _build_message(*, session_id: str, task_id: str = "task-1", agent_id: str = "agent-1"):
    return MemoryHumanMessage(
        content="hello",
        memory_type="message",
        metadata=MessageMetadata(
            agent_id=agent_id,
            agent_name=agent_id,
            session_id=session_id,
            task_id=task_id,
            user_id="user-1",
        ),
    )


def test_get_all_ignores_none_session_filter_when_scanning_sessions(tmp_path):
    store = FileSystemMemoryStore(memory_root=str(tmp_path))
    store.add(_build_message(session_id="session-1"))

    items = store.get_all(
        filters={
            "agent_id": "agent-1",
            "task_id": "task-1",
            "session_id": None,
            "memory_type": "message",
        }
    )

    assert len(items) == 1
    assert items[0].content == "hello"


def test_get_session_path_defaults_none_to_default_session(tmp_path):
    store = FileSystemMemoryStore(memory_root=str(tmp_path))

    assert store._get_session_path(None).name == "default.jsonl"
