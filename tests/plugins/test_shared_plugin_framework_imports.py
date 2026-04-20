import sys
from importlib import import_module
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld.plugins.discovery import discover_plugins
from aworld.plugins.manifest import load_plugin_manifest
from aworld.plugins.registry import PluginCapabilityRegistry
from aworld.plugins.validation import validate_plugin_path
from aworld.plugins.resolution import resolve_plugin_activation
from aworld.plugins import __all__ as shared_plugin_all
from aworld_cli.plugin_capabilities import __all__ as capability_all
from aworld_cli.plugin_capabilities.commands import sync_plugin_commands as capability_sync_plugin_commands
from aworld_cli.plugin_capabilities.context import load_plugin_contexts as capability_load_plugin_contexts
from aworld_cli.plugin_capabilities.hooks import (
    HookEventPayload,
    PluginHookResult,
    StopHookEvent,
    TaskCompletedHookEvent,
    TaskErrorHookEvent,
    TaskInterruptedHookEvent,
    TaskProgressHookEvent,
    TaskStartedHookEvent,
    load_plugin_hooks as capability_load_plugin_hooks,
)
from aworld_cli.plugin_capabilities.hud import collect_hud_lines as capability_collect_hud_lines
from aworld_cli.plugin_capabilities.state import PluginStateStore as CapabilityPluginStateStore


def test_shared_plugin_framework_exports_core_primitives():
    assert callable(discover_plugins)
    assert callable(load_plugin_manifest)
    assert PluginCapabilityRegistry is not None
    assert callable(validate_plugin_path)
    assert callable(resolve_plugin_activation)


def test_plugin_capabilities_exports_cli_runtime_primitives():
    assert callable(capability_sync_plugin_commands)
    assert callable(capability_load_plugin_contexts)
    assert callable(capability_load_plugin_hooks)
    assert callable(capability_collect_hud_lines)
    assert CapabilityPluginStateStore is not None


def test_shared_plugin_public_exports_include_validation_and_resolution():
    assert "validate_plugin_path" in shared_plugin_all
    assert "get_plugin_manifest_schema_path" in shared_plugin_all
    assert "resolve_plugin_activation" in shared_plugin_all


def test_plugin_capabilities_public_exports_include_hook_payload_types():
    assert "PluginHookResult" in capability_all
    assert "HookEventPayload" in capability_all
    assert "StopHookEvent" in capability_all
    assert "TaskStartedHookEvent" in capability_all
    assert "TaskProgressHookEvent" in capability_all
    assert "TaskCompletedHookEvent" in capability_all
    assert "TaskErrorHookEvent" in capability_all
    assert "TaskInterruptedHookEvent" in capability_all


def test_hook_payload_typed_dicts_expose_expected_fields():
    assert StopHookEvent.__required_keys__ == frozenset()
    assert {"transcript_path", "workspace_path", "session_id", "task_id"} <= StopHookEvent.__optional_keys__
    assert {"task_id", "session_id", "message"} <= TaskStartedHookEvent.__optional_keys__
    assert {"task_id", "session_id", "current_tool", "elapsed_seconds", "usage"} <= TaskProgressHookEvent.__optional_keys__
    assert {"task_id", "session_id", "task_status", "final_answer"} <= TaskCompletedHookEvent.__optional_keys__
    assert {"task_id", "session_id", "task_status", "error", "error_type"} <= TaskErrorHookEvent.__optional_keys__
    assert {"task_id", "session_id", "task_status", "partial_answer"} <= TaskInterruptedHookEvent.__optional_keys__
    assert HookEventPayload.__args__ == (
        StopHookEvent,
        TaskStartedHookEvent,
        TaskProgressHookEvent,
        TaskCompletedHookEvent,
        TaskErrorHookEvent,
        TaskInterruptedHookEvent,
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "aworld_cli.plugin_runtime",
        "aworld_cli.plugin_runtime.commands",
        "aworld_cli.plugin_runtime.context",
        "aworld_cli.plugin_runtime.hooks",
        "aworld_cli.plugin_runtime.hud",
        "aworld_cli.plugin_runtime.state",
        "aworld_cli.plugin_framework",
        "aworld_cli.plugin_framework.commands",
        "aworld_cli.plugin_framework.context",
        "aworld_cli.plugin_framework.discovery",
        "aworld_cli.plugin_framework.hooks",
        "aworld_cli.plugin_framework.hud",
        "aworld_cli.plugin_framework.manifest",
        "aworld_cli.plugin_framework.models",
        "aworld_cli.plugin_framework.registry",
        "aworld_cli.plugin_framework.resources",
        "aworld_cli.plugin_framework.state",
    ],
)
def test_legacy_plugin_alias_modules_are_removed(module_name):
    with pytest.raises(ModuleNotFoundError):
        import_module(module_name)
