# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import inspect
import os
import traceback
from typing import Any, Dict, List, Tuple

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
        input_params={
            "source_type": ParamInfo(
                name="source_type",
                type="string",
                required=False,
                desc="Type of resources to list: 'built-in' for plugin agents/skills, 'user' for user-registered agents (default: 'user')"
            )
        },
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


def get_directory_structure(directory_path, max_depth=5, current_depth=0):
    """
    Get the file structure of a directory recursively.
    
    Args:
        directory_path: Path to the directory
        max_depth: Maximum depth to traverse (default: 5)
        current_depth: Current depth in recursion
    
    Returns:
        List of strings representing the file structure
    """
    from pathlib import Path
    
    structure = []
    try:
        dir_path = Path(directory_path)
        if not dir_path.exists() or not dir_path.is_dir():
            return structure
        
        if current_depth >= max_depth:
            return structure
        
        # Sort entries: directories first, then files
        entries = sorted(dir_path.iterdir(), key=lambda x: (x.is_file(), x.name))
        
        for entry in entries:
            # Skip hidden files and directories
            if entry.name.startswith('.'):
                continue
            
            # Calculate indentation based on depth
            indent = "  " * current_depth
            if entry.is_dir():
                structure.append(f"{indent}{entry.name}/")
                # Recursively get subdirectory structure
                sub_structure = get_directory_structure(entry, max_depth, current_depth + 1)
                structure.extend(sub_structure)
            else:
                structure.append(f"{indent}{entry.name}")
    
    except Exception as e:
        logger.warning(f"Failed to get directory structure for {directory_path}: {e}")
    
    return structure

