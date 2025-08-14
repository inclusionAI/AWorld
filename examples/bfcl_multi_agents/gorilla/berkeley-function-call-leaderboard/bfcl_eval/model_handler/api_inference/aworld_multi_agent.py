import json
import os, sys
import time
from openai.types.chat import ChatCompletion
import requests
import asyncio
import aiohttp
import time

from typing import Any

__root_path__ = os.path.dirname(os.path.abspath(__file__))
for _ in range(7):
    __root_path__ = os.path.dirname(__root_path__)
sys.path.append(__root_path__)

from bfcl_eval.multi_query_agent import MultiQueryAgent
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

AT_SYSTEM_PROMPT_WITHOUT_FUNC_DOC = """You are an expert in composing functions. You are given a question and a set of possible functions. Based on the question, you will need to make one or more function/tool calls to achieve the purpose.
If none of the functions can be used, point it out. If the given question lacks the parameters required by the function, also point it out.
You should only return the function calls in your response.

At each turn, you should try your best to complete the tasks requested by the user within the current turn. Continue to output functions to call until you have fulfilled the user's request to the best of your ability. Once you have no more functions to call, the system will consider the current turn complete and proceed to the next turn or task.
"""

AT_SYSTEM_PROMPT = (
    AT_SYSTEM_PROMPT_WITHOUT_FUNC_DOC
    + """
Here is a list of functions in JSON format that you can invoke.\n{functions}\n

### If you decide to invoke any of the function(s), you MUST put it in the format of [func_name1(params_name1=params_value1, params_name2=params_value2...), func_name2(params)], You SHOULD NOT include any other text in THIS KIND of response.

We provide you following helper agents to help you with the previous tasks. You can call them in the following ways:
    1. Call the verifier agent to verify whether the function call you generated meets the documentation requirements. When calling this verifier agent, you need to briefly describe the user's intention and inform the verifier agent of the result of your function call. The verifier agent will return some suggestions, which you can use to modify your function call. You can pass the entire generated function calls as parameters to the verifier agent, instead of making separate calls.

For example, when the user requests to navigate to the 'document' folder and create a file named 'TeamNotes.txt' for tracking ideas, and the function calls you intend to generate are [cd(folder='document'), create_file(file_name='TeamNotes.txt')], you have two options:
    1.If you are confident enough and believe there are no issues, you can directly output [cd(folder='document'), create_file(file_name='TeamNotes.txt')] instead of calling verifier agent. DO NOT call the verifier agent in this case! DO NOT call any tools! Only output the function calls as strings!!!
    2.Alternatively, you can ask the verifier agent to help you check whether the function calls comply with the requirements. You can pass both the user's intent—"The user wants to navigate to the 'document' folder and create a file named 'TeamNotes.txt' for tracking ideas."—and your generated function calls [cd(folder='document'), create_file(file_name='TeamNotes.txt')] to the verifier agent. It will return some suggestions, which you can then use to refine your function calls.

Remember that only one helper agent can be called per round! Don't call multiple helper agents in the same round! Don't call the same helper agent multiple times in the same round!
"""
)

#  1. Call the helper agent to generate a function call.
#  - You can call the helper agent to generate a function call by sending a message to the helper agent.
#  - The helper agent will return a function call in the format of [func_name1(params_name1=params_value1, params_name2=params_value2...), func_name2(params)].



def aworld_swarm_prompt_processing(function_docs):
    """
    Add a system prompt to the chat model to instruct the model on the available functions and the expected response format.
    If the prompts list already contains a system prompt, append the additional system prompt content to the existing system prompt.
    """
    system_prompt_template = AT_SYSTEM_PROMPT
    system_prompt = system_prompt_template.format(functions=function_docs)

    verify_system_prompt_template = VERIFY_SYSTEM_PROMPT
    verify_system_prompt = verify_system_prompt_template.format(functions=function_docs)

    return system_prompt, verify_system_prompt


def _build_swarm(or_model_name, bfcl_func_docs):
    SWARM_MODEL_NAME=or_model_name
    # Get API key from environment variable

    _base_url = "https://openrouter.ai/api/v1"
    api_key = OPENROUTE_API_KEY

    if or_model_name in ["xlam-lp-70b"]:
        _base_url=os.getenv("AGI_BASE_URL")
        api_key  =os.getenv("AGI_API_KEY")

    execute_prompt, verify_system_prompt = aworld_swarm_prompt_processing(function_docs=bfcl_func_docs)

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=SWARM_MODEL_NAME,
        llm_api_key=api_key,
        llm_base_url=_base_url,
        llm_temperature=0.001,
        max_retries=20,
    )

    exe_agent = MultiQueryAgent(
        conf=agent_config,
        name="generate_function_call_agent",
        system_prompt=execute_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )

    verify_agent = MultiQueryAgent(
        conf=agent_config,
        name="verify_function_call_agent",
        system_prompt=verify_system_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )

    swarm = Swarm(exe_agent, verify_agent, max_steps=1)
    return swarm


