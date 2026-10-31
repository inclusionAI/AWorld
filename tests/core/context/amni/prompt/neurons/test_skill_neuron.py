from types import SimpleNamespace
import xml.etree.ElementTree as ET

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
    assert (
        "<skill_usage><![CDATA[Use the planning workflow]]></skill_usage>"
        in rendered
    )


@pytest.mark.asyncio
async def test_skills_neuron_names_model_visible_tools_and_emits_well_formed_xml() -> None:
    neuron = SkillsNeuron()
    context = _FakeContext()

    items = await neuron.format_items(context, namespace="Aworld")
    rendered = await neuron.format(context, items=items, namespace="Aworld")

    assert "SKILL__active_skill" in rendered
    assert "SKILL__offload_skill" in rendered
    assert "use the 'context' tool" not in rendered
    root = ET.fromstring(rendered)
    assert root.tag == "skills_guide"
    active = root.find("./skills_info/skill[@id='writing-plans']")
    inactive = root.find("./skills_info/skill[@id='browser-use']")
    assert active is not None
    assert inactive is not None
    assert active.findtext("./skill_file_content/skill_usage") == (
        "Use the planning workflow"
    )
    assert inactive.find("./skill_file_content/skill_usage") is None

