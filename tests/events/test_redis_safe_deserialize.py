import os
import pickle

import pytest

from aworld.core.event.base import Message
from aworld.events.redis_backend import _safe_loads


def test_safe_loads_preserves_messages():
    message = Message(payload={"value": 3})
    restored = _safe_loads(pickle.dumps(message))

    assert isinstance(restored, Message)
    assert restored.payload == {"value": 3}


class _ExecPayload:
    def __reduce__(self):
        return os.system, ("echo unsafe",)


def test_safe_loads_rejects_global_gadget():
    payload = pickle.dumps(_ExecPayload())

    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        _safe_loads(payload)
