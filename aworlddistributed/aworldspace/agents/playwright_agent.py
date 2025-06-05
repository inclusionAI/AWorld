import logging
import os
import traceback
from typing import Dict, Any, List, Union
from typing import Optional

import aworld.trace as trace
from aworld.config.conf import AgentConfig, ConfigDict
from aworld.config.conf import TaskConfig
from aworld.core.agent.llm_agent import Agent
from aworld.core.common import Observation, ActionModel
from aworld.core.memory import MemoryItem
from aworld.core.task import Task
from aworld.logs.util import logger
from aworld.models.llm import call_llm_model, acall_llm_model
from aworld.models.model_response import ToolCall, Function
from aworld.output import Output, StreamingOutputs
from aworld.output import Outputs
from aworld.output.base import MessageOutput
from aworld.utils.common import sync_exec
from pydantic import BaseModel, Field

from aworldspace.base_agent import AworldBaseAgent

BROWSER_SYSTEM_PROMPT = """You are a GUI agent. You are given a task and your action history, with screenshots. You need to perform the next action to complete the task.
## Output Format
```
Thought: ...
Action: ...
```
## Action Space
navigate(website='') #Open the target website, usually the first action to open browser.
click(start_box='[x1, y1, x2, y2]')
left_double(start_box='[x1, y1, x2, y2]')
right_single(start_box='[x1, y1, x2, y2]')
drag(start_box='[x1, y1, x2, y2]', end_box='[x3, y3, x4, y4]')
hotkey(key='')
type(content='') #If you want to submit your input, use "\n" at the end of `content`.
scroll(direction='down or up or right or left')
wait() #Sleep for 5s and take a screenshot to check for any changes.
finished(content='xxx') # Use escape characters \\', \\", and \\n in content part to ensure we can parse the content in normal python string format.
## Note
- Use Chinese in `Thought` part.
- Write a small plan and finally summarize your next action (with its target element) in one sentence in `Thought` part.
## User Instruction
"""

import json
import re


def parse_action_output(output_text):
    # 提取Thought部分
    thought_match = re.search(r'Thought:(.*?)\nAction:', output_text, re.DOTALL)
    thought = thought_match.group(1).strip() if thought_match else ""

    # 提取Action部分
    action_match = re.search(r'Action:(.*?)(?:\n|$)', output_text, re.DOTALL)
    action_text = action_match.group(1).strip() if action_match else ""

    # 初始化结果字典
    result = {
        "thought": thought,
        "action": "",
        "key": None,
        "content": None,
        "start_box": None,
        "end_box": None,
        "direction": None,
        "website": None,
    }

    if not action_text:
        return json.dumps(result, ensure_ascii=False)

    # 解析action类型
    action_parts = action_text.split('(')
    action_type = action_parts[0]
    result["action"] = action_type

    # 解析参数
    if len(action_parts) > 1:
        params_text = action_parts[1].rstrip(')')
        params = {}

        # gpt-4o兼容
        if 'start_box' in params_text:
            params_text = params_text.replace(",", "")
        if 'end_box' in params_text:
            params_text = params_text.replace(" end_box", ", end_box")

        # 处理键值对参数
        for param in params_text.split(','):
            param = param.strip()

            if '=' in param:
                key, value = param.split('=', 1)
                key = key.strip()
                value = value.strip().strip('\'"')

                # 处理bbox格式
                if 'box' in key:
                    print(value)
                    # 提取坐标数字
                    numbers = re.findall(r'\d+', value)
                    print(numbers)
                    if numbers:
                        coords = [int(num) for num in numbers]
                        if len(coords) == 4:
                            if key == 'start_box':
                                result["start_box"] = coords
                            elif key == 'end_box':
                                result["end_box"] = coords
                elif key == 'key':
                    result["key"] = value.replace("pagedown", "PageDown").replace("pageup", "PageUp")
                elif key == 'content':
                    # 处理转义字符
                    value = value.replace('\\n', '\n').replace('\\"', '"').replace("\\'", "'")
                    result["content"] = value
                elif key == 'website':
                    result["website"] = value
                elif key == 'direction':
                    result["direction"] = value

    return result

