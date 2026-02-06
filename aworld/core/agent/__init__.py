# coding: utf-8
# Copyright (c) 2025 inclusionAI.

# Lazy imports to avoid triggering full dependency chain
# Users should import directly from swarm_builder module if needed

__all__ = [
    'build_swarm_from_yaml',
    'build_swarm_from_dict',
    'SwarmConfigValidator',
    'SwarmYAMLBuilder',
]


def __getattr__(name):
    """Lazy import for swarm_builder components."""
    if name in __all__:
        from aworld.core.agent.swarm_builder import (
            build_swarm_from_yaml,
            build_swarm_from_dict,
            SwarmConfigValidator,
            SwarmYAMLBuilder,
        )
        globals().update({
            'build_swarm_from_yaml': build_swarm_from_yaml,
            'build_swarm_from_dict': build_swarm_from_dict,
            'SwarmConfigValidator': SwarmConfigValidator,
            'SwarmYAMLBuilder': SwarmYAMLBuilder,
        })
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
