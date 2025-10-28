from typing import List

from . import Neuron
from .neuron_factory import neuron_factory
from ... import ApplicationContext

SKILLS_PROMPT = """
<skills_guide>
  <skill_guide>
    To manage skills, use the 'context' tool with following actions:
    
    1. Activate a skill: 
       - action: active_skill
       - params: {{"skill_name": "skill_name_here"}}
    
    2. Offload a skill:
       - action: offload_skill  
       - params: {{"skill_name": "skill_name_here"}}
    
    Guidelines:
    - Only activate skills needed for current task
    - Offload skills when no longer needed
    - Skills are scoped to current agent namespace
  </skill_guide>
  <skills_info>
  {skills}
  </skills_info>
</skills_guide>
"""


@neuron_factory.register(name="skills", desc="skills neuron", prio=2)
class SkillsNeuron(Neuron):
    """Neuron for handling plan related properties"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        active_skills = await context.get_active_skills(namespace)
        if not active_skills:
            return []
        items = []
        # TODO @kevin
        for skill in active_skills:
            items.append(
                f"  <skill id=\"{skill}\">\n"
                f"    <skill_name>{skill}</skill_name>\n"
                f"    <skill_desc>{skill}</skill_desc>\n"
                f"    <skill_usage>{skill}</skill_usage>\n"
                f"    <skill_path>{skill}</skill_path>\n"
                f"  </skill>")

        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
         return SKILLS_PROMPT.format(skills="\n".join(items))


