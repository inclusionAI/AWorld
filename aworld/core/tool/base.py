# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import time
import traceback
from typing import Dict, Tuple, Any, TypeVar, Generic, List, Union

from pydantic import BaseModel


class ToolExecutionDenied(Exception):
    """Exception raised when tool execution is denied by a hook."""
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        self.reason = reason
        super().__init__(f"Tool {tool_name} execution denied: {reason}")

import aworld
from aworld.config.conf import ToolConfig, load_config, ConfigDict
from aworld.core.common import Observation, ActionModel, ActionResult, CallbackItem, CallbackResult, CallbackActionType
from aworld.core.context.base import Context
from aworld.core.event.base import Message, AgentMessage, Constants, MemoryEventMessage, MemoryEventType
from aworld.core.factory import Factory
from aworld.core.tool.action import ToolAction
from aworld.core.tool.action_factory import ActionFactory
from aworld.events import eventbus
from aworld.events.util import send_message, send_message_with_future
from aworld.logs.util import logger
from aworld.models.model_response import ToolCall
from aworld.output import ToolResultOutput
from aworld.output.base import StepOutput
from aworld.runners.hook.hooks import HookPoint
from aworld.runners.hook.utils import run_hooks
from aworld.utils.common import convert_to_snake, sync_exec

AgentInput = TypeVar("AgentInput")
ToolInput = TypeVar("ToolInput")

# Forward declaration of action_executor to fix NameError
action_executor = None


