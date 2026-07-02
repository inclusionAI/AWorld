# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import abc
import inspect
import time
import traceback
from typing import Dict, Tuple, Any, TypeVar, Generic, List, Union, Callable

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
from aworld.runners.post_tool_progress import arm_post_tool_progress_watchdog
from aworld.utils.common import convert_to_snake, sync_exec

AgentInput = TypeVar("AgentInput")
ToolInput = TypeVar("ToolInput")

# Forward declaration of action_executor to fix NameError
action_executor = None


async def maybe_await(result: Any) -> Any:
    """Resolve sync or async hook/reset results uniformly.

    Some call sites operate on ``AsyncTool`` instances and expect async lifecycle
    methods, but a few implementations still return plain values. Accept both so
    tool initialization is resilient to mixed implementations.
    """
    if inspect.isawaitable(result):
        return await result
    return result


def ensure_action_results(
    observation: Observation,
    actions: List["ActionModel"],
    *,
    success: bool,
    default_content: Any = None,
    error: str = None,
) -> Observation:
    """Ensure an observation has one ActionResult per action.

    Some tools return only ``Observation(content=...)`` on error paths. The
    framework's post-processing expects ``action_result`` entries to exist, so
    synthesize them when missing to avoid secondary crashes masking the real
    tool failure.
    """
    if observation.action_result is None:
        observation.action_result = []

    while len(observation.action_result) < len(actions):
        action_result_kwargs = {
            "is_done": True,
            "success": success,
            "content": default_content if default_content is not None else observation.content,
        }
        if error is not None:
            action_result_kwargs["error"] = error
        observation.action_result.append(ActionResult(**action_result_kwargs))
    return observation


def _iter_hook_headers(hook_events: List[Message]):
    for hook_event in hook_events:
        if hook_event and hasattr(hook_event, 'headers') and hook_event.headers is not None:
            yield hook_event.headers


def _apply_hook_headers_to_message(target_message: Message, hook_events: List[Message]):
    """Propagate user-visible hook metadata onto the in-flight tool message."""
    for headers in _iter_hook_headers(hook_events):
        system_message = headers.get('system_message')
        if system_message:
            target_message.headers['system_message'] = system_message

        additional_context = headers.get('additional_context')
        if additional_context:
            existing_context = target_message.headers.get('additional_context', '')
            target_message.headers['additional_context'] = (
                f"{existing_context}\n{additional_context}".strip()
            )


def _coerce_updated_input(updated_input: Any) -> List[ActionModel] | None:
    if isinstance(updated_input, list):
        if updated_input and isinstance(updated_input[0], dict):
            return [ActionModel(**item) if isinstance(item, dict) else item for item in updated_input]
        return updated_input

    if isinstance(updated_input, dict):
        if 'actions' in updated_input:
            actions_data = updated_input['actions']
            if isinstance(actions_data, list) and actions_data and isinstance(actions_data[0], dict):
                return [ActionModel(**item) if isinstance(item, dict) else item for item in actions_data]
            return actions_data
        return [ActionModel(**updated_input)]

    return None


def _coerce_updated_output(
    res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
    updated_output: Any,
) -> Tuple[Observation, float, bool, bool, Dict[str, Any]] | None:
    if isinstance(updated_output, tuple) and len(updated_output) == 5:
        return updated_output

    if isinstance(updated_output, dict):
        res_list = list(res)
        if 'observation' in updated_output:
            obs = updated_output['observation']
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
        return tuple(res_list)

    return None


async def _resolve_sync_tool_permission(
    permission_decision: str,
    stop_reason: str,
    context_dict: Dict[str, Any],
) -> Tuple[str, str]:
    from aworld.runners.hook.v2.permission import get_permission_handler
    handler = get_permission_handler()
    return handler.resolve_permission_sync(permission_decision, stop_reason, context_dict)


async def _resolve_async_tool_permission(
    permission_decision: str,
    stop_reason: str,
    context_dict: Dict[str, Any],
) -> Tuple[str, str]:
    from aworld.runners.hook.v2.permission import get_permission_handler
    handler = get_permission_handler()
    return await handler.resolve_permission(permission_decision, stop_reason, context_dict)


