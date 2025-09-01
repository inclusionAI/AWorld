import json
import os, sys
import time
from tkinter import NO
from numpy import isin
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from regex import F
import requests
import asyncio
import aiohttp
import time
import re
from copy import deepcopy

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

from bfcl_eval.constants.default_prompts import (
    DEFAULT_USER_PROMPT_FOR_ADDITIONAL_FUNCTION_FC,
    DEFAULT_USER_PROMPT_FOR_ADDITIONAL_FUNCTION_PROMPTING,
    MAXIMUM_STEP_LIMIT,
)
from bfcl_eval.constants.eval_config import RESULT_PATH
from bfcl_eval.eval_checker.multi_turn_eval.multi_turn_utils import (
    STATELESS_CLASSES,
    execute_multi_turn_func_call,
    is_empty_execute_response,
)
from bfcl_eval.model_handler.model_style import ModelStyle
from bfcl_eval.utils import load_file, make_json_serializable, sort_key
from overrides import final



from openai import OpenAI, RateLimitError


# from aworld.agents.llm_agent import Agent

# from aworld.agents.bfcl_agent import BFCLAgent
from aworld.config.conf import AgentConfig, TaskConfig
from aworld.core.agent.swarm import Swarm, GraphBuildType
from aworld.core.task import Task
from aworld.core.agent.swarm import Swarm
from aworld.runner import Runners


SINGLE_INPUT_LIMIT = 2

OPENROUTE_API_KEY = os.getenv("OPENROUTER_API_KEY")

mcp_config = {
    "mcpServers": {}
}

# For multi-step plans, you must consider how the output of one function will be used as an input for a subsequent function (e.g., a user_id from a search_user function is needed for a get_order_history(user_id=...) call).

# If the goal cannot be achieved because no suitable function exists, explain this limitation. If required parameters are missing from the user's request, ask the user for the specific missing information.

PARAM_CHECKER_PROMPT_WITHOUT_FUNC_DOC = """
You are a strict parameter auditor in the field of function calls, and your task is to check whether another model fabricates information when passing parameters during a function call.

I will provide you with the complete conversation history, and your job is to rigorously analyze where the parameter values in the model's function call originate from, and whether the parameters comply with the function's documentation specifications.
You must remember: parameter values that do not appear in the conversation history are often incorrect, especially values like user IDs and numeric quantities. Strict validation is mandatory!

You must adhere to the following instructions:
    1. The dialogue may also contain pronouns or parameters with vague references such as "the city" or "the file." You need to identify the specific, explicit values these refer to by examining the preceding context of the conversation.

Your response must adhere to the following format:
    First, think through your process inside <think></think> tags. During this process, all parameters and value types are checked to ensure they strictly conform to the descriptions in the function documentation. 
    Finally, the verification results are generated in the result field. Each line should consist of the executed function, the test result, and the reason. Note that the inspection result can only be one of two values: pass or fail. The following is an example format.
    <result>
    1. mkdir(dir_name='temp') : pass, no error detected
    2. cd(folder='file_name') : fail, because the folder field cannot be a file name type
    3. call(user_id='dsadsa') : fail, because user_id is not mentioned in the context or cannot be inferred
    </result>
"""

