# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_AGENT_REGISTRY = "CONTEXT_AGENT_REGISTRY"


class ContextAgentRegistryAction(ToolAction):
    """Agent Registry Support. Definition of Context agent registry operations."""

    SAVE_AS_SOURCE = ToolActionInfo(
        name="save_as_source",
        input_params={
            "content": ParamInfo(
                name="content",
                type="string",
                required=True,
                desc="The markdown configuration content to save"
            ),
            "name": ParamInfo(
                name="name",
                type="string",
                required=True,
                desc="The name of the resource to save"
            )
        },
        desc="Save configuration as markdown file to the registry"
    )

    LOAD_AS_SOURCE = ToolActionInfo(
        name="load_as_source",
        input_params={
            "agent_name": ParamInfo(
                name="agent_name",
                type="string",
                required=True,
                desc="The name of the agent to retrieve"
            ),
            "version": ParamInfo(
                name="version",
                type="string",
                required=False,
                desc="The version of the agent to retrieve (optional, defaults to latest)"
            )
        },
        desc="Retrieve agent configuration as markdown content from the registry"
    )

    LIST_AS_SOURCE = ToolActionInfo(
        name="list_as_source",
        input_params={},
        desc="List all available resources in the registry"
    )

    LIST_DESC = ToolActionInfo(
        name="list_desc",
        input_params={},
        desc="List all available resources with their descriptions in the registry"
    )

    DYNAMIC_REGISTER = ToolActionInfo(
        name="dynamic_register",
        input_params={
            "local_agent_name": ParamInfo(
                name="local_agent_name",
                type="string",
                required=True,
                desc="The name of the local agent in LocalAgentRegistry"
            ),
            "register_agent_name": ParamInfo(
                name="register_agent_name",
                type="string",
                required=True,
                desc="The name of the agent in AgentVersionControlRegistry to register"
            )
        },
        desc="Dynamically register an agent from AgentVersionControlRegistry to a local agent's team_swarm"
    )


