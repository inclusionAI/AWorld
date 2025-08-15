import json
import os
import time
from openai.types.chat import ChatCompletion
import requests
import asyncio
import aiohttp
import time
from overrides import override

import os, sys

__root_path__ = os.path.dirname(os.path.abspath(__file__))
for _ in range(7):
    __root_path__ = os.path.dirname(__root_path__)
sys.path.append(__root_path__)


from typing import Any

from bfcl_eval.multi_query_agent import MultiQueryAgent
from bfcl_eval.constants.type_mappings import GORILLA_TO_OPENAPI
from bfcl_eval.constants.default_prompts import DEFAULT_SYSTEM_PROMPT
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


from aworld.agents.llm_agent import Agent
# from aworld.agents.bfcl_agent import BFCLAgent
from aworld.config.conf import AgentConfig, TaskConfig

from aworld.core.task import Task
from aworld.runner import Runners


def build_aworld_agent(or_model_name, bfcl_prompt):
    try:
        SWARM_MODEL_NAME=or_model_name
        # Get API key from environment variable

        _base_url = "https://openrouter.ai/api/v1"
        api_key = os.getenv("OPENROUTER_API_KEY")

        if or_model_name in ["xlam-lp-70b"]:
            _base_url=os.getenv("AGI_BASE_URL")
            api_key  =os.getenv("AGI_API_KEY")

        agent_config = AgentConfig(
            llm_provider="openai",
            llm_model_name=SWARM_MODEL_NAME,
            llm_api_key=api_key,
            llm_base_url=_base_url,
            llm_temperature=0.001,
            max_retries=10,
        )

        # Register the MCP tool here, or create a separate configuration file.
        mcp_config = {
            "mcpServers": {}
        }

        # sys_prompt has no effect on BFCLAgent
        file_sys_prompt = bfcl_prompt
        
        exe_agent = MultiQueryAgent(
            conf=agent_config,
            name="file_sys_agent",
            system_prompt=file_sys_prompt,
            mcp_servers=mcp_config.get("mcpServers", []).keys(),
            mcp_config=mcp_config,
        )

        print(f"===================== Agent initialization completed! =====================")
        return exe_agent
    
    except Exception as e:
        print(f"Error in build_aworld_swarm: {e}")
        return None


def aworld_prompt_processing(function_docs):
    """
    Add a system prompt to the chat model to instruct the model on the available functions and the expected response format.
    If the prompts list already contains a system prompt, append the additional system prompt content to the existing system prompt.
    """
    system_prompt_template = DEFAULT_SYSTEM_PROMPT
    system_prompt = system_prompt_template.format(functions=function_docs)

    return system_prompt



