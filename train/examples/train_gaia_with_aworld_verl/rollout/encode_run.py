# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
import asyncio
import sys
import os
from typing import List, Any, Dict

from aworld.models.openai_tokenizer import openai_tokenizer

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

# 添加项目路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../../../'))

from train.adapter.verl.aworld_agent_loop import AworldAgentLoop
from aworld.agents.llm_agent import Agent
from aworld.config import AgentConfig
from transformers import AutoTokenizer


class MockAworldAgentLoop(AworldAgentLoop):
    """Mock AworldAgentLoop for testing encode functionality"""
    
    def __init__(self, tokenizer, agent, config):
        self.tokenizer = tokenizer
        self.agent = agent
        self.config = config
    
    async def build_agents(self):
        return self.agent


async def load_trajectory_from_json(file_path: str) -> List[Any]:
    """读取 traj JSON 文件并转换为 trajectory 格式（消息列表）"""
    with open(file_path, 'r', encoding='utf-8') as f:
        # 尝试读取 JSON，可能是单行格式
        content = f.read().strip()
        
        # 尝试解析 JSON
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            # 如果不是标准 JSON，尝试使用 ast.literal_eval
            import ast
            try:
                data = ast.literal_eval(content)
            except Exception as e2:
                raise ValueError(f"无法解析文件: {file_path}, JSON错误: {e}, literal_eval错误: {e2}")
        
        # 如果 data 是列表，检查是否是消息列表
        if isinstance(data, list):
            # 如果列表中的元素是消息格式（有 role 字段），直接返回
            if len(data) > 0 and isinstance(data[0], dict) and 'role' in data[0]:
                return data
            # 否则可能是 trajectory 格式，需要提取消息
            messages = []
            for item in data:
                if isinstance(item, dict):
                    # 如果是 exp_data 格式，提取 messages
                    if 'exp_data' in item and isinstance(item['exp_data'], dict):
                        exp_messages = item['exp_data'].get('messages', [])
                        if exp_messages:
                            messages.extend(exp_messages)
                    # 如果直接是消息格式
                    elif 'role' in item:
                        messages.append(item)
            return messages if messages else data
        
        # 如果 data 是字典，可能包含 trajectory 或 messages 字段
        elif isinstance(data, dict):
            if 'trajectory' in data:
                traj = data['trajectory']
                # 如果 trajectory 是列表，提取消息
                if isinstance(traj, list):
                    messages = []
                    for item in traj:
                        if isinstance(item, dict) and 'exp_data' in item:
                            exp_messages = item['exp_data'].get('messages', [])
                            if exp_messages:
                                messages.extend(exp_messages)
                    return messages if messages else traj
                return traj
            elif 'messages' in data:
                return data['messages']
            else:
                # 如果是一个 dict，获取其所有的 value 并转换为 list
                values = list(data.values())
                # 如果 values 中有列表，尝试提取消息
                messages = []
                for value in values:
                    if isinstance(value, list):
                        for item in value:
                            if isinstance(item, dict) and 'role' in item:
                                messages.append(item)
                # 如果有提取到消息，返回消息列表；否则返回所有 values
                return messages if messages else values
        else:
            return [data]


