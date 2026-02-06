# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import traceback
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.agent.base import AgentFactory
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

AGENT_REGISTRY = "AGENT_REGISTRY"


class ContextAgentRegistryAction(ToolAction):
    """Agent Registry Support. Definition of Context agent registry operations."""

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
                desc="The name of the agent in AgentScanner to register"
            )
        },
        desc="Dynamically register an agent from AgentScanner to a local agent's team_swarm"
    )


def find_from_agent_factory_by_name(to_find_agent_name: str):
    logger.info(f"AgentFactory._agent_instance.values(): {AgentFactory._agent_instance.values()} {to_find_agent_name}")
    # Find agent with the same name from AgentFactory
    for agent in list(AgentFactory._agent_instance.values()):
        if agent.name() == to_find_agent_name:
            factory_agent = AgentFactory._agent_instance[agent.id()]
            logger.info(f"Found agent '{factory_agent.id()}' (name: '{to_find_agent_name}') from AgentFactory")
            return factory_agent
    return None

async def dynamic_register(local_agent_name: str, register_agent_name: str, context=None) -> bool:
    """
    Dynamically register an agent from AgentScanner to a local agent's team_swarm.
    
    Args:
        local_agent_name: Name of the local agent in LocalAgentRegistry
        register_agent_name: Name of the agent in AgentScanner to register
        context: Optional context for AgentScanner (if None, uses default context)
    
    Returns:
        True if registration successful, False otherwise
    
    Raises:
        ValueError: With detailed error message if any step fails
    
    Process:
        1. Get local_agent_name from LocalAgentRegistry
        2. Read its team_swarm
        3. Get register_agent_name from AgentScanner
        4. Add register_agent_name to local_agent_name's team_swarm
    """
    try:
        from aworld_cli.core.agent_registry import LocalAgentRegistry
        from aworld_cli.core.agent_scanner import AgentScanner

        # Step 1: Get register_agent_name from AgentScanner
        if context is None:
            from aworld_cli.core.agent_scanner import DefaultContext
            context = DefaultContext()

        agent_scanner = AgentScanner(context)

        # Check if agent exists in registry before loading
        register_agent = await agent_scanner.load_agent(agent_name=register_agent_name)
        logger.info(f"register_agent, {register_agent.id()}, {inspect.getfile(register_agent.__class__)}")
        if not register_agent:
            error_msg = (
                f"Agent '{register_agent_name}' exists in registry but could not be loaded. "
                f"This may indicate a problem with the agent file (e.g., syntax error, missing dependencies, "
                f"or invalid agent definition). Please check the agent file and ensure it is valid."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Step 2: Get local_agent_name from LocalAgentRegistry
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
        swarm = await local_agent.get_swarm(context=context)
        # swarm = context.swarm
        logger.info(f"local_agent|swarm: {swarm} {swarm.agents}")
        if not swarm:
            error_msg = (
                f"Failed to get swarm for local agent '{local_agent_name}'. "
                f"The agent exists but its swarm could not be initialized. "
                f"Please check the agent's swarm configuration."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)


        # Step 3: Add register_agent_name to local_agent_name's team_swarm
        origin_agent = find_from_agent_factory_by_name(register_agent_name)
        swarm.add_agents(agents=[register_agent], to_remove_agents=[origin_agent])


        # Step 4: Refresh the root agent's tools cache to include the newly registered agent
        # The root agent's handoffs have been updated, but its tools cache needs to be refreshed
        try:
            if swarm.agent_graph and swarm.agent_graph.root_agent:
                root_agent = swarm.agent_graph.root_agent
                logger.info('root_agent: ', root_agent.id())
                if isinstance(root_agent, list):
                    root_agent = root_agent[0]

                root_agent_name = root_agent.name()

                # Find the agent with the same name from AgentFactory
                from aworld.core.agent.base import AgentFactory
                import re

                # find agent from factory
                factory_agent = find_from_agent_factory_by_name(root_agent_name)
                logger.info('factory_agent: ', factory_agent.id())

                # Use factory agent if found, otherwise use root_agent
                agent_to_refresh = factory_agent if factory_agent else root_agent
                agent_source = "AgentFactory" if factory_agent else "swarm.agent_graph.root_agent"

                # Clear the tools cache so it will be regenerated with the new agent in handoffs
                agent_to_refresh.tools = []
                logger.info(f"Cleared tools cache for agent '{agent_to_refresh.id()}' (from {agent_source}) to force refresh")

                # Try to refresh tools immediately if context is ApplicationContext
                # Note: context parameter might be DefaultContext for AgentVersionControlRegistry,
                # which is not compatible with async_desc_transform, so we check the type
                from aworld.core.context.amni import ApplicationContext
                if context and isinstance(context, ApplicationContext):
                    try:
                        await agent_to_refresh.async_desc_transform(context)
                        logger.info(f'agent_to_refresh2: {agent_to_refresh.id()} tools: {agent_to_refresh.tools}')
                        logger.info(f"Refreshed tools for agent '{agent_to_refresh.id()}' (from {agent_source}) with new agent '{register_agent.id()}'")
                    except Exception as e:
                        logger.warning(f"Failed to refresh tools for agent '{agent_to_refresh.id()}' (from {agent_source}): {e}. Tools will be regenerated on next use.")
                else:
                    logger.info(f"Tools cache cleared for agent '{agent_to_refresh.id()}' (from {agent_source}). Tools will be regenerated on next use when context is available.")
        except Exception as e:
            logger.warning(f"Failed to refresh root agent tools cache: {e}. Tools will be regenerated on next use.")


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


@ToolFactory.register(name=AGENT_REGISTRY,
                      desc=AGENT_REGISTRY,
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
                                 ability=ContextAgentRegistryAction.LIST_DESC.value.name), {}

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
            from aworld_cli.core.agent_scanner import AgentScanner

            def get_agent_registry_service() -> AgentScanner:
                context = message.context._context if hasattr(message.context, '_context') else message.context
                return AgentScanner(context)

            for action in actions:
                logger.info(f"ContextAgentRegistryTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())

                try:
                    if action_name == ContextAgentRegistryAction.LIST_DESC.value.name:
                        service = get_agent_registry_service()
                        resources_with_desc = await service.list_desc()

                        action_result.success = True
                        if resources_with_desc:
                            # Handle both old format (3-tuple) and new format (4-tuple with version)
                            desc_lines = []
                            for item in resources_with_desc:
                                if len(item) == 4:
                                    name, desc, path, version = item
                                    desc_lines.append(f"- {name}: {desc}\n  Path: {path}\n  Version: {version}")
                                else:
                                    # Backward compatibility with old format
                                    name, desc, path = item[:3]
                                    desc_lines.append(f"- {name}: {desc}\n  Path: {path}")
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
                                raise ValueError(
                                    f"Failed to register agent '{register_agent_name}' to local agent '{local_agent_name}'")
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
