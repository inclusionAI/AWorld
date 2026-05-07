import sys
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "aworld-cli" / "src"))

from aworld.plugins.discovery import discover_plugins
from aworld_cli.builtin_plugins.memory_cli.common import append_workspace_session_log
from aworld_cli.builtin_plugins.memory_cli.hooks import task_completed as task_completed_hook_module
from aworld_cli.plugin_capabilities.hooks import PluginHookResult, load_plugin_hooks
from aworld_cli.plugin_capabilities.state import PluginStateStore
from aworld_cli.runtime.base import BaseCliRuntime


def _get_builtin_ralph_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "ralph_session_loop"
    )


def _get_builtin_memory_plugin_root() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "aworld-cli"
        / "src"
        / "aworld_cli"
        / "builtin_plugins"
        / "memory_cli"
    )


def test_load_plugin_hook_entrypoints():
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    hooks = load_plugin_hooks([plugin])

    assert "stop" in hooks
    assert hooks["stop"][0].entrypoint_id == "loop-stop"


@pytest.mark.asyncio
async def test_stop_hook_can_block_and_continue_session(tmp_path):
    plugin_root = Path("tests/fixtures/plugins/ralph_like").resolve()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["stop"][0].run(
        event={"transcript_path": str(tmp_path / "transcript.jsonl")},
        state={"iteration": 1, "prompt": "keep going"},
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"


def test_plugin_hook_result_accepts_claude_style_block_decision():
    result = PluginHookResult.from_payload(
        {
            "decision": "block",
            "reason": "keep going",
            "systemMessage": "Loop continues",
        }
    )

    assert result.action == "block_and_continue"
    assert result.follow_up_prompt == "keep going"
    assert result.system_message == "Loop continues"


def test_plugin_hooks_are_sorted_by_priority_then_identity(tmp_path):
    alpha = tmp_path / "alpha"
    beta = tmp_path / "beta"

    for plugin_root, plugin_id, priority in (
        (alpha, "alpha", 50),
        (beta, "beta", 10),
    ):
        (plugin_root / ".aworld-plugin").mkdir(parents=True)
        (plugin_root / "hooks").mkdir()
        (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hooks\": ["
                "{"
                "\"id\": \"stop-loop\", "
                "\"target\": \"hooks/stop.py\", "
                "\"metadata\": {"
                "\"hook_point\": \"stop\", "
                f"\"priority\": {priority}"
                "}"
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (plugin_root / "hooks" / "stop.py").write_text(
            "def handle_event(event, state):\n    return {'action': 'allow'}\n",
            encoding="utf-8",
        )

    plugins = discover_plugins([alpha, beta])
    hooks = load_plugin_hooks(plugins)

    assert [hook.plugin_id for hook in hooks["stop"]] == ["beta", "alpha"]


class DummyRuntime(BaseCliRuntime):
    async def _load_agents(self):
        return []

    async def _create_executor(self, agent):
        return None

    def _get_source_type(self):
        return "TEST"

    def _get_source_location(self):
        return "test://runtime"


@pytest.mark.asyncio
async def test_runtime_plugin_hook_can_persist_plugin_state(tmp_path):
    plugin_root = tmp_path / "stateful"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "hooks").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"stateful\", "
            "\"name\": \"stateful\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"hooks\": ["
            "{"
            "\"id\": \"task-started\", "
            "\"target\": \"hooks/task_started.py\", "
            "\"scope\": \"session\", "
            "\"metadata\": {\"hook_point\": \"task_started\"}"
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "hooks" / "task_started.py").write_text(
        "def handle_event(event, state):\n"
        "    state['__plugin_state__'].update({'iteration': state.get('iteration', 0) + 1, 'task_id': event['task_id']})\n"
        "    return {'action': 'allow'}\n",
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_hooks = load_plugin_hooks([plugin])
    runtime._plugin_state_store = PluginStateStore(tmp_path / "plugin-state")

    executor_instance = SimpleNamespace(
        session_id="session-1",
        context=SimpleNamespace(workspace_path=str(tmp_path), task_id="task-1"),
    )

    await runtime.run_plugin_hooks(
        "task_started",
        event={"task_id": "task-1"},
        executor_instance=executor_instance,
    )

    state = runtime.build_plugin_hook_state("stateful", "session", executor_instance=executor_instance)

    assert state["iteration"] == 1
    assert state["task_id"] == "task-1"


@pytest.mark.asyncio
async def test_runtime_plugin_hooks_continue_after_timeout(tmp_path):
    slow_root = tmp_path / "slow"
    fast_root = tmp_path / "fast"

    for plugin_root, plugin_id, priority, timeout, source in (
        (
            slow_root,
            "slow",
            10,
            "0.01",
            "import asyncio\nasync def handle_event(event, state):\n    await asyncio.sleep(0.05)\n    return {'action': 'allow'}\n",
        ),
        (
            fast_root,
            "fast",
            20,
            None,
            "def handle_event(event, state):\n    return {'action': 'allow', 'reason': 'fast-path'}\n",
        ),
    ):
        (plugin_root / ".aworld-plugin").mkdir(parents=True)
        (plugin_root / "hooks").mkdir()
        metadata = f'"hook_point": "stop", "priority": {priority}'
        if timeout is not None:
            metadata = f'{metadata}, "timeout_seconds": {timeout}'
        (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
            (
                "{"
                f"\"id\": \"{plugin_id}\", "
                f"\"name\": \"{plugin_id}\", "
                "\"version\": \"1.0.0\", "
                "\"entrypoints\": {"
                "\"hooks\": ["
                "{"
                "\"id\": \"stop-hook\", "
                "\"target\": \"hooks/stop.py\", "
                "\"metadata\": {"
                f"{metadata}"
                "}"
                "}"
                "]"
                "}"
                "}"
            ),
            encoding="utf-8",
        )
        (plugin_root / "hooks" / "stop.py").write_text(source, encoding="utf-8")

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_hooks = load_plugin_hooks(discover_plugins([slow_root, fast_root]))

    results = await runtime.run_plugin_hooks("stop", event={"task_id": "task-1"})

    assert [hook.plugin_id for hook, _ in results] == ["fast"]
    assert results[0][1].reason == "fast-path"


@pytest.mark.asyncio
async def test_plugin_hook_adapter_caches_loaded_handler_module(tmp_path):
    plugin_root = tmp_path / "cached"
    import_log = tmp_path / "imports.log"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "hooks").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"cached\", "
            "\"name\": \"cached\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"hooks\": ["
            "{"
            "\"id\": \"stop-hook\", "
            "\"target\": \"hooks/stop.py\", "
            "\"metadata\": {\"hook_point\": \"stop\"}"
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "hooks" / "stop.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(import_log)!r}).write_text(Path({str(import_log)!r}).read_text(encoding='utf-8') + 'x' if Path({str(import_log)!r}).exists() else 'x', encoding='utf-8')\n"
        "def handle_event(event, state):\n"
        "    return {'action': 'allow'}\n",
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]
    hook = load_plugin_hooks([plugin])["stop"][0]

    await hook.run(event={}, state={})
    await hook.run(event={}, state={})

    assert import_log.read_text(encoding="utf-8") == "x"


