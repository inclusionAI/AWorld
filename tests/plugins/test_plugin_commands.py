import json
from types import SimpleNamespace

from pathlib import Path

import pytest

from aworld_cli.core.command_system import CommandContext, CommandRegistry
from aworld_cli.builtin_plugins.ralph_session_loop.common import (
    extract_completion_promise,
    parse_loop_args,
    summarize_text,
)
from aworld.plugins.discovery import discover_plugins
from aworld_cli.plugin_capabilities.commands import PluginPromptCommand, register_plugin_commands, sync_plugin_commands
from aworld_cli.plugin_capabilities.state import PluginStateStore
from aworld_cli.runtime.base import BaseCliRuntime
from aworld_cli.memory.provider import CliDurableMemoryProvider


def _build_dummy_runtime(tmp_path):
    class DummyRuntime(BaseCliRuntime):
        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://commands"

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_state_store = PluginStateStore(tmp_path / "state")
    return runtime


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


@pytest.mark.parametrize("user_args", ['"Build API" --max-iterations 0', '"Build API" --max-iterations -5'])
def test_parse_loop_args_rejects_non_positive_max_iterations(user_args):
    with pytest.raises(ValueError, match="--max-iterations must be >= 1"):
        parse_loop_args(user_args)


def test_parse_loop_args_rejects_malformed_quotes():
    with pytest.raises(ValueError, match="quotation"):
        parse_loop_args('"Build API --verify unclosed')


def test_extract_completion_promise_strips_multiline_content():
    answer = "Done\n<promise>\nCOMPLETE\n</promise>"

    assert extract_completion_promise(answer) == "COMPLETE"


def test_extract_completion_promise_strips_surrounding_whitespace():
    assert extract_completion_promise("<promise>  COMPLETE  </promise>") == "COMPLETE"


def test_summarize_text_handles_edge_cases():
    assert summarize_text("") == ""
    assert summarize_text("a" * 160) == "a" * 160
    assert summarize_text("a" * 161) == "a" * 157 + "..."
    assert summarize_text("Hello 🎉 World", limit=10) == "Hello 🎉..."
    assert summarize_text(None) is None


def test_register_plugin_command_from_manifest():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("code-review")
        assert command is not None
        assert command.description == "Review the current pull request"
        assert "gh pr view" in command.allowed_tools[0]
    finally:
        CommandRegistry.restore(snapshot)


async def test_plugin_prompt_command_reads_packaged_prompt():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("code-review")
        prompt = await command.get_prompt(CommandContext(cwd=str(plugin_root), user_args="--comment"))

        assert "Provide a code review for the given pull request." in prompt
        assert "--comment" in prompt
    finally:
        CommandRegistry.restore(snapshot)


def test_sync_plugin_commands_removes_stale_plugin_commands():
    plugin_root = Path("tests/fixtures/plugins/code_review_like").resolve()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        assert CommandRegistry.get("code-review") is not None

        sync_plugin_commands([])

        assert CommandRegistry.get("code-review") is None
    finally:
        CommandRegistry.restore(snapshot)


