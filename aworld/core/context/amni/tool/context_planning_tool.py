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

CONTEXT_PLANNING = "PLANNING"


class ContextPlanningAction(ToolAction):
    """Agent Planning Support. Definition of Context planning operations."""

    ADD_TODO = ToolActionInfo(
        name="add_todo",
        input_params={
            "todo_content": ParamInfo(
                name="todo_content",
                type="string",
                required=True,
                desc="The todo content in markdown format. Use [ ] for incomplete tasks and [x] for completed tasks."
            )
        },
        desc="Add or update todo content in workspace. This tool helps create and manage task plans."
    )

    GET_TODO = ToolActionInfo(
        name="get_todo",
        input_params={},
        desc="Get todo content from workspace. Returns the current task plan and progress."
    )


@ToolFactory.register(name=CONTEXT_PLANNING,
                      desc=CONTEXT_PLANNING,
                      supported_action=ContextPlanningAction)
class ContextPlanningTool(AsyncTool):
    """Tool for managing planning and todo operations in context."""
    
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextPlanningTool, self).__init__(conf, **kwargs)
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
                                 ability=ContextPlanningAction.ADD_TODO.value.name), {}

    def init(self) -> None:
        """Initialize the tool."""
        self.initialized = True

    async def close(self) -> None:
        """Close the tool."""
        pass

    async def finished(self) -> bool:
        """Check if the tool step is finished."""
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute planning actions.
        
        Supported actions:
        - add_todo: Add or update todo content
        - get_todo: Get current todo content
        """
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ContextPlanningAction.ADD_TODO.value.name)
        info = {}
        
        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            for action in actions:
                logger.info(f"CONTEXTPlanningTool|do_step: {action}")
                action_name = action.action_name
                namespace = action.agent_name if hasattr(action, 'agent_name') else "default"
                
                if action_name == ContextPlanningAction.ADD_TODO.value.name:
                    todo_content = action.params.get("todo_content", "")
                    if not todo_content:
                        raise ValueError("todo_content is required")
                    
                    # Call knowledge_service.add_todo through context
                    await message.context.knowledge_service.add_todo(todo_content, namespace=namespace)
                    result = f"✅ Todo added/updated successfully in namespace '{namespace}'"
                    
                elif action_name == ContextPlanningAction.GET_TODO.value.name:
                    # Call knowledge_service.get_todo through context
                    todo_content = await message.context.knowledge_service.get_todo(namespace=namespace)
                    
                    if todo_content is None:
                        result = "📋 Todo is empty"
                    else:
                        result = f"📋 Current Todo:\n\n{todo_content}"
                    
                else:
                    raise ValueError(f"Unknown action: {action_name}")

                observation.content = result
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=True,
                                 content=f"{result}",
                                 keep=False))
            reward = 1.
            
        except Exception as e:
            fail_error = str(e)
            logger.warning(f"CONTEXTPlanningTool|failed do_step: {traceback.format_exc()}")
            observation.content = f"❌ Error: {fail_error}"
            observation.action_result.append(
                ActionResult(is_done=True,
                             success=False,
                             content=f"Error: {fail_error}",
                             keep=False))
        finally:
            self.step_finished = True
            
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

