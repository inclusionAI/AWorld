from __future__ import annotations

from pathlib import Path

import pytest

import aworld_cli.executors.file_parse_hook as file_parse_hook_module
from aworld.core.event.base import Message
from aworld.skills.filesystem_provider import FilesystemSkillProvider


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_filex_skill_is_discoverable_with_remote_execution_asset() -> None:
    provider = FilesystemSkillProvider("repo", REPO_ROOT / "aworld-skills")
    descriptor = next(item for item in provider.list_descriptors() if item.skill_name == "filex")
    content = provider.load_content(descriptor.skill_id)

    assert descriptor.display_name == "filex"
    assert "audio" in descriptor.description
    assert "scripts/filex.py" in descriptor.execution_assets["relative_paths"]
    assert "/skills/filex/scripts/filex.py" in content.usage


def test_filex_skill_documents_adapted_workflow_and_runtime_boundaries() -> None:
    provider = FilesystemSkillProvider("repo", REPO_ROOT / "aworld-skills")
    descriptor = next(item for item in provider.list_descriptors() if item.skill_name == "filex")
    usage = provider.load_content(descriptor.skill_id).usage

    assert "FILEX_WORKSPACE_ROOT" in usage
    assert "AWorld runtime supplies the selected FileX VLM" in usage
    assert "`gateway_vllm` object in `--env-file`" in usage
    assert "weaken reproducibility" in usage
    assert "`output_path`" in usage
    assert "`unlimited_ocr` and `page_images` are not providers" in usage
    assert "Follow the parsing workflow" in usage
    assert "Do not change into `/app/mcp_servers/filesystem_server`" in usage
    assert "use `/root/fs_workspace`" in usage
    assert "file-id" not in usage
    assert "AFTS" not in usage


def test_filex_skill_examples_use_the_managed_workspace_contract() -> None:
    provider = FilesystemSkillProvider("repo", REPO_ROOT / "aworld-skills")
    descriptor = next(item for item in provider.list_descriptors() if item.skill_name == "filex")
    usage = provider.load_content(descriptor.skill_id).usage

    assert "--input /workspace/input.docx" in usage
    assert "--input /workspace/report.pdf" in usage
    assert "--input /root/workspace/" not in usage
    assert "managed artifact-producing runtime may set `FILEX_LAYOUT_FORMAT=parse-output`" in usage
    assert "PDF already defaults to Paddle" not in usage


@pytest.mark.asyncio
@pytest.mark.parametrize("file_name", ["input.pdf", "input.docx", "input.mp3"])
async def test_file_parse_hook_keeps_binary_reference_for_filex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    file_name: str,
) -> None:
    file_path = tmp_path / file_name
    file_path.write_bytes(b"binary fixture")
    events: list[str] = []

    class DummyApplicationContext:
        workspace_path = str(tmp_path)

    monkeypatch.setattr(file_parse_hook_module, "ApplicationContext", DummyApplicationContext)
    context = DummyApplicationContext()
    context._aworld_cli_status_sink = events.append
    message = Message(
        category="agent_hook",
        payload={},
        sender="user",
        headers={"user_message": f"Inspect @{file_name}", "console": None},
    )

    result = await file_parse_hook_module.FileParseHook().exec(message, context=context)

    assert result.headers["user_message"] == f"Inspect @{file_name}"
    assert result.headers["task_content"] == f"Inspect @{file_name}"
    assert any("Deferred binary file to FileX" in event for event in events)