async def list_built_in_resources() -> List[tuple]:
    """
    List all built-in resources (agents and skills) from plugins.
    
    Returns:
        List of tuples:
        - For agents: (name, desc, path)
        - For skills: (name, desc, path, file_structure) where file_structure is a string
          containing the directory structure of the skill
    """
    resources_with_desc = []
    logger.info("list_built_in_resources: start")

    try:
        from pathlib import Path
        from ..core.plugin_manager import PluginManager
        from ..core.agent_registry import LocalAgentRegistry

        # Get all plugin directories (built-in and installed)
        plugin_dirs = []

        # Get built-in plugins (inner_plugins)
        import pathlib
        current_dir = pathlib.Path(__file__).parent.parent
        inner_plugins_dir = current_dir / "inner_plugins"

        if inner_plugins_dir.exists() and inner_plugins_dir.is_dir():
            for plugin_dir in inner_plugins_dir.iterdir():
                if plugin_dir.is_dir():
                    plugin_dirs.append(plugin_dir)
        logger.info(f"list_built_in_resources: inner_plugins_dir={inner_plugins_dir}, built-in plugin_dirs count={len(plugin_dirs)}")

        # Get installed plugins
        try:
            plugin_manager = PluginManager()
            installed_plugin_dirs = plugin_manager.get_plugin_dirs()
            # Convert agent dirs back to plugin dirs (parent directory)
            for agent_dir in installed_plugin_dirs:
                plugin_dir = agent_dir.parent
                if plugin_dir not in plugin_dirs:
                    plugin_dirs.append(plugin_dir)
            logger.info(f"list_built_in_resources: after installed plugins, plugin_dirs count={len(plugin_dirs)}, dirs={[str(d) for d in plugin_dirs]}")
        except Exception as e:
            logger.info(f"list_built_in_resources: PluginManager.get_plugin_dirs failed (skipped): {e}")

        # Get agents from plugins
        try:
            local_agents = LocalAgentRegistry.list_agents()
            for local_agent in local_agents:
                if local_agent.name and local_agent.register_dir:
                    register_dir_path = Path(local_agent.register_dir)
                    # Check if agent is from a plugin directory
                    is_from_plugin = False
                    for plugin_dir in plugin_dirs:
                        try:
                            # Try is_relative_to (Python 3.9+)
                            if hasattr(register_dir_path, 'is_relative_to') and register_dir_path.is_relative_to(plugin_dir):
                                is_from_plugin = True
                                break
                        except (AttributeError, ValueError):
                            # Fallback: check if plugin_dir is a parent of register_dir_path
                            try:
                                register_dir_path.resolve().relative_to(plugin_dir.resolve())
                                is_from_plugin = True
                                break
                            except ValueError:
                                # Not relative, continue checking
                                pass
                    
                    if is_from_plugin:
                        desc = local_agent.desc or "No description"
                        path = local_agent.path or str(register_dir_path)
                        resources_with_desc.append((local_agent.name, desc, path))
            agent_count = sum(1 for r in resources_with_desc if len(r) == 3)
            logger.info(f"list_built_in_resources: built-in agents from plugins count={agent_count}, names={[r[0] for r in resources_with_desc if len(r) == 3]}")
        except Exception as e:
            logger.warning(f"Failed to get agents from plugins: {e}")

        # Get skills from plugins
        for plugin_dir in plugin_dirs:
            skills_dir = plugin_dir / "skills"
            if not skills_dir.exists() or not skills_dir.is_dir():
                continue
            
            try:
                for subdir in skills_dir.iterdir():
                    if not subdir.is_dir():
                        continue
                    
                    # Check if directory contains SKILL.md file
                    skill_md_file = subdir / "SKILL.md"
                    if skill_md_file.exists() and skill_md_file.is_file():
                        skill_name = subdir.name
                        # Try to read description from SKILL.md
                        try:
                            with open(skill_md_file, 'r', encoding='utf-8') as f:
                                content = f.read()
                                # Try to extract description from markdown
                                lines = content.split('\n')
                                desc = "No description"
                                
                                # First, look for "description:" prefix line
                                for line in lines:
                                    line_stripped = line.strip()
                                    if line_stripped.lower().startswith('description:'):
                                        # Extract description after "description:" prefix
                                        desc = line_stripped[len('description:'):].strip()[:200]
                                        break
                                
                                # If not found, fall back to original logic
                                if desc == "No description":
                                    for i, line in enumerate(lines):
                                        if line.strip().startswith('#'):
                                            # Found title, next non-empty line might be description
                                            if i + 1 < len(lines) and lines[i + 1].strip():
                                                desc = lines[i + 1].strip()[:200]  # Limit description length
                                                break
                                            else:
                                                desc = line.strip('#').strip()[:200]
                                                break
                                    if desc == "No description" and lines:
                                        # Use first non-empty line as description
                                        for line in lines:
                                            if line.strip() and not line.strip().startswith('#'):
                                                desc = line.strip()[:200]
                                                break
                        except Exception:
                            desc = "No description"
                        
                        # Get file structure of the skill directory
                        file_structure = get_directory_structure(subdir)
                        file_structure_str = "\n".join(file_structure) if file_structure else ""
                        
                        path = str(skill_md_file)
                        resources_with_desc.append((skill_name, desc, path, file_structure_str))
            except Exception as e:
                logger.warning(f"Failed to get skills from plugin {plugin_dir}: {e}")
        agent_count = sum(1 for r in resources_with_desc if len(r) == 3)
        skill_count = sum(1 for r in resources_with_desc if len(r) == 4)
        logger.info(f"list_built_in_resources: done, total={len(resources_with_desc)} (agents={agent_count}, skills={skill_count}), names={[r[0] for r in resources_with_desc]}")
    except Exception as e:
        logger.error(f"Failed to list built-in resources: {e} {traceback.format_exc()}")

    return resources_with_desc


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
    logger.info(f"dynamic_register: start local_agent_name={local_agent_name} register_agent_name={register_agent_name}")
    try:
        from aworld_cli.core.agent_registry import LocalAgentRegistry
        from aworld_cli.core.agent_scanner import AgentScanner

        # Step 1: Get register_agent_name from AgentScanner
        if context is None:
            from aworld_cli.core.agent_scanner import DefaultContext
            context = DefaultContext()
            logger.info("dynamic_register: context was None, using DefaultContext")

        agent_scanner = AgentScanner(context)
        agents_path = os.environ.get("AGENTS_PATH", "~/.aworld/agents")
        logger.info(
            f"dynamic_register: step1 load agent, register_agent_name={register_agent_name} AGENTS_PATH={agents_path}",
        )

        # Step 1: Load register_agent from AgentScanner (must exist under AGENTS_PATH and be in LocalAgentRegistry after module load)
        register_agent = await agent_scanner.load_agent(agent_name=register_agent_name)
        if not register_agent:
            error_msg = (
                f"Agent '{register_agent_name}' could not be loaded. "
                f"Ensure: (1) agent file exists under AGENTS_PATH (e.g. {agents_path}/<agent_name>/<agent_name>.py), "
                f"(2) the file has an @agent decorator with name matching '{register_agent_name}', "
                f"(3) the module loads without errors and the agent is registered in LocalAgentRegistry. "
                f"Check logs above for the exact failure (e.g. file not found, LocalAgentRegistry missing, or swarm empty)."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)
        logger.info(f"dynamic_register: register_agent loaded id={register_agent.id()} name={register_agent.name()} file={inspect.getfile(register_agent.__class__)}")

        # Step 2: Get local_agent_name from LocalAgentRegistry
        logger.info(f"dynamic_register: step2 get local_agent from LocalAgentRegistry, local_agent_name={local_agent_name}")
        local_agent = LocalAgentRegistry.get_agent(local_agent_name)
        logger.info(f"dynamic_register: local_agent={local_agent} register_dir={getattr(local_agent, 'register_dir', None) if local_agent else None}")
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
        swarm_agent_ids = [a for a in swarm.agents] if swarm and getattr(swarm, "agents", None) else []
        swarm_agent_names = [a for a in swarm.agents] if swarm and getattr(swarm, "agents", None) else []
        logger.info(f"dynamic_register: step2 swarm obtained, agent count={len(swarm_agent_ids) if swarm_agent_ids else 0} ids={swarm_agent_ids} names={swarm_agent_names}")
        if not swarm:
            error_msg = (
                f"Failed to get swarm for local agent '{local_agent_name}'. "
                f"The agent exists but its swarm could not be initialized. "
                f"Please check the agent's swarm configuration."
            )
            logger.error(error_msg)
            raise ValueError(error_msg)


        # Step 3: Add register_agent_name to local_agent_name's team_swarm
        logger.info(f"dynamic_register: step3 add agent to swarm, register_agent_name={register_agent_name} register_agent.id={register_agent.id()}")
        origin_agent = find_from_agent_factory_by_name(register_agent_name)
        logger.info(f"dynamic_register: origin_agent from factory={origin_agent.id() if origin_agent else None}")
        swarm.add_agents(agents=[register_agent], to_remove_agents=[origin_agent] if origin_agent else [])
        swarm_agent_ids_after = [a for a in swarm.agents] if getattr(swarm, "agents", None) else []
        swarm_agent_names_after = [a for a in swarm.agents] if getattr(swarm, "agents", None) else []
        logger.info(f"dynamic_register: step3 after add_agents, swarm agent count={len(swarm_agent_ids_after)} ids={swarm_agent_ids_after} names={swarm_agent_names_after}")


        # Step 4: Refresh the root agent's tools cache to include the newly registered agent
        # The root agent's handoffs have been updated, but its tools cache needs to be refreshed
        try:
            if swarm.agent_graph and swarm.agent_graph.root_agent:
                root_agent = swarm.agent_graph.root_agent
                logger.info(f"dynamic_register: step4 root_agent raw={root_agent.id() if hasattr(root_agent, 'id') else root_agent}")
                if isinstance(root_agent, list):
                    root_agent = root_agent[0]

                root_agent_name = root_agent.name()
                logger.info(f"dynamic_register: step4 root_agent_name={root_agent_name}")

                # Find the agent with the same name from AgentFactory
                from aworld.core.agent.base import AgentFactory
                import re

                # find agent from factory
                factory_agent = find_from_agent_factory_by_name(root_agent_name)
                logger.info(f"dynamic_register: step4 factory_agent={factory_agent} (id={factory_agent.id() if factory_agent else None})")

                # Use factory agent if found, otherwise use root_agent
                agent_to_refresh = factory_agent if factory_agent else root_agent
                agent_source = "AgentFactory" if factory_agent else "swarm.agent_graph.root_agent"

                # Clear the tools cache so it will be regenerated with the new agent in handoffs
                agent_to_refresh.tools = []
                logger.info(f"dynamic_register: step4 cleared tools cache for agent={agent_to_refresh.id()} (from {agent_source})")

                # Try to refresh tools immediately if context is ApplicationContext
                # Note: context parameter might be DefaultContext for AgentVersionControlRegistry,
                # which is not compatible with async_desc_transform, so we check the type
                from aworld.core.context.amni import ApplicationContext
                if context and isinstance(context, ApplicationContext):
                    try:
                        await agent_to_refresh.async_desc_transform(context)
                        tool_names = [t.name if hasattr(t, "name") else getattr(t, "name", str(t)) for t in (agent_to_refresh.tools or [])]
                        logger.info(f"dynamic_register: step4 refreshed tools for agent={agent_to_refresh.id()}, tool count={len(agent_to_refresh.tools or [])}, tool names={tool_names}")
                        logger.info(f"dynamic_register: step4 async_desc_transform done for agent={agent_to_refresh.id()} (from {agent_source}) with new agent={register_agent.id()}")
                    except Exception as e:
                        logger.warning(f"Failed to refresh tools for agent '{agent_to_refresh.id()}' (from {agent_source}): {e}. Tools will be regenerated on next use.")
                else:
                    logger.info(f"Tools cache cleared for agent '{agent_to_refresh.id()}' (from {agent_source}). Tools will be regenerated on next use when context is available.")
        except Exception as e:
            logger.warning(f"Failed to refresh root agent tools cache: {e}. Tools will be regenerated on next use.")


        logger.info(f"dynamic_register: success local_agent_name={local_agent_name} register_agent_name={register_agent_name} register_agent.id={register_agent.id()}")
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
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())
                logger.info(f"ContextAgentRegistryTool|do_step: action_name={action_name} params={action.params}")

                try:
                    if action_name == ContextAgentRegistryAction.LIST_DESC.value.name:
                        source_type = action.params.get("source_type", "user")
                        logger.info(f"ContextAgentRegistryTool|list_desc: source_type={source_type}")

                        if source_type == "built-in":
                            # Query built-in resources from plugin_manager
                            resources_with_desc = await list_built_in_resources()
                        else:
                            # Query user resources from AgentScanner (default)
                            service = get_agent_registry_service()
                            resources_with_desc = await service.list_desc()
                        logger.info(f"ContextAgentRegistryTool|list_desc: result count={len(resources_with_desc)}")

                        action_result.success = True
                        if resources_with_desc:
                            # Handle multiple formats:
                            # - 3-tuple: (name, desc, path)
                            # - 4-tuple: (name, desc, path, version) or (name, desc, path, file_structure)
                            desc_lines = []
                            for item in resources_with_desc:
                                if len(item) == 4:
                                    name, desc, path, fourth_field = item
                                    # Check if fourth field is file structure (contains newlines) or version
                                    if isinstance(fourth_field, str) and '\n' in fourth_field:
                                        # It's a file structure - indent each line for better readability
                                        indented_structure = '\n'.join('    ' + line for line in fourth_field.split('\n') if line.strip())
                                        desc_lines.append(f"- {name}: {desc}\n  Path: {path}\n  File Structure:\n{indented_structure}")
                                    else:
                                        # It's a version
                                        desc_lines.append(f"- {name}: {desc}\n  Path: {path}\n  Version: {fourth_field}")
                                else:
                                    # Backward compatibility with old format
                                    name, desc, path = item[:3]
                                    desc_lines.append(f"- {name}: {desc}\n  Path: {path}")
                            
                            source_label = "Built-in" if source_type == "built-in" else "User"
                            action_result.content = f"Available {source_label} resources with descriptions:\n" + "\n".join(desc_lines)
                        else:
                            source_label = "built-in" if source_type == "built-in" else "user"
                            action_result.content = f"No {source_label} resources found"

                    elif action_name == ContextAgentRegistryAction.DYNAMIC_REGISTER.value.name:
                        local_agent_name = action.params.get("local_agent_name", "")
                        register_agent_name = action.params.get("register_agent_name", "")
                        logger.info(f"ContextAgentRegistryTool|dynamic_register: local_agent_name={local_agent_name} register_agent_name={register_agent_name}")

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
                                logger.info(f"ContextAgentRegistryTool|dynamic_register: action result success local={local_agent_name} register={register_agent_name}")
                            else:
                                raise ValueError(
                                    f"Failed to register agent '{register_agent_name}' to local agent '{local_agent_name}'")
                        except ValueError as ve:
                            logger.exception(
                                f"dynamic_register failed: local_agent_name={local_agent_name} register_agent_name={register_agent_name}: {ve}",
                            )
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
        logger.info(
            f"ContextAgentRegistryTool|do_step: done actions={len(actions)} reward={reward} fail_error={bool(fail_error)} result_count={len(action_results)}",
        )
        self.step_finished = True
        return (observation, reward, len(fail_error) > 0, len(fail_error) > 0, info)
