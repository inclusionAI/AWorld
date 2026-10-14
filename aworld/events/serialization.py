# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""Safe pickle helpers for the built-in Redis event bus.

`aworld/events/redis_backend.py` transports one `Message` per Redis stream
entry and uses pickle as the on-the-wire format.  `pickle.loads()` executes
arbitrary code while rebuilding an object graph, so that stream is a
code-execution boundary: anything able to `XADD` to a task stream -- a
compromised or prompt-injected agent, for instance -- would otherwise gain
remote code execution in every consumer (issue #929).

The wire format stays pickle so that a rolling upgrade over mixed versions
keeps working, but decoding now goes through `RestrictedUnpickler`, which only
resolves globals from an allowlist:

* AWorld's own packages (``aworld`` and its submodules);
* a small set of inert standard-library modules used by message payloads;
* ``numpy``, which AWorld uses for observation/action payloads;
* whatever an operator adds through ``AWORLD_PICKLE_ALLOWED_MODULES``
  (comma separated).

Any other global raises `pickle.UnpicklingError` before the object is built,
so a crafted payload can no longer smuggle ``os.system``,
``subprocess.Popen``, ``builtins.eval`` and friends into a consumer.

Operational guidance: the Redis instance backing this event bus must still be
a trusted, network-isolated instance -- the allowlist narrows the attack
surface, it does not make an untrusted stream safe.  The legacy behaviour
stays available, but explicit and loud: ``AWORLD_ALLOW_UNSAFE_PICKLE=1``.
"""
import io
import os
import pickle

from aworld.logs.util import logger

UNSAFE_PICKLE_ENV = "AWORLD_ALLOW_UNSAFE_PICKLE"
ALLOWED_MODULES_ENV = "AWORLD_PICKLE_ALLOWED_MODULES"

# Modules whose globals may be resolved while rebuilding a message.  Only
# modules holding inert data types belong here: resolving or calling their
# classes must not run code as a side effect.
_DEFAULT_ALLOWED_MODULES = frozenset({
    "aworld",
    "builtins",
    "collections",
    "copyreg",
    "datetime",
    "decimal",
    "enum",
    "numpy",
    "pathlib",
    "uuid",
})

# `builtins` itself is on the list, but only these names are resolved: the rest
# of the module can execute code (eval, exec, getattr, __import__, ...).
_ALLOWED_BUILTIN_NAMES = frozenset({
    "bool", "bytearray", "bytes", "complex", "dict", "float", "frozenset",
    "int", "list", "object", "set", "str", "tuple",
})

_TRUE_VALUES = ("1", "true", "yes", "on")


def unsafe_pickle_enabled() -> bool:
    """True when the operator explicitly opted out of the allowlist."""
    return os.environ.get(UNSAFE_PICKLE_ENV, "").strip().lower() in _TRUE_VALUES


def _allowed_modules():
    allowed = set(_DEFAULT_ALLOWED_MODULES)
    allowed.update(m.strip() for m in os.environ.get(ALLOWED_MODULES_ENV, "").split(",") if m.strip())
    return allowed


def _module_allowed(module: str, allowed) -> bool:
    # `module` may be a submodule of an allowed package, e.g.
    # `aworld.core.event.base` for the allowed package `aworld`.
    return module in allowed or module.split(".")[0] in allowed


class RestrictedUnpickler(pickle.Unpickler):
    """`pickle.Unpickler` that refuses globals outside the allowlist."""

    def find_class(self, module, name):
        if unsafe_pickle_enabled():
            return super().find_class(module, name)
        if not _module_allowed(module, _allowed_modules()):
            raise pickle.UnpicklingError(
                f"refusing to unpickle {module}.{name}: module is not on the event bus allowlist; "
                f"add it with {ALLOWED_MODULES_ENV} only if the payload is trusted"
            )
        if module == "builtins" and name not in _ALLOWED_BUILTIN_NAMES:
            raise pickle.UnpicklingError(
                f"refusing to unpickle builtins.{name}: not an allowed builtin for the event bus"
            )
        return super().find_class(module, name)


def dumps_message(message) -> bytes:
    """Serialize a message for the Redis event bus."""
    return pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)


def loads_message(data: bytes):
    """Rebuild a message written by :func:`dumps_message`.

    Raises `pickle.UnpicklingError` when the payload reaches for a global that
    is not on the allowlist, instead of executing it.
    """
    if unsafe_pickle_enabled():
        logger.warning(
            "%s is set: decoding event bus payloads without the pickle allowlist; only use this "
            "with a fully trusted, network-isolated Redis instance", UNSAFE_PICKLE_ENV)
        return pickle.loads(data)
    return RestrictedUnpickler(io.BytesIO(data)).load()
