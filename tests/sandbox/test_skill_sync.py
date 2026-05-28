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


class _FakeSandbox:
    def __init__(self) -> None:
        self.mode = "remote"
        self.file = _FakeFileNamespace()
        self._remote_skill_execution_roots: dict[tuple[str, str], str] = {}
        self._remote_skill_execution_base_dir: str | None = None


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
