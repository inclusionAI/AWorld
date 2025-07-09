#!/usr/bin/env python3
"""Test script for BuiltInPlanner with LLM model integration."""

import asyncio
import os
from aworld.planner.built_in_planner import BuiltInPlanner, load_built_in_planner
from aworld.models.llm import get_llm_model
from aworld.core.context.base import Context
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_planner_with_llm():
    """Test BuiltInPlanner with LLM model integration."""
    
    # Create LLM model
    # You need to set your API key and endpoint
    llm_model = get_llm_model(
        llm_provider="openai",  # or "anthropic", "azure_openai"
        model_name="gpt-4o-mini",  # or other supported models
        api_key=os.getenv("OPENAI_API_KEY", "your-api-key-here"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        temperature=0.1
    )
    
    # Create planner with LLM model
    planner = BuiltInPlanner(
        llm_model=llm_model,
        temperature=0.1,
        max_tokens=1500
    )
    
    # Create context with tools information
    context = Context()
    context.agent_name = "PlanningAgent"
    context.context_info = {
        "tools": {
            "search_agent": {
                "description": "搜索和检索网络信息、学术论文、新闻等的工具",
                "parameters": ["query", "source_type", "limit"]
            },
            "analysis_agent": {
                "description": "数据分析和推理工具，可以进行统计分析、趋势分析等",
                "parameters": ["data", "analysis_type", "output_format"]
            },
            "summary_agent": {
                "description": "文本总结工具，可以对长文本进行摘要和关键信息提取", 
                "parameters": ["text", "summary_length", "focus_areas"]
            }
        }
    }
    
    # Test planning with different inputs
    test_inputs = [
        {"input": "分析苹果公司2023年的业务发展情况"},
        {"input": "研究人工智能在医疗领域的最新应用"},
        {"input": "帮我制定一个学习Python编程的计划"}
    ]
    
    for i, inputs in enumerate(test_inputs, 1):
        print(f"\n=== Test Case {i} ===")
        print(f"Input: {inputs['input']}")
        
        try:
            # Generate plan using LLM
            plan = planner.plan(context, inputs)
            
            print(f"Generated plan with {len(plan.steps)} steps:")
            for j, step in enumerate(plan.steps, 1):
                print(f"  {j}. {step.value}")
                
        except Exception as e:
            print(f"Error generating plan: {e}")

async def test_async_planner_with_llm():
    """Test async BuiltInPlanner with LLM model integration."""
    
    # Create LLM model
    llm_model = get_llm_model(
        llm_provider="openai",
        model_name="gpt-4o-mini",
        api_key=os.getenv("OPENAI_API_KEY", "your-api-key-here"),
        base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        temperature=0.1
    )
    
    # Create planner using load function
    planner = load_built_in_planner(
        llm_model=llm_model,
        temperature=0.1,
        max_tokens=1500
    )
    
    # Create context
    context = Context()
    context.context_info = {
        "tools": {
            "web_search": {"description": "网络搜索工具"},
            "data_analysis": {"description": "数据分析工具"},
            "report_generator": {"description": "报告生成工具"}
        }
    }
    
    inputs = {"input": "创建一个关于区块链技术发展趋势的研究报告"}
    
    print(f"\n=== Async Test ===")
    print(f"Input: {inputs['input']}")
    
    try:
        # Generate plan asynchronously
        plan = await planner.aplan(context, inputs)
        
        print(f"Generated async plan with {len(plan.steps)} steps:")
        for i, step in enumerate(plan.steps, 1):
            print(f"  {i}. {step.value}")
            
    except Exception as e:
        print(f"Error generating async plan: {e}")

def test_planner_without_llm():
    """Test BuiltInPlanner without LLM model (mock mode)."""
    
    # Create planner without LLM model (will use mock responses)
    planner = BuiltInPlanner()
    
    # Create simple context
    context = Context()
    context.context_info = {"tools": {"basic_tool": "Basic tool description"}}
    
    inputs = {"input": "测试没有LLM模型的规划器"}
    
    print(f"\n=== Mock Test (No LLM) ===")
    print(f"Input: {inputs['input']}")
    
    try:
        plan = planner.plan(context, inputs)
        
        print(f"Generated mock plan with {len(plan.steps)} steps:")
        for i, step in enumerate(plan.steps, 1):
            print(f"  {i}. {step.value}")
            
    except Exception as e:
        print(f"Error generating mock plan: {e}")

def test_custom_prompt():
    """Test BuiltInPlanner with custom system prompt."""
    
    custom_prompt = """你是一个专业的项目规划助手。请按照以下格式创建详细的执行计划：

按照以下流程回答问题：(1) 首先用自然语言制定计划；(2) 然后使用工具执行计划，在工具代码片段之间提供推理来总结当前状态和下一步；(3) 最后返回最终答案。

请按照此格式回答问题：(1) 规划部分应在 {{PLANNING_TAG}} 下；(2) 工具代码片段应在 {{ACTION_TAG}} 下，推理部分应在 {{REASONING_TAG}} 下；(3) 最终答案部分应在 {{FINAL_ANSWER_TAG}} 下。"""
    
    # Note: Using mock mode for this test
    planner = BuiltInPlanner(system_prompt=custom_prompt)
    
    context = Context()
    context.context_info = {
        "tools": {
            "project_tool": {"description": "项目管理工具"},
            "resource_tool": {"description": "资源分配工具"}
        }
    }
    
    inputs = {"input": "为一个新产品开发项目制定6个月的执行计划"}
    
    print(f"\n=== Custom Prompt Test ===")
    print(f"Input: {inputs['input']}")
    
    try:
        plan = planner.plan(context, inputs)
        
        print(f"Generated custom plan with {len(plan.steps)} steps:")
        for i, step in enumerate(plan.steps, 1):
            print(f"  {i}. {step.value}")
            
    except Exception as e:
        print(f"Error generating custom plan: {e}")

if __name__ == "__main__":
    print("=== Testing BuiltInPlanner with LLM Integration ===\n")
    
    # Test 1: Mock mode (no LLM required)
    test_planner_without_llm()
    
    # Test 2: Custom prompt
    test_custom_prompt()
    
    # Test 3: LLM mode (requires API key)
    if os.getenv("OPENAI_API_KEY"):
        test_planner_with_llm()
        
        # Test 4: Async LLM mode
        asyncio.run(test_async_planner_with_llm())
    else:
        print("\n=== Skipping LLM tests (OPENAI_API_KEY not set) ===")
        print("To test with real LLM, set OPENAI_API_KEY environment variable")
        print("Example:")
        print("export OPENAI_API_KEY='your-api-key-here'")
        print("python test_llm_planner.py")
    
    print("\n=== All tests completed ===") 