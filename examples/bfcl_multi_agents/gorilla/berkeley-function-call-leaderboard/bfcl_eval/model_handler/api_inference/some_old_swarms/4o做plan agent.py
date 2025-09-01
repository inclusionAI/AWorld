import json
import os, sys
import time
from openai.types.chat import ChatCompletion, ChatCompletionMessage
import requests
import asyncio
import aiohttp
import time
import re

from typing import Any

__root_path__ = os.path.dirname(os.path.abspath(__file__))
for _ in range(7):
    __root_path__ = os.path.dirname(__root_path__)
sys.path.append(__root_path__)

from bfcl_eval.aworld_agents.multi_query_agent import MultiQueryAgent
from bfcl_eval.aworld_agents.fc_agent import FCModelAgent

from bfcl_eval.constants.type_mappings import GORILLA_TO_OPENAPI
from bfcl_eval.constants.default_prompts import DEFAULT_SYSTEM_PROMPT
from bfcl_eval.constants.aworld_ma_prompts import VERIFY_SYSTEM_PROMPT
from bfcl_eval.model_handler.base_handler import BaseHandler
from bfcl_eval.model_handler.model_style import ModelStyle
from bfcl_eval.model_handler.utils import (
    convert_to_function_call,
    convert_to_tool,
    default_decode_ast_prompting,
    default_decode_execute_prompting,
    format_execution_results_prompting,
    func_doc_language_specific_pre_processing,
    retry_with_backoff,
    system_prompt_pre_processing_chat_model,
)
from openai import OpenAI, RateLimitError


# from aworld.agents.llm_agent import Agent

# from aworld.agents.bfcl_agent import BFCLAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.core.task import Task
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners


OPENROUTE_API_KEY = os.getenv("OPENROUTER_API_KEY")

mcp_config = {
    "mcpServers": {}
}

# For multi-step plans, you must consider how the output of one function will be used as an input for a subsequent function (e.g., a user_id from a search_user function is needed for a get_order_history(user_id=...) call).

# If the goal cannot be achieved because no suitable function exists, explain this limitation. If required parameters are missing from the user's request, ask the user for the specific missing information.

PLAN_MAKER_PROMPT_WITHOUT_FUNC_DOC = """
You are an expert in making plan for composing functions. You are given a question and a set of possible functions. Based on the question, you will need to make a plan containing one or more function/tool calls to achieve the purpose. The plan should be clear and concise, and should not contain any unnecessary steps. 
The plan you generate will be interpreted by another model to produce a strict function call format (you do not need to consider this part).

At each turn, you must try your best to complete the tasks requested by the user within the current turn. Continue to output functions to call until you have fulfilled the user's request to the best of your ability. Once you have no more functions to call, the system will consider the current turn complete and proceed to the next turn or task.

You must follow these steps to create your plan:
    1. Analyze the Request: Deeply understand the user's goal and identify the key information provided in their query.
    2. Assess Available Functions: Review the provided function descriptions. Determine which functions are relevant to the user's goal and whether they can be used to achieve it.
    3. Verify Parameters and Dependencies in context: In multi-round tasks, parameters may not only appear in the input for the current round. Some parameters may be present in previous conversations or in the tool's execution feedback. Be sure to check carefully. Remember, missing functions or parameters are extremely rare!
    4. Formulate a Plan or a Response: If a viable sequence of function calls can be constructed, create the plan.

Your plan must meet the following requirements:
    1. Sequential Order: The plan must be presented in the actual execution order.
    2. Atomic Steps: Each step in the plan must consist of only a single function call.
    3. Complete Calls: Each function call must include the function's name and all of its required parameters. 

Your response must adhere to the following format:
    First, think through your process inside <think></think> tags. This should include your analysis of the user's request, which functions you considered, and how you constructed the final plan.
    If a plan can be created, present the complete plan inside <plan> tags, for example : 
        <plan>
        1. Call function_name_1, the value of required parameter parameter_1 is "value1", which meets the type requirement for parameter_1.
        2. Call function_name_2, the value of required parameter parameter_2 is "value2", which meets the type requirement for parameter_2, and the value of required parameter parameter_3 is "value3", which meets the type requirement for parameter_3.
        </plan>
    Otherwise, use the <response> tag. For example:
        <response>
        The plan has been executed successfully!
        </response>
"""

AT_SYSTEM_PROMPT = (
    PLAN_MAKER_PROMPT_WITHOUT_FUNC_DOC
    + """
Here is a list of functions in JSON format for your reference. \n{functions}\n
"""
)