@pytest.mark.asyncio
async def test_ralph_stop_hook_blocks_and_continues_when_active(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    store = PluginStateStore(tmp_path / "plugin-state")
    state_path = store.session_state("ralph-session-loop", "session-1")
    handle = store.handle(state_path)
    handle.write(
        {
            "active": True,
            "prompt": "Build a REST API",
            "iteration": 1,
            "max_iterations": 5,
            "completion_promise": "COMPLETE",
            "verify_commands": ["pytest tests/api -q"],
            "last_final_answer": "not done yet",
        }
    )

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    assert result.action == "block_and_continue"
    assert "Task:" in result.follow_up_prompt
    assert "Verification requirements:" in result.follow_up_prompt
    assert "pytest tests/api -q" in result.follow_up_prompt
    assert handle.read()["iteration"] == 2


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_records_shadow_decision_without_durable_write(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "shadow")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "workspace_path": str(workspace),
            "task_status": "idle",
            "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
        },
        state={"workspace_path": str(workspace)},
    )

    assert not (workspace / ".aworld" / "memory" / "durable.jsonl").exists()

    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    session_payload = json.loads(session_log.read_text(encoding="utf-8").strip())
    candidate = session_payload["candidates"][0]
    assert candidate["candidate_id"]
    assert "governed_decision" not in candidate
    assert candidate["auto_promoted"] is False

    decision_payload = json.loads(
        (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").read_text(
            encoding="utf-8"
        ).strip()
    )
    assert decision_payload["reason"] == "shadow_mode_no_auto_promotion"
    assert decision_payload["source_ref"]["candidate_id"] == candidate["candidate_id"]
    assert decision_payload["source_ref"]["session_log_recorded_at"] == session_payload["recorded_at"]
    assert decision_payload["source_ref"]["session_log_path"] == str(session_log)


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_governed_mode_writes_durable_memory(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "workspace_path": str(workspace),
            "task_status": "idle",
            "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
        },
        state={"workspace_path": str(workspace)},
    )

    durable_payload = json.loads(
        (workspace / ".aworld" / "memory" / "durable.jsonl").read_text(encoding="utf-8").strip()
    )
    assert durable_payload["source"] == "governed_auto_promotion"

    session_log = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    session_payload = json.loads(session_log.read_text(encoding="utf-8").strip())
    candidate = session_payload["candidates"][0]
    assert "governed_decision" not in candidate
    assert candidate["auto_promoted"] is False

    decision_payload = json.loads(
        (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").read_text(
            encoding="utf-8"
        ).strip()
    )
    assert decision_payload["reason"] == "governed_policy_pass"
    assert decision_payload["source_ref"]["candidate_id"] == candidate["candidate_id"]
    assert decision_payload["source_ref"]["session_log_recorded_at"] == session_payload["recorded_at"]
    assert durable_payload["decision_id"] == decision_payload["decision_id"]
    assert durable_payload["source_ref"] == decision_payload["source_ref"]


