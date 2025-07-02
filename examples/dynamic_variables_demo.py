# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Dynamic Variables Demo

演示如何在PromptTemplate中使用动态变量函数
"""

import sys
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 简化导入，避免循环导入
from aworld.core.context.prompts.string_prompt_template import PromptTemplate
from aworld.core.context.prompts.dynamic_variables import (
    # 时间函数
    get_current_time, get_current_date, get_current_weekday,
    
    # 系统信息函数
    get_system_os, get_username, get_hostname,
    
    # 预定义变量集合
    TIME_VARIABLES, SYSTEM_VARIABLES,
    
    # 工厂函数
    create_custom_time_formatter,
    create_agent_variables,
    create_all_variables,
)


# 模拟一个简单的AgentContext类用于演示
class MockAgentContext:
    def __init__(self):
        self.agent_id = "demo_agent_001"
        self.agent_name = "DemoAssistant"
        self.agent_desc = "A demonstration AI assistant"
        self.step = 5
        self.tool_names = ["search", "calculator", "file_reader"]
        self.messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "How are you?"},
        ]
        self.system_prompt = "You are a helpful AI assistant designed to help users with various tasks."
    
    def get_context_usage_ratio(self):
        return 0.65  # 65%使用率


def demo_basic_usage():
    """演示基本用法"""
    print("=== 基本动态变量使用 ===")
    
    # 创建包含动态变量的模板
    prompt = PromptTemplate(
        template="""你好！我是一个AI助手。
当前时间: {current_time}
今天是: {current_weekday}
运行在: {system_os}
用户: {username}

请回答用户的问题: {user_input}""",
        input_variables=["user_input"],
        partial_variables={
            "current_time": get_current_time,
            "current_weekday": get_current_weekday,
            "system_os": get_system_os,
            "username": get_username,
        }
    )
    
    # 格式化模板
    formatted = prompt.format(user_input="什么是人工智能？")
    print(formatted)
    print()


def demo_predefined_variable_sets():
    """演示预定义变量集合的使用"""
    print("=== 预定义变量集合使用 ===")
    
    # 使用TIME_VARIABLES集合
    time_prompt = PromptTemplate(
        template="""时间信息报告:
- 当前时间: {current_time}
- 今天日期: {current_date}
- 星期: {current_weekday}
- 月份: {current_month}
- 年份: {current_year}

{user_input}""",
        input_variables=["user_input"],
        partial_variables=TIME_VARIABLES
    )
    
    formatted = time_prompt.format(user_input="请基于当前时间制定学习计划")
    print(formatted)
    print()


def demo_agent_context_usage():
    """演示Agent上下文变量的使用"""
    print("=== Agent上下文变量使用 ===")
    
    # 创建模拟的AgentContext
    agent_context = MockAgentContext()
    
    # 创建Agent相关的动态变量
    agent_variables = create_agent_variables(agent_context)
    
    # 创建包含Agent变量的模板
    agent_prompt = PromptTemplate(
        template="""Agent信息:
- ID: {agent_id}
- 名称: {agent_name}
- 描述: {agent_desc}
- 当前步骤: {agent_step}
- 可用工具: {tool_names}
- 消息数量: {message_count}
- 上下文使用率: {context_usage_ratio}
- 模型: {model_name}

{user_input}""",
        input_variables=["user_input"],
        partial_variables=agent_variables
    )
    
    formatted = agent_prompt.format(user_input="请介绍一下当前Agent的状态")
    print(formatted)
    print()


def demo_combined_variables():
    """演示组合使用时间、系统和Agent变量"""
    print("=== 组合变量使用 ===")
    
    # 创建模拟的AgentContext
    agent_context = MockAgentContext()
    
    # 获取所有动态变量
    all_variables = create_all_variables(agent_context)
    
    # 创建综合模板
    comprehensive_prompt = PromptTemplate(
        template="""完整系统状态报告:

📅 时间信息:
- 当前时间: {current_time}
- 日期: {current_date}
- 星期: {current_weekday}

💻 系统信息:
- 操作系统: {system_os}
- 用户: {username}
- 主机名: {hostname}

🤖 Agent信息:
- Agent: {agent_name} ({agent_id})
- 描述: {agent_desc}
- 可用工具: {tool_names}
- 消息历史: {message_count} 条
- 上下文使用率: {context_usage_ratio}

用户请求: {user_input}""",
        input_variables=["user_input"],
        partial_variables=all_variables
    )
    
    formatted = comprehensive_prompt.format(user_input="生成完整的状态报告")
    print(formatted)
    print()


def demo_factory_functions():
    """演示工厂函数创建自定义动态变量"""
    print("=== 工厂函数使用 ===")
    
    # 创建自定义时间格式函数
    get_chinese_time = create_custom_time_formatter("%Y年%m月%d日 %H时%M分")
    get_time_only = create_custom_time_formatter("%H:%M:%S")
    
    prompt = PromptTemplate(
        template="""自定义格式演示:
中文时间: {chinese_time}
仅时间: {time_only}

{user_input}""",
        input_variables=["user_input"],
        partial_variables={
            "chinese_time": get_chinese_time,
            "time_only": get_time_only,
        }
    )
    
    formatted = prompt.format(user_input="展示自定义格式")
    print(formatted)
    print()


def demo_system_variables():
    """演示系统变量的使用"""
    print("=== 系统变量使用 ===")
    
    # 使用SYSTEM_VARIABLES集合
    system_prompt = PromptTemplate(
        template="""系统环境信息:
- 操作系统: {system_os}
- 平台: {system_platform}
- Python版本: {python_version}
- 主机名: {hostname}
- 用户: {username}
- 工作目录: {working_directory}

{user_input}""",
        input_variables=["user_input"],
        partial_variables=SYSTEM_VARIABLES
    )
    
    formatted = system_prompt.format(user_input="分析当前系统环境")
    print(formatted)
    print()


def main():
    """主函数：运行所有演示"""
    print("🚀 Dynamic Variables Demo")
    print("=" * 50)
    
    demo_basic_usage()
    demo_predefined_variable_sets()
    demo_agent_context_usage()
    demo_combined_variables()
    demo_factory_functions()
    demo_system_variables()
    
    print("✅ 演示完成！")
    
    # 显示所有可用的动态变量
    print("\n📋 可用的动态变量示例:")
    print("\n时间变量:")
    for var_name in TIME_VARIABLES.keys():
        print(f"  - {var_name}")
    
    print("\n系统变量:")
    for var_name in SYSTEM_VARIABLES.keys():
        print(f"  - {var_name}")
    
    print("\nAgent变量:")
    print("  - 需要通过 create_agent_variables(agent_context) 创建")
    print("  - 包括: agent_id, agent_name, agent_desc, tool_names 等")


if __name__ == "__main__":
    main() 