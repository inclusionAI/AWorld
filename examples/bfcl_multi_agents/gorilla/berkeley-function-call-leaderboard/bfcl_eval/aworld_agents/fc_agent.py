import sys, os
import time
import json
import traceback
from datetime import datetime

__root_path__ = os.path.dirname(os.path.abspath(__file__))
for _ in range(6):
    __root_path__ = os.path.dirname(__root_path__)
sys.path.append(__root_path__)

from typing import Dict, Any, List, Union, Callable

from aworld.agents.llm_agent import Agent
from aworld.core.context.prompts.string_prompt_template import StringPromptTemplate
import aworld.trace as trace
from aworld.config.conf import AgentConfig, ConfigDict, ContextRuleConfig, OptimizationConfig, \
    LlmCompressionConfig
from aworld.core.agent.agent_desc import get_agent_desc
from aworld.core.agent.base import AgentFactory, BaseAgent, AgentResult, is_agent_by_name, is_agent, AgentStatus
from aworld.core.common import ActionResult, Observation, ActionModel
from aworld.core.context.base import Context
from aworld.core.context.processor.prompt_processor import PromptProcessor
from aworld.core.event import eventbus
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, GroupMessage, TopicType
from aworld.core.memory import AgentMemoryConfig
from aworld.core.tool.tool_desc import get_tool_desc
from aworld.events.util import send_message
from aworld.logs.util import logger, color_log, Color
from aworld.mcp_client.utils import sandbox_mcp_tool_desc_transform
from aworld.memory.main import MemoryFactory
from aworld.memory.models import MessageMetadata, MemoryAIMessage, MemoryToolMessage, MemoryHumanMessage, \
    MemorySystemMessage, MemoryMessage
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.models.model_response import ModelResponse, ToolCall
from aworld.models.utils import tool_desc_transform, agent_desc_transform
from aworld.output import Outputs
from aworld.output.base import StepOutput, MessageOutput, Output
from aworld.runners.hook.hooks import HookPoint
from aworld.trace.constants import SPAN_NAME_PREFIX_AGENT
from aworld.trace.instrumentation import semconv
from aworld.utils.common import sync_exec, nest_dict_counter