def parse_tool_call(line) :
    # 提取 Action和param
    result = parse_action_output(line)
    action = result['action']
    # action_param = line.split("Action:")[-1].strip()
    # action = action_param.split("(")[0]
    # func_name = ""
    # content = ""

    # 映射到实际函数名和参数
    if action == 'navigate':
        func_name = 'mcp__ms-playwright__browser_navigate'
        # 正则表达式提取网址
        # url = action_param[action_param.find("'") + 1: action_param.rfind("'")]
        content = {'url': result['website']}

    elif action == 'click':
        func_name = 'mcp__ms-playwright__browser_screen_click'
        # cleaned = action_param.replace("click(start_box='<|box_start|>(", "").replace(")<|box_end|>')", "")
        # x_str, y_str = cleaned.split(",")
        # x = int(x_str)
        # y = int(y_str)
        x = int((result["start_box"][0] + result["start_box"][2])/2)
        y = int((result["start_box"][1] + result["start_box"][3])/2)
        content = {'element': '', 'x': x, 'y': y}

    elif action == 'right_single':
        func_name = 'mcp__ms-playwright__browser_screen_click'

        # cleaned = action_param.replace("right_single(start_box='<|box_start|>(", "").replace(")<|box_end|>')", "")
        # x_str, y_str = cleaned.split(",")
        # x = int(x_str)
        # y = int(y_str)
        x = int((result["start_box"][0] + result["start_box"][2]) / 2)
        y = int((result["start_box"][1] + result["start_box"][3]) / 2)
        content = {'element': 'right click target', 'x': x, 'y': y, 'button': 'right'}

    elif action == 'drag':
        func_name = 'mcp__ms-playwright__browser_screen_drag'

        # pattern = r"start_box=['\"](.*?)['\"].*?end_box=['\"](.*?)['\"]"
        # match = re.search(pattern, action_param, re.DOTALL)
        # if match:
        #     x1y1, x2y2 = match.groups()
        #     x1y1 = x1y1.replace("<|box_start|>(", "").replace(")<|box_end|>", "")
        #     x2y2 = x2y2.replace("<|box_start|>(", "").replace(")<|box_end|>", "")
        #     x1_str, y1_str = x1y1.split(",")
        #     x1 = int(x1_str)
        #     y1 = int(y1_str)
        #     x2_str, y2_str = x2y2.split(",")
        #     x2 = int(x2_str)
        #     y2 = int(y2_str)
        x1 = int((result["start_box"][0] + result["start_box"][2]) / 2)
        y1 = int((result["start_box"][1] + result["start_box"][3]) / 2)
        x2 = int((result["end_box"][0] + result["end_box"][2]) / 2)
        y2 = int((result["end_box"][1] + result["end_box"][3]) / 2)

        content = {
            'element': f'drag from [{x1},{y1}] to [{x2},{y2}]',
            'startX': x1,
            'startY': y1,
            'endX': x2,
            'endY': y2
        }
    elif action == 'hotkey':
        func_name = 'mcp__ms-playwright__browser_press_key'
        # key = action_param[action_param.find("'") + 1: action_param.rfind("'")]
        content = {'key':  result["key"]}
    elif action == 'type':
        func_name = 'mcp__ms-playwright__browser_screen_type'
        # content = action_param[action_param.find("'") + 1: action_param.rfind("'")]
        content = {'text': result['content']}
    elif action == 'scroll':
        # 暂时使用presskey代替scroll
        func_name = 'mcp__ms-playwright__browser_press_key'
        # direction = action_param[action_param.find("'") + 1: action_param.rfind("'")]
        direction = result['direction']
        key_map = {
            'up': 'PageUp',
            'down': 'PageDown',
            'left': 'ArrowLeft',
            'right': 'ArrowRight'
        }
        key = key_map.get(direction, 'ArrowDown')
        content = {'key': key}
    elif action == 'wait':
        func_name = 'mcp__ms-playwright__browser_wait_for'
        content = {'time': 5}
    elif action == 'finished':
        func_name ="finished"
        # content = action_param[action_param.find("'") + 1: action_param.rfind("'")]
        content = result['content']
    else:
        return ""

    return Function(name=func_name, arguments=json.dumps(content))


