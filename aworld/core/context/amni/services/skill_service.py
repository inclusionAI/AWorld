# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Skill Service - Manages agent skills and skill operations.

This service abstracts skill management operations from ApplicationContext,
providing a clean interface for initializing, activating, deactivating, and querying skills.
"""
import abc
from typing import Dict, Any, List, Optional

from aworld.config import AgentConfig
from aworld.core.context.amni.config import ContextEnvConfig

SKILL_LIST_KEY = "skill_list"
ACTIVE_SKILLS_KEY = "active_skills"
SKILL_CONTENT_CACHE_KEY = "skill_content_cache"
SKILL_CONTENT_STAGING_KEY = "skill_content_staging"


class ISkillService(abc.ABC):
    """Interface for skill management operations."""
    
    @abc.abstractmethod
    async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str) -> None:
        """
        Initialize skill list from agent configuration.
        
        Args:
            skill_list: Dictionary of skill configurations
            namespace: Namespace (agent_id) for the skills
        """
        pass
    
    @abc.abstractmethod
    async def active_skill(self, skill_name: str, namespace: str) -> str:
        """
        Activate a skill for the given namespace.
        
        Args:
            skill_name: Name of the skill to activate
            namespace: Namespace (agent_id) for the skill
            
        Returns:
            Status message indicating activation result
        """
        pass
    
    @abc.abstractmethod
    async def offload_skill(self, skill_name: str, namespace: str) -> str:
        """
        Deactivate/offload a skill for the given namespace.
        
        Args:
            skill_name: Name of the skill to offload
            namespace: Namespace (agent_id) for the skill
            
        Returns:
            Status message indicating offload result
        """
        pass
    
    @abc.abstractmethod
    async def get_active_skills(self, namespace: str) -> List[str]:
        """
        Get list of currently active skills for the namespace.
        
        Args:
            namespace: Namespace (agent_id) to query
            
        Returns:
            List of active skill names
        """
        pass
    
    @abc.abstractmethod
    async def get_skill_list(self, namespace: str) -> Dict[str, Any]:
        """
        Get the complete skill list for the namespace.
        
        Args:
            namespace: Namespace (agent_id) to query
            
        Returns:
            Dictionary of skill configurations
        """
        pass
    
    @abc.abstractmethod
    async def get_skill(self, skill_name: str, namespace: str) -> Dict[str, Any]:
        """
        Get a specific skill configuration.
        
        Args:
            skill_name: Name of the skill
            namespace: Namespace (agent_id) to query
            
        Returns:
            Skill configuration dictionary, or empty dict if not found
        """
        pass
    
    @abc.abstractmethod
    async def get_skill_name_list(self, namespace: str) -> List[str]:
        """
        Get list of all available skill names for the namespace.
        
        Args:
            namespace: Namespace (agent_id) to query
            
        Returns:
            List of skill names
        """
        pass
    
    @abc.abstractmethod
    async def load_skill_agent_mcp_config(self, skill_agent: str) -> Dict[str, Any]:
        """
        Load MCP configuration for a skill agent.
        
        Args:
            skill_agent: Name of the skill agent
            
        Returns:
            MCP configuration dictionary
        """
        pass


class SkillService(ISkillService):
    """
    Skill Service implementation.
    
    Manages agent skills including initialization, activation, deactivation, and querying.
    Handles skill agent creation and integration with swarm.
    """
    
    def __init__(self, context):
        """
        Initialize SkillService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides access to context state and swarm
        """
        self._context = context

    def _split_skill_config(
        self, skill_config: Dict[str, Any]
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        descriptor = dict(skill_config)
        content = {
            "usage": skill_config.get("usage", ""),
            "tool_list": dict(skill_config.get("tool_list", {}) or {}),
        }
        descriptor["usage"] = ""
        descriptor["tool_list"] = {}
        return descriptor, content

    def _get_skill_descriptors(self, namespace: str) -> Dict[str, Any]:
        return self._context.get(SKILL_LIST_KEY, namespace=namespace) or {}

    def _get_loaded_content_cache(self, namespace: str) -> Dict[str, Any]:
        return self._context.get(SKILL_CONTENT_CACHE_KEY, namespace=namespace) or {}

    def _get_staged_content(self, namespace: str) -> Dict[str, Any]:
        return self._context.get(SKILL_CONTENT_STAGING_KEY, namespace=namespace) or {}

    def _merge_skill_config(
        self,
        descriptor: Dict[str, Any],
        content: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        merged = dict(descriptor)
        if content:
            merged.update(content)
        return merged

    def _promote_skill_content(self, skill_name: str, namespace: str) -> Dict[str, Any]:
        loaded = dict(self._get_loaded_content_cache(namespace))
        if skill_name in loaded:
            return loaded[skill_name]

        staged = dict(self._get_staged_content(namespace))
        content = dict(staged.get(skill_name, {}))
        if not content:
            return {}

        loaded[skill_name] = content
        self._context.put(SKILL_CONTENT_CACHE_KEY, loaded, namespace=namespace)
        return content
    
    async def init_skill_list(self, skill_list: Dict[str, Any], namespace: str) -> None:
        """Initialize skill list from agent configuration."""
        descriptors: Dict[str, Any] = {}
        staged_content: Dict[str, Any] = {}
        for skill_name, skill_config in skill_list.items():
            descriptor, content = self._split_skill_config(skill_config)
            descriptors[skill_name] = descriptor
            staged_content[skill_name] = content

        self._context.put(SKILL_LIST_KEY, descriptors, namespace=namespace)
        self._context.put(SKILL_CONTENT_CACHE_KEY, {}, namespace=namespace)
        self._context.put(SKILL_CONTENT_STAGING_KEY, staged_content, namespace=namespace)
        for skill_name, skill_config in skill_list.items():
            if skill_config.get('active', False):
                await self.active_skill(skill_name, namespace)
    
    async def active_skill(self, skill_name: str, namespace: str) -> str:
        """Activate a skill for the given namespace."""
        if not skill_name:
            return "skill name is required"
        
        agent_skills = await self.get_skill_name_list(namespace)
        if skill_name not in agent_skills:
            return "skill not found"
        
        activate_skills = await self.get_active_skills(namespace)
        if not activate_skills:
            activate_skills = []
        if skill_name in activate_skills:
            return f"skill {skill_name} already activated, current skills: {activate_skills}"
        
        activate_skills.append(skill_name)
        skill = await self.get_skill(skill_name=skill_name, namespace=namespace)
        
        # Handle agent-type skills
        if skill.get('type') == "agent":
            skill_name = skill.get('name')
            agent_type = skill.get('agent_type', 'aworld.agents.llm_agent.Agent')
            if agent_type == "aworld.agents.llm_agent.Agent":
                from aworld.agents.llm_agent import Agent
                orchestrator_agent = self._context.swarm.agents.get(namespace)
                agent_config = AgentConfig(
                    llm_config=orchestrator_agent.conf.llm_config,
                    use_vision=False
                )
                skill_agent = Agent(
                    name=skill.get('name'),
                    desc=skill.get('description'),
                    conf=agent_config,
                    system_prompt=skill.get('usage', ''),
                    mcp_servers=list(skill.get('tool_list').keys()),
                    mcp_config=orchestrator_agent.mcp_config
                )
                self._context.swarm.add_agents([skill_agent])
                orchestrator_agent.handoffs.append(skill_agent.id())
            else:
                raise Exception(f"agent type {agent_type} not supported")
        
        self._context.put(ACTIVE_SKILLS_KEY, activate_skills, namespace=namespace)
        return (f"skill {skill_name} activated, current skills: {activate_skills} \n\n"
                f"<skill_guide>{skill.get('usage', '')}</skill_guide>\n\n"
                f"<skill_path>{skill.get('skill_path', '')}</skill_path>\n\n")
    
    async def offload_skill(self, skill_name: str, namespace: str) -> str:
        """Deactivate/offload a skill for the given namespace."""
        skills = await self.get_active_skills(namespace)
        if not skills or skill_name not in skills:
            return f"skill {skill_name} not found, current skills: {skills}"
        skills.remove(skill_name)
        self._context.put(ACTIVE_SKILLS_KEY, skills, namespace=namespace)
        return f"skill {skill_name} offloaded, current skills: {skills}"
    
    async def get_active_skills(self, namespace: str) -> List[str]:
        """Get list of currently active skills for the namespace."""
        skills = self._context.get(ACTIVE_SKILLS_KEY, namespace=namespace)
        if not skills:
            skills = []
        return skills
    
    async def get_skill_list(self, namespace: str) -> Dict[str, Any]:
        """Get the complete skill list for the namespace."""
        descriptors = self._get_skill_descriptors(namespace)
        loaded = self._get_loaded_content_cache(namespace)
        return {
            skill_name: self._merge_skill_config(
                descriptor,
                loaded.get(skill_name),
            )
            for skill_name, descriptor in descriptors.items()
        }
    
    async def get_skill(self, skill_name: str, namespace: str) -> Dict[str, Any]:
        """Get a specific skill configuration."""
        descriptors = self._get_skill_descriptors(namespace)
        if not descriptors:
            return {}
        descriptor = descriptors.get(skill_name)
        if not descriptor:
            return {}
        content = self._promote_skill_content(skill_name, namespace)
        return self._merge_skill_config(descriptor, content)
    
    async def get_skill_name_list(self, namespace: str) -> List[str]:
        """Get list of all available skill names for the namespace."""
        agent_skills = self._get_skill_descriptors(namespace)
        skill_names = []
        if not agent_skills:
            return []
        for skill_name, skill_config in agent_skills.items():
            skill_names.append(skill_name)
        return skill_names
    
    async def load_skill_agent_mcp_config(self, skill_agent: str) -> Dict[str, Any]:
        """Load MCP configuration for a skill agent."""
        env_config_obj = await self._context.get_env_config(skill_agent)
        mcp_config_path = env_config_obj.env_config.get('MCP_CONFIG_PATH')
        if mcp_config_path:
            with open(mcp_config_path, 'r') as f:
                import json
                return json.load(f)
        return {}
