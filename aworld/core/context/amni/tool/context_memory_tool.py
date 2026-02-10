# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Context memory tool: write user long-term memory via MemoryFactory.instance().

Supports:
- user_profile: generated from user query (key-value profile).
- fact: generated from conversation (key-value fact).
"""
import traceback
from typing import Any, Dict, Tuple, Optional

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.memory.main import MemoryFactory
from aworld.memory.models import UserProfile, Fact
from aworld.tools.utils import build_observation

CONTEXT_MEMORY = "MEMORY"


class ContextMemoryAction(ToolAction):
    """Agent long-term memory support. Write user_profile and fact via MemoryFactory."""

    WRITE_USER_PROFILE = ToolActionInfo(
        name="write_user_profile",
        input_params={
            "key": ParamInfo(
                name="key",
                type="string",
                required=True,
                desc="Profile key (e.g. preference, interest). Typically generated from user query."
            ),
            "value": ParamInfo(
                name="value",
                type="string",
                required=True,
                desc="Profile value (string or serializable)."
            ),
            "user_id": ParamInfo(
                name="user_id",
                type="string",
                required=False,
                desc="User identifier. If omitted, uses context.user_id."
            )
        },
        desc="Write a user profile entry to long-term memory. user_profile is typically generated from the user's query."
    )

    WRITE_FACT = ToolActionInfo(
        name="write_fact",
        input_params={
            "key": ParamInfo(
                name="key",
                type="string",
                required=True,
                desc="Fact key/topic (e.g. topic, decision). Typically generated from conversation."
            ),
            "value": ParamInfo(
                name="value",
                type="string",
                required=True,
                desc="Fact content (string or serializable)."
            ),
            "user_id": ParamInfo(
                name="user_id",
                type="string",
                required=False,
                desc="User identifier. If omitted, uses context.user_id."
            )
        },
        desc="Write a fact to long-term memory. fact is typically generated from the conversation."
    )


@ToolFactory.register(name=CONTEXT_MEMORY, desc=CONTEXT_MEMORY, supported_action=ContextMemoryAction)
class ContextMemoryTool(AsyncTool):
    """Tool for writing user long-term memory (user_profile, fact) via MemoryFactory.instance()."""

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextMemoryTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.keyframes = []
        self.init()
        self.step_finished = True

    async def reset(
        self,
        *,
        seed: int | None = None,
        options: Dict[str, str] | None = None,
    ) -> Tuple[Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)
        await self.close()
        self.step_finished = True
        return build_observation(
            observer=self.name(),
            ability=ContextMemoryAction.WRITE_USER_PROFILE.value.name,
        ), {}

    def init(self) -> None:
        """Initialize the tool."""
        self.initialized = True

    async def close(self) -> None:
        """Close the tool."""
        pass

    async def finished(self) -> bool:
        """Check if the tool step is finished."""
        return self.step_finished

    def _user_id(self, message: Message, action: ActionModel) -> str:
        """Resolve user_id from action params or context."""
        uid = action.params.get("user_id") if action.params else None
        if uid:
            return uid
        if not isinstance(message.context, AmniContext):
            raise ValueError("context is not AmniContext")
        return message.context.user_id or "user"

    async def do_step(
        self,
        actions: list[ActionModel],
        message: Message = None,
        **kwargs,
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute memory write actions.

        Supported actions:
        - write_user_profile: Write user profile (key, value) to long-term memory.
        - write_fact: Write fact (key, value) to long-term memory.
        """
        self.step_finished = False
        reward = 0.0
        fail_error = ""
        observation = build_observation(
            observer=self.name(),
            ability=ContextMemoryAction.WRITE_USER_PROFILE.value.name,
        )
        info = {}

        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            memory = MemoryFactory.instance()

            for action in actions:
                logger.info(f"ContextMemoryTool|do_step: {action}")
                action_name = action.action_name
                user_id = self._user_id(message, action)

                if action_name == ContextMemoryAction.WRITE_USER_PROFILE.value.name:
                    key = action.params.get("key", "")
                    value = action.params.get("value", "")
                    if not key:
                        raise ValueError("key is required for write_user_profile")
                    if value is None:
                        value = ""
                    item = UserProfile(user_id=user_id, key=key, value=value)
                    await memory.add(item, agent_memory_config=None)
                    result = (
                        f"✅ User profile written\n"
                        f"🧠 user_id={user_id} key={key} id={item.id}\n"
                        f"💡 user_profile is typically generated from user query."
                    )
                    logger.info(f"ContextMemoryTool|write_user_profile user_id={user_id} key={key} id={item.id}")

                elif action_name == ContextMemoryAction.WRITE_FACT.value.name:
                    key = action.params.get("key", "")
                    value = action.params.get("value", "")
                    if not key:
                        raise ValueError("key is required for write_fact")
                    if value is None:
                        value = ""
                    content = {"key": key, "value": value}
                    item = Fact(user_id=user_id, content=content)
                    await memory.add(item, agent_memory_config=None)
                    result = (
                        f"✅ Fact written\n"
                        f"🧠 user_id={user_id} key={key} id={item.id}\n"
                        f"💡 fact is typically generated from conversation."
                    )
                    logger.info(f"ContextMemoryTool|write_fact user_id={user_id} key={key} id={item.id}")

                else:
                    raise ValueError(f"Unknown action: {action_name}")

                observation.content = result
                observation.action_result.append(
                    ActionResult(is_done=True, success=True, content=result, keep=False)
                )

            reward = 1.0

        except Exception as e:
            fail_error = str(e)
            logger.warning(f"ContextMemoryTool|do_step failed: {traceback.format_exc()}")
            observation.content = f"❌ Error: {fail_error}"
            observation.action_result.append(
                ActionResult(is_done=True, success=False, content=f"Error: {fail_error}", keep=False)
            )
        finally:
            self.step_finished = True

        info["exception"] = fail_error
        info.update(kwargs)
        return (
            observation,
            reward,
            kwargs.get("terminated", False),
            kwargs.get("truncated", False),
            info,
        )
