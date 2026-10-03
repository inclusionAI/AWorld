"""Skills supply instructions/tools; file metadata needs only explicit YAML loading."""

import importlib.util
from pathlib import Path
import tempfile
import unittest

from aworld.core.agent import Agent, Skill, load_skills
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.session import create_session
from aworld.core.tool import default_tools


class SkillTests(unittest.IsolatedAsyncioTestCase):
    async def test_file_skill_is_advertised_then_loaded_by_ordinary_read(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "SKILL.md"
            file.write_text("# Workflow\nOnly read this body when needed.\n")
            class Model:
                def __init__(self):
                    self.requests = []
                async def complete(self, request):
                    self.requests.append(request)
                    if len(self.requests) == 1:
                        return AssistantMessage(tool_calls=(ToolCall("load", "read", {"path": str(file)}),))
                    return AssistantMessage("done")
            model = Model()
            session = await create_session(agent=Agent(model=model, tools=default_tools(directory), skills=[Skill("workflow", "Use for workflow tasks", location=str(file))]))
            await (await session.submit("workflow")).result()
            self.assertIn(str(file), model.requests[0].system_prompt)
            self.assertNotIn("Only read this body", model.requests[0].system_prompt)
            self.assertIn("Only read this body", model.requests[1].messages[-1].content["content"])

    async def test_skill_validation_rejects_conflicts_and_missing_read_capability(self):
        class Model:
            async def complete(self, request):
                return AssistantMessage("done")
        with self.assertRaises(ValueError):
            Agent(model=Model(), skills=[Skill("same", "first"), Skill("same", "second")])
        with self.assertRaises(LookupError):
            Agent(model=Model(), tools=[], skills=[Skill("file", "Read instructions", location="SKILL.md")])

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "Explicit SKILL.md loader requires optional PyYAML")
    async def test_explicit_skill_roots_frontmatter_and_collision(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first"
            second = Path(directory) / "second"
            first.mkdir()
            second.mkdir()
            (first / "SKILL.md").write_text("---\nname: first\ndescription: |\n  Workflow instructions\n---\nBODY\n")
            skills = load_skills(directory)
            self.assertEqual(skills[0].name, "first")
            self.assertEqual(skills[0].instructions, "")
            self.assertEqual(skills[0].location, str((first / "SKILL.md").resolve()))
            (second / "SKILL.md").write_text("---\nname: first\ndescription: Duplicate\n---\n")
            with self.assertRaises(ValueError):
                load_skills(directory)