class PlayWrightAgent(Agent):

    def __init__(self, conf: Union[Dict[str, Any], ConfigDict, AgentConfig], **kwargs):
        self.screen_capture = True
        super().__init__(conf, **kwargs)

    def policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        output = None
        if kwargs.get("outputs") and isinstance(kwargs.get("outputs"), Outputs):
            output = kwargs["outputs"]

        # Get current step information for trace recording
        step = kwargs.get("step", 0)
        exp_id = kwargs.get("exp_id", None)
        source_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        self._finished = False
        self.desc_transform()
        self.tools = None

        if "data:image/jpeg;base64," in observation.content:
            logger.info("transfer base64 content to image")
            observation.image = observation.content
            observation.content = "observation:"

        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]

        messages = self.messages_transform(content=observation.content,
                                           image_urls=images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt)

        self._log_messages(messages)

        if isinstance(messages[-1]['content'], list):
            messages[-1]['role'] = 'user'  # 有image的话必须使用user请求，而且不写入历史对话
        else:
            self.memory.add(MemoryItem(
                content=messages[-1]['content'],
                metadata={
                    "role": messages[-1]['role'],
                    "agent_name": self.name(),
                }
            ))

        llm_response = None
        span_name = f"llm_call_{exp_id}"
        with trace.span(span_name) as llm_span:
            llm_span.set_attributes({
                "exp_id": exp_id,
                "step": step,
                "messages": json.dumps([str(m) for m in messages], ensure_ascii=False)
            })
            if source_span:
                source_span.set_attribute("messages", json.dumps([str(m) for m in messages], ensure_ascii=False))

            try:
                logger.info("start")
                logger.info(messages)
                logger.info("end")
                llm_response = call_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if self.use_tools_in_prompt and self.tools else None
                )

                logger.info(f"Execute response: {llm_response.message}")
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
                                "agent_name": self.name(),
                                "tool_calls": llm_response.tool_calls if self.use_tools_in_prompt else use_tools,
                                "is_use_tool_prompt": is_use_tool_prompt if self.use_tools_in_prompt else False
                            }
                        ))
                        function = parse_tool_call(llm_response.message['content'])
                        if function.name == "finished":
                            self._finished = True
                            llm_response.content = "<answer>" + llm_response.content + "</answer>"
                            llm_response.tool_calls = None
                        else:
                            llm_response.content = None

                            tool_call = ToolCall(
                                id="tooluse_mock",
                                type="function",
                                function=function,
                            )
                            screen_capture = ToolCall(
                                id="screen_capture",
                                type="function",
                                function=Function(
                                    name="mcp__ms-playwright__browser_screen_capture",
                                    arguments="{}"
                                )
                            )

                            llm_response.tool_calls = [tool_call, screen_capture]

                else:
                    logger.error(f"{self.name()} failed to get LLM response")
                    raise RuntimeError(f"{self.name()} failed to get LLM response")

        logger.info(llm_response)
        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True

        if output:
            output.add_part(MessageOutput(source=llm_response, json_parse=False))
            output.mark_finished()

        logger.info(agent_result.actions)
        return agent_result.actions

    async def async_policy(self, observation: Observation, info: Dict[str, Any] = {}, **kwargs) -> Union[
        List[ActionModel], None]:
        """The strategy of an agent can be to decide which tools to use in the environment, or to delegate tasks to other agents.

        Args:
            observation: The state observed from tools in the environment.
            info: Extended information is used to assist the agent to decide a policy.

        Returns:
            ActionModel sequence from agent policy
        """
        outputs = None
        if kwargs.get("outputs") and isinstance(kwargs.get("outputs"), Outputs):
            outputs = kwargs.get("outputs")

        # Get current step information for trace recording
        step = kwargs.get("step", 0)
        exp_id = kwargs.get("exp_id", None)
        source_span = trace.get_current_span()

        if hasattr(observation, 'context') and observation.context:
            self.task_histories = observation.context

        self._finished = False
        await self.async_desc_transform()

        self.tools = None
        if "data:image/jpeg;base64," in observation.content:
            logger.info("transfer base64 content to image")
            observation.image = observation.content
            observation.content = "observation:"

        images = observation.images if self.conf.use_vision else None
        if self.conf.use_vision and not images and observation.image:
            images = [observation.image]
        messages = self.messages_transform(content=observation.content,
                                           image_urls=images,
                                           sys_prompt=self.system_prompt,
                                           agent_prompt=self.agent_prompt)

        self._log_messages(messages)
        if isinstance(messages[-1]['content'], list):
            messages[-1]['role'] = 'user'  # 有image的话必须使用user请求，而且不写入历史对话
        else:
            self.memory.add(MemoryItem(
                content=messages[-1]['content'],
                metadata={
                    "role": messages[-1]['role'],
                    "agent_name": self.name(),
                }
            ))

        llm_response = None
        span_name = f"llm_call_{exp_id}"
        with trace.span(span_name) as llm_span:
            llm_span.set_attributes({
                "exp_id": exp_id,
                "step": step,
                "messages": json.dumps([str(m) for m in messages], ensure_ascii=False)
            })
            if source_span:
                source_span.set_attribute("messages", json.dumps([str(m) for m in messages], ensure_ascii=False))

            try:
                llm_response = await acall_llm_model(
                    self.llm,
                    messages=messages,
                    model=self.model_name,
                    temperature=self.conf.llm_config.llm_temperature,
                    tools=self.tools if self.use_tools_in_prompt and self.tools else None,
                    stream=kwargs.get("stream", False)
                )

                # Record LLM response
                llm_span.set_attributes({
                    "llm_response": json.dumps(llm_response.to_dict(), ensure_ascii=False),
                    "tool_calls": json.dumps([tool_call.model_dump() for tool_call in llm_response.tool_calls] if llm_response.tool_calls else [], ensure_ascii=False),
                    "error": llm_response.error if llm_response.error else ""
                })

            except Exception as e:
                logger.warn(traceback.format_exc())
                llm_span.set_attribute("error", str(e))
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
                                "agent_name": self.name(),
                                "tool_calls": llm_response.tool_calls if self.use_tools_in_prompt else use_tools,
                                "is_use_tool_prompt": is_use_tool_prompt if self.use_tools_in_prompt else False
                            }
                        ))

                        function = parse_tool_call(llm_response.message['content'])
                        if function.name == "finished":
                            self._finished = True
                            llm_response.content = "<answer>" + llm_response.content + "</answer>"
                            llm_response.tool_calls = None
                        else:
                            llm_response.content = None

                            tool_call = ToolCall(
                                id="tooluse_mock",
                                type="function",
                                function=function,
                            )
                            screen_capture = ToolCall(
                                id="screen_capture",
                                type="function",
                                function=Function(
                                    name="mcp__ms-playwright__browser_screen_capture",
                                    arguments="{}"
                                )
                            )
                            llm_response.tool_calls = [tool_call, screen_capture]
                else:
                    logger.error(f"{self.name()} failed to get LLM response")
                    raise RuntimeError(f"{self.name()} failed to get LLM response")

        if outputs and isinstance(outputs, Outputs):
            await outputs.add_output(MessageOutput(source=llm_response, json_parse=False))

        agent_result = sync_exec(self.resp_parse_func, llm_response)
        if not agent_result.is_call_tool:
            self._finished = True
        return agent_result.actions