class FCModelAgent(Agent):
    def __init__(self, **kwargs):
        super(FCModelAgent, self).__init__(**kwargs)
        self.bfcl_tools = kwargs.get("bfcl_tools", [])

    def update_bfcl_tools(self, new_bfcl_tools):
        self.bfcl_tools = new_bfcl_tools

    async def _add_system_message_to_memory(self, context: Context, content: str):
        if not self.system_prompt:
            return
        session_id = context.get_task().session_id
        task_id = context.get_task().id
        user_id = context.get_task().user_id

        histories = self.memory.get_last_n(0, filters={
            "agent_id": self.id(),
            "session_id": session_id,
        }, agent_memory_config=self.memory_config)
        if histories and len(histories) > 0:
            logger.debug(
                f"🧠 [MEMORY:short-term] histories is not empty, do not need add system input to agent memory")
            return
        if not self.system_prompt:
            return
        content = await self.custom_system_prompt(context=context, content=content, tool_list=self.tools)
        logger.info(f'system prompt content: {content}')

        await self.memory.add(MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name(),
            )
        ), agent_memory_config=self.memory_config)
        logger.info(
            f"🧠 [MEMORY:short-term] Added system input to agent memory:  Agent#{self.id()}, 💬 {content[:100]}...")

    async def async_messages_transform(self,
                                       image_urls: List[str] = None,
                                       observation: Observation = None,
                                       message: Message = None,
                                       **kwargs):
        """Transform the original content to LLM messages of native format.

        Args:
            content: User content.
            image_urls: List of images encoded using base64.
            sys_prompt: Agent system prompt.
            max_step: The maximum list length obtained from memory.
        Returns:
            Message list for LLM.
        """
        agent_prompt = self.agent_prompt
        messages = []
        # append sys_prompt to memory
        await self._add_system_message_to_memory(context=message.context, content=observation.content)

        session_id = message.context.get_task().session_id
        task_id = message.context.get_task().id
        histories = self.memory.get_all(filters={
            "agent_id": self.id(),
            "session_id": session_id,
            "task_id": task_id,
            "memory_type": "message"
        })
        last_history = histories[-1] if histories and len(histories) > 0 else None

        wait_to_process_content = observation.content
        if type(wait_to_process_content) == list:
            # tool_res = ActionResult(tool_call_id="111", content="this is tool result")
            for _content in wait_to_process_content:
                # append observation to memory
                try:
                    if _content['role'] == 'agent_tool_call':
                        continue

                    if _content['role'] == "tool":
                        tool_res = ActionResult(tool_call_id=_content['tool_call_id'], content=_content['content'])
                        await self._add_tool_result_to_memory(_content['tool_call_id'], tool_result=tool_res,
                                                            context=message.context)
                    else:
                        # FIXME:这里需要修改。
                        content = _content['content']
                        logger.debug(f"agent_prompt: {agent_prompt}")
                        if agent_prompt:
                            content = agent_prompt.format(task=content, current_date=datetime.now().strftime("%Y-%m-%d"))
                        if image_urls:
                            urls = [{'type': 'text', 'text': content}]
                            for image_url in image_urls:
                                urls.append(
                                    {'type': 'image_url', 'image_url': {"url": image_url}})
                            content = urls
                        await self._add_human_input_to_memory(content, message.context, memory_type="message")
                except Exception as e:
                    logger.warning(f"Failed to process messages in messages_transform: {e} : {_content}")
        else:

            # append observation to memory
            if observation.is_tool_result:
                for action_item in observation.action_result:
                    tool_call_id = action_item.tool_call_id
                    await self._add_tool_result_to_memory(tool_call_id, tool_result=action_item, context=message.context)
            elif not self.use_tools_in_prompt and last_history and last_history.metadata and "tool_calls" in last_history.metadata and \
                    last_history.metadata[
                        'tool_calls']:
                for tool_call in last_history.metadata['tool_calls']:
                    tool_call_id = tool_call['id']
                    tool_name = tool_call['function']['name']
                    if tool_name and tool_name == message.sender:
                        await self._add_tool_result_to_memory(tool_call_id, tool_result=observation.content,
                                                            context=message.context)
                        break
            else:
                content = observation.content
                logger.debug(f"agent_prompt: {agent_prompt}")
                if agent_prompt:
                    content = agent_prompt.format(task=content, current_date=datetime.now().strftime("%Y-%m-%d"))
                if image_urls:
                    urls = [{'type': 'text', 'text': content}]
                    for image_url in image_urls:
                        urls.append(
                            {'type': 'image_url', 'image_url': {"url": image_url}})
                    content = urls
                await self._add_human_input_to_memory(content, message.context, memory_type="message")

        # from memory get last n messages
        histories = self.memory.get_last_n(self.history_messages, filters={
            "agent_id": self.id(),
            "session_id": session_id,
            # "task_id": task_id
        }, agent_memory_config=self.memory_config)
        if histories:
            # default use the first tool call
            for history in histories:
                if isinstance(history, MemoryMessage):
                    messages.append(history.to_openai_message())
                else:
                    if not self.use_tools_in_prompt and "tool_calls" in history.metadata and history.metadata[
                        'tool_calls']:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         'tool_calls': [history.metadata["tool_calls"][0]]})
                    else:
                        messages.append({'role': history.metadata['role'], 'content': history.content,
                                         "tool_call_id": history.metadata.get("tool_call_id")})

        # truncate and other process
        try:
            messages = self._process_messages(messages=messages, context=self.context)
            # print(messages[1:])
        except Exception as e:
            logger.warning(f"Failed to process messages in messages_transform: {e}")
            logger.debug(f"Process messages error details: {traceback.format_exc()}")
        return messages


    def bfcl_parse_query_response_FC(self, api_response: any):
        try:
            model_responses = [
                {func_call.function.name: func_call.function.arguments}
                for func_call in api_response.tool_calls
            ]
            tool_call_ids = [
                func_call.id for func_call in api_response.tool_calls
            ]
            # model_responses = json.dumps(model_responses)
            model_responses_with_ids = json.dumps({"id": tool_call_ids, "response": model_responses})
        except Exception as e:
            model_responses_with_ids = api_response.message['content']
        
        return model_responses_with_ids


    async def _add_llm_response_to_memory(self, llm_response, context: Context):
        """Add LLM response to memory"""
        custom_prompt_tool_calls = []
        if self.use_tools_in_prompt:
            custom_prompt_tool_calls = self.use_tool_list(llm_response)
        if not context.get_task():
            logger.error(f"Task is None")
        session_id = context.get_task().session_id
        user_id = context.get_task().user_id
        task_id = context.get_task().id

        await self.memory.add(MemoryAIMessage(
            content=llm_response.content,
            tool_calls=llm_response.tool_calls if not self.use_tools_in_prompt else custom_prompt_tool_calls,
            metadata=MessageMetadata(
                session_id=session_id,
                user_id=user_id,
                task_id=task_id,
                agent_id=self.id(),
                agent_name=self.name()
            )
        ), agent_memory_config=self.memory_config)
        logger.info(f"🧠 [MEMORY:short-term] Added LLM response to task memory: "
                    f"User#{user_id}, "
                    f"Session#{session_id}, "
                    f"Task#{task_id}, "
                    f"Agent#{self.id()},"
                    f" 💬 tool_calls size: {len(llm_response.tool_calls) if llm_response.tool_calls else 0},"
                    f" content: {llm_response.content[:100] if llm_response.content else ''}... ")


    def policy(self, observation: Observation, info: Dict[str, Any] = {}, message: Message = None, **kwargs) -> List[ActionModel]:
        """
            The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

            Args:
                observation: The state observed from tools in the environment.
                info: Extended information is used to assist the agent to decide a policy.

            Returns:
                ActionModel sequence from agent policy
        """
        output = None
        if kwargs.get("output") and isinstance(kwargs.get("output"), StepOutput):
            output = kwargs["output"]

        # Get current step information for trace recording
        step = kwargs.get("step", 0)
        exp_id = kwargs.get("exp_id", None)
        source_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        try:
            self._run_hooks_sync(self.context, HookPoint.PRE_LLM_CALL)
        except Exception as e:
            logger.warn(traceback.format_exc())

        self._finished = False
        self.desc_transform()
        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
            observation.images = images
        messages = self.messages_transform(content=observation.content,
                                           image_urls=observation.images,
                                           observation=observation,
                                           message=message
                                           )

        self._log_messages(messages)

        llm_response = None
        span_name = f"llm_call_{exp_id}"
        serializable_messages = self._to_serializable(messages)
        with trace.span(span_name) as llm_span:
            llm_span.set_attributes({
                "exp_id": exp_id,
                "step": step,
                "messages": json.dumps(serializable_messages, ensure_ascii=False)
            })
            if source_span:
                source_span.set_attribute("messages", json.dumps(
                    serializable_messages, ensure_ascii=False))

            try:
                # FIXME: 这里写成了强制传递 tools。 很可能导致不满足服务端的传参要求
                _tools = [*self.tools, *self.bfcl_tools] 

                llm_response = call_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=_tools, 
                )

                logger.info(f"Execute response: {llm_response.message}")
            except Exception as e:
                logger.error(traceback.format_exc())
                raise e
            finally:
                if llm_response:
                    if llm_response.error:
                        logger.info(f"llm result error: {llm_response.error}")
                    else:
                        sync_exec(self._add_llm_response_to_memory, llm_response=llm_response, context=message.context)
                        # rewrite
                        self.context.context_info[self.id()] = info
                else:
                    logger.error(f"{self.id()} failed to get LLM response")
                    raise RuntimeError(
                        f"{self.id()} failed to get LLM response")

        try:
            self._run_hooks_sync(self.context, HookPoint.POST_LLM_CALL)
        except Exception as e:
            logger.warn(traceback.format_exc())
        
        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True

        if output:
            output.add_part(MessageOutput(source=llm_response, json_parse=False, task_id=self.context.task_id))
            output.mark_finished()
        return agent_result.actions


    def response_parse(self, resp: ModelResponse) -> AgentResult:
        """Default parse response by LLM."""

        # convert tool calls messages to json string.
        parsed_response = self.bfcl_parse_query_response_FC(resp)
        resp.content = parsed_response
        resp.message['content'] = parsed_response
        # clear tool calls for fc_agent        
        storage_tool_calls = resp.tool_calls
        resp.tool_calls = []
        resp.message['tool_calls'] = []

        results = []
        if not resp:
            logger.warning("LLM no valid response!")
            return AgentResult(actions=[], current_state=None)

        use_tool_list = self.use_tool_list(resp)
        is_call_tool = False
        content = '' if resp.content is None else resp.content
        if resp.tool_calls:
            is_call_tool = True
            for tool_call in resp.tool_calls:
                full_name: str = tool_call.function.name
                if not full_name:
                    logger.warning("tool call response no tool name.")
                    continue
                try:
                    params = json.loads(tool_call.function.arguments)
                except:
                    logger.warning(
                        f"{tool_call.function.arguments} parse to json fail.")
                    params = {}
                # format in framework
                names = full_name.split("__")
                tool_name = names[0]
                logger.info(
                    f"param_info={params} tool_name={tool_name} full_name={full_name} is_agent_by_name={is_agent_by_name(full_name)} AgentFactory._agent_instance={AgentFactory._agent_instance}")
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=tool_call.id,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(
                        names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=tool_call.id,
                                               action_name=action_name,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content))
        elif use_tool_list and len(use_tool_list) > 0:
            is_call_tool = True
            for use_tool in use_tool_list:
                full_name = use_tool["tool"]
                if not full_name:
                    logger.warning("tool call response no tool name.")
                    continue
                params = use_tool["arguments"]
                if not params:
                    logger.warning("tool call response no tool params.")
                    continue
                names = full_name.split("__")
                tool_name = names[0]
                if is_agent_by_name(full_name):
                    param_info = params.get('content', "") + ' ' + params.get('info', '')
                    results.append(ActionModel(tool_name=full_name,
                                               tool_call_id=use_tool.get('id'),
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content + param_info))
                else:
                    action_name = '__'.join(
                        names[1:]) if len(names) > 1 else ''
                    results.append(ActionModel(tool_name=tool_name,
                                               tool_call_id=use_tool.get('id'),
                                               action_name=action_name,
                                               agent_name=self.id(),
                                               params=params,
                                               policy_info=content))
        else:
            if content:
                content = content.replace("```json", "").replace("```", "")
            # no tool call, agent name is itself.
            results.append(ActionModel(
                agent_name=self.id(), policy_info=content))
        
        # is_call_tool always false, we have to call them in BFCL evaluation
        return AgentResult(actions=results, current_state=None, is_call_tool=False)



if __name__ == '__main__':
    ...