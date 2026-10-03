from types import SimpleNamespace
from aworld.core.context.simple import ContextEntry
from aworld.core.context.storage import MemoryStoreAdapter, InMemoryContextStorage


class Store:
    def __init__(self):
        self.items = []

    def add(self, item):
        self.items.append(item)

    def get_all(self, *, filters):
        return [item for item in self.items if item.memory_type == filters['memory_type']
                and item.metadata.get('session_id') == filters['session_id']]


def test_explicit_storage_adapter_isolates_sessions_and_unrelated_records():
    store = Store()
    adapter = MemoryStoreAdapter(store, item_factory=SimpleNamespace)
    entry = ContextEntry('run-1', 'input', {'text': '你好'})
    adapter.append('a', entry)
    adapter.append('b', ContextEntry('run-2', 'input', 'other'))
    store.add(SimpleNamespace(memory_type='message', content='unrelated invalid JSON', metadata={'session_id': 'a'}))
    assert adapter.read('a') == (entry,)
    assert adapter.read('b')[0].data == 'other'
    assert adapter.read('missing') == ()
    loaded = adapter.read('a')[0]
    loaded.data['text'] = 'changed'
    assert adapter.read('a')[0].data == {'text': '你好'}


def test_default_history_does_not_alias_mutable_payloads():
    store = InMemoryContextStorage()
    value = {'text': ['first']}
    store.append('a', ContextEntry('run', 'input', value))
    value['text'].append('later')
    store.read('a')[0].data['text'].append('mutated')
    assert store.read('a')[0].data == {'text': ['first']}