def test_task_completed_resolves_persisted_candidate_by_identity_not_last_line(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    session_log_path = append_workspace_session_log(
        workspace_path=workspace,
        session_id="session-1",
        payload={
            "event": "task_completed",
            "session_id": "session-1",
            "task_id": "task-1",
            "candidates": [
                {
                    "candidate_id": "session-1:task-1:0",
                    "content": "Use pnpm for workspace package management",
                    "confidence": "high",
                    "memory_type": "workspace",
                }
            ],
        },
    )
    append_workspace_session_log(
        workspace_path=workspace,
        session_id="session-1",
        payload={
            "event": "task_completed",
            "session_id": "session-1",
            "task_id": "task-other",
            "candidates": [
                {
                    "candidate_id": "session-1:task-other:0",
                    "content": "Unrelated later append",
                    "confidence": "low",
                    "memory_type": "workspace",
                }
            ],
        },
    )

    persisted_entry, persisted_candidate = task_completed_hook_module._read_persisted_candidate_entry(
        session_log_path=session_log_path,
        session_id="session-1",
        task_id="task-1",
        candidate_id="session-1:task-1:0",
    )

    assert persisted_entry["task_id"] == "task-1"
    assert persisted_candidate["candidate_id"] == "session-1:task-1:0"
    assert persisted_candidate["content"] == "Use pnpm for workspace package management"


@pytest.mark.asyncio
async def test_ralph_stop_hook_denies_when_handle_is_missing_for_active_state(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"active": True},
    )

    assert result.action == "deny"
    assert "unavailable" in result.reason.lower()