class AWorldOpenAICompletionsHandlerXLAM(BaseHandler):
    def __init__(self, model_name, temperature) -> None:
        super().__init__(model_name, temperature)

        _tmp_res = model_name.split('[')
        assert len(_tmp_res) == 2, "model_name should be in the format of [model_name]"
        self.or_model_name = _tmp_res[1][:-1]

        self.model_style = ModelStyle.OpenAI_Completions
        self.aworld_agent = None
        self.test_entry_id = None


    def inference(self, test_entry: dict, include_input_log: bool, exclude_state_log: bool):
            # This method is used to retrive model response for each model.

        self.test_entry_id = test_entry["id"]

        if self.aworld_agent is None:
            self.aworld_agent = build_aworld_agent(or_model_name=self.or_model_name, bfcl_prompt=aworld_prompt_processing(test_entry['function']))

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


    # def _format_prompt(self, messages, function):
    #     formatted_prompt = "<|begin_of_text|>"

    #     system_message = "You are a helpful assistant that can use tools. You are developed by Salesforce xLAM team."
    #     remaining_messages = messages
    #     if messages[0]["role"] == "system":
    #         system_message = messages[0]["content"].strip()
    #         remaining_messages = messages[1:]

    #     # Format system message with tool instructions
    #     formatted_prompt += "<|start_header_id|>system<|end_header_id|>\n\n"
    #     formatted_prompt += system_message + "\n"
    #     formatted_prompt += "You have access to a set of tools. When using tools, make calls in a single JSON array: \n\n"
    #     formatted_prompt += '[{"name": "tool_call_name", "arguments": {"arg1": "value1", "arg2": "value2"}}, ... (additional parallel tool calls as needed)]\n\n'
    #     formatted_prompt += "If no tool is suitable, state that explicitly. If the user's input lacks required parameters, ask for clarification. "
    #     formatted_prompt += "Do not interpret or respond until tool results are returned. Once they are available, process them or make additional calls if needed. "
    #     formatted_prompt += "For tasks that don't require tools, such as casual conversation or general advice, respond directly in plain text. The available tools are:\n\n"

    #     for func in function:
    #         formatted_prompt += json.dumps(func, indent=4) + "\n\n"
    #     formatted_prompt += "<|eot_id|>"

    #     # Format conversation messages
    #     for message in remaining_messages:
    #         if message["role"] == "tool":
    #             formatted_prompt += "<|start_header_id|>ipython<|end_header_id|>\n\n"
    #             if isinstance(message["content"], (dict, list)):
    #                 formatted_prompt += json.dumps(message["content"])
    #             else:
    #                 formatted_prompt += message["content"]
    #             formatted_prompt += "<|eot_id|>"
    #         elif "tool_calls" in message and message["tool_calls"]:
    #             formatted_prompt += "<|start_header_id|>assistant<|end_header_id|>\n\n"
    #             tool_calls = []
    #             for tool_call in message["tool_calls"]:
    #                 tool_calls.append(
    #                     {
    #                         "name": tool_call["function"]["name"],
    #                         "arguments": json.loads(tool_call["function"]["arguments"]),
    #                     }
    #                 )
    #             formatted_prompt += json.dumps(tool_calls) + "<|eot_id|>"
    #         else:
    #             formatted_prompt += f"<|start_header_id|>{message['role']}<|end_header_id|>\n\n{message['content'].strip()}<|eot_id|>"

    #     formatted_prompt += "<|start_header_id|>assistant<|end_header_id|>\n\n"
    #     return formatted_prompt

    @override
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

    @override
    def decode_execute(self, result):
        try:
            function_calls = json.loads(result)
            if not isinstance(function_calls, list):
                function_calls = [function_calls]
        except json.JSONDecodeError:
            function_calls = [json.loads(call.strip()) for call in result.split(";")]

        execution_list = []
        for func_call in function_calls:
            name = func_call["name"]
            arguments = func_call["arguments"]
            execution_list.append(
                f"{name}({','.join([f'{k}={repr(v)}' for k,v in arguments.items()])})"
            )

        return execution_list


    @retry_with_backoff(error_type=RateLimitError)
    def generate_with_backoff(self, **kwargs):
        messages = kwargs.get("messages")

        task_id = self.test_entry_id
        task = Task(
            session_id=task_id,
            name=task_id,
            input=messages[-1]['content'],
            agent=self.aworld_agent,
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

    # def _pre_query_processing_prompting(self, test_entry: dict) -> dict:
    #     functions: list = test_entry["function"]
    #     test_category: str = test_entry["id"].rsplit("_", 1)[0]

    #     functions = func_doc_language_specific_pre_processing(functions, test_category)

    #     test_entry["question"][0] = system_prompt_pre_processing_chat_model(
    #         test_entry["question"][0], functions, test_category
    #     )

    #     return {"message": []}


    @override
    def _pre_query_processing_prompting(self, test_entry: dict) -> dict:
        functions: list = test_entry["function"]
        test_category: str = test_entry["id"].rsplit("_", 1)[0]
        functions = func_doc_language_specific_pre_processing(functions, test_category)
        # override the default bfcl system prompt, xLAM uses its own system prompt
        return {"message": [], "function": functions}



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