async def _process_pre_tool_hook_events(
    *,
    hook_events: List[Message],
    tool_name: str,
    action: List[ActionModel],
    message: Message,
    resolve_permission: Callable[[str, str, Dict[str, Any]], Any],
) -> List[ActionModel]:
    for headers in _iter_hook_headers(hook_events):
        permission_decision = headers.get('permission_decision')
        if permission_decision not in ('deny', 'ask'):
            continue

        stop_reason = headers.get('permission_decision_reason', 'Tool execution requires permission')
        if permission_decision == 'ask':
            try:
                context_dict = {
                    'tool_name': tool_name,
                    'action': [act.model_dump() for act in action] if action else []
                }
                final_decision, resolution_reason = await maybe_await(
                    resolve_permission(permission_decision, stop_reason, context_dict)
                )
                if final_decision == 'deny':
                    logger.warning(f"PRE_TOOL_CALL hook denied tool {tool_name} after resolution: {resolution_reason}")
                    raise ToolExecutionDenied(tool_name, resolution_reason)
                logger.info(f"PRE_TOOL_CALL hook allowed tool {tool_name} after resolution: {resolution_reason}")
            except ToolExecutionDenied:
                raise
            except Exception as e:
                logger.error(f"Failed to resolve permission 'ask': {e}")
                raise ToolExecutionDenied(tool_name, f"Permission resolution failed: {e}")
        else:
            logger.warning(f"PRE_TOOL_CALL hook denied tool {tool_name}: {stop_reason}")
            raise ToolExecutionDenied(tool_name, stop_reason)

    current_action = action
    for headers in _iter_hook_headers(hook_events):
        updated_input = headers.get('updated_input')
        if not updated_input:
            continue

        rewritten_action = _coerce_updated_input(updated_input)
        if rewritten_action is not None:
            current_action = rewritten_action
            message.payload = current_action
        logger.info(f"PRE_TOOL_CALL hook modified input for tool {tool_name}")

    return current_action


def _process_post_tool_hook_events(
    *,
    hook_events: List[Message],
    tool_name: str,
    res: Tuple[Observation, float, bool, bool, Dict[str, Any]],
) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
    current_res = res
    for headers in _iter_hook_headers(hook_events):
        updated_output = headers.get('updated_output')
        if not updated_output:
            continue

        rewritten_res = _coerce_updated_output(current_res, updated_output)
        if rewritten_res is not None:
            current_res = rewritten_res
        logger.info(f"POST_TOOL_CALL hook modified output for tool {tool_name}")

    return current_res


