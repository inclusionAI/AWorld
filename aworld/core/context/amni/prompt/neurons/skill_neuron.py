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
    - only support skills_info internal skills
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
        total_skills = await context.get_skill_list(namespace)
        if not total_skills:
            return []
        items = []
        for skill_id, skill in total_skills.items():
            items.append(
                f"  <skill id=\"{skill_id}\" status=\"{skill.get('active', False)}\">\n"
                f"    <skill_name>{skill['name']}</skill_name>\n"
                f"    <skill_name>{skill['desc']}</skill_name>\n"
                f"  </skill>")

        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        if not items:
            return ""
        return SKILLS_PROMPT.format(skills="\n".join(items))


