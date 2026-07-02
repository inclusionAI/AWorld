from pathlib import Path

import pytest

from aworld.sandbox.run.mcp_servers import McpServers


class _FakeSandbox:
    def __init__(
        self,
        *,
        mode: str = "remote",
        remote_workspace_root: str = "/remote/workspace",
    ) -> None:
        self.mode = mode
        self.remote_workspace_root = remote_workspace_root
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
        return f"{self.remote_workspace_root}/.aworld/skills/{skill_name}/{digest}"


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
async def test_check_tool_params_rewrites_remote_paths_for_terminal_server_alias(
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
            "digest": "a1b2c3d4a1b2c3d4",
            "entrypoint": "run.py",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal-server"],
        mcp_config={"mcpServers": {"terminal-server": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    servers.tool_list[0]["function"]["name"] = "terminal-server__run_code"
    parameter = {"code": f"python {run_file}"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal-server",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["code"]
        == "python /remote/workspace/.aworld/skills/browser-use/a1b2c3d4a1b2c3d4/run.py"
    )
    assert sandbox.calls == [("browser-use", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_paths_for_secondary_terminal_alias_when_canonical_exists(
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
            "digest": "1111aaaa1111aaaa",
            "entrypoint": "run.py",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal", "terminal-server"],
        mcp_config={"mcpServers": {"terminal": {}, "terminal-server": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    servers.tool_list[0]["function"]["name"] = "terminal-server__run_code"
    parameter = {"code": f"python {run_file}"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal-server",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["code"]
        == "python /remote/workspace/.aworld/skills/browser-use/1111aaaa1111aaaa/run.py"
    )
    assert sandbox.calls == [("browser-use", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_quotes_rewritten_remote_paths_with_spaces(
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
    sandbox = _FakeSandbox(mode="remote", remote_workspace_root="/remote/My Project")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    parameter = {
        "code": f"cd {skill_root} && python {run_file} && python /skills/browser-use/run.py"
    }

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["code"] == (
        "cd '/remote/My Project/.aworld/skills/browser-use/abcd1234abcd1234'"
        " && python '/remote/My Project/.aworld/skills/browser-use/abcd1234abcd1234/run.py'"
        " && python '/remote/My Project/.aworld/skills/browser-use/abcd1234abcd1234'/run.py"
    )
    assert sandbox.calls == [("browser-use", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_uses_windows_safe_quotes_for_rewritten_remote_paths(
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
    sandbox = _FakeSandbox(mode="remote", remote_workspace_root=r"C:\remote\My Project")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"browser-use": skill_config},
    )
    servers.tool_list = [_terminal_tool("run_code", "code")]
    parameter = {"code": "python /skills/browser-use/run.py"}

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["code"] == (
        'python "C:\\remote\\My Project/.aworld/skills/browser-use/abcd1234abcd1234"/run.py'
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
async def test_check_tool_params_does_not_rewrite_relative_path_without_active_skill(
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
            "digest": "deaf1234deaf1234",
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

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="run_code",
        parameter=parameter,
    )

    assert ok is True
    assert parameter["code"] == "python scripts/run.py"
    assert sandbox.calls == []


@pytest.mark.asyncio
async def test_check_tool_params_syncs_active_skill_before_relative_cd_command(
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
            "digest": "f00d1234f00d1234",
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
    parameter = {"code": "cd scripts && python run.py"}

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
        == "cd /remote/workspace/.aworld/skills/browser-use/f00d1234f00d1234/scripts"
        " && python run.py"
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
async def test_check_tool_params_rewrites_legacy_claude_alias_without_declared_path_aliases(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "html-to-image"
    skill_root.mkdir(parents=True)
    render_file = skill_root / "scripts" / "render.py"
    render_file.parent.mkdir()
    render_file.write_text("print('render')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["scripts/render.py"],
            "digest": "ddd12345ddd12345",
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
        == "python /remote/workspace/.aworld/skills/html-to-image/ddd12345ddd12345/scripts/render.py"
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
async def test_check_tool_params_keeps_default_legacy_aliases_when_skill_config_has_custom_aliases(
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
            "digest": "eeee1234eeee1234",
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
        == "python /remote/workspace/.aworld/skills/html-to-image/eeee1234eeee1234/scripts/render.py"
    )
    assert sandbox.calls == [("html-to-image", skill_config)]


@pytest.mark.asyncio
async def test_check_tool_params_rewrites_tilde_claude_skill_alias(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "lennys-podcast-newsletter"
    skill_root.mkdir(parents=True)
    search_file = skill_root / "scripts" / "lenny_search.py"
    search_file.parent.mkdir()
    search_file.write_text("print('search')\n", encoding="utf-8")

    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["scripts/lenny_search.py"],
            "digest": "tilde1234tilde1234",
        },
    }
    sandbox = _FakeSandbox(mode="remote")
    servers = McpServers(
        mcp_servers=["terminal"],
        mcp_config={"mcpServers": {"terminal": {}}},
        sandbox=sandbox,
        skill_configs={"lennys-podcast-newsletter": skill_config},
    )
    servers.tool_list = [_terminal_tool("mcp_execute_command", "command")]
    parameter = {
        "command": "SCRIPT=~/.claude/skills/lennys-podcast-newsletter/scripts/lenny_search.py && python $SCRIPT"
    }

    ok = await servers.check_tool_params(
        context=None,
        server_name="terminal",
        tool_name="mcp_execute_command",
        parameter=parameter,
    )

    assert ok is True
    assert (
        parameter["command"]
        == "SCRIPT=/remote/workspace/.aworld/skills/lennys-podcast-newsletter/tilde1234tilde1234/scripts/lenny_search.py && python $SCRIPT"
    )
    assert sandbox.calls == [("lennys-podcast-newsletter", skill_config)]


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