def test_register_python_backed_plugin_command_from_manifest(tmp_path):
    plugin_root = tmp_path / "python_plugin"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "commands").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"python-plugin\", "
            "\"name\": \"python-plugin\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"commands\": ["
            "{"
            "\"id\": \"python-backed\", "
            "\"name\": \"python-backed\", "
            "\"target\": \"commands/python_backed.py\""
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "commands" / "python_backed.py").write_text(
        "from aworld_cli.core.command_system import Command\n"
        "class PythonBackedCommand(Command):\n"
        "    @property\n"
        "    def name(self):\n"
        "        return 'python-backed'\n"
        "    @property\n"
        "    def description(self):\n"
        "        return 'Python backed command'\n"
        "    async def get_prompt(self, context):\n"
        "        return f'hello {context.user_args}'\n"
        "def build_command(plugin, entrypoint):\n"
        "    return PythonBackedCommand()\n",
        encoding="utf-8",
    )

    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("python-backed")
        assert command is not None
        assert command.command_type == "prompt"
        prompt = __import__("asyncio").run(
            command.get_prompt(CommandContext(cwd=str(plugin_root), user_args="world"))
        )
        assert prompt == "hello world"
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_workspace_layers(tmp_path, monkeypatch):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (home / ".aworld").mkdir(parents=True)
    (home / ".aworld" / "AWORLD.md").write_text("global rule", encoding="utf-8")
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text("workspace rule", encoding="utf-8")
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-04-29T00:01:00+00:00","memory_type":"reference","content":"Document release steps","source":"remember_command"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "sessions").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","final_answer":"Temporary note"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","memory_type":"workspace","confidence":"medium","promotion":"session_log_only","reason":"instructional_candidate_auto_promotion_disabled","eligible_for_auto_promotion":true}\n'
        '{"recorded_at":"2026-04-29T00:01:00+00:00","session_id":"session-1","task_id":"task-2","memory_type":"workspace","confidence":"low","promotion":"session_log_only","reason":"non_instructional_turn_end_observation","eligible_for_auto_promotion":false}\n',
        encoding="utf-8",
    )

    monkeypatch.setattr(Path, "home", lambda: home)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))

        assert "Memory instruction status" in result
        assert str(home / ".aworld" / "AWORLD.md") in result
        assert str(workspace / ".aworld" / "AWORLD.md") in result
        assert str(workspace / ".aworld" / "AWORLD.md") in result
        assert "Durable record file:" in result
        assert str(workspace / ".aworld" / "memory" / "durable.jsonl") in result
        assert "Durable record count: 2" in result
        assert "Session log directory:" in result
        assert str(workspace / ".aworld" / "memory" / "sessions") in result
        assert "Session log file count: 1" in result
        assert "- reference: 1" in result
        assert "- workspace: 1" in result
        assert "Promotion evaluations: 2" in result
        assert "Eligible for auto-promotion: 1" in result
        assert "Governance mode: shadow" in result
        assert "Governed default rollout ready: no" in result
        assert "Auto-promotion enabled: no" in result
        assert "- medium: 1" in result
        assert "- low: 1" in result
        assert "Promotion outcomes:" in result
        assert "- session_log_only: 2" in result
        assert "Promotion reasons:" in result
        assert "- instructional_candidate_auto_promotion_disabled: 1" in result
        assert "- non_instructional_turn_end_observation: 1" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_clear_defaults_to_session_logs_only(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","final_answer":"Temporary note"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True, exist_ok=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="clear"))

        assert "Cleared session logs: 1 file(s)" in result
        assert not (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").exists()
        assert (workspace / ".aworld" / "memory" / "durable.jsonl").exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_durable_record_kind_counts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","memory_kind":"workflow","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-05-08T00:01:00+00:00","memory_type":"reference","memory_kind":"fact","content":"Document release steps","source":"remember_command"}\n'
        '{"recorded_at":"2026-05-08T00:02:00+00:00","memory_type":"workspace","content":"Keep release notes current","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))

        assert "Durable record kinds:" in result
        assert "- fact: 1" in result
        assert "- legacy_untyped: 1" in result
        assert "- workflow: 1" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_clear_all_removes_session_logs_durable_records_and_metrics(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","final_answer":"Temporary note"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","memory_type":"workspace","confidence":"low","promotion":"session_log_only","reason":"non_instructional_turn_end_observation","eligible_for_auto_promotion":false}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="clear --scope all")
        )

        assert "Cleared session logs: 1 file(s)" in result
        assert "Cleared durable records: yes" in result
        assert "Cleared promotion metrics: yes" in result
        assert not (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").exists()
        assert not (workspace / ".aworld" / "memory" / "durable.jsonl").exists()
        assert not (workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl").exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_cache_reports_request_linked_cache_observability(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "sessions" / "session-1.jsonl").write_text(
        '\n'.join(
            [
                json.dumps(
                    {
                        "recorded_at": "2026-05-07T00:00:00+00:00",
                        "event": "task_completed",
                        "task_id": "task-1",
                        "llm_calls": [
                            {
                                "task_id": "task-1",
                                "request_id": "llm_req_1",
                                "provider_request_id": "req_provider_1",
                                "model": "gpt-4.1",
                                "request": {
                                    "messages": [
                                        {"role": "system", "content": "You are Aworld. Follow workspace guidance carefully."},
                                        {"role": "user", "content": "Inspect the repo and explain the failing tests."},
                                    ]
                                },
                                "usage_raw": {
                                    "cache_hit_tokens": 80,
                                    "cache_write_tokens": 20,
                                    "prompt_tokens_details": {"cached_tokens": 80},
                                },
                            },
                            {
                                "task_id": "task-1",
                                "request_id": "llm_req_2",
                                "provider_request_id": "req_provider_2",
                                "model": "gpt-4.1",
                                "request": {
                                    "messages": [
                                        {"role": "system", "content": "You are Aworld. Follow workspace guidance carefully."},
                                        {"role": "user", "content": "Inspect the repo and explain the failing tests."},
                                        {"role": "assistant", "content": "Working..."},
                                    ]
                                },
                                "usage_raw": {
                                    "cache_hit_tokens": 40,
                                    "prompt_tokens_details": {"cached_tokens": 40},
                                },
                            },
                        ],
                    },
                    ensure_ascii=False,
                )
            ]
        )
        + '\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="cache"))

        assert "Cache observability summary" in result
        assert "LLM calls analyzed: 2" in result
        assert "Calls with cache usage: 2" in result
        assert "Total cache hit tokens: 120" in result
        assert "llm_req_2" in result
        assert "req_provider_2" in result
        assert "Stable cacheable prefix candidates" in result
        assert "You are Aworld" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_recent_promotion_explanations(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","session_id":"session-1","task_id":"task-1","memory_type":"workspace","confidence":"medium","promotion":"session_log_only","reason":"instructional_candidate_auto_promotion_disabled","eligible_for_auto_promotion":true,"content":"Use pnpm for workspace package management"}\n'
        '{"recorded_at":"2026-04-29T00:01:00+00:00","session_id":"session-1","task_id":"task-2","memory_type":"workspace","confidence":"low","promotion":"session_log_only","reason":"non_instructional_turn_end_observation","eligible_for_auto_promotion":false,"content":"Temporary debug note for the current task only."}\n'
        '{"recorded_at":"2026-04-29T00:02:00+00:00","session_id":"session-1","task_id":"task-3","memory_type":"workspace","confidence":"high","promotion":"durable_memory","reason":"high_confidence_workspace_instruction_auto_promoted","eligible_for_auto_promotion":true,"content":"Always use pnpm for workspace package management and never run npm install here."}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))

        assert "Auto-promotion enabled: no" in result
        assert "Latest promotion decision: durable_memory (high)" in result
        assert "Latest decision content: Always use pnpm for workspace package management and never run npm install here." in result
        assert "Last auto-promoted reason: high_confidence_workspace_instruction_auto_promoted" in result
        assert "Last auto-promoted content: Always use pnpm for workspace package management and never run npm install here." in result
        assert "Last eligible but blocked reason: instructional_candidate_auto_promotion_disabled" in result
        assert "Last eligible but blocked content: Use pnpm for workspace package management" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_auto_promotion_flag_enabled(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_ENABLE_AUTO_PROMOTION", "1")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))

        assert "Auto-promotion enabled: yes" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_reports_governed_mode_and_rollout_ready(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AWORLD_CLI_PROMOTION_MODE", "governed")
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)

    decisions_path = (
        workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl"
    )
    reviews_path = (
        workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"
    )
    with decisions_path.open("w", encoding="utf-8") as decisions_handle, reviews_path.open(
        "w",
        encoding="utf-8",
    ) as reviews_handle:
        for index in range(100):
            decision_id = f"gdec_{index}"
            decisions_handle.write(
                json.dumps(
                    {
                        "decision_id": decision_id,
                        "candidate_id": f"cand_{index}",
                        "policy_mode": "governed",
                        "policy_version": "2026-05-07",
                        "decision": "durable_memory",
                        "reason": "governed_policy_pass",
                        "confidence": "high",
                        "memory_type": "workspace",
                        "content": f"Use pnpm rule {index}",
                        "source_ref": {
                            "session_id": "session-1",
                            "task_id": f"task-{index}",
                            "candidate_id": f"cand_{index}",
                        },
                        "blockers": [],
                        "evaluated_at": f"2026-05-07T00:{index % 60:02d}:00+00:00",
                    },
                    ensure_ascii=False,
                )
            )
            decisions_handle.write("\n")
            reviews_handle.write(
                json.dumps(
                    {"decision_id": decision_id, "review_action": "confirmed"},
                    ensure_ascii=False,
                )
            )
            reviews_handle.write("\n")

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="status"))

        assert "Governance mode: governed" in result
        assert "Governed default rollout ready: yes" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_lists_governed_decisions(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"shadow_mode_no_auto_promotion","confidence":"high","blockers":["review_required"],"memory_type":"workspace","content":"Use pnpm for workspace package management","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n'
        '{"decision_id":"gdec_2","candidate_id":"cand_2","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"medium","blockers":[],"memory_type":"workspace","content":"Keep release notes current","source_ref":{"session_id":"s2","task_id":"t2","candidate_id":"cand_2"},"evaluated_at":"2026-05-07T00:01:00+00:00"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl").write_text(
        '{"decision_id":"gdec_1","review_action":"declined"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions")
        )

        assert "Governed promotions" in result
        assert "decision_id=gdec_1" in result
        assert "policy_mode=shadow" in result
        assert "policy_version=2026-05-07" in result
        assert "decision=session_log_only" in result
        assert "reason=shadow_mode_no_auto_promotion" in result
        assert "confidence=high" in result
        assert "source_ref=session_id=s1, task_id=t1, candidate_id=cand_1" in result
        assert "blockers=review_required" in result
        assert "decision_id=gdec_2" in result
        assert "policy_mode=governed" in result
        assert "decision=durable_memory" in result
        assert "confidence=medium" in result
        assert "reviews=declined" in result
        assert "content=Keep release notes current" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_show_memory_kind_with_legacy_fallback(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"shadow_mode_no_auto_promotion","confidence":"high","blockers":["review_required"],"memory_type":"workspace","memory_kind":"workflow","content":"Use pnpm for workspace package management","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n'
        '{"decision_id":"gdec_2","candidate_id":"cand_2","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"medium","blockers":[],"memory_type":"workspace","content":"Keep release notes current","source_ref":{"session_id":"s2","task_id":"t2","candidate_id":"cand_2"},"evaluated_at":"2026-05-07T00:01:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions")
        )

        assert "memory_kind=workflow" in result
        assert "memory_kind=legacy_untyped" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_accept_confirms_and_promotes_shadow_candidate(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"shadow_mode_no_auto_promotion","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions accept gdec_1")
        )

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        workspace_memory_file = workspace / ".aworld" / "AWORLD.md"
        assert "Recorded review action: confirmed for gdec_1" in result
        assert "Promoted to durable memory: workspace" in result
        assert review_file.exists()
        assert (
            review_file.read_text(encoding="utf-8").strip()
            == json.dumps(
                {"decision_id": "gdec_1", "review_action": "confirmed"},
                ensure_ascii=False,
            )
        )
        assert durable_file.exists()
        durable_records = [json.loads(line) for line in durable_file.read_text(encoding="utf-8").splitlines()]
        assert durable_records == [
            {
                "recorded_at": durable_records[0]["recorded_at"],
                "memory_type": "workspace",
                "content": "Use pnpm",
                "source": "governed_auto_promotion",
                "decision_id": "gdec_1",
                "source_ref": {
                    "session_id": "s1",
                    "task_id": "t1",
                    "candidate_id": "cand_1",
                },
            }
        ]
        assert workspace_memory_file.exists()
        assert "## Remembered Guidance" in workspace_memory_file.read_text(encoding="utf-8")
        assert "- Use pnpm" in workspace_memory_file.read_text(encoding="utf-8")
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_accept_preserves_memory_kind_without_instruction_mirroring(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_fact","candidate_id":"cand_fact","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"shadow_mode_no_auto_promotion","confidence":"high","blockers":[],"memory_type":"workspace","memory_kind":"fact","content":"The release checklist lives in docs/release.md","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_fact"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions accept gdec_fact")
        )

        review_file = workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl"
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        assert "Recorded review action: confirmed for gdec_fact" in result
        assert review_file.exists()
        assert durable_file.exists()
        durable_records = [json.loads(line) for line in durable_file.read_text(encoding="utf-8").splitlines()]
        assert durable_records == [
            {
                "recorded_at": durable_records[0]["recorded_at"],
                "memory_type": "workspace",
                "memory_kind": "fact",
                "content": "The release checklist lives in docs/release.md",
                "source": "governed_auto_promotion",
                "decision_id": "gdec_fact",
                "source_ref": {
                    "session_id": "s1",
                    "task_id": "t1",
                    "candidate_id": "cand_fact",
                },
            }
        ]
        assert not (workspace / ".aworld" / "AWORLD.md").exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_invalid_accept_does_not_write_review(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        with pytest.raises(ValueError, match="Unknown governed decision: missing"):
            await command.execute(
                CommandContext(cwd=str(workspace), user_args="promotions accept missing")
            )

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        assert not review_file.exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision_payload", "expected_error"),
    [
        (
            '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"rejected","policy_mode":"governed","policy_version":"2026-05-07","reason":"ineligible_extraction_candidate","confidence":"low","blockers":["ineligible_extraction_candidate"],"memory_type":"workspace","content":"I updated the workspace and ran the tests successfully.","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
            "Governed decision gdec_1 is not reviewable for acceptance",
        ),
        (
            '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"temporary_candidate","confidence":"high","blockers":["temporary_candidate"],"memory_type":"workspace","content":"Temporary debug note for the current task only.","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
            "Governed decision gdec_1 is not reviewable for acceptance",
        ),
    ],
)
async def test_memory_plugin_promotions_accept_rejects_non_reviewable_decisions(
    tmp_path,
    decision_payload,
    expected_error,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        decision_payload,
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        with pytest.raises(ValueError, match=expected_error):
            await command.execute(
                CommandContext(cwd=str(workspace), user_args="promotions accept gdec_1")
            )

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        assert not review_file.exists()
        assert not durable_file.exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user_args", "expected_action"),
    [
        ("promotions reject gdec_1", "declined"),
    ],
)
async def test_memory_plugin_promotions_non_accept_review_actions_record_reviews(
    tmp_path,
    user_args,
    expected_action,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"session_log_only","policy_mode":"shadow","policy_version":"2026-05-07","reason":"shadow_mode_no_auto_promotion","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args=user_args)
        )

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        assert f"Recorded review action: {expected_action} for gdec_1" == result
        assert review_file.exists()
        assert (
            review_file.read_text(encoding="utf-8").strip()
            == json.dumps(
                {"decision_id": "gdec_1", "review_action": expected_action},
                ensure_ascii=False,
            )
        )
        assert not durable_file.exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
@pytest.mark.parametrize("user_args", ["promotions reject missing", "promotions revert missing"])
async def test_memory_plugin_promotions_non_accept_unknown_decision_fails_safely(
    tmp_path,
    user_args,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        with pytest.raises(ValueError, match="Unknown governed decision: missing"):
            await command.execute(CommandContext(cwd=str(workspace), user_args=user_args))

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        assert not review_file.exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_revert_deactivates_promoted_governed_record(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    provider = CliDurableMemoryProvider()
    provider.append_durable_memory_record(
        workspace_path=workspace,
        text="Use pnpm",
        memory_type="workspace",
        source="governed_auto_promotion",
        decision_id="gdec_1",
        source_ref={
            "session_id": "s1",
            "task_id": "t1",
            "candidate_id": "cand_1",
        },
    )
    assert [record.decision_id for record in provider.get_active_durable_memory_records(workspace)] == [
        "gdec_1"
    ]

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions revert gdec_1")
        )

        review_file = (
            workspace
            / ".aworld"
            / "memory"
            / "metrics"
            / "promotion_reviews.jsonl"
        )
        assert result == "Recorded review action: reverted for gdec_1"
        assert review_file.exists()
        assert (
            review_file.read_text(encoding="utf-8").strip()
            == json.dumps(
                {"decision_id": "gdec_1", "review_action": "reverted"},
                ensure_ascii=False,
            )
        )
        assert provider.get_active_durable_memory_records(workspace) == ()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_status_and_view_hide_reverted_governed_records(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "# Workspace Instructions\n\n## Remembered Guidance\n- Use pnpm\n- Keep release notes current\n",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-07T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"governed_auto_promotion","decision_id":"gdec_1","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"}}\n'
        '{"recorded_at":"2026-05-07T00:01:00+00:00","memory_type":"workspace","content":"Keep release notes current","source":"remember_command"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_reviews.jsonl").write_text(
        '{"decision_id":"gdec_1","review_action":"reverted"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        status_result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="status")
        )
        view_result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="view")
        )

        assert "Durable record count: 1" in status_result
        assert "- workspace: 1" in status_result
        assert "- [workspace] Keep release notes current" in view_result
        assert "- [workspace] Use pnpm" not in view_result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_revert_removes_guidance_when_no_active_record_remains(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld").mkdir(parents=True)
    workspace_memory_file = workspace / ".aworld" / "AWORLD.md"
    workspace_memory_file.write_text(
        "# Workspace Instructions\n\n## Remembered Guidance\n- Use pnpm\n- Keep release notes current\n",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-07T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"governed_auto_promotion","decision_id":"gdec_1","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"}}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions revert gdec_1")
        )
        view_result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="view")
        )

        assert result == "Recorded review action: reverted for gdec_1"
        content = workspace_memory_file.read_text(encoding="utf-8")
        assert "- Use pnpm" not in content
        assert "- Keep release notes current" in content
        assert "Use pnpm" not in view_result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_revert_keeps_guidance_when_duplicate_active_record_remains(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    (workspace / ".aworld").mkdir(parents=True)
    workspace_memory_file = workspace / ".aworld" / "AWORLD.md"
    workspace_memory_file.write_text(
        "# Workspace Instructions\n\n## Remembered Guidance\n- Use pnpm\n",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-07T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"governed_auto_promotion","decision_id":"gdec_1","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"}}\n'
        '{"recorded_at":"2026-05-07T00:01:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n',
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_1","candidate_id":"cand_1","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_1"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="promotions revert gdec_1")
        )
        view_result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="view")
        )

        assert result == "Recorded review action: reverted for gdec_1"
        content = workspace_memory_file.read_text(encoding="utf-8")
        assert "- Use pnpm" in content
        assert "- [workspace] Use pnpm" in view_result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_promotions_revert_removes_multiline_guidance(
    tmp_path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    multiline_content = "Use pnpm for workspace package management\nand avoid npm install here."
    provider = CliDurableMemoryProvider()
    provider.append_durable_memory_record(
        workspace_path=workspace,
        text=multiline_content,
        memory_type="workspace",
        source="governed_auto_promotion",
        decision_id="gdec_multiline",
        source_ref={
            "session_id": "s1",
            "task_id": "t1",
            "candidate_id": "cand_multiline",
        },
    )
    (workspace / ".aworld" / "memory" / "metrics").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "metrics" / "promotion_decisions.jsonl").write_text(
        '{"decision_id":"gdec_multiline","candidate_id":"cand_multiline","decision":"durable_memory","policy_mode":"governed","policy_version":"2026-05-07","reason":"governed_policy_pass","confidence":"high","blockers":[],"memory_type":"workspace","content":"Use pnpm for workspace package management\\nand avoid npm install here.","source_ref":{"session_id":"s1","task_id":"t1","candidate_id":"cand_multiline"},"evaluated_at":"2026-05-07T00:00:00+00:00"}\n',
        encoding="utf-8",
    )

    workspace_memory_file = workspace / ".aworld" / "AWORLD.md"
    assert (
        "- Use pnpm for workspace package management and avoid npm install here."
        in workspace_memory_file.read_text(encoding="utf-8")
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="promotions revert gdec_multiline",
            )
        )

        assert result == "Recorded review action: reverted for gdec_multiline"
        content = workspace_memory_file.read_text(encoding="utf-8")
        assert "Use pnpm for workspace package management and avoid npm install here." not in content
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_view_includes_explicit_durable_records(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "# Workspace Instructions\n\n## Remembered Guidance\n- Use pnpm",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-04-29T00:01:00+00:00","memory_type":"reference","content":"Document release steps","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="view"))

        assert "Memory instruction view" in result
        assert "## Remembered Guidance" in result
        assert "Explicit durable memory" in result
        assert "- [workspace] Use pnpm" in result
        assert "- [reference] Document release steps" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_view_shows_type_and_kind_labels_for_durable_records(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "# Workspace Instructions\n\n## Remembered Guidance\n- Use pnpm",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-05-08T00:00:00+00:00","memory_type":"workspace","memory_kind":"workflow","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-05-08T00:01:00+00:00","memory_type":"reference","content":"Document release steps","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args="view"))

        assert "- [workspace] Use pnpm (kind=workflow)" in result
        assert "- [reference] Document release steps (kind=legacy_untyped)" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_view_filters_explicit_durable_records_by_type(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    (workspace / ".aworld").mkdir(parents=True)
    (workspace / ".aworld" / "AWORLD.md").write_text(
        "# Workspace Instructions\n\nFollow local conventions.",
        encoding="utf-8",
    )
    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:00:00+00:00","memory_type":"workspace","content":"Use pnpm","source":"remember_command"}\n'
        '{"recorded_at":"2026-04-29T00:01:00+00:00","memory_type":"reference","content":"Document release steps","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="view --type reference")
        )

        assert "Memory instruction view" in result
        assert "Explicit durable memory (reference)" in result
        assert "- [reference] Document release steps" in result
        assert "- [workspace] Use pnpm" not in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_view_shows_filtered_durable_records_without_instruction_file(
    tmp_path,
    monkeypatch,
):
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)

    (workspace / ".aworld" / "memory").mkdir(parents=True)
    (workspace / ".aworld" / "memory" / "durable.jsonl").write_text(
        '{"recorded_at":"2026-04-29T00:01:00+00:00","memory_type":"reference","content":"Document release steps","source":"remember_command"}\n',
        encoding="utf-8",
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(
            CommandContext(cwd=str(workspace), user_args="view --type reference")
        )

        assert "Memory instruction view" in result
        assert "No instruction files found." in result
        assert "Explicit durable memory (reference)" in result
        assert "- [reference] Document release steps" in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_memory_plugin_edit_creates_workspace_file_from_compatibility_source(
    tmp_path,
    monkeypatch,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)
    compatibility_file = workspace / "AWORLD.md"
    compatibility_file.write_text("# legacy workspace rule", encoding="utf-8")

    launched = {}

    class CompletedProcess:
        returncode = 0

    monkeypatch.setenv("EDITOR", "fake-editor --wait")
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kwargs: launched.update({"argv": argv, "kwargs": kwargs}) or CompletedProcess(),
    )

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("memory")
        result = await command.execute(CommandContext(cwd=str(workspace), user_args=""))

        canonical_file = workspace / ".aworld" / "AWORLD.md"
        assert canonical_file.exists()
        assert canonical_file.read_text(encoding="utf-8") == "# legacy workspace rule"
        assert launched["argv"] == ["fake-editor", "--wait", str(canonical_file)]
        assert "Seeded from compatibility file" in result
        assert str(canonical_file) in result
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_remember_plugin_appends_workspace_memory_entry(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("remember")
        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="Use pnpm for workspace package management",
            )
        )

        canonical_file = workspace / ".aworld" / "AWORLD.md"
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        content = canonical_file.read_text(encoding="utf-8")
        durable_content = durable_file.read_text(encoding="utf-8")

        assert "Saved durable memory" in result
        assert "Use pnpm for workspace package management" in content
        assert "## Remembered Guidance" in content
        assert '"memory_type": "workspace"' in durable_content
        assert '"content": "Use pnpm for workspace package management"' in durable_content
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_remember_plugin_accepts_kind(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("remember")
        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="--kind workflow Use pnpm for workspace package management",
            )
        )

        canonical_file = workspace / ".aworld" / "AWORLD.md"
        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        content = canonical_file.read_text(encoding="utf-8")
        durable_content = durable_file.read_text(encoding="utf-8")

        assert "Saved durable memory" in result
        assert "Use pnpm for workspace package management" in content
        assert '"memory_type": "workspace"' in durable_content
        assert '"memory_kind": "workflow"' in durable_content
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_remember_plugin_reference_type_does_not_mutate_instruction_file(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("remember")
        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args='--type reference "Document release steps in CHANGELOG.md"',
            )
        )

        durable_file = workspace / ".aworld" / "memory" / "durable.jsonl"
        durable_content = durable_file.read_text(encoding="utf-8")

        assert "Saved reference durable memory" in result
        assert '"memory_type": "reference"' in durable_content
        assert '"content": "Document release steps in CHANGELOG.md"' in durable_content
        assert not (workspace / ".aworld" / "AWORLD.md").exists()
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_remember_plugin_rejects_unknown_memory_type(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    plugin = discover_plugins([_get_builtin_memory_plugin_root()])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])

        command = CommandRegistry.get("remember")
        result = await command.execute(
            CommandContext(
                cwd=str(workspace),
                user_args="--type unknown keep tests fast",
            )
        )

        assert "Invalid durable memory type" in result
        assert "user, feedback, workspace, reference" in result
    finally:
        CommandRegistry.restore(snapshot)