def aworld_swarm_prompt_processing(function_docs):
    """
    Add a system prompt to the chat model to instruct the model on the available functions and the expected response format.
    If the prompts list already contains a system prompt, append the additional system prompt content to the existing system prompt.
    """
    system_prompt_template = AT_SYSTEM_PROMPT
    planner_prompt = system_prompt_template.format(functions=function_docs)

    verify_system_prompt_template = VERIFY_SYSTEM_PROMPT
    verify_system_prompt = verify_system_prompt_template.format(functions=function_docs)

    return planner_prompt, verify_system_prompt


def _build_swarm(or_model_name, bfcl_func_docs, compiled_tools):
    SWARM_MODEL_NAME=or_model_name
    # Get API key from environment variable
    _base_url = "https://openrouter.ai/api/v1"

    xlam_base_url = os.getenv("AGI_BASE_URL")
    xlam_api_key  = os.getenv("AGI_API_KEY")

    plan_maker_prompt, verify_system_prompt = aworld_swarm_prompt_processing(function_docs=bfcl_func_docs)

    plan_agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=SWARM_MODEL_NAME,
        llm_api_key=OPENROUTE_API_KEY,
        llm_base_url="https://openrouter.ai/api/v1",
        llm_temperature=0.001,
        max_retries=20,
    )

    plan_agent = MultiQueryAgent(
        conf=plan_agent_config,
        name="generate_function_call_plan_agent",
        system_prompt=plan_maker_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )


    fc_agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="xlam-lp-70b",
        llm_api_key = xlam_api_key,
        llm_base_url= xlam_base_url,
        llm_temperature=0.001,
        max_retries=10,
        # human_tools=[ _tool['name'] for _tool in test_tools]
    )

    fc_agent = FCModelAgent(
        conf=fc_agent_config,
        name="file_sys_agent",
        system_prompt=None,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
        bfcl_tools=compiled_tools,
    )

    # verify_agent = MultiQueryAgent(
    #     conf=agent_config,
    #     name="verify_function_call_agent",
    #     system_prompt=verify_system_prompt,
    #     mcp_servers=mcp_config.get("mcpServers", []).keys(),
    #     mcp_config=mcp_config,
    # )

    # swarm = Swarm(exe_agent, verify_agent, max_steps=1)
    return plan_agent, fc_agent


# def build_swarm_in_agent_as_tool(or_model_name, bfcl_func_docs):
#     SWARM_MODEL_NAME=or_model_name
#     # Get API key from environment variable

#     _base_url = "https://openrouter.ai/api/v1"
#     api_key = OPENROUTE_API_KEY

#     if or_model_name in ["xlam-lp-70b"]:
#         _base_url="https://agi.alipay.com/api"
#         api_key="123"

#     execute_prompt, verify_system_prompt = aworld_swarm_prompt_processing(function_docs=bfcl_func_docs)

#     agent_config = AgentConfig(
#         llm_provider="openai",
#         llm_model_name=SWARM_MODEL_NAME,
#         llm_api_key=api_key,
#         llm_base_url=_base_url,
#         llm_temperature=0.001,
#         max_retries=20,
#     )

#     exe_agent = MultiQueryAgent(
#         conf=agent_config,
#         name="generate_function_call_agent",
#         system_prompt=execute_prompt,
#         mcp_servers=mcp_config.get("mcpServers", []).keys(),
#         mcp_config=mcp_config,
#         use_tools_in_prompt=True,
#     )

#     verify_agent = MultiQueryAgent(
#         conf=agent_config,
#         name="verifier_agent",
#         desc="You can pass the complete function calls along with the purpose of calling them as parameters to the verifier agent, which will return some suggestions that you can use to modify your function calls.",
#         system_prompt=verify_system_prompt,
#         mcp_servers=mcp_config.get("mcpServers", []).keys(),
#         mcp_config=mcp_config,
#     )

#     swarm = Swarm( (exe_agent, verify_agent), max_steps=6, build_type=GraphBuildType.HANDOFF)
#     return swarm