@pytest.mark.asyncio
async def test_ralph_stop_hook_handles_missing_prompt_field(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    store = PluginStateStore(tmp_path / "plugin-state")
    state_path = store.session_state("ralph-session-loop", "session-1")
    handle = store.handle(state_path)
    handle.write(
        {
            "active": True,
            "iteration": 1,
            "max_iterations": 5,
        }
    )

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    assert result.action == "block_and_continue"
    assert "Task:" in result.follow_up_prompt
    assert handle.read()["iteration"] == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("iteration_value", [None, -3, "oops"])
async def test_ralph_stop_hook_handles_invalid_iteration_value(tmp_path, iteration_value):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    store = PluginStateStore(tmp_path / "plugin-state")
    state_path = store.session_state("ralph-session-loop", "session-1")
    handle = store.handle(state_path)
    handle.write(
        {
            "active": True,
            "prompt": "Build a REST API",
            "iteration": iteration_value,
            "max_iterations": 5,
            "verify_commands": [],
        }
    )

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    assert result.action == "block_and_continue"
    assert handle.read()["iteration"] == 2


@pytest.mark.asyncio
async def test_ralph_stop_hook_allows_exit_on_exact_completion_promise(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    store = PluginStateStore(tmp_path / "plugin-state")
    state_path = store.session_state("ralph-session-loop", "session-1")
    handle = store.handle(state_path)
    handle.write(
        {
            "active": True,
            "prompt": "Build a REST API",
            "iteration": 1,
            "completion_promise": "COMPLETE",
            "verify_commands": [],
        }
    )

    await hooks["task_completed"][0].run(
        event={"final_answer": "All done.\n<promise>COMPLETE</promise>", "task_status": "completed"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    assert result.action == "allow"
    assert handle.read() == {}


@pytest.mark.asyncio
async def test_ralph_stop_hook_allows_exit_when_max_iterations_reached(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]
    hooks = load_plugin_hooks([plugin])

    store = PluginStateStore(tmp_path / "plugin-state")
    state_path = store.session_state("ralph-session-loop", "session-1")
    handle = store.handle(state_path)
    handle.write(
        {
            "active": True,
            "prompt": "Build a REST API",
            "iteration": 5,
            "max_iterations": 5,
            "completion_promise": "COMPLETE",
            "verify_commands": [],
            "last_final_answer": "still failing",
        }
    )

    result = await hooks["stop"][0].run(
        event={"session_id": "session-1"},
        state={"__plugin_state__": handle, **handle.read()},
    )

    assert result.action == "allow"
    assert handle.read() == {}


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_appends_workspace_session_log(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Use pnpm and keep tests fast.",
            "usage": {
                "request_id": "llm_req_123",
                "provider_request_id": "req_provider_123",
            },
            "llm_calls": [
                {
                    "request_id": "llm_req_123",
                    "provider_request_id": "req_provider_123",
                    "request": {"messages": [{"role": "user", "content": "hi"}]},
                    "usage_raw": {"cache_hit_tokens": 80},
                }
            ],
        },
        state={"workspace_path": str(workspace)},
    )

    log_file = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    metrics_file = workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl"

    assert result.action == "allow"
    assert log_file.exists()
    assert metrics_file.exists()
    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    assert '"task_id": "task-1"' in lines[0]
    assert '"final_answer": "Use pnpm and keep tests fast."' in lines[0]
    payload = json.loads(lines[0])
    assert payload["usage"]["request_id"] == "llm_req_123"
    assert payload["llm_calls"][0]["provider_request_id"] == "req_provider_123"
    assert payload["llm_calls"][0]["usage_raw"]["cache_hit_tokens"] == 80
    assert payload["candidates"][0]["confidence"] == "medium"
    assert payload["candidates"][0]["promotion"] == "session_log_only"
    assert payload["candidates"][0]["reason"] == "instructional_candidate_auto_promotion_disabled"
    assert payload["candidates"][0]["eligible_for_auto_promotion"] is True
    assert payload["candidates"][0]["evaluated_at"]
    metrics_payload = json.loads(metrics_file.read_text(encoding="utf-8").strip())
    assert metrics_payload["session_id"] == "session-1"
    assert metrics_payload["task_id"] == "task-1"
    assert metrics_payload["eligible_for_auto_promotion"] is True
    assert metrics_payload["promotion"] == "session_log_only"


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_extracts_instructional_candidate_from_mixed_answer(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-9",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": (
                "I updated the workspace and ran the tests successfully. "
                "Use pnpm for workspace package management. "
                "Everything passed."
            ),
        },
        state={"workspace_path": str(workspace)},
    )

    log_file = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    payload = json.loads(log_file.read_text(encoding="utf-8").strip())

    assert payload["final_answer"].startswith("I updated the workspace and ran the tests successfully.")
    assert payload["candidates"][0]["content"] == "Use pnpm for workspace package management."


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_governed_mode_promotes_high_confidence_instruction(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-1",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Always use pnpm for workspace package management and never run npm install here.",
        },
        state={"workspace_path": str(workspace)},
    )

    log_file = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    metrics_file = workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl"
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
    instruction_file = workspace / ".aworld" / "AWORLD.md"

    assert result.action == "allow"
    assert log_file.exists()
    assert metrics_file.exists()
    assert durable_file.exists()
    assert instruction_file.exists()

    session_payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    candidate = session_payload["candidates"][0]
    assert candidate["confidence"] == "high"
    assert candidate["promotion"] == "session_log_only"
    assert candidate["reason"] == "high_confidence_workspace_instruction_candidate"
    assert candidate["eligible_for_auto_promotion"] is True
    assert candidate["auto_promoted"] is False
    assert candidate["candidate_id"]

    metrics_payload = json.loads(metrics_file.read_text(encoding="utf-8").strip())
    assert metrics_payload["promotion"] == "durable_memory"
    assert metrics_payload["reason"] == "high_confidence_workspace_instruction_candidate"

    decisions_payload = json.loads(
        (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").read_text(
            encoding="utf-8"
        ).strip()
    )
    assert decisions_payload["decision"] == "durable_memory"
    assert decisions_payload["source_ref"]["candidate_id"] == candidate["candidate_id"]
    assert decisions_payload["source_ref"]["session_log_recorded_at"] == session_payload["recorded_at"]

    durable_payload = json.loads(durable_file.read_text(encoding="utf-8").strip())
    assert durable_payload["memory_type"] == "workspace"
    assert durable_payload["content"] == "Always use pnpm for workspace package management and never run npm install here."
    assert durable_payload["source"] == "governed_auto_promotion"
    assert durable_payload["decision_id"] == decisions_payload["decision_id"]
    assert durable_payload["source_ref"] == decisions_payload["source_ref"]
    assert "Always use pnpm for workspace package management" in instruction_file.read_text(
        encoding="utf-8"
    )


@pytest.mark.asyncio
async def test_memory_plugin_task_completed_hook_does_not_auto_promote_non_workspace_instruction(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    monkeypatch.setenv("AWORLD_CLI_ENABLE_AUTO_PROMOTION", "1")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]
    hooks = load_plugin_hooks([plugin])

    result = await hooks["task_completed"][0].run(
        event={
            "session_id": "session-1",
            "task_id": "task-2",
            "task_status": "idle",
            "workspace_path": str(workspace),
            "final_answer": "Must never ship broken onboarding copy to customers.",
        },
        state={"workspace_path": str(workspace)},
    )

    log_file = workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl"
    metrics_file = workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl"
    durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"

    assert result.action == "allow"
    assert log_file.exists()
    assert metrics_file.exists()
    assert not durable_file.exists()

    session_payload = json.loads(log_file.read_text(encoding="utf-8").strip())
    candidate = session_payload["candidates"][0]
    assert candidate["confidence"] == "medium"
    assert candidate["promotion"] == "session_log_only"
    assert candidate["reason"] == "instructional_candidate_auto_promotion_disabled"
    assert candidate["auto_promoted"] is False

    metrics_payload = json.loads(metrics_file.read_text(encoding="utf-8").strip())
    assert metrics_payload["promotion"] == "session_log_only"
    assert metrics_payload["reason"] == "instructional_candidate_auto_promotion_disabled"
