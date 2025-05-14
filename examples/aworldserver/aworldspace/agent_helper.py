from pydantic import BaseModel

from aworldspace.base_agent import AworldBaseAgent

"""
Agent Space 抽象
TODO 
"""

class AgentMeta(BaseModel):
    name: str = None
    desc: str = None



class AgentSpace(BaseModel):

    agent_modules: dict
    namespace: dict

    def register(self, agent_name: str, agent_desc: str, agent_instance: AworldBaseAgent
    , metadata: dict):
        # Register agent metadata and instance
        self.agent_modules[agent_name] = agent_instance
        self.namespace[agent_name] = agent_name

    async def get_agent_modules(self):
        return self.agent_modules

    async def get_namespace(self):
        return self.namespace

# AGENT_SPACE = AgentSpace(agent_modules={}, namespace={})