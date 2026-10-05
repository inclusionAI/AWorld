from types import SimpleNamespace

import pytest

from aworld.core.context.amni.prompt.neurons.action_info_neuron import ActionInfoNeuron
from aworld.output.artifact import Artifact, ArtifactType
from aworld.output.workspace import WorkSpace


class _FakeContext:
    def get_config(self):
        return SimpleNamespace(debug_mode=False)


@pytest.mark.asyncio
async def test_action_info_neuron_uses_supported_knowledge_tool_names():
    neuron = ActionInfoNeuron()

    result = await neuron.format(
        _FakeContext(),
        items=["  <knowledge id='artifact-1' summary='preview'></knowledge>\n"],
    )

    assert "artifact-1" in result
    assert "list_knowledge_info(limit, offset)" in result
    assert "get_knowledge_by_id(knowledge_id)" in result
    assert "grep_knowledge(knowledge_id, pattern)" in result
    assert "get_knowledge_by_lines(knowledge_id, start_line, end_line)" in result
    assert "get_knowledge(knowledge_id_xxx)" not in result


@pytest.mark.asyncio
async def test_action_info_neuron_supports_base_workspace(tmp_path):
    workspace = WorkSpace(
        workspace_id="base-workspace",
        storage_path=str(tmp_path),
        clear_existing=True,
        use_default_observer=False,
    )
    artifacts = [
        Artifact(
            artifact_id="action-1",
            artifact_type=ArtifactType.TEXT,
            content="bounded action record",
            metadata={"context_type": "actions_info", "summary": "preview"},
        ),
        Artifact(
            artifact_id="other-1",
            artifact_type=ArtifactType.TEXT,
            content="other record",
            metadata={"context_type": "other", "summary": "ignore"},
        ),
    ]
    for artifact in artifacts:
        await workspace.add_artifact(artifact)

    class Context(_FakeContext):
        async def _ensure_workspace(self):
            return workspace

    items = await ActionInfoNeuron().format_items(Context())

    assert items == [
        "  <knowledge id='action-1' summary='preview'></knowledge>\n"
    ]