def build_swarm_in_agent_as_tool(or_model_name, bfcl_func_docs):
    SWARM_MODEL_NAME=or_model_name
    # Get API key from environment variable

    _base_url = "https://openrouter.ai/api/v1"
    api_key = OPENROUTE_API_KEY

    if or_model_name in ["xlam-lp-70b"]:
        _base_url="https://agi.alipay.com/api"
        api_key="123"

    execute_prompt, verify_system_prompt = aworld_swarm_prompt_processing(function_docs=bfcl_func_docs)

    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=SWARM_MODEL_NAME,
        llm_api_key=api_key,
        llm_base_url=_base_url,
        llm_temperature=0.001,
        max_retries=20,
    )

    exe_agent = MultiQueryAgent(
        conf=agent_config,
        name="generate_function_call_agent",
        system_prompt=execute_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
        use_tools_in_prompt=True,
    )

    verify_agent = MultiQueryAgent(
        conf=agent_config,
        name="verifier_agent",
        desc="You can pass the complete function calls along with the purpose of calling them as parameters to the verifier agent, which will return some suggestions that you can use to modify your function calls.",
        system_prompt=verify_system_prompt,
        mcp_servers=mcp_config.get("mcpServers", []).keys(),
        mcp_config=mcp_config,
    )

    swarm = Swarm( (exe_agent, verify_agent), max_steps=6, build_type=GraphBuildType.HANDOFF)
    return swarm



class LocalAWorldSwarmOpenAICompletionsHandler(BaseHandler):
    def __init__(self, model_name, temperature) -> None:
        super().__init__(model_name, temperature)

        _tmp_res = model_name.split('[')
        assert len(_tmp_res) == 2, "model_name should be in the format of [model_name]"
        self.or_model_name = _tmp_res[1][:-1]

        self.model_style = ModelStyle.OpenAI_Completions
        self.aworld_swarm = None
        self.test_entry_id = None


    def inference(self, test_entry: dict, include_input_log: bool, exclude_state_log: bool):
            # This method is used to retrive model response for each model.

        self.test_entry_id = test_entry["id"]
        if self.aworld_swarm is None:
            self.aworld_swarm = build_swarm_in_agent_as_tool(or_model_name=self.or_model_name, bfcl_func_docs=test_entry['function'])

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
        if "FC" in self.model_name or self.is_fc_model:
            decoded_output = []
            for invoked_function in result:
                name = list(invoked_function.keys())[0]
                params = json.loads(invoked_function[name])
                decoded_output.append({name: params})
            return decoded_output
        else:
            return default_decode_ast_prompting(result, language)

    def decode_execute(self, result):
        if "FC" in self.model_name or self.is_fc_model:
            return convert_to_function_call(result)
        else:
            return default_decode_execute_prompting(result)

    @retry_with_backoff(error_type=RateLimitError)
    def generate_with_backoff(self, **kwargs):
        messages = kwargs.get("messages")

        task_id = self.test_entry_id

        task_input = messages[-1]['content']
        task = Task(
            # id=task_id,
            session_id=task_id,
            name=task_id,
            input=task_input,
            swarm=self.aworld_swarm,
            conf=TaskConfig(system_prompt="You are a helpful assistant.")
        )

        start_time = time.time()
        result = Runners.sync_run_task(task=task)
        # change to submit task
        
        response_text=result[task.id].answer
        response_status=result[task.id].success
        time_cost=result[task.id].time_cost
        usage=result[task.id].usage
        trajectory=result[task.id].trajectory
        
        print(f"{self.test_entry_id}:{task_input}")
        print(f"{self.test_entry_id}:{response_text}")

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
                        "content": response_text,
                    },
                    "finish_reason": "stop"
                }
            ],
            "usage": {
                "prompt_tokens": usage['prompt_tokens'],
                "completion_tokens": usage['completion_tokens'],
                "total_tokens": usage['total_tokens'],
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
            model_responses = [
                {func_call.function.name: func_call.function.arguments}
                for func_call in api_response.choices[0].message.tool_calls
            ]
            tool_call_ids = [
                func_call.id for func_call in api_response.choices[0].message.tool_calls
            ]
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

