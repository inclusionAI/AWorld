from pathlib import Path

import pytest

from aworld.sandbox.run.mcp_servers import McpServers


class _FakeSandbox:
    def __init__(self, *, mode: str = "remote") -> None:
        self.mode = mode
        self.sandbox_id = None
        self.reuse = False
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def ensure_skill_execution_assets_ready(
        self,
        skill_name: str,
        skill_config: dict[str, object],
    ) -> str:
        self.calls.append((skill_name, skill_config))
        digest = skill_config["execution_assets"]["digest"]
        return f"/remote/workspace/.aworld/skills/{skill_name}/{digest}"


class _FakeContext:
    def __init__(
        self,
        active_skills: list[str],
        *,
        namespace: str | None = None,
    ) -> None:
        self._active_skills = active_skills
        self.agent_info = type("AgentInfo", (), {"current_agent_id": namespace})()
        self.last_namespace: str | None = None

    async def get_active_skills(self, namespace: str | None = None):
        self.last_namespace = namespace
        return list(self._active_skills)


def _terminal_tool(tool_name: str, param_name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"terminal__{tool_name}",
            "parameters": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": "string",
                    }
                },
            },
        },
    }


def _generic_tool(server_name: str, tool_name: str, param_name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {
            "name": f"{server_name}__{tool_name}",
            "parameters": {
                "type": "object",
                "properties": {
                    param_name: {
                        "type": "string",
                    }
                },
            },
        },
    }


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_remote_run_code_skill_paths(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    run_file = skill_root / "run.py"
    run_file.write_text("print('hi')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["run.py"],
            "digest": "abcd1234abcd1234",
            "entrypoint": "run.py",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    parameter = {"code": f"cd {skill_root} && python {run_file}"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["code"] == (
        "cd /remote/workspace/.aworld/skills/browser-use/abcd1234abcd1234"
        " && python /remote/workspace/.aworld/skills/browser-use/abcd1234abcd1234/run.py"
    )
    assert sandbox.calls == [("browser-use", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_leaves_local_mode_skill_paths_unchanged(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    run_file = skill_root / "run.sh"
    run_file.write_text("echo hi\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["run.sh"],
            "digest": "dcba4321dcba4321",
            "entrypoint": "run.sh",
        },
    }
    sandbox = _FakeSandbox(mode="local")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("mcp_execute_command", "command")]
    parameter = {"command": f"bash {run_file}"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["command"] == f"bash {run_file}"
    assert sandbox.calls == []


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_entrypoint_relative_path_for_active_skill(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "scripts").mkdir()
    (skill_root / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["scripts/run.py"],
            "digest": "feed1234feed1234",
            "entrypoint": "scripts/run.py",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    parameter = {"code": "python scripts/run.py"}

    context = _FakeContext(["browser-use"], namespace="designer-agent")
    ok = await servers.check_tool_params(
        context=context,
        server_name="terminal",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["code"]
        == "python /remote/workspace/.aworld/skills/browser-use/feed1234feed1234/scripts/run.py"
    )
    assert context.last_namespace == "designer-agent"
    assert sandbox.calls == [("browser-use", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_virtual_skill_path_reference(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "code-review"
    skill_root.mkdir(parents=True)
    lint_file = skill_root / "lint_check.py"
    lint_file.write_text("print('lint')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["lint_check.py"],
            "digest": "c0de1234c0de1234",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"code-review": skill_config},
    )
    servers.tool_list = [_terminal_tool("mcp_execute_command", "command")]
    parameter = {"command": "python /skills/code-review/lint_check.py ."}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["command"]
        == "python /remote/workspace/.aworld/skills/code-review/c0de1234c0de1234/lint_check.py ."
    )
    assert sandbox.calls == [("code-review", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_legacy_claude_skill_path_reference(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "html-to-image"
    skill_root.mkdir(parents=True)
    render_file = skill_root / "scripts" / "render.py"
    render_file.parent.mkdir()
    render_file.write_text("print('render')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "path_aliases": [
            "/skills/html-to-image",
            ".claude/skills/html-to-image",
            "./.claude/skills/html-to-image",
        ],
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["scripts/render.py"],
            "digest": "abc12345abc12345",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"html-to-image": skill_config},
    )
    servers.tool_list = [_terminal_tool("mcp_execute_command", "command")]
    parameter = {"command": "python .claude/skills/html-to-image/scripts/render.py"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["command"]
        == "python /remote/workspace/.aworld/skills/html-to-image/abc12345abc12345/scripts/render.py"
    )
    assert sandbox.calls == [("html-to-image", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_custom_skill_path_alias_from_skill_config(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "html-to-image"
    skill_root.mkdir(parents=True)
    render_file = skill_root / "scripts" / "render.py"
    render_file.parent.mkdir()
    render_file.write_text("print('render')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "path_aliases": ["vendor/skills/html-to-image"],
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["scripts/render.py"],
            "digest": "bcd23456bcd23456",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"html-to-image": skill_config},
    )
    servers.tool_list = [_terminal_tool("mcp_execute_command", "command")]
    parameter = {"command": "python vendor/skills/html-to-image/scripts/render.py"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["command"]
        == "python /remote/workspace/.aworld/skills/html-to-image/bcd23456bcd23456/scripts/render.py"
    )
    assert sandbox.calls == [("html-to-image", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_does_not_sync_for_non_terminal_tool_calls(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    run_file = skill_root / "run.py"
    run_file.write_text("print('hi')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["run.py"],
            "digest": "abcd1234abcd1234",
            "entrypoint": "run.py",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["filesystem"],
        mcp_config={"mcpServers": {"filesystem": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_generic_tool("filesystem", "write_file", "content")]
    parameter = {"content": f"python {run_file}"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="filesystem",
        tool_name="write_file",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["content"] == f"python {run_file}"
    assert sandbox.calls == []