def test_command_context_carries_executor_session_id():
    context = CommandContext(
        cwd="/tmp",
        user_args="--flag",
        runtime=SimpleNamespace(),
        session_id="session-123",
    )

    assert context.session_id == "session-123"


def test_plugin_command_workspace_state_is_shared_with_hook_runtime(tmp_path):
    plugin_root = tmp_path / "shared_plugin"
    (plugin_root / ".aworld-plugin").mkdir(parents=True)
    (plugin_root / "commands").mkdir()
    (plugin_root / ".aworld-plugin" / "plugin.json").write_text(
        (
            "{"
            "\"id\": \"shared-plugin\", "
            "\"name\": \"shared-plugin\", "
            "\"version\": \"1.0.0\", "
            "\"entrypoints\": {"
            "\"commands\": ["
            "{"
            "\"id\": \"review-loop\", "
            "\"name\": \"review-loop\", "
            "\"target\": \"commands/review-loop.md\", "
            "\"scope\": \"workspace\""
            "}"
            "]"
            "}"
            "}"
        ),
        encoding="utf-8",
    )
    (plugin_root / "commands" / "review-loop.md").write_text("shared state", encoding="utf-8")

    plugin = discover_plugins([plugin_root])[0]
    entrypoint = plugin.manifest.entrypoints["commands"][0]
    command = PluginPromptCommand(plugin, entrypoint)

    class DummyRuntime(BaseCliRuntime):
        async def _load_agents(self):
            return []

        async def _create_executor(self, agent):
            return None

        def _get_source_type(self):
            return "TEST"

        def _get_source_location(self):
            return "test://shared"

    runtime = DummyRuntime(agent_name="Aworld")
    runtime._plugin_state_store = PluginStateStore(tmp_path / "state")
    workspace_path = str(tmp_path / "workspace")

    state_path = command.resolve_state_path(
        CommandContext(cwd=workspace_path, user_args="", runtime=runtime)
    )
    assert state_path is not None
    state_path.write_text('{"iteration": 2}', encoding="utf-8")

    hook_state = runtime.build_plugin_hook_state(
        plugin_id="shared-plugin",
        scope="workspace",
        executor_instance=SimpleNamespace(
            context=SimpleNamespace(workspace_path=workspace_path, session_id="session-1")
        ),
    )

    assert hook_state["iteration"] == 2