async def _emit_tool_failed_hooks(
    *,
    run_hooks_fn: Callable[..., Any],
    message: Message,
    hook_from: str,
    tool_name: str,
    action: List[ActionModel],
    error: Exception,
    error_traceback: str,
):
    try:
        await maybe_await(
            run_hooks_fn(
                message=message,
                hook_point=HookPoint.TOOL_CALL_FAILED,
                hook_from=hook_from,
                payload={
                    'tool_name': tool_name,
                    'action': action,
                    'error': str(error),
                    'error_type': type(error).__name__,
                    'traceback': error_traceback
                }
            )
        )
    except Exception as hook_e:
        logger.warning(f"TOOL_CALL_FAILED hook execution failed: {hook_e}")


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
                metadata = dict(step_res[0].action_result[idx].metadata or {})
                if input_message.headers.get("system_message"):
                    metadata["system_message"] = input_message.headers["system_message"]
                updated_output = input_message.headers.get("updated_output")
                if isinstance(updated_output, dict) and "info" in updated_output:
                    metadata["hook_info"] = updated_output["info"]
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
                    metadata=metadata,
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
            action = sync_exec(
                _process_pre_tool_hook_events,
                hook_events=pre_hook_events,
                tool_name=self.name(),
                action=action,
                message=message,
                resolve_permission=_resolve_sync_tool_permission,
            )

            _apply_hook_headers_to_message(message, pre_hook_events)

            self.pre_step(action, **kwargs)
            res = self.do_step(action, message=message, **kwargs)

            # Execute POST_TOOL_CALL hooks and check for updated_output
            post_hook_events = self.run_hooks(message=message, hook_point=HookPoint.POST_TOOL_CALL, hook_from=message.sender, payload=res)
            res = _process_post_tool_hook_events(
                hook_events=post_hook_events,
                tool_name=self.name(),
                res=res,
            )

            _apply_hook_headers_to_message(message, post_hook_events)

            final_res = self.post_step(res, action,message=message, **kwargs)
            if isinstance(final_res, Message):
                self._update_headers(final_res, message)
            self._internal_process(
                res, action, message, tool_id_mapping=tool_id_mapping, **kwargs)
            return final_res
        except Exception as e:
            error_traceback = traceback.format_exc()
            logger.error(
                f"Failed to execute {self.name()}: {e}."
                f"Debug info: session_id = {message.session_id}, action = {message.payload}."
                f"Traceback:\n{error_traceback}"
            )

            sync_exec(
                _emit_tool_failed_hooks,
                run_hooks_fn=self.run_hooks,
                message=message,
                hook_from=message.sender,
                tool_name=self.name(),
                action=action,
                error=e,
                error_traceback=error_traceback,
            )

            raise
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
        info = step_res[4] if len(step_res) > 4 and isinstance(step_res[4], dict) else {}
        if info:
            merged_info = dict(step_res[0].info or {})
            merged_info.update(info)
            step_res[0].info = merged_info
        ensure_action_results(
            step_res[0],
            action,
            success=step_res[1] > 0,
            default_content=step_res[0].content,
            error=info.get('error'),
        )

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
            arm_post_tool_progress_watchdog(
                context,
                tool_name=self.name(),
                agent_id=action[0].agent_name,
                actions=action,
                followup_observation=step_res[0],
                followup_sender=self.name(),
            )
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
        import asyncio
        from aworld.runners.hook.utils import run_hooks as async_run_hooks

        # If payload provided, update message instead of creating new one
        if payload is not None:
            message.payload = payload

        # Get workspace_path from context
        workspace_path = getattr(message.context, 'workspace_path', None)

        # Use async run_hooks, passing original message
        hook_events = []

        async def _run():
            async for event in async_run_hooks(
                context=message.context,
                hook_point=hook_point,
                hook_from=hook_from,
                message=message,
                workspace_path=workspace_path
            ):
                hook_events.append(event)

        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # In async context, use sync_exec
                from aworld.utils.common import sync_exec
                sync_exec(_run)
            else:
                loop.run_until_complete(_run())
        except Exception as e:
            logger.warning(f"Hook {hook_point} execution failed: {traceback.format_exc()}")
            raise e

        return hook_events

    def _update_headers(self, message: Message, input_message: Message):
        headers = input_message.headers.copy()
        headers['context'] = message.context
        headers['level'] = headers.get('level', 0) + 1
        message.headers = headers


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
                metadata = dict(step_res[0].action_result[idx].metadata or {})
                if input_message.headers.get("system_message"):
                    metadata["system_message"] = input_message.headers["system_message"]
                updated_output = input_message.headers.get("updated_output")
                if isinstance(updated_output, dict) and "info" in updated_output:
                    metadata["hook_info"] = updated_output["info"]
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
                    metadata=metadata,
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

        agent_name = f"{action[0].agent_name if action else ''}"
        closed_step = context.close_step(
            namespace=agent_name or None,
            expected_name=agent_name or None,
        ) if context is not None else None
        await send_message(Message(
            category=Constants.OUTPUT,
            payload=StepOutput.build_finished_output(
                name=closed_step["name"] if closed_step else agent_name,
                alias_name=closed_step["alias_name"] if closed_step else None,
                step_num=closed_step["step_num"] if closed_step else 0,
                task_id=context.task_id,
                step_id=closed_step["step_id"] if closed_step else None,
                parent_step_id=closed_step["parent_step_id"] if closed_step else None,
            ),
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
            action = await _process_pre_tool_hook_events(
                hook_events=pre_hook_events,
                tool_name=self.name(),
                action=action,
                message=message,
                resolve_permission=_resolve_async_tool_permission,
            )

            _apply_hook_headers_to_message(message, pre_hook_events)

            await self.pre_step(action, message=message,**kwargs)
            res = await self.do_step(action, message=message, **kwargs)

            # Execute POST_TOOL_CALL hooks and check for updated_output
            post_hook_events = await self.run_hooks(message=message, hook_point=HookPoint.POST_TOOL_CALL, hook_from=message.sender,
                                 payload=res)
            res = _process_post_tool_hook_events(
                hook_events=post_hook_events,
                tool_name=self.name(),
                res=res,
            )

            _apply_hook_headers_to_message(message, post_hook_events)

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
            error_traceback = traceback.format_exc()
            logger.error(
                f"Failed to execute {self.name()}: {e}."
                f"Debug info: session_id = {message.session_id}, action = {message.payload}."
                f"Traceback:\n{error_traceback}"
            )

            await _emit_tool_failed_hooks(
                run_hooks_fn=self.run_hooks,
                message=message,
                hook_from=message.sender,
                tool_name=self.name(),
                action=action,
                error=e,
                error_traceback=error_traceback,
            )

            raise
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

        info = step_res[4] if len(step_res) > 4 and isinstance(step_res[4], dict) else {}
        if info:
            merged_info = dict(step_res[0].info or {})
            merged_info.update(info)
            step_res[0].info = merged_info
        ensure_action_results(
            step_res[0],
            action,
            success=step_res[1] > 0,
            default_content=step_res[0].content,
            error=info.get('error'),
        )
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
            arm_post_tool_progress_watchdog(
                context,
                tool_name=self.name(),
                agent_id=action[0].agent_name,
                actions=action,
                followup_observation=step_res[0],
                followup_sender=self.name(),
            )
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

        context = message.context
        if not getattr(context, 'event_manager', None):
            logger.debug(
                f"Skip tool callback wait for message {message.id}: "
                f"no active event_manager in context."
            )
            return

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
        workspace_path = getattr(message.context, 'workspace_path', None)
        hook_events = []
        async for event in run_hooks(context=message.context,
                                     hook_from=hook_from,
                                     payload=payload,
                                     hook_point=hook_point,
                                     workspace_path=workspace_path):
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
