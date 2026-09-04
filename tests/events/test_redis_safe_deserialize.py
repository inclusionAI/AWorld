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


class _AWorldCallablePayload:
    def __reduce__(self):
        # Functions in trusted modules must not be callable reducers.  Keeping
        # this blocked prevents a future helper added to AWorld from becoming
        # an accidental deserialization gadget.
        from aworld.events.redis_backend import _safe_loads
        return _safe_loads, (b"not-a-pickle",)


def test_safe_loads_rejects_aworld_callable_reducer():
    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        _safe_loads(pickle.dumps(_AWorldCallablePayload()))


class _CopyregPayload:
    def __reduce__(self):
        # ``copyreg._reconstructor`` is a callable reducer and is deliberately
        # not part of the safe global set.
        import copyreg
        return copyreg._reconstructor, (object, object, None)


def test_safe_loads_rejects_copyreg_reconstructor():
    with pytest.raises(pickle.UnpicklingError, match="not allowed"):
        _safe_loads(pickle.dumps(_CopyregPayload()))