async def test_ralph_loop_command_initializes_session_state(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("ralph-loop")
        runtime = _build_dummy_runtime(tmp_path)
        workspace_path = str(tmp_path / "workspace")

        prompt = await command.get_prompt(
            CommandContext(
                cwd=workspace_path,
                user_args='"Build a REST API" --verify "pytest tests/api -q" --completion-promise "COMPLETE" --max-iterations 5',
                runtime=runtime,
                session_id="session-1",
            )
        )

        state_path = runtime._resolve_plugin_state_path(
            plugin_id=plugin.manifest.plugin_id,
            scope="session",
            session_id="session-1",
            workspace_path=workspace_path,
        )
        payload = runtime._plugin_state_store.handle(state_path).read()

        assert payload["active"] is True
        assert payload["prompt"] == "Build a REST API"
        assert payload["iteration"] == 1
        assert payload["max_iterations"] == 5
        assert payload["completion_promise"] == "COMPLETE"
        assert payload["verify_commands"] == ["pytest tests/api -q"]
        assert "Task:" in prompt
        assert "Build a REST API" in prompt
        assert "Verification requirements:" in prompt
        assert "1. Run: pytest tests/api -q" in prompt
        assert "Completion rule:" in prompt
        assert "Only output <promise>COMPLETE</promise>" in prompt
    finally:
        CommandRegistry.restore(snapshot)


@pytest.mark.asyncio
async def test_ralph_loop_command_rejects_missing_state_handle_at_prompt_time(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        command = CommandRegistry.get("ralph-loop")

        with pytest.raises(ValueError, match="session-aware plugin state"):
            await command.get_prompt(
                CommandContext(
                    cwd=str(tmp_path / "workspace"),
                    user_args='"Build API"',
                    runtime=None,
                    session_id=None,
                )
            )
    finally:
        CommandRegistry.restore(snapshot)


async def test_cancel_ralph_clears_session_state(tmp_path):
    plugin_root = _get_builtin_ralph_plugin_root()
    plugin = discover_plugins([plugin_root])[0]

    snapshot = CommandRegistry.snapshot()
    try:
        CommandRegistry.clear()
        register_plugin_commands([plugin])
        runtime = _build_dummy_runtime(tmp_path)
        workspace_path = str(tmp_path / "workspace")
        state_path = runtime._resolve_plugin_state_path(
            plugin_id=plugin.manifest.plugin_id,
            scope="session",
            session_id="session-1",
            workspace_path=workspace_path,
        )
        runtime._plugin_state_store.handle(state_path).write(
            {
                "active": True,
                "prompt": "Build a REST API",
                "iteration": 2,
            }
        )

        command = CommandRegistry.get("cancel-ralph")
        result = await command.execute(
            CommandContext(
                cwd=workspace_path,
                user_args="",
                runtime=runtime,
                session_id="session-1",
            )
        )

        assert "cancel" in result.lower()
        assert runtime._plugin_state_store.handle(state_path).read() == {}
    finally:
        CommandRegistry.restore(snapshot)
