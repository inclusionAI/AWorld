from unittest.mock import AsyncMock

import pytest

from aworld.core.context.amni.services.knowledge_service import KnowledgeService
from aworld.output import Artifact, ArtifactType


class _FakeContext:
    def __init__(self) -> None:
        self._workspace = None
        self.task_id = "task-1"
        self.session_id = "session-1"

    def _get_working_state(self, namespace="default"):
        return None


@pytest.mark.asyncio
async def test_offload_by_workspace_compacts_single_text_artifact_instead_of_returning_raw_content():
    service = KnowledgeService(_FakeContext())
    service.add_knowledge_list = AsyncMock()
    raw_content = "HEADER\n" + ("A" * 32000) + "\nFOOTER"
    artifact = Artifact(
        artifact_id="artifact-1",
        artifact_type=ArtifactType.TEXT,
        content=raw_content,
        metadata={
            "origin_tool_name": "terminal",
            "origin_action_name": "exec",
        },
    )

    offloaded = await service.offload_by_workspace([artifact], biz_id="call-1")

    service.add_knowledge_list.assert_awaited_once()
    assert artifact.metadata["biz_id"] == "call-1"
    assert offloaded != raw_content
    assert len(offloaded) < len(raw_content)
    assert "artifact-1" in offloaded
    assert "Tool output compacted for context reuse." in offloaded
    assert "terminal" in offloaded
    assert "exec" in offloaded
    assert "get_knowledge_by_id(knowledge_id)" in offloaded
    assert "grep_knowledge(knowledge_id, pattern)" in offloaded
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in offloaded
    assert "HEADER" in offloaded
    assert "FOOTER" in offloaded


@pytest.mark.asyncio
async def test_offload_by_workspace_prefers_existing_artifact_summary():
    service = KnowledgeService(_FakeContext())
    service.add_knowledge_list = AsyncMock()
    artifact = Artifact(
        artifact_id="artifact-2",
        artifact_type=ArtifactType.TEXT,
        content="X" * 5000,
        metadata={
            "summary": "Command completed and wrote the requested file successfully.",
            "origin_tool_name": "terminal",
            "origin_action_name": "exec",
        },
    )

    offloaded = await service.offload_by_workspace([artifact], biz_id="call-2")

    assert "Command completed and wrote the requested file successfully." in offloaded
    assert "Tool output compacted for context reuse." not in offloaded
    assert "get_knowledge_by_id(knowledge_id)" in offloaded
    assert "grep_knowledge(knowledge_id, pattern)" in offloaded
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in offloaded


class _FakeWorkspace:
    def __init__(self, artifacts):
        self._artifacts = artifacts

    def _load_workspace_data(self, load_artifact_content=False):
        return None

    async def query_artifacts(self, search_filter=None):
        return self._artifacts


@pytest.mark.asyncio
async def test_get_actions_info_guides_agents_to_supported_knowledge_tools():
    context = _FakeContext()
    context._workspace = _FakeWorkspace([
        Artifact(
            artifact_id="artifact-3",
            artifact_type=ArtifactType.TEXT,
            content="example",
            metadata={
                "context_type": "actions_info",
                "task_id": context.task_id,
                "summary": "Summarized tool result.",
            },
        )
    ])
    context._ensure_workspace = AsyncMock(return_value=context._workspace)

    service = KnowledgeService(context)

    actions_info = await service.get_actions_info()

    assert "artifact-3" in actions_info
    assert "list_knowledge_info(limit, offset)" in actions_info
    assert "get_knowledge_by_id(knowledge_id)" in actions_info
    assert "grep_knowledge(knowledge_id, pattern)" in actions_info
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in actions_info
    assert "get_knowledge(knowledge_id_xxx)" not in actions_info