async def test_encode():
    """测试 encode 功能"""
    path = "/Users/hgc/hgc_repo/aworldcore/pipelines/logs/trajectory/1093217cdb524b148c5275027c615e4a/traj_1093217cdb524b148c5275027c615e4a.json"
    
    print(f"正在读取文件: {path}")
    
    # 读取 trajectory
    trajectory = await load_trajectory_from_json(path)
    print(f"成功读取 trajectory，长度: {len(trajectory)}")
    
    # 打印前几个元素的结构
    if trajectory:
        print(f"\n第一个元素类型: {type(trajectory[0])}")
        if isinstance(trajectory[0], dict):
            print(f"第一个元素的键: {list(trajectory[0].keys())[:10]}")
            # 如果是消息格式，打印 role
            if 'role' in trajectory[0]:
                print(f"第一个消息的 role: {trajectory[0].get('role')}")
    
    # 创建 mock tokenizer（需要根据实际情况调整）
    # 这里使用一个简单的 tokenizer，实际使用时需要根据配置加载
    model_name = os.getenv("LLM_MODEL_NAME", "meta-llama/Llama-3.1-8B-Instruct")
    print(f"\n正在加载 tokenizer: {model_name}")
    try:
        tokenizer = AutoTokenizer.from_pretrained("Qwen/Qwen3-0.6B", trust_remote_code=True)
    except Exception as e:
        print(f"警告: 无法加载 tokenizer {model_name}: {e}")
        print("使用默认 tokenizer...")
        tokenizer = AutoTokenizer.from_pretrained("gpt2", trust_remote_code=True)

    # 创建 mock agent
    agent = Agent(
        conf=AgentConfig(
            llm_model_name=model_name,
            llm_base_url=os.getenv("LLM_BASE_URL", ""),
            llm_api_key=os.getenv("LLM_API_KEY", "")
        ),
        name="test_agent",
        system_prompt="You are a helpful assistant."
    )
    
    # 创建 mock config
    class MockConfig:
        class ActorRolloutRef:
            class Rollout:
                response_length = 128000
            rollout = Rollout()
        actor_rollout_ref = ActorRolloutRef()
    
    config = MockConfig()
    
    # 创建 mock loop
    loop = MockAworldAgentLoop(tokenizer=tokenizer, agent=agent, config=config)
    
    # 调用 convert_memory_trajectory_agent_output
    print("\n正在调用 convert_memory_trajectory_agent_output...")
    try:
        # with open("model_config/qwen_chat_template.jinja", "r") as f:
        #     chat_template = f.read()
        # chat_template = "{%- if tools %}\n {{- '<|im_start|>system\\n' }}\n {%- if messages[0].role == 'system' %}\n {{- messages[0].content + '\\n\\n' }}\n {%- endif %}\n {{- \"# Tools\\n\\nYou may call one or more functions to assist with the user query.\\n\\nYou are provided with function signatures within <tools></tools> XML tags:\\n<tools>\" }}\n {%- for tool in tools %}\n {{- \"\\n\" }}\n {{- tool | tojson }}\n {%- endfor %}\n {{- \"\\n</tools>\\n\\nFor each function call, return a json object with function name and arguments within <tool_call></tool_call> XML tags:\\n<tool_call>\\n{\\\"name\\\": <function-name>, \\\"arguments\\\": <args-json-object>}\\n</tool_call><|im_end|>\\n\" }}\n{%- else %}\n {%- if messages[0].role == 'system' %}\n {{- '<|im_start|>system\\n' + messages[0].content + '<|im_end|>\\n' }}\n {%- endif %}\n{%- endif %}\n{%- set ns = namespace(multi_step_tool=true, last_query_index=messages|length - 1) %}\n{%- for message in messages[::-1] %}\n {%- set index = (messages|length - 1) - loop.index0 %}\n {%- if ns.multi_step_tool and message.role == \"user\" and not(message.content.startswith('<tool_response>') and message.content.endswith('</tool_response>')) %}\n {%- set ns.multi_step_tool = false %}\n {%- set ns.last_query_index = index %}\n {%- endif %}\n{%- endfor %}\n{%- for message in messages %}\n {%- if (message.role == \"user\") or (message.role == \"system\" and not loop.first) %}\n {{- '<|im_start|>' + message.role + '\\n' + message.content + '<|im_end|>' + '\\n' }}\n {%- elif message.role == \"assistant\" %}\n {%- set content = message.content %}\n {%- set reasoning_content = '' %}\n {%- if message.reasoning_content is defined and message.reasoning_content is not none %}\n {%- set reasoning_content = message.reasoning_content %}\n {%- else %}\n {%- if '</think>' in message.content %}\n {%- set content = message.content.split('</think>')[-1].lstrip('\\n') %}\n {%- set reasoning_content = message.content.split('</think>')[0].rstrip('\\n').split('<think>')[-1].lstrip('\\n') %}\n {%- endif %}\n {%- endif %}\n {%- if loop.index0 > ns.last_query_index %}\n {%- if loop.last or (not loop.last and reasoning_content) %}\n {{- '<|im_start|>' + message.role + '\\n<think>\\n' + reasoning_content.strip('\\n') + '\\n</think>\\n\\n' + content.lstrip('\\n') }}\n {%- else %}\n {{- '<|im_start|>' + message.role + '\\n' + content }}\n {%- endif %}\n {%- else %}\n {{- '<|im_start|>' + message.role + '\\n' + content }}\n {%- endif %}\n {%- if message.tool_calls %}\n {%- for tool_call in message.tool_calls %}\n {%- if (loop.first and content) or (not loop.first) %}\n {{- '\\n' }}\n {%- endif %}\n {%- if tool_call.function %}\n {%- set tool_call = tool_call.function %}\n {%- endif %}\n {{- '<tool_call>\\n{\"name\": \"' }}\n {{- tool_call.name }}\n {{- '\", \"arguments\": ' }}\n {%- if tool_call.arguments is string %}\n {{- tool_call.arguments }}\n {%- else %}\n {{- tool_call.arguments | tojson }}\n {%- endif %}\n {{- '}\\n</tool_call>' }}\n {%- endfor %}\n {%- endif %}\n {{- '<|im_end|>\\n' }}\n {%- elif message.role == \"tool\" %}\n {%- if loop.first or (messages[loop.index0 - 1].role != \"tool\") %}\n {{- '<|im_start|>user' }}\n {%- endif %}\n {{- '\\n<tool_response>\\n' }}\n {{- message.content }}\n {{- '\\n</tool_response>' }}\n {%- if loop.last or (messages[loop.index0 + 1].role != \"tool\") %}\n {{- '<|im_end|>\\n' }}\n {%- endif %}\n {%- endif %}\n{%- endfor %}\n{%- if add_generation_prompt %}\n {{- '<|im_start|>assistant\\n' }}\n {%- if enable_thinking is defined and enable_thinking is false %}\n {{- '<think>\\n\\n</think>\\n\\n' }}\n {%- endif %}\n{%- endif %}"

        output = await loop.convert_memory_trajectory_agent_output(trajectory=trajectory, chat_template=None)
        print(f"\n✅ 成功执行 encode!")
        print(f"prompt_ids 长度: {len(output.prompt_ids)}")
        print(f"response_ids 长度: {len(output.response_ids)}")
        print(f"response_mask 长度: {len(output.response_mask)}")
        
        # 打印mask前的response_ids（解码后的内容）
        mask_before_decoded = tokenizer.decode(output.response_ids, skip_special_tokens=True)
        print(f"\nmask前的response_ids (解码后): {mask_before_decoded}")
        
        # 计算mask后的response_ids（只保留response_mask中对应为1的位置）
        masked_response_ids = [output.response_ids[i] for i in range(len(output.response_ids)) 
                              if i < len(output.response_mask) and output.response_mask[i] == 1]
        mask_after_decoded = tokenizer.decode(masked_response_ids, skip_special_tokens=True)
        print(f"mask后的response_ids (解码后): {mask_after_decoded}")
        print(f"mask后的response_ids长度: {len(masked_response_ids)}")
        
        print(f"num_turns: {output.num_turns}")
        print(f"metrics: {output.metrics}")
    except Exception as e:
        print(f"\n❌ 执行失败: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(test_encode())