class BaseTool(Generic[AgentInput, ToolInput]):
    """The basic generic classes of tools in the environment, with two parameterized types: AgentInput and ToolInput.

    We follow the gym/gymnasium protocol to be compatible with gym games, can also build special env tool in the framework.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, ToolConfig], **kwargs) -> None:
        self.conf = conf
        if isinstance(conf, ConfigDict):
            pass
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, ToolConfig):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")
        self._finished = False

        self._name = kwargs.pop('name', self.conf.get(
            "name", convert_to_snake(self.__class__.__name__)))
        action_executor.register(name=self.name(), tool=self)
        self.action_executor = action_executor
        self.event_driven = kwargs.pop(
            'event_driven', self.conf.get('event_driven', False))
        self.handler = kwargs.get('handler', self.conf.get('handler', None))

        for k, v in kwargs.items():
            setattr(self, k, v)

    def name(self):
        """Tool unique name."""
        return self._name

    def pre_step(self, action: ToolInput, **kwargs):
        pass

    def post_step(self,
                  step_res: Tuple[AgentInput, float, bool, bool, Dict[str, Any]],
                  action: ToolInput,
                  **kwargs) -> Message:
        pass

    def step(self, message: Message, **kwargs) -> Message:
        action = message.payload
        self.pre_step(action, message=message,**kwargs)
        res = self.do_step(action, message =message, **kwargs)
        final_res = self.post_step(res, action, message=message, **kwargs)
        return final_res

    @abc.abstractmethod
    def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
            AgentInput, dict[str, Any]]:
        """Resets the initial internal state, returning an initial state and extended info."""

    @abc.abstractmethod
    def do_step(self, action: ToolInput, **kwargs) -> Tuple[AgentInput, float, bool, bool, Dict[str, Any]]:
        """Run one step of the tool's in env using the actions.

        Args:
            action(ToolInput): Actions provided by the agent to update the observation.
        Return:
            Quintuple，key information: AgentInput and extended info dict.
        """

    @property
    def finished(self) -> bool:
        """The final execution status of the task from agent instructions."""
        return self._finished

    @abc.abstractmethod
    def close(self) -> None:
        """Close the tool resources in the environment."""

    def render(self):
        """For interface compatibility."""
        pass


class AsyncBaseTool(Generic[AgentInput, ToolInput]):
    """The basic generic classes of tools in the environment, with two parameterized types: AgentInput and ToolInput.

    We follow the gym/gymnasium protocol to be compatible with gym games, can also build special env tool in the framework.
    """
    __metaclass__ = abc.ABCMeta

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, ToolConfig], **kwargs) -> None:
        self.conf = conf
        if isinstance(conf, ConfigDict):
            pass
        elif isinstance(conf, Dict):
            self.conf = ConfigDict(conf)
        elif isinstance(conf, ToolConfig):
            # To add flexibility
            self.conf = ConfigDict(conf.model_dump())
        else:
            logger.warning(f"Unknown conf type: {type(conf)}")
        self._finished = False

        self._name = kwargs.pop('name', self.conf.get(
            "name", convert_to_snake(self.__class__.__name__)))
        action_executor.register(name=self.name(), tool=self)
        self.action_executor = action_executor
        self.event_driven = kwargs.pop(
            'event_driven', self.conf.get('event_driven', False))
        self.handler = kwargs.get('handler', self.conf.get('handler', None))

        for k, v in kwargs.items():
            setattr(self, k, v)

    def name(self):
        """Tool unique name."""
        return self._name

    async def pre_step(self, action: ToolInput, **kwargs):
        pass

    async def post_step(self,
                        step_res: Tuple[AgentInput, float, bool, bool, Dict[str, Any]],
                        action: ToolInput,
                        **kwargs) -> Message:
        pass

    async def step(self, message: Message, **kwargs) -> Message:
        action = message.payload
        await self.pre_step(action,message=message, **kwargs)
        res = await self.do_step(action,message=message, **kwargs)
        final_res = await self.post_step(res, action,message=message, **kwargs)
        return final_res

    @abc.abstractmethod
    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
            AgentInput, dict[str, Any]]:
        """Resets the initial internal state, returning an initial state and extended info."""

    @abc.abstractmethod
    async def do_step(self, action: ToolInput, **kwargs) -> Tuple[AgentInput, float, bool, bool, Dict[str, Any]]:
        """Run one step of the tool's in env using the actions.

        Args:
            action(ToolInput): Actions provided by the agent to update the observation.
        Return:
            Quintuple，key information: AgentInput and extended info dict.
        """

    @property
    def finished(self) -> bool:
        """The final execution status of the task from agent instructions."""
        return self._finished

    @abc.abstractmethod
    async def close(self) -> None:
        """Close the tool resources in the environment."""

    async def render(self):
        """For interface compatibility."""
        pass


class Tool(BaseTool[Observation, List[ActionModel]]):
    def _internal_process(self, step_res: Tuple[AgentInput, float, bool, bool, Dict[str, Any]],
                          action: ToolInput,
                          input_message: Message,
                          **kwargs):
        context = input_message.context
        if not step_res or not action:
            return
        for idx, act in enumerate(action):
            if eventbus is not None:
                tool_output = ToolResultOutput(
                    tool_type=kwargs.get("tool_id_mapping", {}).get(
                        act.tool_call_id) or self.name(),
                    tool_name=act.tool_name,
                    action_name=act.action_name,
                    data=step_res[0].action_result[idx].content,
                    origin_tool_call=ToolCall.from_dict({
                        "id": act.tool_call_id,
                        "function": {
                            "name": act.action_name,
                            "arguments": act.params,
                        }
                    }),
                    metadata=step_res[0].action_result[idx].metadata,
                    task_id=context.task_id
                )
                tool_output_message = Message(
                    category=Constants.OUTPUT,
                    payload=tool_output,
                    sender=self.name(),
                    session_id=context.session_id if context else "",
                    headers={"context": context}
                )
                sync_exec(send_message, tool_output_message)

        # add results to memory after sending outputs
        try:
            # step_res typing narrowed: Tuple[Observation, ...]
            self._add_tool_results_to_memory(step_res, action, input_message.context)
        except Exception:
            logger.warning(f"Tool {self.name()} post internal process memory write failed: {traceback.format_exc()}")

    def step(self, message: Message, **kwargs) -> Message:
        final_res = None
        try:
            action = message.payload
            tool_id_mapping = {}
            for act in action:
                tool_id = act.tool_call_id
                tool_name = act.tool_name
                tool_id_mapping[tool_id] = tool_name
            # Execute PRE_TOOL_CALL hooks and check for updated_input
            pre_hook_events = self.run_hooks(message=message, hook_point=HookPoint.PRE_TOOL_CALL, hook_from=message.sender,
                           payload=action)

            # P0 Fix: Check permission_decision to gate tool execution
            for hook_event in pre_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    permission_decision = hook_event.headers.get('permission_decision')
                    if permission_decision in ('deny', 'ask'):
                        stop_reason = hook_event.headers.get('permission_decision_reason', 'Tool execution requires permission')

                        # Handle 'ask' by resolving with permission handler
                        if permission_decision == 'ask':
                            try:
                                from aworld.runners.hook.v2.permission import get_permission_handler
                                handler = get_permission_handler()
                                context_dict = {
                                    'tool_name': self.name(),
                                    'action': [act.model_dump() for act in action] if action else []
                                }
                                final_decision, resolution_reason = handler.resolve_permission_sync(
                                    permission_decision, stop_reason, context_dict
                                )
                                if final_decision == 'deny':
                                    logger.warning(f"PRE_TOOL_CALL hook denied tool {self.name()} after resolution: {resolution_reason}")
                                    raise ToolExecutionDenied(self.name(), resolution_reason)
                                else:
                                    logger.info(f"PRE_TOOL_CALL hook allowed tool {self.name()} after resolution: {resolution_reason}")
                                    # Continue execution
                            except ToolExecutionDenied:
                                raise
                            except Exception as e:
                                logger.error(f"Failed to resolve permission 'ask': {e}")
                                raise ToolExecutionDenied(self.name(), f"Permission resolution failed: {e}")
                        else:
                            # Direct deny
                            logger.warning(f"PRE_TOOL_CALL hook denied tool {self.name()}: {stop_reason}")
                            raise ToolExecutionDenied(self.name(), stop_reason)

            # Apply updated_input from hooks if present (chain all modifications)
            for hook_event in pre_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    updated_input = hook_event.headers.get('updated_input')
                    if updated_input:
                        # Update action with modified input, convert to List[ActionModel]
                        if isinstance(updated_input, list):
                            # Convert list[dict] to list[ActionModel] if needed
                            if updated_input and isinstance(updated_input[0], dict):
                                action = [ActionModel(**item) if isinstance(item, dict) else item for item in updated_input]
                            else:
                                action = updated_input
                            message.payload = action
                        elif isinstance(updated_input, dict):
                            if 'actions' in updated_input:
                                # Handle {'actions': [...]} format
                                actions_data = updated_input['actions']
                                if isinstance(actions_data, list) and actions_data and isinstance(actions_data[0], dict):
                                    action = [ActionModel(**item) if isinstance(item, dict) else item for item in actions_data]
                                else:
                                    action = actions_data
                                message.payload = action
                            else:
                                # Treat single dict as one action
                                action = [ActionModel(**updated_input)]
                                message.payload = action
                        logger.info(f"PRE_TOOL_CALL hook modified input for tool {self.name()}")
                        # Continue to next hook to allow chaining

            self.pre_step(action, **kwargs)
            res = self.do_step(action, message=message, **kwargs)

            # Execute POST_TOOL_CALL hooks and check for updated_output
            post_hook_events = self.run_hooks(message=message, hook_point=HookPoint.POST_TOOL_CALL, hook_from=message.sender, payload=res)

            # Apply updated_output from hooks if present (chain all modifications)
            for hook_event in post_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    updated_output = hook_event.headers.get('updated_output')
                    if updated_output:
                        # Update res with modified output
                        if isinstance(updated_output, tuple) and len(updated_output) == 5:
                            res = updated_output
                        elif isinstance(updated_output, dict):
                            # Allow hook to modify specific parts of res tuple
                            res_list = list(res)
                            if 'observation' in updated_output:
                                obs = updated_output['observation']
                                # Convert dict to Observation object if needed
                                if isinstance(obs, dict) and not isinstance(obs, Observation):
                                    res_list[0] = Observation(**obs)
                                else:
                                    res_list[0] = obs
                            if 'reward' in updated_output:
                                res_list[1] = updated_output['reward']
                            if 'done' in updated_output:
                                res_list[2] = updated_output['done']
                            if 'truncated' in updated_output:
                                res_list[3] = updated_output['truncated']
                            if 'info' in updated_output:
                                res_list[4] = updated_output['info']
                            res = tuple(res_list)
                        logger.info(f"POST_TOOL_CALL hook modified output for tool {self.name()}")
                        # Continue to next hook to allow chaining

            final_res = self.post_step(res, action,message=message, **kwargs)
            self._internal_process(
                res, action, message, tool_id_mapping=tool_id_mapping, **kwargs)
            return final_res
        except Exception as e:
            logger.error(
                f"Failed to execute {self.name()}: {e}."
                f"Debug info: session_id = {message.session_id}, action = {message.payload}."
                f"Traceback:\n{traceback.format_exc()}"
            )

            # Trigger TOOL_CALL_FAILED hook
            try:
                self.run_hooks(
                    message=message,
                    hook_point=HookPoint.TOOL_CALL_FAILED,
                    hook_from=message.sender,
                    payload={
                        'tool_name': self.name(),
                        'action': action,
                        'error': str(e),
                        'error_type': type(e).__name__,
                        'traceback': traceback.format_exc()
                    }
                )
            except Exception as hook_e:
                logger.warning(f"TOOL_CALL_FAILED hook execution failed: {hook_e}")

            raise e
        finally:
            logger.info(
                f"Tool {self.name()} result: {final_res}, session_id: {message.session_id}, task_id: {message.context.task_id}"
            )

    def post_step(self,
                  step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                  action: List[ActionModel],
                  message: Message,
                  **kwargs) -> Tuple[Observation, float, bool, bool, Dict[str, Any]] | Message:
        if not step_res:
            raise Exception(f'{self.name()} no observation has been made.')

        context = message.context

        step_res[0].from_agent_name = action[0].agent_name
        for idx, act in enumerate(action):
            step_res[0].action_result[idx].tool_call_id = act.tool_call_id
            step_res[0].action_result[idx].tool_name = act.tool_name

        if context.swarm:
            agent = context.swarm.agents.get(action[0].agent_name)
            feedback_tool_result = agent.feedback_tool_result if agent else False
        else:
            feedback_tool_result = True
        if feedback_tool_result:
            return AgentMessage(payload=step_res,
                                caller=action[0].agent_name,
                                sender=self.name(),
                                receiver=action[0].agent_name,
                                session_id=context.session_id,
                                headers={"context": context})
        else:
            return AgentMessage(payload=step_res,
                                sender=action[0].agent_name,
                                session_id=context.session_id,
                                headers={"context": context})

    def _add_tool_results_to_memory(self,
                                    step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                                    action: List[ActionModel],
                                    context: Context):
        try:
            if not step_res or not action:
                return
            observation = step_res[0]
            if not hasattr(observation, 'action_result') or observation.action_result is None:
                return
            for idx, act in enumerate(action):
                if idx >= len(observation.action_result):
                    continue
                tool_result = observation.action_result[idx]
                receive_agent = None
                if context.swarm and context.swarm.agents:
                    receive_agent = context.swarm.agents.get(act.agent_name)
                if not receive_agent:
                    logger.warning(f"agent {act.agent_name} not found in swarm {context.swarm}.")
                    return
                sync_exec(send_message, MemoryEventMessage(
                    payload=tool_result,
                    agent=receive_agent,
                    memory_event_type=MemoryEventType.TOOL,
                    session_id=context.session_id if context else "",
                    headers={"context": context}
                ))
        except Exception:
            logger.warning(f"Tool {self.name()} write tool results to memory failed: {traceback.format_exc()}")

    def run_hooks(self, message: Message, hook_point: str, hook_from: str, payload: Any = None) -> List[Message]:
        """Execute hooks and break by exception"""
        from aworld.runners.hook.hook_factory import HookFactory
        from aworld.core.event.base import Message

        # Get all hooks for the specified hook point
        all_hooks = HookFactory.hooks(hook_point)
        hooks = all_hooks.get(hook_point, [])
        context = message.context
        hook_events = []
        for hook in hooks:
            try:
                # Create a temporary Message object to pass to the hook
                message = Message(
                    category="agent_hook",
                    payload=payload,
                    sender=hook_from,
                    session_id=context.session_id if hasattr(
                        context, 'session_id') else None,
                    headers={"context": context}
                )

                # Execute hook
                msg = sync_exec(hook.exec, message, context)
                if msg:
                    logger.debug(f"Hook {hook.point()} executed successfully")
                    hook_events.append(msg)
            except Exception as e:
                logger.warning(f"Hook {hook.point()} execution failed: {traceback.format_exc()}")
                raise e
        return hook_events


class AsyncTool(AsyncBaseTool[Observation, List[ActionModel]]):
    async def _internal_process(self, step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                                action: List[ActionModel],
                                input_message: Message,
                                **kwargs):
        # logger.warning(f"tool {self.name()} sleep 60s start")
        # await asyncio.sleep(60)
        # logger.warning(f"tool {self.name()} sleep 60s finish")
        context = input_message.context
        for idx, act in enumerate(action):
            # send tool results output
            if eventbus is not None:
                tool_output = ToolResultOutput(
                    tool_type=kwargs.get("tool_id_mapping", {}).get(
                        act.tool_call_id) or self.name(),
                    tool_name=act.tool_name,
                    action_name=act.action_name,
                    data=step_res[0].action_result[idx].content,
                    origin_tool_call=ToolCall.from_dict({
                        "id": act.tool_call_id,
                        "function": {
                            "name": act.action_name,
                            "arguments": act.params,
                        }
                    }),
                    metadata=step_res[0].action_result[idx].metadata,
                    task_id=context.task_id
                )
                tool_output_message = Message(
                    category=Constants.OUTPUT,
                    payload=tool_output,
                    sender=self.name(),
                    session_id=context.session_id if context else "",
                    headers={"context": context}
                )
                await send_message(tool_output_message)

        # add results to memory after sending outputs
        try:
            await self._add_tool_results_to_memory(step_res, action, input_message.context)
        except Exception:
            logger.warning(f"AsyncTool {self.name()} post internal process memory write failed: {traceback.format_exc()}")

        logger.info("[tag for memory tool]======= Send memory message finished")

        await send_message(Message(
            category=Constants.OUTPUT,
            payload=StepOutput.build_finished_output(name=f"{action[0].agent_name if action else ''}",
                                                     step_num=0,
                                                     task_id=context.task_id),
            sender=self.name(),
            receiver=action[0].agent_name,
            session_id=context.session_id if context else "",
            headers={"context": context}
        ))
        try:
            await self._exec_tool_callback(step_res, action,
                                           Message(
                                               category=Constants.TOOL_CALLBACK,
                                               payload=CallbackItem(
                                                   data=step_res,
                                                   actions=action,
                                                   node_id=input_message.id
                                               ),
                                               sender=self.name(),
                                               receiver=action[0].agent_name,
                                               session_id=context.session_id,
                                               headers={"context": context}
                                           ),
                                           **kwargs)
        except Exception as e:
            logger.warning(f"AsyncTool {self.name()} exec tool callback failed: {traceback.format_exc()}")

    async def step(self, message: Message, **kwargs) -> Message:
        final_res = None
        action = message.payload
        try:
            tool_id_mapping = {}
            for act in action:
                tool_id = act.tool_call_id
                tool_name = act.tool_name
                tool_id_mapping[tool_id] = tool_name
            # Execute PRE_TOOL_CALL hooks and check for updated_input
            pre_hook_events = await self.run_hooks(message=message, hook_point=HookPoint.PRE_TOOL_CALL, hook_from=message.sender,
                                 payload=action)

            # P0 Fix: Check permission_decision to gate tool execution
            for hook_event in pre_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    permission_decision = hook_event.headers.get('permission_decision')
                    if permission_decision in ('deny', 'ask'):
                        stop_reason = hook_event.headers.get('permission_decision_reason', 'Tool execution requires permission')

                        # Handle 'ask' by resolving with permission handler
                        if permission_decision == 'ask':
                            try:
                                from aworld.runners.hook.v2.permission import get_permission_handler
                                handler = get_permission_handler()
                                context_dict = {
                                    'tool_name': self.name(),
                                    'action': [act.model_dump() for act in action] if action else []
                                }
                                final_decision, resolution_reason = await handler.resolve_permission(
                                    permission_decision, stop_reason, context_dict
                                )
                                if final_decision == 'deny':
                                    logger.warning(f"PRE_TOOL_CALL hook denied tool {self.name()} after resolution: {resolution_reason}")
                                    raise ToolExecutionDenied(self.name(), resolution_reason)
                                else:
                                    logger.info(f"PRE_TOOL_CALL hook allowed tool {self.name()} after resolution: {resolution_reason}")
                                    # Continue execution
                            except ToolExecutionDenied:
                                raise
                            except Exception as e:
                                logger.error(f"Failed to resolve permission 'ask': {e}")
                                raise ToolExecutionDenied(self.name(), f"Permission resolution failed: {e}")
                        else:
                            # Direct deny
                            logger.warning(f"PRE_TOOL_CALL hook denied tool {self.name()}: {stop_reason}")
                            raise ToolExecutionDenied(self.name(), stop_reason)

            # Apply updated_input from hooks if present (chain all modifications)
            for hook_event in pre_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    updated_input = hook_event.headers.get('updated_input')
                    if updated_input:
                        # Update action with modified input, convert to List[ActionModel]
                        if isinstance(updated_input, list):
                            # Convert list[dict] to list[ActionModel] if needed
                            if updated_input and isinstance(updated_input[0], dict):
                                action = [ActionModel(**item) if isinstance(item, dict) else item for item in updated_input]
                            else:
                                action = updated_input
                            message.payload = action
                        elif isinstance(updated_input, dict):
                            if 'actions' in updated_input:
                                # Handle {'actions': [...]} format
                                actions_data = updated_input['actions']
                                if isinstance(actions_data, list) and actions_data and isinstance(actions_data[0], dict):
                                    action = [ActionModel(**item) if isinstance(item, dict) else item for item in actions_data]
                                else:
                                    action = actions_data
                                message.payload = action
                            else:
                                # Treat single dict as one action
                                action = [ActionModel(**updated_input)]
                                message.payload = action
                        logger.info(f"PRE_TOOL_CALL hook modified input for tool {self.name()}")
                        # Continue to next hook to allow chaining

            await self.pre_step(action, message=message,**kwargs)
            res = await self.do_step(action, message=message, **kwargs)

            # Execute POST_TOOL_CALL hooks and check for updated_output
            post_hook_events = await self.run_hooks(message=message, hook_point=HookPoint.POST_TOOL_CALL, hook_from=message.sender,
                                 payload=res)

            # Apply updated_output from hooks if present (chain all modifications)
            for hook_event in post_hook_events:
                if hook_event and hasattr(hook_event, 'headers'):
                    updated_output = hook_event.headers.get('updated_output')
                    if updated_output:
                        # Update res with modified output
                        if isinstance(updated_output, tuple) and len(updated_output) == 5:
                            res = updated_output
                        elif isinstance(updated_output, dict):
                            # Allow hook to modify specific parts of res tuple
                            res_list = list(res)
                            if 'observation' in updated_output:
                                obs = updated_output['observation']
                                # Convert dict to Observation object if needed
                                if isinstance(obs, dict) and not isinstance(obs, Observation):
                                    res_list[0] = Observation(**obs)
                                else:
                                    res_list[0] = obs
                            if 'reward' in updated_output:
                                res_list[1] = updated_output['reward']
                            if 'done' in updated_output:
                                res_list[2] = updated_output['done']
                            if 'truncated' in updated_output:
                                res_list[3] = updated_output['truncated']
                            if 'info' in updated_output:
                                res_list[4] = updated_output['info']
                            res = tuple(res_list)
                        logger.info(f"POST_TOOL_CALL hook modified output for tool {self.name()}")
                        # Continue to next hook to allow chaining

            final_res = await self.post_step(res, action, message=message,**kwargs)
            await self._internal_process(res, action, message, tool_id_mapping=tool_id_mapping, **kwargs)
            if isinstance(final_res, Message):
                self._update_headers(final_res, message)
            if message.group_id and message.headers.get('level', 0) == 0:
                from aworld.runners.state_manager import RuntimeStateManager, RunNodeStatus, RunNodeBusiType
                state_mng = RuntimeStateManager.instance()
                await state_mng.finish_sub_group(message.group_id, message.headers.get('root_message_id'), [final_res])
                final_res.headers['_tool_finished'] = True
            return final_res
        except Exception as e:
            logger.error(
                f"Failed to execute {self.name()}: {e}."
                f"Debug info: session_id = {message.session_id}, action = {message.payload}."
                f"Traceback:\n{traceback.format_exc()}"
            )

            # Trigger TOOL_CALL_FAILED hook
            try:
                await self.run_hooks(
                    message=message,
                    hook_point=HookPoint.TOOL_CALL_FAILED,
                    hook_from=message.sender,
                    payload={
                        'tool_name': self.name(),
                        'action': action,
                        'error': str(e),
                        'error_type': type(e).__name__,
                        'traceback': traceback.format_exc()
                    }
                )
            except Exception as hook_e:
                logger.warning(f"TOOL_CALL_FAILED hook execution failed: {hook_e}")

            raise e
        finally:
            logger.warning(
                f"Tool {self.name()} result: {final_res}, session_id: {message.session_id}, task_id: {message.context.task_id}"
            )
            if aworld.debug_mode:
                logger.info(f"{self.name()} {[act.action_name for act in action]} payload: {final_res.payload}")

    async def post_step(self,
                        step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                        action: List[ActionModel],
                        message: Message,
                        **kwargs) -> Tuple[Observation, float, bool, bool, Dict[str, Any]] | Message:
        if not step_res:
            raise Exception(f'{self.name()} no observation has been made.')

        step_res[0].from_agent_name = action[0].agent_name
        for idx, act in enumerate(action):
            step_res[0].action_result[idx].tool_call_id = act.tool_call_id
            step_res[0].action_result[idx].tool_name = act.tool_name

        context = message.context
        if context.swarm:
            agent = context.swarm.agents.get(action[0].agent_name)
            feedback_tool_result = agent.feedback_tool_result if agent else False
        else:
            feedback_tool_result = True
        if feedback_tool_result:
            result = AgentMessage(payload=step_res,
                                caller=action[0].agent_name,
                                sender=self.name(),
                                receiver=action[0].agent_name,
                                session_id=context.session_id,
                                headers={"context": context})
        else:
            result = AgentMessage(payload=step_res,
                                sender=action[0].agent_name,
                                session_id=context.session_id,
                                headers={"context": context})
        return result

    async def _exec_tool_callback(self, step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                                  action: List[ActionModel],
                                  message: Message,
                                  **kwargs):
        from aworld.runners.state_manager import RunNodeStatus

        logger.info(f"send callback message: {message}")
        # Send via message system by default
        results = None
        try:
            future = await send_message_with_future(message)
            logger.debug(f"Waiting for callback response, message_id: {message.id}, timeout: 300s")
            results = await future.wait(timeout=300)
            if not results:
                logger.warning(f"context write task failed: no results received for message {message.id}")
                return  # Early return if no results
            logger.debug(f"Callback response received successfully for message {message.id}")
        except TimeoutError as e:
            logger.error(f"context write task timeout after 300s, message_id: {message.id}, receiver: {message.receiver}")
            return  # Early return on timeout
        except Exception as e:
            logger.warn(f"context write task failed: {traceback.format_exc()}")
            return  # Early return on exception

        tool_act_results = step_res[0].action_result
        callback_act_results = results.results
        if not callback_act_results:
            logger.warn(
                f"tool {self.name()} callback finished with empty node result.")
            return
        if len(tool_act_results) != len(callback_act_results):
            logger.warn(
                "tool action result and callback action result length not match.")
            return
        for idx, res in enumerate(callback_act_results):
            if res.status == RunNodeStatus.SUCCESS:
                callback_res = res.result.payload
                if isinstance(callback_res, CallbackResult):
                    if callback_res.callback_action_type == CallbackActionType.OVERRIDE:
                        tool_act_results[idx].content = callback_res.result_data
            else:
                logger.warn(
                    f"tool {self.name()} callback finished with node result: {res}.")
                continue

        return

    async def _add_tool_results_to_memory(self,
                                          step_res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
                                          action: List[ActionModel],
                                          context: Context):
        try:
            if not step_res or not action:
                return
            observation = step_res[0]
            if not hasattr(observation, 'action_result') or observation.action_result is None:
                return
            for idx, act in enumerate(action):
                if idx >= len(observation.action_result):
                    continue
                tool_result = observation.action_result[idx]
                receive_agent = None
                if context.swarm and context.swarm.agents:
                    receive_agent = context.swarm.agents.get(act.agent_name)
                if not receive_agent:
                    logger.warning(f"agent {act.agent_name} not found in swarm {context.swarm}.")
                    return
                memory_msg = MemoryEventMessage(
                    payload=tool_result,
                    agent=receive_agent,
                    memory_event_type=MemoryEventType.TOOL,
                    session_id=context.session_id if context else "",
                    headers={"context": context}
                )

                # Send via message system (DIRECT mode handling is now in send_message_with_future)
                try:
                    future = await send_message_with_future(memory_msg)
                    results = await future.wait(context=context)
                    if not results:
                        logger.warning(f"Memory write task failed: {memory_msg}")
                except Exception as e:
                    logger.warn(f"Memory write task failed: {traceback.format_exc()}")

        except Exception:
            logger.warning(f"AsyncTool {self.name()} write tool results to memory failed: {traceback.format_exc()}")

    def _update_headers(self, message: Message, input_message: Message):
        headers = input_message.headers.copy()
        headers['context'] = message.context
        headers['level'] = headers.get('level', 0) + 1
        message.headers = headers

    async def run_hooks(self, message: Message, hook_point: str, hook_from: str, payload: Any = None) -> List[Message]:
        """Execute hooks and break by exception"""
        hook_events = []
        async for event in run_hooks(context=message.context,
                                     hook_from=hook_from,
                                     payload=payload,
                                     hook_point=hook_point):
            hook_events.append(event)
        return hook_events


class ToolsManager(Factory):
    def __init__(self, type_name: str = None):
        super(ToolsManager, self).__init__(type_name)
        self._tool_with_action = {}
        self._tool_conf = {}
        self._tool_instance = {}
        self._asyn = {}

    def __iter__(self):
        for name in self._cls:
            name = "async_" + name if self._asyn.get(name, False) else name
            yield name

    def __contains__(self, name: str) -> bool:
        """Whether the name in the factory."""
        name = "async_" + name if self._asyn.get(name, False) else name
        return name in self._cls

    def __call__(self, name: str = None, *args, **kwargs):
        if name is None:
            return self

        asyn = kwargs.pop("asyn", False)
        self._asyn[name] = asyn
        name = "async_" + name if asyn else name

        conf = self._tool_conf.get(name)
        if not conf:
            logger.warning(f"{name} not find conf in tool factory")
            conf = dict()
        elif isinstance(conf, BaseModel):
            conf = conf.model_dump()

        user_conf = kwargs.pop('conf', None)
        if user_conf:
            if isinstance(user_conf, dict):
                conf.update(user_conf)
            elif isinstance(user_conf, BaseModel):
                conf.update(user_conf.model_dump())
            else:
                logger.warning(
                    f"Unknown conf type: {type(user_conf)}, ignored!")
        self._tool_conf[name] = conf

        # must is a dict
        conf['name'] = name
        conf = ConfigDict(conf)

        if kwargs.get("reuse", conf.get('reuse', False)) is True and name in self._tool_instance:
            return self._tool_instance[name]

        if name in self._cls:
            tool = self._cls[name](conf=conf, **kwargs)
            self._tool_instance[name] = tool
        else:
            raise RuntimeError(
                f"can not find {name} tool in the ToolFactory, register it first.")

        action_executor.register(name, tool)
        return tool

    def desc(self, name: str) -> str:
        """Obtain the description by name."""
        name = "async_" + name if self._asyn.get(name) else name
        return self._desc.get(name, "")

    def get_ext_info(self, name: str) -> Dict[Any, Any]:
        """Obtain the extent info by name."""
        name = "async_" + name if self._asyn.get(name) else name
        return self._ext_info.get(name, {})

    def get_tool_action(self, tool: str, asyn: bool = False):
        if asyn:
            tool = "async_" + tool
        return self._tool_with_action.get(tool)

    def register(self, name: str, desc: str, supported_action: ToolAction = None, conf_file_name: str = None, **kwargs):
        """Register a tool to tool factory.

        Args:
            name: Tool name
            desc: Tool description
            supported_action: Tool abilities
            conf_file_name: Default tool config
        """
        asyn = kwargs.pop("asyn", False)
        prefix = "async_" if asyn else ""
        res = super(ToolsManager, self).register(prefix + name, desc, **kwargs)
        conf_file_name = conf_file_name if conf_file_name else f"{name}_tool.yaml"
        conf = load_config(conf_file_name, kwargs.get("dir"))
        if not conf:
            logger.debug(f"can not load conf from {conf_file_name}")
            # use general tool config
            conf = ToolConfig().model_dump()
        name = prefix + name
        self._tool_with_action[name] = supported_action
        self._tool_conf[name] = conf
        logger.debug(f"{name} register to the tool factory.")
        return res

    def unregister(self, name: str):
        super().unregister(name)
        if name in self._asyn:
            del self._asyn[name]
            del self._tool_conf[name]
            del self._tool_instance[name]
            del self._tool_with_action[name]

ToolFactory = ToolsManager("env_tool_type")


class ToolActionExecutor(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, tool: Tool = None):
        self.tool = tool
        self.tools: Dict[str, Tool] = {}

    def register(
            self,
            name: str,
            tool: Union[Tool, AsyncTool]):
        self.tools[name] = tool

    @abc.abstractmethod
    def execute_action(self, actions: List[ActionModel], **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        return self.execute_env_action(actions, self.tool, **kwargs)

    @abc.abstractmethod
    async def async_execute_action(self, actions: List[ActionModel], **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        return await self.async_execute_env_action(actions, self.tool, **kwargs)

    @abc.abstractmethod
    def execute_env_action(self,
                           actions: List[ActionModel],
                           tool: Tool,
                           **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        action_results = []
        ctx = None
        for action in actions:
            if action is None:
                logger.warning("empty action, ignore it.")
                continue

            if tool is None:
                tool_name = action.tool_name
                tool = self.tools.get(tool_name)
                if tool is None:
                    tool = ToolFactory(
                        tool_name, conf=kwargs.get("conf", ToolConfig()))
                    self.tools[tool_name] = tool

            try:
                action_result, ctx = self.do_act(action, tool, **kwargs)
            except:
                logger.warning(traceback.format_exc())
                action_result = ActionResult(
                    error=traceback.format_exc(), success=False)
            action_result.action_name = action.action_name
            action_result.tool_name = action.tool_name
            action_results.append(action_result)
        return action_results, ctx

    async def async_execute_env_action(self,
                                       actions: List[ActionModel],
                                       tool: Tool,
                                       **kwargs) -> Tuple[List[ActionResult], Any]:
        """"""
        action_results = []
        ctx = None
        for action in actions:
            if action is None:
                logger.warning("empty action, ignore it.")
                continue

            if tool is None:
                tool_name = action.tool_name
                tool = self.tools.get(tool_name)
                if tool is None:
                    tool = ToolFactory(
                        tool_name, conf=kwargs.get("conf", ToolConfig()))
                    self.tools[tool_name] = tool
            try:
                action_result, ctx = await self.async_do_act(action, tool, **kwargs)
            except:
                logger.warning(traceback.format_exc())
                action_result = ActionResult(
                    error=traceback.format_exc(), success=False)
            action_result.action_name = action.action_name
            action_result.tool_name = action.tool_name
            action_results.append(action_result)
        return action_results, ctx

    def do_act(self, action_model: ActionModel, tool: Tool, **kwargs):
        action_name = action_model.action_name
        if action_name not in ActionFactory:
            action_name = action_model.tool_name + action_model.action_name
            if action_name not in ActionFactory:
                raise ValueError(
                    f'Action {action_model.action_name} not found in ActionFactory')

        action = ActionFactory(action_name)
        action_result, page = action.act(action_model, tool=tool, **kwargs)
        logger.info(
            f"{tool.name()}-{action_model.action_name} execute finished")
        return action_result, page

    async def async_do_act(self, action_model: ActionModel, tool: Tool,
                           **kwargs):
        action_name = action_model.action_name
        if action_name not in ActionFactory:
            action_name = action_model.tool_name + action_model.action_name
            if action_name not in ActionFactory:
                # for auto register
                if action_name.startswith("async_"):
                    action_name = action_name[6:]
                    if action_name not in ActionFactory:
                        raise ValueError(
                            f'Action {action_model.action_name} not found in ActionFactory')

        action = ActionFactory(action_name)
        action_result, page = await action.async_act(action_model, tool=tool, **kwargs)
        logger.info(f"{tool.name()}-{action_model.action_name} execute finished")
        return action_result, page


action_executor = ToolActionExecutor()
