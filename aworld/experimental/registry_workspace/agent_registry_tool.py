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
