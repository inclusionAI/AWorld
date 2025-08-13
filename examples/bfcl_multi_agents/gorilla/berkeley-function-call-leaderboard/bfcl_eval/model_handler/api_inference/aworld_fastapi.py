import json
import os
import time
from openai.types.chat import ChatCompletion
import requests
import asyncio
import aiohttp
import time

from typing import Any

from bfcl_eval.constants.type_mappings import GORILLA_TO_OPENAPI
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


class BFCLAPIClient:
    
    def __init__(self, base_url: str = "http://localhost:8010"):
        self.base_url = base_url
    
    def health_check(self):
        try:
            response = requests.get(f"{self.base_url}/health")
            return response.json()
        except Exception as e:
            return {"error": str(e)}

    def exec_aworld_task(self, task_id, **kwargs) -> 'ChatCompletion | None':
        try:
            url = f"{self.base_url}/create/" 
            headers = {"Content-Type": "application/json"}

            query_params = {"task_id": task_id}
            json_body = kwargs
            
            response = requests.post(
                url,
                headers=headers,
                params=query_params,
                json=json_body
            )
            
            response.raise_for_status()

            chat_com_response = response.json()

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
                            "content": chat_com_response['response_text'],
                        },
                        "finish_reason": "stop"
                    }
                ],
                "usage": {
                    "prompt_tokens": chat_com_response['usage']['prompt_tokens'],
                    "completion_tokens": chat_com_response['usage']['completion_tokens'],
                    "total_tokens": chat_com_response['usage']['total_tokens'],
                }
            }
            return ChatCompletion.model_validate(result_json)

        except requests.exceptions.HTTPError as e:
            # 建议增加更详细的错误日志，而不是静默返回 None
            print(f"HTTP Error: {e.response.status_code}, Response: {e.response.text}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return None

            # # 返回一个带错误信息的ChatCompletion对象
            # error_content = f"[Local API Error] {str(e)}"
            # error_json = {
            #     "id": "chatcmpl-local-error",
            #     "object": "chat.completion",
            #     "created": int(time.time()),
            #     "model": kwargs.get("model", "unknown"),
            #     "choices": [
            #         {
            #             "index": 0,
            #             "message": {
            #                 "role": "assistant",
            #                 "content": error_content
            #             },
            #             "finish_reason": "stop"
            #         }
            #     ],
            #     "usage": {
            #         "prompt_tokens": 0,
            #         "completion_tokens": 0,
            #         "total_tokens": 0
            #     }
            # }
            # return ChatCompletion.model_validate(error_json)

    def create_agent(self, task_id:str):
        try:
            url = f"{self.base_url}/create-swarm/"
            headers = {"Content-Type": "application/json"}

            payload = {
                "task_id": task_id,
            }

            response = requests.post(
                url, headers=headers, params=payload,
            )
            response.raise_for_status()
        except Exception as e:
            print(f"Error creating agent: {e}")

    def del_agent(self, task_id:str):
        try:
            url = f"{self.base_url}/delete-swarm/"
            headers = {"Content-Type": "application/json"}
            payload = {
                "task_id": task_id,
            }
            response = requests.post(
                url, headers=headers, params=payload,
            )
            response.raise_for_status()
        except Exception as e:
            print(f"Error creating agent: {e}")


class AWorldOpenAICompletionsHandler(BaseHandler):
    def __init__(self, model_name, temperature) -> None:
        super().__init__(model_name, temperature)
        self.model_style = ModelStyle.OpenAI_Completions
        self.client = BFCLAPIClient()
        self.test_entry_id = None


    def inference(self, test_entry: dict, include_input_log: bool, exclude_state_log: bool):
            # This method is used to retrive model response for each model.

        self.test_entry_id = test_entry["id"]
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
        start_time = time.time()
        api_response = self.client.exec_aworld_task(task_id=self.test_entry_id, **kwargs)
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


if __name__ == "__main__":
    client = BFCLAPIClient()
    response = client.health_check()
    print(response)

    client.create_agent("task_1")
    client.create_agent("task_2")
    client.del_agent("task_1")
    client.create_agent("task_3")
    client.del_agent("task_2")
    client.del_agent("task_3")