async def dynamic_register(local_agent_name: str, register_agent_name: str, context=None) -> bool:
    """
    Dynamically register an agent from AgentVersionControlRegistry to a local agent's team_swarm.
    
    Args:
        local_agent_name: Name of the local agent in LocalAgentRegistry
        register_agent_name: Name of the agent in AgentVersionControlRegistry to register
        context: Optional context for AgentVersionControlRegistry (if None, uses default context)
    
    Returns:
        True if registration successful, False otherwise
    
    Raises:
        ValueError: With detailed error message if any step fails
    
    Process:
        1. Get local_agent_name from LocalAgentRegistry
        2. Read its team_swarm
        3. Get latest version of register_agent_name from AgentVersionControlRegistry
        4. Add register_agent_name to local_agent_name's team_swarm
    """
    try:
        from aworld_cli.core.agent_registry import LocalAgentRegistry
        from aworld.experimental.registry_workspace.agent_version_control_registry import AgentVersionControlRegistry
        
        # Step 1: Get local_agent_name from LocalAgentRegistry
        local_agent = LocalAgentRegistry.get_agent(local_agent_name)
        logger.info(f"local_agent: {local_agent}")
        if not local_agent:
            # Get available agent names for better error message
            available_agents = LocalAgentRegistry.list_agent_names()
            available_list = ", ".join(available_agents) if available_agents else "none"
            error_msg = (
                f"Local agent '{local_agent_name}' not found in LocalAgentRegistry. "
                f"Available agents: {available_list}. "
                f"Please check the agent name and ensure it is registered in LocalAgentRegistry."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Step 2: Read its team_swarm
        swarm = await local_agent.get_swarm(context=context)
        logger.info(f"swarm: {swarm}")
        if not swarm:
            error_msg = (
                f"Failed to get swarm for local agent '{local_agent_name}'. "
                f"The agent exists but its swarm could not be initialized. "
                f"Please check the agent's swarm configuration."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Step 3: Get latest version of register_agent_name from AgentVersionControlRegistry
        if context is None:
            from aworld.experimental.registry_workspace.agent_version_control_registry import DefaultContext
            context = DefaultContext()
        
        version_control_registry = AgentVersionControlRegistry(context)
        
        # Check if agent exists in registry before loading
        available_resources = await version_control_registry.list_as_source()
        if register_agent_name not in available_resources:
            available_list = ", ".join(available_resources) if available_resources else "none"
            error_msg = (
                f"Agent '{register_agent_name}' not found in AgentVersionControlRegistry. "
                f"Available agents: {available_list}. "
                f"Please ensure the agent file exists in AGENT_REGISTRY_STORAGE_PATH "
                f"and the agent name matches the name in the @agent decorator."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        register_agent = await version_control_registry.load_agent(agent_name=register_agent_name)
        if not register_agent:
            error_msg = (
                f"Agent '{register_agent_name}' exists in registry but could not be loaded. "
                f"This may indicate a problem with the agent file (e.g., syntax error, missing dependencies, "
                f"or invalid agent definition). Please check the agent file and ensure it is valid."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        
        # Step 4: Add register_agent_name to local_agent_name's team_swarm
        swarm.add_agents([register_agent])
        
        # If swarm was a callable, update local_agent to store the modified swarm instance
        # so the changes persist. Also update the registry to ensure the change is saved.
        if callable(local_agent.swarm):
            local_agent.swarm = swarm
            # Update the registry to persist the change
            LocalAgentRegistry.get_instance().upsert(local_agent)
            logger.info(f"Updated local_agent.swarm from callable to Swarm instance in registry")
        
        logger.info(f"Successfully added agent '{register_agent_name}' to local agent '{local_agent_name}'s team_swarm")
        return True
        
    except ValueError:
        # Re-raise ValueError with detailed messages
        raise
    except Exception as e:
        error_msg = (
            f"Unexpected error in dynamic_register: {str(e)}. "
            f"local_agent_name='{local_agent_name}', register_agent_name='{register_agent_name}'. "
            f"Please check the logs for more details."
        )
        logger.error(f"Error in dynamic_register: {traceback.format_exc()}")
        raise ValueError(error_msg) from e


@ToolFactory.register(name=CONTEXT_AGENT_REGISTRY,
                      desc=CONTEXT_AGENT_REGISTRY,
                      supported_action=ContextAgentRegistryAction)
class ContextAgentRegistryTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextAgentRegistryTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.keyframes = []
        self.init()
        self.step_finished = True

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=ContextAgentRegistryAction.LIST_AS_SOURCE.value.name), {}

    def init(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        pass

    async def finished(self) -> bool:
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        self.step_finished = False
        reward = 0.
        fail_error = ""
        action_results = []
        info = {}

        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            # Get agent registry service from context
            from aworld.experimental.registry_workspace.agent_version_control_registry import AgentVersionControlRegistry

            def get_agent_registry_service() -> AgentVersionControlRegistry:
                context = message.context._context if hasattr(message.context, '_context') else message.context
                return AgentVersionControlRegistry(context)

            for action in actions:
                logger.info(f"ContextAgentRegistryTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())

                try:
                    if action_name == ContextAgentRegistryAction.SAVE_AS_SOURCE.value.name:
                        content = action.params.get("content", "")
                        name = action.params.get("name", "")

                        if not content:
                            raise ValueError("content is required")
                        if not name:
                            raise ValueError("name is required")

                        service = get_agent_registry_service()
                        success = await service.save_as_source(content=content, name=name)

                        if success:
                            action_result.success = True
                            action_result.content = f"Successfully saved resource '{name}' as markdown"
                        else:
                            raise ValueError(f"Failed to save resource '{name}'")

                    elif action_name == ContextAgentRegistryAction.LOAD_AS_SOURCE.value.name:
                        agent_name = action.params.get("agent_name", "")
                        version = action.params.get("version")

                        if not agent_name:
                            raise ValueError("agent_name is required")

                        service = get_agent_registry_service()
                        content = await service.load_as_source(name=agent_name, version=version)

                        if content:
                            action_result.success = True
                            action_result.content = f"Content: {content}"
                        else:
                            raise ValueError(f"Agent '{agent_name}' not found or no content available")

                    elif action_name == ContextAgentRegistryAction.LIST_AS_SOURCE.value.name:
                        service = get_agent_registry_service()
                        resources = await service.list_as_source()

                        action_result.success = True
                        action_result.content = f"Available resources: {', '.join(resources)}" if resources else "No resources found"

                    elif action_name == ContextAgentRegistryAction.LIST_DESC.value.name:
                        service = get_agent_registry_service()
                        resources_with_desc = await service.list_desc()

                        action_result.success = True
                        if resources_with_desc:
                            desc_lines = [f"- {name}: {desc}" for name, desc in resources_with_desc]
                            action_result.content = "Available resources with descriptions:\n" + "\n".join(desc_lines)
                        else:
                            action_result.content = "No resources found"

                    elif action_name == ContextAgentRegistryAction.DYNAMIC_REGISTER.value.name:
                        local_agent_name = action.params.get("local_agent_name", "")
                        register_agent_name = action.params.get("register_agent_name", "")

                        if not local_agent_name:
                            raise ValueError("local_agent_name is required")
                        if not register_agent_name:
                            raise ValueError("register_agent_name is required")

                        # Get context for dynamic_register
                        context = message.context._context if hasattr(message.context, '_context') else message.context
                        try:
                            success = await dynamic_register(
                                local_agent_name=local_agent_name,
                                register_agent_name=register_agent_name,
                                context=context
                            )

                            if success:
                                action_result.success = True
                                action_result.content = f"Successfully registered agent '{register_agent_name}' to local agent '{local_agent_name}'s team_swarm"
                            else:
                                raise ValueError(f"Failed to register agent '{register_agent_name}' to local agent '{local_agent_name}'")
                        except ValueError as ve:
                            # Re-raise ValueError with detailed error message
                            raise ve

                    else:
                        raise ValueError(f"Unknown action: {action_name}")

                except Exception as e:
                    action_result.success = False
                    action_result.error = str(e)
                    fail_error = str(e)
                    reward = -1.0

                action_results.append(action_result)

        except Exception as e:
            logger.error(f"ContextAgentRegistryTool|do_step error: {traceback.format_exc()}")
            fail_error = str(e)
            reward = -1.0
            # Create failed action results for all actions
            for action in actions:
                action_result = ActionResult(
                    action_name=action.action_name,
                    tool_name=self.name(),
                    success=False,
                    error=str(e)
                )
                action_results.append(action_result)

        observation = build_observation(
            observer=self.name(),
            ability=action_name,
            action_result=action_results
        )

        self.step_finished = True
        return (observation, reward, len(fail_error) > 0, len(fail_error) > 0, info)
