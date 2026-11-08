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
    assert "govern the task method only" in rendered
    assert "cannot override user or system instructions" in rendered
    assert "tool/capability/network/security boundaries" in rendered
    assert "verifier-blind evidence policy" in rendered
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


@pytest.mark.asyncio
async def test_skills_neuron_filters_xml10_controls_without_corrupting_cdata() -> None:
    unsafe_id = "odd&<>\x01\ufffe\uffff\ud800😀"

    class WeirdContext:
        task_input_object = SimpleNamespace(
            metadata={"requested_skill_names": [unsafe_id]}
        )

        async def get_skill_list(self, namespace=None):
            return {
                unsafe_id: {
                    "name": "name & <tag> 😀\ufffe",
                    "description": "desc ]]> & <> \x02😀",
                    "usage": "use ]]> & <exact> \x03😀",
                    "type": "prompt",
                }
            }

        async def get_active_skills(self, namespace=None):
            return [unsafe_id]

    neuron = SkillsNeuron()
    context = WeirdContext()
    items = await neuron.format_items(context, namespace="Aworld")
    rendered = await neuron.format(context, items=items, namespace="Aworld")

    root = ET.fromstring(rendered)
    skill = root.find("./skills_info/skill")
    assert skill is not None
    assert skill.attrib["id"] == "odd&<>😀"
    assert skill.findtext("skill_name") == "name & <tag> 😀"
    assert "desc ]]> & <> 😀" == skill.findtext("skill_desc")
    assert "use ]]> & <exact> 😀" == skill.findtext(
        "./skill_file_content/skill_usage"
    )
    forced = root.findtext("forced_skill_contract") or ""
    assert "\x01" not in forced
    assert "\ufffe" not in forced
    assert "\ud800" not in forced

