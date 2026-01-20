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
    - do not repeat activate or load skills if active status is True <skill id=\"xxx\" active_status="True">
    - If <skill_file_content> already contains <skill_usage> content, do NOT read skill.md file again 
  </skill_guide>
  <skills_info>
  {skills}
  </skills_info>
</skills_guide>
"""

SKILL_NEURON_NAME = "skills"
@neuron_factory.register(name=SKILL_NEURON_NAME, desc="skills neuron", prio=8)
class SkillsNeuron(Neuron):
    """Neuron for handling plan related properties"""

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        total_skills = await context.get_skill_list(namespace)
        if not total_skills:
            return []
        # Get actually activated skills from context
        active_skills = await context.get_active_skills(namespace)
        active_skills_set = set(active_skills) if active_skills else set()
        
        items = []
        for skill_id, skill in total_skills.items():
            # Check if skill is actually activated (in ACTIVE_SKILLS_KEY), not just in skill config
            is_active = skill_id in active_skills_set
            # Include skill_usage content if skill is active and not an agent-type skill
            skill_usage = f"    <skill_usage>{skill.get('usage', '')}</skill_usage>\n" if is_active and skill.get("type") != "agent" else ""
            items.append(
                f"  <skill id=\"{skill_id}\" active_status=\"{is_active}\">\n"
                f"    <skill_name>{skill.get('name')}</skill_name>\n"
                f"    <skill_desc>{skill.get('description', skill.get('desc'))}</skill_desc>\n"
                f"    <skill_file_content>"
                f"    {skill_usage}<skill_file_content>\n"
                f"  </skill>")

        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None,
                     **kwargs) -> str:
        if not items:
            return ""
        return SKILLS_PROMPT.format(skills="\n".join(items))