AT_SYSTEM_PROMPT = (
    PARAM_CHECKER_PROMPT_WITHOUT_FUNC_DOC
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

    checker_agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name=SWARM_MODEL_NAME,
        llm_api_key=OPENROUTE_API_KEY,
        llm_base_url="https://openrouter.ai/api/v1",
        llm_temperature=0.001,
        max_retries=20,
    )

    check_agent = MultiQueryAgent(
        conf=checker_agent_config,
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
    return check_agent, fc_agent



class BFCLAWorldSwarmFCHandler(BaseHandler):
    def __init__(self, model_name, temperature) -> None:
        super().__init__(model_name, temperature)

        _tmp_res = model_name.split('[')
        assert len(_tmp_res) == 2, "model_name should be in the format of [model_name]"
        self.or_model_name = _tmp_res[1][:-1]

        self.model_style = ModelStyle.OpenAI_Completions
        self.check_swarm = None
        self.fc_agent = None

        self.test_entry_id = None

        self.tool_results = None

        self.dialog_record = []
        self.param_feedbacks = []
        self.last_turn_executed_tools = []

        self.param_check_logs = []


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
        self.dialog_record = []

        if self.check_swarm is None:
            self.check_swarm, self.fc_agent = _build_swarm(or_model_name=self.or_model_name, bfcl_func_docs=test_entry['function'], compiled_tools=compiled_tools)

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

        if isinstance(result, str):
            raise ValueError("result should be a list of function calls")
        
        if isinstance(result[0], dict):
            execution_list = []
            try:
                function_calls = json.loads(result)
                if not isinstance(function_calls, list):
                    function_calls = [function_calls]
            except json.JSONDecodeError:
                function_calls = [json.loads(call.strip()) for call in result.split(";")]

            for func_call in function_calls:
                name = list(func_call.keys())[0]
                arguments = json.loads(list(func_call.values())[0])
                execution_list.append(
                    f"{name}({','.join([f'{k}={repr(v)}' for k,v in arguments.items()])})"
                )
        else:
            execution_list = result
     
        return execution_list


    def param_checker(self,  origin_execution_list):
        """
            origin_execution_list: ('cd', {'folder': 'documents'})
            param_check_results = [ True, {'p1':True, 'p2':False} ]
        """

        skip_func_names = ['fillFuelTank', 'activateParkingBrake', 'displayCarStatus']

        def skip_param_check(param_name):
            # 使用集合存储所有需要检查的关键词，使代码更简洁高效
            skip_keywords = {'date', 'passport', 'symbol', 'precision'}
            param_name_lower = param_name.lower()
            return any(keyword in param_name_lower for keyword in skip_keywords)

        def my_is_number(s):
            try:
                float(s)
                return True
            except ValueError:
                return False


        param_check_results = []
        for fc in origin_execution_list:
            func_name, arguments = fc
            if len(arguments) == 0 or (func_name in skip_func_names):
                param_check_results.append(True)
                continue

            # arguments to string 
            arguments_values = [ str(v) for _, v in arguments.items() ]
            arguments_names = [ str(k) for k, _ in arguments.items()  ]
            exist_dict = dict( zip( arguments_values, [False for _ in arguments]  ) )

            for history_seq in self.dialog_record:
                split_res = history_seq.split(':')

                role = split_res[0]
                response = ':'.join(split_res[1:])
                
                if role in ['human', 'tool']:
                    # check whether parameter exist
                    for _param_name, _param_val in zip( arguments_names, arguments_values ):
                        if skip_param_check(_param_name):
                            exist_dict[_param_val] = True
                            continue

                        _flag = exist_dict[_param_val]

                        if not _flag:
                            if _param_val.lower() in ['main_card', '20000.0', 'economy', 'comprehensive', 'true', 'false', 'start', 'driver', 'passenger', 'rear_left', 'rear_right', 'buy', "rmb", 'usd', 'eur', 'jpy', 'gbp', 'cad', 'aud', 'inr', 'rub', 'brl', 'mxn', '0', '1', '1.0', '0.0']:
                                # keyword check
                                _flag = True
                            elif "[" in _param_val and "]" in _param_val:
                                # skip list check
                                _flag = True
                            else:
                                _flag = _param_val in response
                                
                                if not _flag:
                                    if my_is_number(_param_val):
                                        try:
                                            _flag = (str(int(float(_param_val))) in response)
                                        except:
                                            _flag = False
                                
                                if (not _flag) and not my_is_number(_param_val):
                                    if '.' in _param_val or '..' in _param_val:
                                        # 路径
                                        if '/' in _param_val:
                                            _split_param = _param_val.split('/')[-1]
                                            _flag = _split_param in response
                                        else:
                                            _flag = True
                                    else:
                                        _flag = _param_val.lower() in response.lower()
                                    
                            exist_dict[_param_val] = _flag
                else:
                    continue

                if all(exist_dict.values()):
                    break
            
            param_check_results.append(exist_dict)
        return param_check_results


    def param_post_process(self, check_results, execution_list, tool_call_ids, origin_execution_list):
        executable_fc_lst = []
        executable_tool_call_ids = []

        param_feedbacks = []

        exec_flag = True 
        # use to truncate the executable fc call list
        for fc_idx, check_result in enumerate(check_results):
            executable_flag = False
            
            if isinstance(check_result, bool):
                executable_flag = check_result
            else:
                assert isinstance(check_result, dict)
                executable_flag = all( list(check_result.values()) )
            
            if exec_flag and executable_flag:
                executable_fc_lst.append(execution_list[fc_idx])
                executable_tool_call_ids.append(tool_call_ids[fc_idx])
            else:
                fc_name, fc_arguments = origin_execution_list[fc_idx]

                if not executable_flag and exec_flag:
                    # 第一个未通过参数校验的出现
                    exec_flag = False
                    feedback_str = "ERROR!!! Following parameters did not pass the verification:'"

                    _s = []
                    for arg, status in check_result.items():
                        if not status:
                            # _s.append(f"{arg}={fc_arguments[arg]}")
                            _s.append( f"{arg}" )
                    feedback_str += ','.join(_s)
                    feedback_str += "\n Consider calling related functions to obtain parameters and re-call this function!"


                else:
                    # 这些则都是因为前面存在不可执行的造成的
                    feedback_str = 'ERROR!!! Previous function call parameters did not pass the verification! You need to re-call this function!'
                    if isinstance(check_result, dict):
                        if not executable_flag:
                            _s = []
                            for arg, status in check_result.items():
                                if not status:
                                    _s.append(f"{arg}")
                            feedback_str += "Also note that" + ','.join(_s) + "did not pass the parameter verification."
                
                param_feedbacks.append( (feedback_str, tool_call_ids[fc_idx])  )

        return executable_fc_lst, executable_tool_call_ids, param_feedbacks


    @retry_with_backoff(error_type=RateLimitError)
    def generate_with_backoff(self, **kwargs):
        messages = kwargs.get("messages")
        task_id = self.test_entry_id

        wait_to_process_msgs = messages[self.processed_idx:]
        self.processed_idx = len(messages)

        param_check_logs = []


        def post_process_plan_generation(plan_generation):
            # 使用正则表达式提取 <plan> 和 </plan> 之间的内容
            think_pattern = r'<think>(.*?)</think>'
            think_match = re.search(think_pattern, plan_generation, re.DOTALL)
            if think_match:
                plan_content = think_match.group(1).strip()
                return f"<think>{plan_content}</think>"
            else:
                return f"<think>{plan_generation}</think>"

        # add try here.
        def extract_function_calls(json_message):
            try:
                response_json = json.loads( json_message )
                model_responses = response_json['response']
                tool_call_ids = response_json['id']

                function_calls = model_responses
                
                execution_list = []
                origin_execution_list = []
                for func_call in function_calls:
                    name = list(func_call.keys())[0]
                    arguments = json.loads(list(func_call.values())[0])
                    origin_execution_list.append( (name, arguments) )
                    execution_list.append(
                        f"{name}({','.join([f'{k}={repr(v)}' for k,v in arguments.items()])})"
                    )
            except:
                execution_list, tool_call_ids, origin_execution_list = [], [], []

            return execution_list, tool_call_ids, origin_execution_list

        
        def tool_call_post_process(extracted_fc_tuple, fc_trun_idx, param_check_logs):
            continue_submit_task = False

            execution_list, tool_call_ids, origin_execution_list = extracted_fc_tuple # (["echo(content='Q1: $5000, Q2: $7000, Q3: $6000, Q4: $8000',file_name='annual_report.txt')"], ['call_0_8260236c15ad4159a94f769d6c20b8e4'], [(...)])
            # tool call turn
            check_results = self.param_checker(origin_execution_list=origin_execution_list)
            # 后置处理
            # 1. 去除没有通过参数校验的函数调用
            # execution_list, tool_call_ids, origin_execution_list

            executable_fc_lst, executable_tool_call_ids, param_feedbacks = self.param_post_process(
                check_results=check_results,
                execution_list=execution_list, 
                tool_call_ids=tool_call_ids, 
                origin_execution_list=origin_execution_list
            )

            param_check_logs.append( {
                "fc_trun_idx": fc_trun_idx,
                "execution_list": execution_list,
                "check_results": check_results,
                "executable_fc_lst": executable_fc_lst,
                "param_feedbacks": param_feedbacks,
            } )

            # do parameter check here
            # check: last agent's function call
            # source : user input , successful tool execution result
            # check_task = Task(
            #     # session_id=f"check_{task_id}",
            #     name=f"check_{task_id}",
            #     input='\n'.join(self.dialog_record),
            #     # swarm=self.plan_swarm,
            #     agent=self.check_swarm,
            #     conf=TaskConfig(system_prompt="You are a helpful assistant.")
            # )
            # check_result = Runners.sync_run_task(task=check_task)
            # check_response = check_result[check_task.id].answer
            # print(check_response)

            # 2. 重整为可接受的 json
            if len(executable_fc_lst) > 0:
                # 有可以执行的, 把能执行的执行了:
                continue_submit_task = False
                self.dialog_record.append( f"agent: { ','.join(executable_fc_lst) }" )

                fc_response_text = json.dumps( { "response": executable_fc_lst, "id": executable_tool_call_ids} )
                self.param_feedbacks = param_feedbacks
                self.last_turn_executed_tools = executable_fc_lst
                return fc_response_text, continue_submit_task
            else:
                # 全错了
                if fc_trun_idx == SINGLE_INPUT_LIMIT-1:
                    # 别试了，FC model想怎么执行怎么执行
                    continue_submit_task = False
                    self.dialog_record.append( f"agent: { ','.join(execution_list) }" )

                    fc_response_text = json.dumps( { "response": execution_list, "id": tool_call_ids } )
                    self.last_turn_executed_tools = execution_list
                    self.param_feedbacks = []
                    return fc_response_text, continue_submit_task
                else:
                    # 继续试 mock tool call results, 返回给模型重新执行
                    continue_submit_task = True

                    self.last_turn_executed_tools = None
                    self.param_feedbacks = param_feedbacks

                    return None, continue_submit_task


        def preprocess_task_input(wait_to_process_msgs, have_added_to_fc_input=False):
            # wait_to_process_msgs : List
            fc_input = []    

            if not have_added_to_fc_input:
                fc_input.extend(wait_to_process_msgs)
                # add human input and fc tool result to record:
                for msg_idx, msg in enumerate(wait_to_process_msgs):
                    try:    
                        if msg['role'] == 'tool':
                            self.dialog_record.append( f"tool: { msg['content'] }")
                        elif msg['role'] == 'user':
                            self.dialog_record.append( f"human: { msg['content'] }" )
                    except:
                        assert isinstance(msg, ChatCompletionMessage)
                        continue

            for feedback in self.param_feedbacks:
                fc_input.append( 
                    {
                        'role': 'tool',
                        'content': feedback[0],
                        'tool_call_id': feedback[1]
                    }
                )
            # clear the param_feedbacks
            self.param_feedbacks = []
            return fc_input

        def submit_and_postprocess_task(task_id, wait_to_process_msgs, param_check_logs):
            
            for fc_trun_idx in range(SINGLE_INPUT_LIMIT):
                # local processed idx
                have_added_to_fc_input = fc_trun_idx != 0
                fc_input = preprocess_task_input(wait_to_process_msgs=wait_to_process_msgs, have_added_to_fc_input=have_added_to_fc_input )
                # _processed_message_len                 

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
                execution_list, tool_call_ids, origin_execution_list = extract_function_calls(fc_response_text)
                
                if len(execution_list) > 0:
                    extracted_fc_tuple = execution_list, tool_call_ids, origin_execution_list
                    fc_response_text, continue_submit_task = tool_call_post_process(extracted_fc_tuple, fc_trun_idx, param_check_logs)
                    if continue_submit_task:
                        continue
                    else:
                        return fc_response_text, fc_result[fc_task.id]
                else:
                    # 直接返回
                    return fc_response_text, fc_result[fc_task.id]


        # 为了兼容 model要求，要mock成 tool call 的message 传递进去。

        start_time = time.time()
    
        fc_response_text, fc_result = submit_and_postprocess_task(
            task_id=task_id,
            wait_to_process_msgs=wait_to_process_msgs,
            param_check_logs=param_check_logs
        )

        fc_usage = fc_result.usage

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

        self.param_check_logs = param_check_logs 

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
        """
            第一段处理
        """
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


    # ————————————————————————————————— 我们修改了该函数，去除了final的设定，以便添加额外的日志 —————————————————————————————————————————
    def inference_multi_turn_FC(
        self, test_entry: dict, include_input_log: bool, exclude_state_log: bool
    ) -> tuple[list[list], dict]:
        initial_config: dict = test_entry["initial_config"]
        involved_classes: list = test_entry["involved_classes"]
        test_entry_id: str = test_entry["id"]
        test_category: str = test_entry_id.rsplit("_", 1)[0]

        # This is only for the miss function category
        # A mapping from turn index to function to holdout
        holdout_function: dict[int, list] = test_entry.get("missed_function", {})

        total_input_token_count: list[list[float]] = []
        total_output_token_count: list[list[float]] = []
        total_latency: list[list[float]] = []
        all_model_response: list[list] = (
            []
        )  # The model response that will be used for later evaluation
        all_inference_log: list[list[dict]] = (
            []
        )  # The debugging log for human to understand
        force_quit = False  # Whether the model has been forced to quit. If True, this whole entry will be failed.

        all_reasoning_content: list[list] = []
        # Execute no function call, but just to get a reference to all the instances to get the initial state for logging purpose
        if not exclude_state_log:
            # init the evaluation system
            _, involved_instances = execute_multi_turn_func_call(
                [],
                initial_config,
                involved_classes,
                self.model_name_underline_replaced,
                test_entry_id,
                long_context=(
                    "long_context" in test_category or "composite" in test_category
                ),
                is_evaL_run=False,
            )
            state_log = []
            for class_name, class_instance in involved_instances.items():
                if class_name in STATELESS_CLASSES:
                    continue
                # Avoid modification in future turns
                class_instance = deepcopy(class_instance)
                state_log.append(
                    {
                        "role": "state_info",
                        "class_name": class_name,
                        "content": {
                            key: value
                            for key, value in vars(class_instance).items()
                            if not key.startswith("_")
                        },
                    }
                )
            all_inference_log.append(state_log)

        inference_data: dict = {}
        inference_data = self._pre_query_processing_FC(inference_data, test_entry)
        inference_data = self._compile_tools(inference_data, test_entry)

        all_multi_turn_messages: list[list[dict]] = test_entry["question"]
        for turn_idx, current_turn_message in enumerate(all_multi_turn_messages):
            current_turn_message: list[dict]

            if str(turn_idx) in holdout_function:
                test_entry["function"].extend(holdout_function[str(turn_idx)])
                # Since we have added new functions, we need to recompile the tools
                inference_data = self._compile_tools(inference_data, test_entry)
                assert (
                    len(current_turn_message) == 0
                ), "Holdout turn should not have user message."
                current_turn_message = [
                    {
                        "role": "user",
                        "content": DEFAULT_USER_PROMPT_FOR_ADDITIONAL_FUNCTION_FC,
                    }
                ]

            if turn_idx == 0:
                inference_data = self.add_first_turn_message_FC(
                    inference_data, current_turn_message
                )
            else:
                inference_data = self._add_next_turn_user_message_FC(
                    inference_data, current_turn_message
                )

            current_turn_response = []
            current_turn_inference_log: list[dict] = {
                "begin_of_turn_query": current_turn_message
            }
            current_turn_input_token_count: list[float] = []
            current_turn_output_token_count: list[float] = []
            current_turn_latency: list[float] = []
            current_turn_reasoning_content = []

            count = 0
            while True:
                print("-" * 100)
                print(
                    f"ID: {test_entry_id.replace('multi_turn_', '')}, Turn: {turn_idx}, Step: {count}"
                )
                current_step_inference_log: list[dict] = []
                # Add to the current_turn_inference_log at beginning of each step so that we don't need to bother dealing with the break statements
                current_turn_inference_log[f"step_{count}"] = current_step_inference_log

                api_response, query_latency = self._query_FC(inference_data)

                # This part of logging is disabled by default because it is too verbose and will make the result file extremely large
                # It is only useful to see if the inference pipeline is working as expected (eg, does it convert all the inputs correctly)
                if include_input_log:
                    current_step_inference_log.append(
                        {
                            "role": "inference_input",
                            "content": inference_data.get("inference_input_log", ""),
                        }
                    )

                # Try parsing the model response
                model_response_data = self._parse_query_response_FC(api_response)
                model_responses = model_response_data["model_responses"]
                
                print(f"Model response: {model_responses}")
                # Add the assistant message to the chat history
                inference_data = self._add_assistant_message_FC(
                    inference_data, model_response_data
                )

                # Process the metadata
                current_turn_input_token_count.append(model_response_data["input_token"])
                current_turn_output_token_count.append(model_response_data["output_token"])
                current_turn_latency.append(query_latency)

                current_turn_response.append(model_responses)

                reasoning_content = model_response_data.get("reasoning_content", "")
                current_turn_reasoning_content.append(reasoning_content)

                log_entry = {
                    "role": "assistant",
                    "content": model_responses,
                    "param_check_logs": self.param_check_logs,
                }
                if reasoning_content:
                    log_entry["reasoning_content"] = reasoning_content

                current_step_inference_log.append(log_entry)

                # Try decoding the model response
                try:
                    decoded_model_responses = self.decode_execute(model_responses)
                    current_step_inference_log.append(
                        {
                            "role": "handler_log",
                            "content": "Successfully decoded model response.",
                            "origin_model_output" : model_responses,
                            "model_response_decoded": decoded_model_responses,
                        }
                    )

                    if is_empty_execute_response(decoded_model_responses):
                        print("Empty response from the model. Proceed to next turn.")
                        current_step_inference_log.append(
                            {
                                "role": "handler_log",
                                "content": f"Empty response from the model. Proceed to next turn.",
                                "model_response_decoded": decoded_model_responses,
                            }
                        )
                        break

                except Exception as e:
                    print("Failed to decode the model response. Proceed to next turn.")
                    current_step_inference_log.append(
                        {
                            "role": "handler_log",
                            "content": f"Error decoding the model response. Proceed to next turn.",
                            "origin_model_output" : model_responses,
                            "error": str(e),
                        }
                    )
                    break

                # Obtain the execution results
                execution_results, involved_instances = execute_multi_turn_func_call(
                    decoded_model_responses,
                    initial_config,
                    involved_classes,
                    self.model_name_underline_replaced,
                    test_entry_id,
                    long_context=(
                        "long_context" in test_category or "composite" in test_category
                    ),
                    is_evaL_run=False,
                )

                # Add the execution results to the chat history for the next turn
                inference_data = self._add_execution_results_FC(
                    inference_data, execution_results, model_response_data
                )

                for execution_result in execution_results:
                    current_step_inference_log.append(
                        {
                            "role": "tool",
                            "content": execution_result,
                        }
                    )

                count += 1
                # Force quit after too many steps
                if count > MAXIMUM_STEP_LIMIT:
                    force_quit = True
                    current_step_inference_log.append(
                        {
                            "role": "handler_log",
                            "content": f"Model has been forced to quit after {MAXIMUM_STEP_LIMIT} steps.",
                        }
                    )

                    break

            # Add to the total list
            all_model_response.append(current_turn_response)
            all_inference_log.append(current_turn_inference_log)
            all_reasoning_content.append(current_turn_reasoning_content)
            total_input_token_count.append(current_turn_input_token_count)
            total_output_token_count.append(current_turn_output_token_count)
            total_latency.append(current_turn_latency)

            if not exclude_state_log:
                state_log = []
                for class_name, class_instance in involved_instances.items():
                    if class_name in STATELESS_CLASSES:
                        continue
                    # Avoid modification in future turns
                    class_instance = deepcopy(class_instance)
                    state_log.append(
                        {
                            "role": "state_info",
                            "class_name": class_name,
                            "content": {
                                key: value
                                for key, value in vars(class_instance).items()
                                if not key.startswith("_")
                            },
                        }
                    )
                all_inference_log.append(state_log)

            if force_quit:
                break

        metadata = {
            "input_token_count": total_input_token_count,
            "output_token_count": total_output_token_count,
            "latency": total_latency,
            "inference_log": all_inference_log,
        }

        if not all(
            all(content == "" for content in single_turn_reasoning_content)
            for single_turn_reasoning_content in all_reasoning_content
        ):
            metadata["reasoning_content"] = all_reasoning_content

        return all_model_response, metadata


    # ———————————————————————————————————————— PROMPTING PART —————————————————————————————————————————————————

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