class BFCLAWorldSwarmFCHandler(BaseHandler):
    def __init__(self, model_name, temperature) -> None:
        super().__init__(model_name, temperature)

        _tmp_res = model_name.split('[')
        assert len(_tmp_res) == 2, "model_name should be in the format of [model_name]"
        self.or_model_name = _tmp_res[1][:-1]

        self.model_style = ModelStyle.OpenAI_Completions
        self.plan_swarm = None
        self.fc_agent = None

        self.test_entry_id = None


    def compile_tools_for_xlam(self, test_entry: dict) -> dict:
        functions: list = test_entry["function"]
        test_category: str = test_entry["id"].rsplit("_", 1)[0]

        functions = func_doc_language_specific_pre_processing(functions, test_category)
        tools = convert_to_tool(functions, GORILLA_TO_OPENAPI, self.model_style)
        return tools


    def inference(self, test_entry: dict, include_input_log: bool, exclude_state_log: bool):
        # This method is used to retrive model response for each model.
        compiled_tools = self.compile_tools_for_xlam(test_entry)

        self.test_entry_id = test_entry["id"]
        self.processed_idx = 0
        if self.plan_swarm is None:
            self.plan_swarm, self.fc_agent = _build_swarm(or_model_name=self.or_model_name, bfcl_func_docs=test_entry['function'], compiled_tools=compiled_tools)

        # self.client.create_agent(self.test_entry_id)
        try:
            # FC model
            if "FC" in self.model_name or self.is_fc_model:
                if "multi_turn" in test_entry["id"]:

                    exec_result = self.inference_multi_turn_FC(
                        test_entry, include_input_log, exclude_state_log
                    )
                else:
                    exec_result = self.inference_single_turn_FC(test_entry, include_input_log)
            # Prompting model
            else:
                if "multi_turn" in test_entry["id"]:
                    exec_result = self.inference_multi_turn_prompting(
                        test_entry, include_input_log, exclude_state_log
                    )
                else:
                    exec_result = self.inference_single_turn_prompting(test_entry, include_input_log)

        except Exception as e:
            print(f"Error in inference: {e}")
            exec_result = None
        finally:
            pass
            # self.client.del_agent(self.test_entry_id)
        
        return exec_result


    def decode_ast(self, result, language="Python"):
        try:
            # Parse the JSON array of function calls
            function_calls = json.loads(result)
            if not isinstance(function_calls, list):
                function_calls = [function_calls]
        except json.JSONDecodeError:
            # Fallback for semicolon-separated format
            function_calls = [json.loads(call.strip()) for call in result.split(";")]

        decoded_output = []
        for func_call in function_calls:
            name = func_call["name"]
            arguments = func_call["arguments"]
            decoded_output.append({name: arguments})

        return decoded_output


    def decode_execute(self, result):
        # try:
        #     function_calls = json.loads(result)
        #     if not isinstance(function_calls, list):
        #         function_calls = [function_calls]
        # except json.JSONDecodeError:
        #     function_calls = [json.loads(call.strip()) for call in result.split(";")]

        function_calls = result

        execution_list = []
        for func_call in function_calls:
            name = list(func_call.keys())[0]
            arguments = json.loads(list(func_call.values())[0])
            execution_list.append(
                f"{name}({','.join([f'{k}={repr(v)}' for k,v in arguments.items()])})"
            )

        return execution_list


    @retry_with_backoff(error_type=RateLimitError)
    def generate_with_backoff(self, **kwargs):
        messages = kwargs.get("messages")

        task_id = self.test_entry_id

        wait_to_process_msgs = messages[self.processed_idx:]
        self.processed_idx = len(messages)

        task_input = ''
        _input_init_flag = True
        for msg_idx, msg in enumerate(wait_to_process_msgs):
            try:    
                if msg['role'] == 'user':
                    task_input = msg['content']
                    break 
                elif msg['role'] == 'tool':
                    if _input_init_flag:
                        task_input = f"Plan execution results are shown as follow: {msg['content']}"
                        _input_init_flag = False
                    else:
                        task_input += f"\n{msg['content']}"
            except:
                assert isinstance(msg, ChatCompletionMessage)
                continue


        if task_input is None:
            task_input = "Your plan has been executed successfully with no further feedback. Please continue to finish the task."
        print(f"{task_input=}")
        task = Task(
            # id=task_id,
            session_id=task_id,
            name=task_id,
            input=task_input,
            # swarm=self.plan_swarm,
            agent=self.plan_swarm,
            conf=TaskConfig(system_prompt="You are a helpful assistant.")
        )

        start_time = time.time()
        result = Runners.sync_run_task(task=task)
        # change to submit task
        
        # TODO: prepaer input for fc_agent
        planner_response = result[task.id].answer

        def post_process_plan_generation(plan_generation):
            # 使用正则表达式提取 <plan> 和 </plan> 之间的内容
            plan_pattern = r'<plan>(.*?)</plan>'
            plan_match = re.search(plan_pattern, plan_generation, re.DOTALL)

            response_pattern = r'<response>(.*?)</response>'
            response_match = re.search(response_pattern, plan_generation, re.DOTALL)

            if plan_match:
                plan_content = plan_match.group(1).strip()
                return plan_content, True
            elif response_match:
                response_content = response_match.group(1).strip()
                return response_content, False
            else:
                return None, None

        extract_planner_response, plan_flag = post_process_plan_generation(planner_response)
        print(f"Planner:{planner_response}")
        assert extract_planner_response is not None

        if plan_flag:
            fc_input = [{"role":"user", "content":extract_planner_response}]

            fc_task = Task(
                session_id=f"fc_{task_id}",
                name=f"fc_{task_id}",
                input=fc_input,
                # swarm=self.plan_swarm,
                agent=self.fc_agent,
                conf=TaskConfig(system_prompt="You are a helpful assistant.")
            )

            fc_result = Runners.sync_run_task(task=fc_task)

            fc_response_text=fc_result[fc_task.id].answer
            fc_response_status=fc_result[fc_task.id].success
            fc_time_cost=fc_result[fc_task.id].time_cost
            fc_usage=fc_result[fc_task.id].usage
            fc_trajectory=fc_result[fc_task.id].trajectory
        else:
            fc_response_text = extract_planner_response
            fc_response_status = True
            fc_time_cost = 0
            fc_usage = {'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}
            fc_trajectory = []
        
        print(f"{self.test_entry_id}:{task_input}")
        print(f"{self.test_entry_id}:{fc_response_text}")

        result_json = {
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": kwargs.get("model", "unknown"),
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": fc_response_text,
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": fc_usage['prompt_tokens'],
                "completion_tokens": fc_usage['completion_tokens'],
                "total_tokens": fc_usage['total_tokens'],
            }
        }


        api_response = ChatCompletion.model_validate(result_json)
        # api_response = self.client.exec_aworld_task(task_id=self.test_entry_id, **kwargs)
        end_time = time.time()

        return api_response, end_time - start_time

    #### FC methods ####

    def _query_FC(self, inference_data: dict):
        message: list[dict] = inference_data["message"]
        tools = inference_data["tools"]
        inference_data["inference_input_log"] = {"message": repr(message), "tools": tools}

        kwargs = {
            "messages": message,
            "model": self.model_name.replace("-FC", ""),
            "temperature": self.temperature,
            "store": False,
        }

        if len(tools) > 0:
            kwargs["tools"] = tools

        return self.generate_with_backoff(**kwargs)

    def _pre_query_processing_FC(self, inference_data: dict, test_entry: dict) -> dict:
        inference_data["message"] = []
        return inference_data

    def _compile_tools(self, inference_data: dict, test_entry: dict) -> dict:
        functions: list = test_entry["function"]
        test_category: str = test_entry["id"].rsplit("_", 1)[0]

        functions = func_doc_language_specific_pre_processing(functions, test_category)
        tools = convert_to_tool(functions, GORILLA_TO_OPENAPI, self.model_style)

        inference_data["tools"] = tools

        return inference_data


    def _parse_query_response_FC(self, api_response: any) -> dict:
        try:
            response_json = json.loads(api_response.choices[0].message.content)
            model_responses = response_json['response']
            tool_call_ids = response_json['id']
        except:
            model_responses = api_response.choices[0].message.content
            tool_call_ids = []

        model_responses_message_for_chat_history = api_response.choices[0].message

        return {
            "model_responses": model_responses,
            "model_responses_message_for_chat_history": model_responses_message_for_chat_history,
            "tool_call_ids": tool_call_ids,
            "input_token": api_response.usage.prompt_tokens,
            "output_token": api_response.usage.completion_tokens,
        }


    def add_first_turn_message_FC(
        self, inference_data: dict, first_turn_message: list[dict]
    ) -> dict:
        inference_data["message"].extend(first_turn_message)
        return inference_data

    def _add_next_turn_user_message_FC(
        self, inference_data: dict, user_message: list[dict]
    ) -> dict:
        inference_data["message"].extend(user_message)
        return inference_data

    def _add_assistant_message_FC(
        self, inference_data: dict, model_response_data: dict
    ) -> dict:
        inference_data["message"].append(
            model_response_data["model_responses_message_for_chat_history"]
        )
        return inference_data

    def _add_execution_results_FC(
        self,
        inference_data: dict,
        execution_results: list[str],
        model_response_data: dict,
    ) -> dict:
        # Add the execution results to the current round result, one at a time
        for execution_result, tool_call_id in zip(
            execution_results, model_response_data["tool_call_ids"]
        ):
            tool_message = {
                "role": "tool",
                "content": execution_result,
                "tool_call_id": tool_call_id,
            }
            inference_data["message"].append(tool_message)

        return inference_data

    def _add_reasoning_content_if_available_FC(
        self, api_response: Any, response_data: dict
    ) -> None:
        """
        OpenAI models don't show reasoning content in the api response,
        but many other models that use the OpenAI interface do, such as DeepSeek and Grok.
        This method is included here to avoid code duplication.

        These models often don't take reasoning content in the chat history for next turn.
        Thus, this method saves reasoning content to response_data (for local result file) if present in the response,
        but does not include it in the chat history.
        """
        # Original assistant message object (contains `reasoning_content` on DeepSeek).
        message = api_response.choices[0].message

        # Preserve tool_call information but strip the unsupported `reasoning_content` field before inserting into chat history.
        if getattr(message, "tool_calls", None):
            assistant_message = {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": tool_call.type,
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments,
                        },
                    }
                    for tool_call in message.tool_calls
                ],
            }
            response_data["model_responses_message_for_chat_history"] = assistant_message

        # If no tool_calls, we still need to strip reasoning_content.
        elif hasattr(message, "reasoning_content"):
            response_data["model_responses_message_for_chat_history"] = {
                "role": "assistant",
                "content": message.content,
            }

        # Capture the reasoning trace so it can be logged to the local result file.
        if hasattr(message, "reasoning_content"):
            response_data["reasoning_content"] = message.reasoning_content

    #### Prompting methods ####

    def _query_prompting(self, inference_data: dict):
        inference_data["inference_input_log"] = {"message": repr(inference_data["message"])}

        return self.generate_with_backoff(
            messages=inference_data["message"],
            model=self.model_name,
            temperature=self.temperature,
            store=False,
        )

    def _pre_query_processing_prompting(self, test_entry: dict) -> dict:
        functions: list = test_entry["function"]
        test_category: str = test_entry["id"].rsplit("_", 1)[0]

        functions = func_doc_language_specific_pre_processing(functions, test_category)

        test_entry["question"][0] = system_prompt_pre_processing_chat_model(
            test_entry["question"][0], functions, test_category
        )

        return {"message": []}

    def _parse_query_response_prompting(self, api_response: any) -> dict:
        return {
            "model_responses": api_response.choices[0].message.content,
            "model_responses_message_for_chat_history": api_response.choices[0].message,
            "input_token": api_response.usage.prompt_tokens,
            "output_token": api_response.usage.completion_tokens,
        }

    def add_first_turn_message_prompting(
        self, inference_data: dict, first_turn_message: list[dict]
    ) -> dict:
        inference_data["message"].extend(first_turn_message)
        return inference_data

    def _add_next_turn_user_message_prompting(
        self, inference_data: dict, user_message: list[dict]
    ) -> dict:
        inference_data["message"].extend(user_message)
        return inference_data

    # def _add_assistant_message_prompting(
    #     self, inference_data: dict, model_response_data: dict
    # ) -> dict:
    #     inference_data["message"].append(
    #         model_response_data["model_responses_message_for_chat_history"]
    #     )
    #     return inference_data

    def _add_assistant_message_prompting(
        self, inference_data: dict, model_response_data: dict
    ) -> dict:
        _chat_message = model_response_data["model_responses_message_for_chat_history"]
        _dict = {
            "role": _chat_message.role,
            "content": _chat_message.content,
        }

        # inference_data["message"].append(
            # model_response_data["model_responses_message_for_chat_history"]
        # )
        inference_data["message"].append(_dict)
        return inference_data

    def _add_execution_results_prompting(
        self, inference_data: dict, execution_results: list[str], model_response_data: dict
    ) -> dict:
        formatted_results_message = format_execution_results_prompting(
            inference_data, execution_results, model_response_data
        )
        inference_data["message"].append(
            {"role": "user", "content": formatted_results_message}
        )

        return inference_data

    def _add_reasoning_content_if_available_prompting(
        self, api_response: Any, response_data: dict
    ) -> None:
        """
        OpenAI models don't show reasoning content in the api response,
        but many other models that use the OpenAI interface do, such as DeepSeek and Grok.
        This method is included here to avoid code duplication.

        These models often don't take reasoning content in the chat history for next turn.
        Thus, this method saves reasoning content to response_data (for local result file) if present in the response,
        but does not include it in the chat history.
        """
        message = api_response.choices[0].message
        if hasattr(message, "reasoning_content"):
            response_data["reasoning_content"] = message.reasoning_content
            # Reasoning content should not be included in the chat history
            response_data["model_responses_message_for_chat_history"] = {
                "role": "assistant",
                "content": str(response_data["model_responses"]),
            }

