# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import abc
import json
import os
import traceback
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Union, Callable
from datetime import datetime

from aworld.core.context.processor.llm_compressor import LLMCompressor
from aworld.core.context.prompts import StringPromptTemplate
from aworld.models.model_response import ModelResponse
import aworld.trace as trace
from aworld.agents.parallel_llm_agent import ParallelizableAgent
from aworld.agents.serial_llm_agent import SerialableAgent
from aworld.config.conf import AgentConfig, ConfigDict, ModelConfig, RunConfig
from aworld.core.agent.base import AgentFactory, BaseAgent, AgentResult, is_agent_by_name, is_agent
from aworld.core.common import Observation, ActionModel
from aworld.core.context.base import AgentContext
from aworld.core.context.base import Context
from aworld.core.event.base import Message, ToolMessage, Constants, AgentMessage, TopicType
from aworld.core.memory import MemoryItem, MemoryConfig
from aworld.logs.util import logger
from aworld.models.llm import get_llm_model, call_llm_model, acall_llm_model, acall_llm_model_stream
from aworld.runner import Runners
from aworld.runners.hook.hooks import HookPoint
from aworld.utils.common import sync_exec
from aworld.agents.llm_agent import Agent
from aworld.runners.utils import choose_runners, execute_runner
from aworld.core.task import Task


simple_extract_prompt = """Please extract key information from the following JSON data, handle Unicode encoding and organize it into a structured text format.

**Requirements:**
1. Correctly decode Unicode characters, for example: \u5730 will be decoded to 地
2. Extract title, summary, and source for each entry
3. Remove duplicate and irrelevant content
4. Identify main topics and key information

**Output Format:**
Please provide a comprehensive text summary that includes:

1. **Main Topics Identified**: List the primary subjects covered in the data
2. **Key Information by Rank**: For each relevant entry, provide:
   - Rank and title
   - Key content points
   - Source information
3. **Overall Summary**: A concise summary of the most important findings
4. **Data Quality Assessment**: Brief evaluation of the information quality

**Guidelines:**
- Focus on the most relevant and high-quality information
- Skip entries that are clearly duplicates or low-quality
- Present information in a clear, readable format
- Use bullet points for better organization

Please process the following data:

{content}
"""

def compress_content(llm_config: ModelConfig, content: str) -> str:
    compressor = LLMCompressor(
        llm_config=llm_config,
        config={"compression_prompt": simple_extract_prompt}
    )
    return compressor.compress(content)


"""创建解析函数的工厂函数"""
def parse_multiple_contents(llm_resp: ModelResponse):
    """解析包含多个内容的工具调用响应"""
    from aworld.core.agent.base import AgentResult
    from aworld.core.common import ActionModel
    
    if llm_resp.tool_calls is None or len(llm_resp.tool_calls) == 0:
        # 如果没有工具调用，返回AgentResult: is_call_tool=False
        return AgentResult(actions=[ActionModel(policy_info=llm_resp.content)], current_state="done", is_call_tool=False)

    actions = []
    
    # 遍历所有的tool_calls，而不是只处理第一个
    for tool_call in llm_resp.tool_calls:
        func_content = tool_call.function
        try:
            arguments = json.loads(func_content.arguments)
            
            # 检查是否有content参数，且content是列表
            if 'content' in arguments and isinstance(arguments['content'], list):
                contents = arguments['content']
                # 为每个content创建一个独立的ActionModel
                for content in contents:
                    new_arguments = {'content': content}
                    print(f"new_arguments: {new_arguments}")
                    actions.append(ActionModel(
                        tool_name=func_content.name,
                        tool_id=f"{tool_call.id}_{content}" if len(contents) > 1 else tool_call.id,
                        agent_name="planer_agent",  # 使用字符串避免循环引用
                        params=new_arguments,
                        policy_info=llm_resp.content or ""
                    ))
            else:
                # 如果content不是列表或不存在，直接使用原始参数
                actions.append(ActionModel(
                    tool_name=func_content.name,
                    tool_id=tool_call.id,
                    agent_name="planer_agent",
                    params=arguments,
                    policy_info=llm_resp.content or ""
                ))
                
        except Exception as e:
            print(f"Failed to parse tool call arguments: {tool_call}, error: {e}")
            # 跳过解析失败的tool_call，继续处理下一个
            continue
    
    print(f'Total tool_calls processed: {len(llm_resp.tool_calls)}')
    print(f'Total actions created: {len(actions)}')
    print(f'Actions: {actions}')
    
    return AgentResult(actions=actions, current_state=None)

