from __future__ import annotations

from pathlib import Path

import pytest

import aworld_cli.executors.file_parse_hook as file_parse_hook_module
from aworld.core.event.base import Message
from aworld.skills.filesystem_provider import FilesystemSkillProvider


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_filex_pdf_skill_is_discoverable_with_remote_execution_asset() -> None:
    provider = FilesystemSkillProvider("repo", REPO_ROOT / "aworld-skills")
    descriptor = next(
        item for item in provider.list_descriptors() if item.skill_name == "filex-pdf"
    )
    content = provider.load_content(descriptor.skill_id)

    assert descriptor.display_name == "filex-pdf"
    assert "PDF" in descriptor.description
    assert "scripts/parse_pdf.py" in descriptor.execution_assets["relative_paths"]
    assert "/skills/filex-pdf/scripts/parse_pdf.py" in content.usage


@pytest.mark.asyncio
async def test_file_parse_hook_keeps_pdf_reference_for_document_skill(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pdf_path = tmp_path / "input.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\nminimal fixture\n")
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
        headers={"user_message": "Summarize @input.pdf", "console": None},
    )

    result = await file_parse_hook_module.FileParseHook().exec(message, context=context)

    assert result.headers["user_message"] == "Summarize @input.pdf"
    assert result.headers["task_content"] == "Summarize @input.pdf"
    assert any("Deferred PDF" in event for event in events)