class Pipeline(AworldBaseAgent):
    class Valves(BaseModel):
        llm_provider: Optional[str] = Field(default=None, description="llm_model_name")
        llm_model_name: Optional[str] = Field(default=None, description="llm_model_name")
        llm_base_url: Optional[str] = Field(default=None,description="llm_base_urly")
        llm_api_key: Optional[str] = Field(default=None,description="llm api key")
        system_prompt: str = Field(default=BROWSER_SYSTEM_PROMPT,description="system_prompt")
        history_messages: int = Field(default=100, description="rounds of history messages")

    def __init__(self):
        self.valves = self.Valves()
        self.agent_config = AgentConfig(
            name=self.agent_name(),
            llm_provider=self.valves.llm_provider if self.valves.llm_provider else os.environ.get("LLM_PROVIDER"),
            llm_model_name=self.valves.llm_model_name if self.valves.llm_model_name else os.environ.get("LLM_MODEL_NAME"),
            llm_api_key=self.valves.llm_api_key if self.valves.llm_api_key else os.environ.get("LLM_API_KEY"),
            llm_base_url=self.valves.llm_base_url if self.valves.llm_base_url else os.environ.get("LLM_BASE_URL"),
            system_prompt=self.valves.system_prompt if self.valves.system_prompt else BROWSER_SYSTEM_PROMPT
        )

        self.m2w_files = os.path.abspath(os.path.join(os.path.curdir, "aworldspace", "datasets", "online-mind2web"))

        logging.info(f"m2w_files path {self.m2w_files}")
        file_path = os.path.join(self.m2w_files, "Online_Mind2Web.json")

        with open(file_path, 'r') as file:
            self.full_dataset = json.load(file)
            logging.info("playwright_agent init success")

    # 重写build_agent
    async def build_agent(self, body: dict):
        agent_config = await self.get_agent_config(body)
        mcp_servers = await self.get_mcp_servers(body)

        agent = PlayWrightAgent(
            conf=agent_config,
            name=agent_config.name,
            system_prompt=agent_config.system_prompt,
            mcp_servers=mcp_servers,
            mcp_config=await self.load_mcp_config(),
            history_messages=await self.get_history_messages(body)
        )
        return agent

    async def get_custom_input(self, user_message: str, model_id: str, messages: List[dict], body: dict) -> Any:
        task = await self.get_m2w_task(int(user_message))
        return task['Task']

    async def get_agent_config(self, body):
        default_llm_provider = self.valves.llm_provider if self.valves.llm_provider else os.environ.get("LLM_PROVIDER")
        llm_model_name = self.valves.llm_model_name if self.valves.llm_model_name else os.environ.get("LLM_MODEL_NAME")
        llm_api_key = self.valves.llm_api_key if self.valves.llm_api_key else os.environ.get("LLM_API_KEY")
        llm_base_url = self.valves.llm_base_url if self.valves.llm_base_url else os.environ.get("LLM_BASE_URL")
        system_prompt = self.valves.system_prompt if self.valves.system_prompt else BROWSER_SYSTEM_PROMPT

        task = await self.get_task_from_body(body)
        logging.info(
            f"task llm config is: {task.llm_provider}, {task.llm_model_name}, {task.llm_api_key}, {task.llm_base_url}")

        return AgentConfig(
            name=self.agent_name(),
            llm_provider=task.llm_provider if task and task.llm_provider else default_llm_provider,
            llm_model_name=task.llm_model_name if task and task.llm_model_name else llm_model_name,
            llm_api_key=task.llm_api_key if task and task.llm_api_key else llm_api_key,
            llm_base_url=task.llm_base_url if task and task.llm_base_url else llm_base_url,
            system_prompt=task.task_system_prompt if task and task.task_system_prompt else system_prompt
        )

    def agent_name(self) -> str:
        return "PlaywrightAgent"

    async def get_mcp_servers(self, body) -> list[str]:
        task = await self.get_task_from_body(body)
        if task.mcp_servers:
            logging.info(f"mcp_servers from task: {task.mcp_servers}")
            return task.mcp_servers

        return [
            "ms-playwright"
        ]

    async def get_m2w_task(self, index) -> dict:
        logging.info(f"Start to process: m2w_task_{index}")
        m2w_task = self.full_dataset[index]
        logging.info(f"Detail: {m2w_task}")
        logging.info(f"Task: {m2w_task['confirmed_task']}")
        logging.info(f"Level: {m2w_task['level']}")
        logging.info(f"Website: {m2w_task['website']}")

        return self.add_file_path(m2w_task)

    async def custom_output_before_task(self, outputs: Outputs, chat_id: str, task: Task) -> None:
        task_config:TaskConfig = task.conf
        m2w_task = await self.get_m2w_task(int(task_config.ext['origin_message']))

        result = f"\n\n`Web TASK#{task_config.ext['origin_message']}`\n\n---\n\n"
        result += f"**Task**: {m2w_task['Task']}\n"
        result += f"**Level**: {m2w_task['level']}\n"
        result += f"**Website**: \n {m2w_task['website']}\n"
        result += f"\n\n-----\n\n"
        await outputs.add_output(Output(data=result))

    async def custom_output_after_task(self, outputs: Outputs, chat_id: str, task: Task):
        """
        check gaia task output
        Args:
            outputs:
            chat_id:
            task:

        Returns:

        """
        task_config: TaskConfig = task.conf
        web_task_id = int(task_config['ext']['origin_message'])
        web_task = await self.get_m2w_task(web_task_id)
        agent_result = ""
        if isinstance(outputs, StreamingOutputs):
            agent_result = await outputs._visited_outputs[-2].get_finished_response() # read llm result
        match = re.search(r"<answer>(.*?)</answer>", agent_result)
        result = ""
        if match:
            answer = match.group(1)
            logging.info(f"Agent answer: {answer}")

        metadata = await outputs.get_metadata()
        if not metadata:
            await outputs.set_metadata({})
            metadata = await outputs.get_metadata()
        metadata['web_task'] = web_task
        return result

    def add_file_path(self, task: Dict[str, Any]
                      ):
        task["Task"] = "Task: " + task['confirmed_task'] + '\n' + "Please first navigate to the target " + "Website: " + task['website']
        return task

    async def load_mcp_config(self) -> dict:
        return {
            "mcpServers": {
                "ms-playwright": {
                    "command": "npx",
                    "args": [
                        "@playwright/mcp@0.0.27",
                        "--vision",
                        "--no-sandbox",
                        "--headless",
                        "--isolated"
                    ],
                    "env": {
                        "PLAYWRIGHT_TIMEOUT": "120000",
                        "SESSION_REQUEST_CONNECT_TIMEOUT": "120"
                    }
                }
            }
        }

