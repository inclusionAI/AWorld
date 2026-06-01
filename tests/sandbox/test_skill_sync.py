from pathlib import Path

import pytest

from aworld.sandbox.skill_sync import ensure_remote_skill_assets_ready


class _FakeFileNamespace:
    def __init__(self) -> None:
        self.created: list[str] = []
        self.written: list[tuple[str, str]] = []

    async def list_allowed_directories(self):
        return {
            "success": True,
            "data": "Allowed directories:\n/remote/workspace",
            "error": None,
        }

    async def create_directory(self, path: str):
        self.created.append(path)
        return {"success": True, "data": path, "error": None}

    async def write_file(self, path: str, content: str):
        self.written.append((path, content))
        return {"success": True, "data": path, "error": None}


class _FakeTerminalNamespace:
    def __init__(self) -> None:
        self.commands: list[str] = []

    async def run_code(self, code: str, timeout: int = 30, output_format: str = "markdown"):
        self.commands.append(code)
        return {"success": True, "data": "ok", "error": None}


class _FailingWriteFileNamespace(_FakeFileNamespace):
    def __init__(self, fail_path_suffix: str) -> None:
        super().__init__()
        self.fail_path_suffix = fail_path_suffix

    async def write_file(self, path: str, content: str):
        if path.endswith(self.fail_path_suffix):
            return {"success": False, "data": None, "error": f"failed: {path}"}
        return await super().write_file(path, content)


class _FakeSandbox:
    def __init__(self) -> None:
        self.mode = "remote"
        self.file = _FakeFileNamespace()
        self.terminal = _FakeTerminalNamespace()
        self._remote_skill_execution_roots: dict[tuple[str, str], str] = {}
        self._remote_skill_execution_base_dir: str | None = None


class _FailingWriteSandbox(_FakeSandbox):
    def __init__(self, fail_path_suffix: str) -> None:
        super().__init__()
        self.file = _FailingWriteFileNamespace(fail_path_suffix)


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_writes_manifest_files(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "run.py").write_text("print('hi')\n", encoding="utf-8")
    (skill_root / "config.json").write_text('{"debug": true}\n', encoding="utf-8")

    sandbox = _FakeSandbox()
    remote_root = await ensure_remote_skill_assets_ready(
        sandbox,
        "browser-use",
        {
            "asset_root": str(skill_root),
            "execution_assets": {
                "enabled": True,
                "relative_paths": ["config.json", "run.py"],
                "digest": "1234abcd5678ef00",
            },
        },
    )

    assert remote_root == "/remote/workspace/.aworld/skills/browser-use/1234abcd5678ef00"
    assert sandbox.file.created == [remote_root]
    assert sandbox.file.written == [
        (
            f"{remote_root}/config.json",
            '{"debug": true}\n',
        ),
        (
            f"{remote_root}/run.py",
            "print('hi')\n",
        ),
    ]


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_reuses_cached_root(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "run.sh").write_text("echo hi\n", encoding="utf-8")

    sandbox = _FakeSandbox()
    skill_config = {
        "asset_root": str(skill_root),
        "execution_assets": {
            "enabled": True,
            "relative_paths": ["run.sh"],
            "digest": "abcd1234abcd1234",
        },
    }

    first = await ensure_remote_skill_assets_ready(sandbox, "browser-use", skill_config)
    second = await ensure_remote_skill_assets_ready(sandbox, "browser-use", skill_config)

    assert first == second
    assert sandbox.file.created == [first]
    assert sandbox.file.written == [(f"{first}/run.sh", "echo hi\n")]


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_excludes_understanding_assets(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text("---\n---\n", encoding="utf-8")
    (skill_root / "scripts").mkdir()
    (skill_root / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    sandbox = _FakeSandbox()
    remote_root = await ensure_remote_skill_assets_ready(
        sandbox,
        "browser-use",
        {
            "asset_root": str(skill_root),
            "execution_assets": {
                "enabled": True,
                "relative_paths": ["scripts/run.py"],
                "digest": "bead1234bead1234",
            },
        },
    )

    assert sandbox.file.written == [
        (
            f"{remote_root}/scripts/run.py",
            "print('hi')\n",
        )
    ]


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_rejects_non_utf8_assets(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "payload.bin").write_bytes(b"\xff\xfe\x00\x01")

    sandbox = _FakeSandbox()

    with pytest.raises(RuntimeError, match="not UTF-8 text: payload.bin"):
        await ensure_remote_skill_assets_ready(
            sandbox,
            "browser-use",
            {
                "asset_root": str(skill_root),
                "execution_assets": {
                    "enabled": True,
                    "relative_paths": ["payload.bin"],
                    "digest": "deadbeefdeadbeef",
                },
            },
        )


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_fails_on_partial_remote_write(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    (skill_root / "config.json").write_text('{"debug": true}\n', encoding="utf-8")
    (skill_root / "scripts").mkdir()
    (skill_root / "scripts" / "run.py").write_text("print('hi')\n", encoding="utf-8")

    sandbox = _FailingWriteSandbox("scripts/run.py")

    with pytest.raises(RuntimeError, match="write remote execution asset 'scripts/run.py'"):
        await ensure_remote_skill_assets_ready(
            sandbox,
            "browser-use",
            {
                "asset_root": str(skill_root),
                "execution_assets": {
                    "enabled": True,
                    "relative_paths": ["config.json", "scripts/run.py"],
                    "digest": "face1234face1234",
                },
            },
        )

    assert sandbox.file.written == [
        (
            "/remote/workspace/.aworld/skills/browser-use/face1234face1234/config.json",
            '{"debug": true}\n',
        )
    ]
    assert sandbox._remote_skill_execution_roots == {}


@pytest.mark.asyncio
async def test_ensure_remote_skill_assets_ready_preserves_executable_modes(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills" / "browser-use"
    skill_root.mkdir(parents=True)
    script = skill_root / "scripts" / "run.sh"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\necho hi\n", encoding="utf-8")
    script.chmod(0o755)

    sandbox = _FakeSandbox()
    remote_root = await ensure_remote_skill_assets_ready(
        sandbox,
        "browser-use",
        {
            "asset_root": str(skill_root),
            "execution_assets": {
                "enabled": True,
                "relative_paths": ["scripts/run.sh"],
                "digest": "bada55bada55bada",
            },
        },
    )

    assert sandbox.file.written == [
        (
            f"{remote_root}/scripts/run.sh",
            "#!/bin/sh\necho hi\n",
        )
    ]
    assert sandbox.terminal.commands == [
        f"chmod 755 {remote_root}/scripts/run.sh"
    ]
