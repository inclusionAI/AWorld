"""Verify original storage backends without model/tokenizer initialization."""

import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

from aworld.core.session import Context, SessionEntry, create_session
from aworld.core.context.storage import MemoryStoreAdapter
from aworld.memory.db import FileSystemMemoryStore, InMemoryMemoryStore, SQLiteMemoryStore
from aworld.memory.models import MemoryItem


@pytest.mark.parametrize("backend", ["memory", "sqlite", "filesystem"])
def test_original_store_crud_and_session_filters(backend, tmp_path):
    store = {
        "memory": lambda: InMemoryMemoryStore(),
        "sqlite": lambda: SQLiteMemoryStore(str(tmp_path / "memory.db")),
        "filesystem": lambda: FileSystemMemoryStore(memory_root=str(tmp_path / "memory")),
    }[backend]()
    first = MemoryItem(content="first", metadata={"session_id": "one"})
    other = MemoryItem(content="other", metadata={"session_id": "two"})
    store.add(first)
    store.add(other)
    assert store.get(first.id).content == "first"
    assert [item.id for item in store.get_all({"session_id": "one"})] == [first.id]
    assert store.total_rounds({"session_id": "one"}) == 1
    first.content = "updated"
    store.update(first)
    assert store.get_first({"session_id": "one"}).content == "updated"
    store.delete(first.id)
    assert store.get_all({"session_id": "one"}) == []


@pytest.mark.parametrize("backend", ["memory", "sqlite", "filesystem"])
def test_session_reuses_memory_history_and_persistent_stores_can_reopen(backend, tmp_path):
    def make_store():
        if backend == "memory":
            return InMemoryMemoryStore()
        if backend == "sqlite":
            return SQLiteMemoryStore(str(tmp_path / "memory.db"))
        return FileSystemMemoryStore(memory_root=str(tmp_path / "memory"))

    store = make_store()
    history = MemoryStoreAdapter(store)

    class Echo:
        def validate_input(self, input):
            pass

        async def run(self, input, context):
            context.append("tool.result", {"value": input})
            return input

    async def exercise():
        session = await create_session(agent=Echo(), context=Context(storage=history))
        run = await session.submit({"hello": ["world"]})
        result = await asyncio.wait_for(run.result(), 2)
        assert result.output == {"hello": ["world"]}
        expected = await session.history()
        # Existing memories in the same session are isolated from core history.
        store.add(MemoryItem(content="unrelated", memory_type="session_entry", metadata={"session_id": session.id}))
        assert await session.history() == expected
        assert [entry.kind for entry in expected] == ["input", "tool.result", "output"]
        assert history.read("another-session") == ()
        if backend != "memory":
            assert MemoryStoreAdapter(make_store()).read(session.id) == expected

    asyncio.run(exercise())


def test_memory_bridge_rejects_non_json_before_writing():
    store = InMemoryMemoryStore()
    history = MemoryStoreAdapter(store)
    for value in (object(), float("nan")):
        with pytest.raises((TypeError, ValueError)):
            history.append("session", SessionEntry("run", "input", value))
    assert store.get_all() == []


def test_storage_imports_do_not_load_models_tokenizers_or_optional_backends():
    code = """
import sys
import importlib.abc
class BlockNonStorageDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'yaml', 'loguru', 'executing', 'tiktoken', 'transformers', 'numpy'}:
            raise ImportError(fullname)
sys.meta_path.insert(0, BlockNonStorageDependencies())
from aworld.memory.db import InMemoryMemoryStore
assert 'aworld.memory.db.filesystem' not in sys.modules
assert 'aworld.memory.db.sqlite' not in sys.modules
from aworld.memory.db import SQLiteMemoryStore, FileSystemMemoryStore
from aworld.memory.models import MemoryItem
import tempfile
from pathlib import Path
with tempfile.TemporaryDirectory() as tmp:
    stores = [InMemoryMemoryStore(), SQLiteMemoryStore(str(Path(tmp) / 'memory.db')),
              FileSystemMemoryStore(memory_root=tmp)]
    for store in stores:
        item = MemoryItem(content='hello', metadata={'session_id': 'session'})
        store.add(item)
        assert store.get(item.id).content == 'hello'
for prefix in ('aworld.models.llm', 'aworld.models.utils', 'aworld.memory.main',
               'aworld.core.context', 'tiktoken', 'transformers', 'numpy',
               'aworld.memory.db.postgres', 'aworld.memory.db.mysql', 'aworld.config', 'aworld.logs', 'aworld.trace'):
    assert not any(name == prefix or name.startswith(prefix + '.') for name in sys.modules), prefix
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr
