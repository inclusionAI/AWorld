from types import SimpleNamespace

import pytest

from aworld.core.context.amni.prompt.neurons.skill_neuron import SkillsNeuron


class _FakeContext:
    def __init__(self) -> None:
        self.task_input_object = SimpleNamespace(
            metadata={"requested_skill_names": ["writing-plans"]}
        )

    async def get_skill_list(self, namespace: str | None = None):
        return {
            "writing-plans": {
                "name": "writing-plans",
                "description": "Create implementation plans",
                "usage": "Use the planning workflow",
                "type": "prompt",
            },
            "browser-use": {
                "name": "browser-use",
                "description": "Browser automation",
                "usage": "",
                "type": "prompt",
            },
        }

    async def get_active_skills(self, namespace: str | None = None):
        return ["writing-plans"]


@pytest.mark.asyncio
async def test_skills_neuron_includes_forced_skill_contract() -> None:
    neuron = SkillsNeuron()
    context = _FakeContext()

    items = await neuron.format_items(context, namespace="Aworld")
    rendered = await neuron.format(context, items=items, namespace="Aworld")

    assert "The user explicitly requested these skill(s) for this task: writing-plans." in rendered
    assert '<skill id="writing-plans" active_status="True">' in rendered
    assert "<skill_usage>Use the planning workflow</skill_usage>" in rendered