class PlanAgent(Agent):
    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        super().__init__(conf, **kwargs)
        self.cur_action_step = 0
        self.max_steps = 10
        self.cur_step = 0
        
        # 初始化trajectories文件相关属性
        self.trajectories_file = None
        self.written_trajectories = set()  # 记录已写入的trajectories key

        # 结果解析函数
        self.resp_parse_func = parse_multiple_contents

        self._finished = False

    def _init_trajectories_file(self):
        """初始化trajectories文件路径"""
        # 创建trajectories目录
        trajectories_dir = Path("./trajectories")
        trajectories_dir.mkdir(exist_ok=True)
        
        # 使用session_id和timestamp生成唯一文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        session_id = getattr(self.context, 'session_id', 'unknown') if hasattr(self, 'context') and self.context else 'unknown'
        filename = f"plan_agent_{timestamp}_{session_id}.jsonl"
        self.trajectories_file = trajectories_dir / filename
        
        logger.info(f"Trajectories will be written to: {self.trajectories_file}")

    def _write_trajectories_incrementally(self):
        """增量写入context.trajectories到文件"""
        if not self.context or not hasattr(self.context, 'trajectories'):
            return
        
        if not self.trajectories_file:
            self._init_trajectories_file()
        
        # 获取新的trajectories entries
        new_entries = {}
        for key, value in self.context.trajectories.items():
            if key not in self.written_trajectories:
                new_entries[key] = value
                self.written_trajectories.add(key)
        
        # 如果有新的entries，写入文件
        if new_entries:
            try:
                with open(self.trajectories_file, 'a', encoding='utf-8') as f:
                    for key, value in new_entries.items():
                        # 创建包含step信息的记录
                        record = {
                            "step_key": key,
                            "step_data": value,
                            "global_step": self.cur_step,
                            "action_step": self.cur_action_step,
                            "agent_id": self.id(),
                            "session_id": getattr(self.context, 'session_id', 'unknown'),
                            "write_timestamp": datetime.now().isoformat()
                        }
                        # 每行写入一个JSON对象（JSONL格式）
                        f.write(json.dumps(record, ensure_ascii=False) + '\n')
                
                logger.info(f"Written {len(new_entries)} new trajectory entries to {self.trajectories_file}")
            except Exception as e:
                logger.error(f"Failed to write trajectories to file: {e}")

    async def async_run(self, message: Message, **kwargs) -> Message:
        self.context = message.context
        # Only init context if cur_step = 0
        if self.cur_step == 0:
            self._init_context(message.context)
            print(f"context_rule: {self.context_rule}")
            # 初始化trajectories文件
            self._init_trajectories_file()
        # Check if maximum steps reached
        if self.cur_step >= self.max_steps:
            logger.info(f"Maximum steps {self.max_steps} reached, interrupt execution")
            # 写入最终的trajectories
            self._write_trajectories_incrementally()
            # Maximum steps limit reached, interrupt execution
            actions = await self.interrupt_plan(message.payload, **kwargs)
            if actions and actions[0].policy_info:
                return self._create_finished_message(message, actions)
            return self._create_finished_message(message, "Maximum steps limit reached")

        observation = message.payload
        if not isinstance(observation, Observation):
            logger.warn(f"Invalid message payload, 'Observation' expected, got: {observation}")
            return self._create_finished_message(message, "Invalid message payload")

        actions = await self.async_policy(message.payload, **kwargs)
        
        # 添加actions详细日志
        logger.info(f"plan_agent received {len(actions) if actions else 0} actions from async_policy")
        if actions:
            for i, action in enumerate(actions):
                logger.info(f"Action {i+1}: tool_name={action.tool_name}, params={action.params}")
        
        if not actions:
            # 写入trajectories后返回
            self._write_trajectories_incrementally()
            return self._create_finished_message(message, "No valid actions from llm response")
        self._llm = None
        # Create corresponding agents or tools based on actions
        agents, tools = await self._create_agents_and_tools(actions)

        # should stop
        if self._is_done(actions) or (not agents and not tools):
            logger.info("No more actions, all tasks completed.")
            # 写入trajectories后返回
            self._write_trajectories_incrementally()
            return self._create_finished_message(message, actions)

        # todo: parallelize tool execution and agent execution
        tool_results = []
        if tools:
            tool_tasks = []
            # get tool results
            for action in tools:
                tool_tasks.append(self.fork_new_task(input=Observation(content=[action]), context=self.context))

            if not tool_tasks:
                raise RuntimeError("no tool task need to run in plan agent.")

            runners = await choose_runners(tool_tasks)
            res = await execute_runner(runners, RunConfig(reuse_process=True))

            for k, v in res.items():
                tool_results.append(ActionModel(agent_name=self.id(), policy_info=v.answer))
                # 获取对应的工具名称
                tool_name = tools[len(tool_results)-1].tool_name if len(tool_results) <= len(tools) else "unknown_tool"
                self._save_action_trajectory(self.cur_action_step, None, tool_name, k, v.answer)
                logger.info(f"Tool execution - Step {self.cur_action_step}, Tool: {tool_name}, Task: {k} -> Result: {v}")
                self.cur_action_step += 1

        agent_results = []
        # if agents:
        #     parallel_agents = []
        #     inputs = []
        #     for agent_action in agents:
        #         agent_name = agent_action.tool_name
        #         agent = AgentFactory.agent_instance(agent_name)
        #         agent = Agent(name=agent.name(), conf=agent.conf, system_prompt=agent.system_prompt,
        #                       agent_prompt=agent.agent_prompt)
        #         parallel_agents.append(agent)
        #         inputs.append(Observation(content=agent_action.params.get("content")))
        #     parallel_agent = ParallelizableAgent(conf=AgentConfig(), name="parallel", agents=parallel_agents)
        #     agent_results = await parallel_agent.run(inputs, self.context)

        if agents:
            parallel_agent_res = None
            # Decide whether to use parallel or serial execution
            agent_tasks = []
            for agent_action in agents:
                agent_name = agent_action.tool_name
                agent = AgentFactory.agent_instance(agent_name)
                input = agent_action.params.get("content") # TODO: 需要修改
                agent_tasks.append(self.fork_new_task(input=input, agent=agent, context=self.context))
                if not agent_tasks:
                    raise RuntimeError("no agent task need to run in plan agent.")

            if self._should_use_parallel(actions):
                agent_runners = await choose_runners(agent_tasks)
                parallel_agent_res = await execute_runner(agent_runners, RunConfig(reuse_process=True))

                for k, v in parallel_agent_res.items():
                    agent_results.append(ActionModel(agent_name=self.id(), policy_info=v.answer))
                    # 获取对应的Agent名称
                    agent_name = agents[len(agent_results)-1].tool_name if len(agent_results) <= len(agents) else "unknown_agent"
                    self._save_action_trajectory(self.cur_action_step, agent_name, None, k, v.answer)
                    logger.info(f"Parallel agent execution - Step {self.cur_action_step}, Agent: {agent_name}, Task: {k} -> Result: {v}")
                    self.cur_action_step += 1
                # logger.info("Using parallel execution mode")
                # parallel_agent = ParallelizableAgent(conf=self.conf, agents=agents)
                # parallel_agent_res = await parallel_agent.async_run(message, **kwargs)
            else:
                for task in agent_tasks:
                    agent_runners = await choose_runners([task])
                    agent_res = await execute_runner(agent_runners, RunConfig(reuse_process=True))

                    for k, v in agent_res.items():
                        result = v.answer
                        # 压缩content
                        if self.context_rule.optimization_config.enabled:
                            compressed_result = compress_content(llm_config=self.context_rule.llm_compression_config.compress_model, content = v.answer)
                            result = compressed_result.compressed_content
                            logger.info(f'to deserialize result= {len(v.answer)} deserialize result= {len(result)}')
                        agent_results.append(ActionModel(agent_name=task.agent.id(), policy_info=result))
                        # 使用task.agent获取Agent名称
                        agent_name = task.agent.id() if task.agent else "unknown_agent"
                        self._save_action_trajectory(self.cur_action_step, agent_name, None, task.input, result)
                        logger.info(f"Serial agent execution - Step {self.cur_action_step}, Agent: {agent_name}, Task: {k} -> Result: {v}")
                        self.cur_action_step += 1
                # logger.info("Using serial execution mode")
                # serial_agent = SerialableAgent(conf=self.conf, agents=agents)
                # parallel_agent_res = await serial_agent.async_run(message, **kwargs)

        # replan
        # Increment step counter
        self.cur_step += 1
        logger.info(f"Current execution step: {self.cur_step}/{self.max_steps}\ntrajectories: {self.context.trajectories}")
        
        # 每个step结束后写入trajectories
        self._write_trajectories_incrementally()
        
        # todo:
        #  1. update context
        #  2. build next step message from agent_results and tools_results
        next_message = self._actions_to_message(agent_results, tool_results, message)
        return await self.async_run(next_message, **kwargs)

    async def async_policy(self, observation: Observation, **kwargs) -> List[ActionModel]:
        self._finished = False
        # Prepare LLM input
        llm_messages = await self._prepare_llm_input(observation)
        llm_response = None
        try:
            llm_response = await self._call_llm_model(observation, llm_messages, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                use_tools = self.use_tool_list(llm_response)
                is_use_tool_prompt = len(use_tools) > 0
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                else:
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.id(),
                            "tool_calls": llm_response.tool_calls if not self.use_tools_in_prompt else use_tools,
                            "is_use_tool_prompt": is_use_tool_prompt if not self.use_tools_in_prompt else False
                        }
                    ))
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        try:
            events = []
            async for event in self.run_hooks(self.context, HookPoint.POST_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.warn(traceback.format_exc())

        self._save_action_trajectory(self.cur_action_step, self.id(), None, observation.content, llm_response.content)
        logger.info(f"Serial agent execution - Step {self.cur_action_step}, Agent: {self.name()}")
        self.cur_action_step += 1

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        logger.debug(f"plan_agent.agent_result: {agent_result}, agent._finished:{self._finished}")
        if agent_result and not agent_result.is_call_tool:
            self._finished = True
            return agent_result.actions
        return agent_result.actions

    def fork_new_task(self, input, agent:Agent = None, context: Context = None):
        # Use the new deep_copy method for complete and generic copying
        new_context = context.deep_copy()
        new_task = Task(input=input, agent=agent, context=new_context)
        new_context.set_task(new_task)
        new_context.task_id = new_task.id
        return new_task

    async def interrupt_plan(self, observation: Observation, **kwargs) -> List[ActionModel]:
        interrupt_prompt = """
            Based on all information collected so far, please generate a comprehensive research report about "{task}".
            DO NOT USE ANY TOOL! 
            The report should synthesize and analyze the following collected information:
            
            {trajectories}
            
            ## Report Structure Guidelines:
            1. **Executive Summary**: Provide a concise overview of the key findings and conclusions.
            2. **Introduction**: Briefly introduce the research context, objectives, and methodology.
            3. **Key Findings**: Present the main insights discovered during the research process, organized by themes or categories.
            4. **Analysis**: Critically analyze the collected information, identifying patterns, trends, and implications.
            5. **Conclusions**: Summarize the main takeaways and their significance.
            6. **Recommendations**: If applicable, suggest actionable next steps or areas for further investigation.
            
            Please ensure the report is:
            - Well-structured with clear sections and logical flow
            - Comprehensive yet concise
            - Objective and evidence-based
            - Focused on addressing the original task requirements
            - Written in professional language suitable for the intended audience
            
            Format the report appropriately with headings, bullet points, and paragraphs as needed for optimal readability.
        """
        interrupt_prompt_template = StringPromptTemplate(interrupt_prompt)
        interrupt_prompt_input = interrupt_prompt_template.format(context=self.context, task=self.task)
        llm_messages = [
            {
                "role": "system",
                "content": "You are a helpful general summary agent.You are an expert research assistant analyzing summaries."
            },
            {
                "role": "user",
                "content": interrupt_prompt_input
            }
        ]
        self._finished = False
        # Prepare LLM input
        llm_response = None
        try:
            llm_response = await self._call_llm_model(observation, llm_messages, **kwargs)
        except Exception as e:
            logger.warn(traceback.format_exc())
            raise e
        finally:
            if llm_response:
                use_tools = self.use_tool_list(llm_response)
                is_use_tool_prompt = len(use_tools) > 0
                if llm_response.error:
                    logger.info(f"llm result error: {llm_response.error}")
                else:
                    self.memory.add(MemoryItem(
                        content=llm_response.content,
                        metadata={
                            "role": "assistant",
                            "agent_name": self.id(),
                            "tool_calls": llm_response.tool_calls if not self.use_tools_in_prompt else use_tools,
                            "is_use_tool_prompt": is_use_tool_prompt if not self.use_tools_in_prompt else False
                        }
                    ))
            else:
                logger.error(f"{self.id()} failed to get LLM response")
                raise RuntimeError(f"{self.id()} failed to get LLM response")

        try:
            events = []
            async for event in self.run_hooks(self.context, HookPoint.POST_LLM_CALL):
                events.append(event)
        except Exception as e:
            logger.warn(traceback.format_exc())

        self._save_action_trajectory(self.cur_action_step, self.id(), None, observation.content, llm_response.content)
        logger.info(f"Serial agent execution - Step {self.cur_action_step}, Agent: {self.name()}")
        self.cur_action_step += 1

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        self._finished = True
        return agent_result.actions

    def _save_action_trajectory(self, step, agent_name: str, tool_name: str, params: str, result: str):
        # 将agent_results和tool_results保存到trajectories中
        step_key = f"step_{step}"
        step_data = {
            "step": step,
            "params": params,
            "result": result,
            "timestamp": datetime.now().isoformat(),
            "agent_name": agent_name,
            "tool_name": tool_name
        }
        self.context.trajectories[step_key] = step_data

    async def _create_agents_and_tools(self, actions: List[ActionModel]):
        """Create corresponding agents or tools based on actions"""
        agents_and_tools = []
        agents = []
        tools = []
        
        for action in actions:
            try:
                if is_agent(action):
                    agents.append(action)
                    logger.debug(f"Added action to agents: {action.tool_name}")
                elif action.tool_name:
                    tools.append(action)
                    logger.debug(f"Added action to tools: {action.tool_name}")
                else:
                    logger.warning(f"Action has no tool_name, skipping: {action}")
            except Exception as e:
                logger.error(f"Failed to parse actions from actions to agent or tool: {e}")
            if not action.agent_name:
                action.agent_name = self.id()

        logger.debug(f"Final split result - {len(agents)} agents, {len(tools)} tools")
        return agents, tools
        
    def _should_use_parallel(self, agent_tasks) -> bool:
        return False
        """Decide whether to use parallel execution mode"""
        # More complex logic can be implemented based on actual requirements
        # For example: check if there are dependencies between actions
        
        # By default, use parallel mode if there are more than one action
        return len(agent_tasks) > 1

        
    def _is_done(self, actions: List[ActionModel]) -> bool:
        """Check if all tasks are completed"""
        return self._finished

    def _create_finished_message(self, original_message: Message, result: Any) -> Message:
        """Create a message indicating task completion"""
        # Create a message containing the final result
        # todo: create message from context
        return Message(
                        category=Constants.TASK,
                        payload=result[0].policy_info,
                        sender=self.id(),
                        session_id=self.context.session_id,
                        topic=TopicType.FINISHED,
                        headers={"context": self.context}
                    )
        # return AgentMessage(payload=result,
        #                     sender=self.id(),
        #                     receiver=self.id(),
        #                     session_id=self.context.session_id if self.context else "",
        #                     headers={"context": self.context})

    def _actions_to_message(self, agents_result_actions: List[ActionModel],tools_result_actions: List[ActionModel], original_message: Message) -> Message:
        """Convert actions to a new message"""
        # Create a new message containing actions
        content_list = []
        content_list.extend(agents_result_actions)
        content_list.extend(tools_result_actions)
        new_message = AgentMessage(
            payload=Observation(content=json.dumps([content.model_dump() for content in content_list], ensure_ascii=False)),
            sender=self.id(),
            receiver=self.id(),
            session_id=self.context.session_id,
            headers={"context": self.context}
        )
        return new_message

