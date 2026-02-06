#!/usr/bin/env python3
"""
简化的层次化Span管理器测试

直接测试核心逻辑，不依赖其他模块
"""
import sys
import os
from contextvars import ContextVar
from typing import Optional, Dict, List
import threading


class SpanContext:
    """Span上下文信息"""

    def __init__(self, hierarchical_id: str, level: int, span_type: str, **metadata):
        self.hierarchical_id = hierarchical_id
        self.level = level
        self.span_type = span_type
        self.metadata = metadata

    def __repr__(self):
        return f"SpanContext(id={self.hierarchical_id}, level={self.level}, type={self.span_type})"


class HierarchicalSpanManager:
    """简化版层次化Span管理器（用于测试）"""

    def __init__(self):
        self._span_stack: ContextVar[List[SpanContext]] = ContextVar('span_stack', default=[])
        self._task_counter = 0
        self._task_counter_lock = threading.Lock()
        self._child_counters: ContextVar[Dict[str, int]] = ContextVar('child_counters', default={})

    def _get_next_task_id(self) -> str:
        with self._task_counter_lock:
            task_id = self._int_to_letter(self._task_counter)
            self._task_counter += 1
            return task_id

    @staticmethod
    def _int_to_letter(n: int) -> str:
        result = ""
        while True:
            result = chr(ord('a') + (n % 26)) + result
            n = n // 26
            if n == 0:
                break
            n -= 1
        return result

    def _get_next_child_id(self, parent_id: str) -> str:
        counters = self._child_counters.get()
        if parent_id not in counters:
            counters[parent_id] = 0

        counters[parent_id] += 1
        child_id = f"{parent_id}.{counters[parent_id]}"
        self._child_counters.set(counters)
        return child_id

    def create_task_span(self, task_id: str, **metadata) -> SpanContext:
        hierarchical_id = self._get_next_task_id()
        span_context = SpanContext(
            hierarchical_id=hierarchical_id,
            level=0,
            span_type='task',
            task_id=task_id,
            **metadata
        )

        stack = self._span_stack.get().copy()
        stack.append(span_context)
        self._span_stack.set(stack)
        return span_context

    def create_agent_span(self, agent_name: str, **metadata) -> SpanContext:
        stack = self._span_stack.get()
        if not stack:
            raise ValueError("No parent task span found. Must create task span first.")

        parent = stack[-1]
        hierarchical_id = self._get_next_child_id(parent.hierarchical_id)

        span_context = SpanContext(
            hierarchical_id=hierarchical_id,
            level=parent.level + 1,
            span_type='agent',
            agent_name=agent_name,
            **metadata
        )

        new_stack = stack.copy()
        new_stack.append(span_context)
        self._span_stack.set(new_stack)
        return span_context

    def create_operation_span(self, operation_type: str, operation_name: str, **metadata) -> SpanContext:
        stack = self._span_stack.get()
        if not stack or stack[-1].level < 1:
            raise ValueError("No parent agent span found. Must create agent span first.")

        parent = stack[-1]
        hierarchical_id = self._get_next_child_id(parent.hierarchical_id)

        span_context = SpanContext(
            hierarchical_id=hierarchical_id,
            level=parent.level + 1,
            span_type=operation_type,
            operation_name=operation_name,
            **metadata
        )

        new_stack = stack.copy()
        new_stack.append(span_context)
        self._span_stack.set(new_stack)
        return span_context

    def pop_span(self) -> Optional[SpanContext]:
        stack = self._span_stack.get()
        if not stack:
            return None

        new_stack = stack[:-1]
        self._span_stack.set(new_stack)
        return stack[-1]

    def get_current_span(self) -> Optional[SpanContext]:
        stack = self._span_stack.get()
        return stack[-1] if stack else None

    def get_span_stack(self) -> List[SpanContext]:
        return self._span_stack.get().copy()

    def clear_stack(self):
        self._span_stack.set([])
        self._child_counters.set({})

    def reset(self):
        with self._task_counter_lock:
            self._task_counter = 0
        self.clear_stack()


def test_integration_scenario():
    """集成测试场景：模拟完整的任务执行流程"""
    manager = HierarchicalSpanManager()
    manager.reset()

    print("=== 集成测试：完整任务执行流程 ===")

    # 任务1开始
    task1 = manager.create_task_span("analyze_document")
    print(f"任务1: {task1.hierarchical_id} - {task1.metadata['task_id']}")

    # 代理1：文档分析代理
    agent1 = manager.create_agent_span("DocumentAnalyzer")
    print(f"  代理1: {agent1.hierarchical_id} - {agent1.metadata['agent_name']}")

    # LLM调用：分析文档内容
    llm1 = manager.create_operation_span("llm", "gpt-4-turbo")
    print(f"    LLM调用: {llm1.hierarchical_id} - {llm1.metadata['operation_name']}")
    manager.pop_span()  # LLM调用完成

    # 工具调用：搜索相关信息
    tool1 = manager.create_operation_span("tool", "WebSearchTool")
    print(f"    工具调用: {tool1.hierarchical_id} - {tool1.metadata['operation_name']}")
    manager.pop_span()  # 工具调用完成

    manager.pop_span()  # 代理1完成

    # 代理2：报告生成代理
    agent2 = manager.create_agent_span("ReportGenerator")
    print(f"  代理2: {agent2.hierarchical_id} - {agent2.metadata['agent_name']}")

    # LLM调用：生成报告
    llm2 = manager.create_operation_span("llm", "claude-3-sonnet")
    print(f"    LLM调用: {llm2.hierarchical_id} - {llm2.metadata['operation_name']}")
    manager.pop_span()  # LLM调用完成

    manager.pop_span()  # 代理2完成
    manager.pop_span()  # 任务1完成

    # 任务2开始
    manager.clear_stack()  # 新的上下文
    task2 = manager.create_task_span("generate_summary")
    print(f"任务2: {task2.hierarchical_id} - {task2.metadata['task_id']}")

    # 代理：摘要生成代理
    agent3 = manager.create_agent_span("SummaryAgent")
    print(f"  代理: {agent3.hierarchical_id} - {agent3.metadata['agent_name']}")

    # LLM调用：生成摘要
    llm3 = manager.create_operation_span("llm", "gpt-4")
    print(f"    LLM调用: {llm3.hierarchical_id} - {llm3.metadata['operation_name']}")

    print("\n期望的层次结构：")
    print("任务1 (a) -> 代理1 (a.1) -> [LLM (a.1.1), Tool (a.1.2)]")
    print("         -> 代理2 (a.2) -> LLM (a.2.1)")
    print("任务2 (b) -> 代理 (b.1) -> LLM (b.1.1)")

    # 验证最终状态
    assert task1.hierarchical_id == 'a', f"Expected 'a', got {task1.hierarchical_id}"
    assert agent1.hierarchical_id == 'a.1', f"Expected 'a.1', got {agent1.hierarchical_id}"
    assert llm1.hierarchical_id == 'a.1.1', f"Expected 'a.1.1', got {llm1.hierarchical_id}"
    assert tool1.hierarchical_id == 'a.1.2', f"Expected 'a.1.2', got {tool1.hierarchical_id}"
    assert agent2.hierarchical_id == 'a.2', f"Expected 'a.2', got {agent2.hierarchical_id}"
    assert llm2.hierarchical_id == 'a.2.1', f"Expected 'a.2.1', got {llm2.hierarchical_id}"
    assert task2.hierarchical_id == 'b', f"Expected 'b', got {task2.hierarchical_id}"
    assert agent3.hierarchical_id == 'b.1', f"Expected 'b.1', got {agent3.hierarchical_id}"
    assert llm3.hierarchical_id == 'b.1.1', f"Expected 'b.1.1', got {llm3.hierarchical_id}"

    print("\n✅ 集成测试通过！层次化ID生成正确。")


def test_basic_functionality():
    """测试基本功能"""
    print("\n=== 基本功能测试 ===")

    manager = HierarchicalSpanManager()
    manager.reset()

    # 测试整数到字母转换
    assert manager._int_to_letter(0) == 'a'
    assert manager._int_to_letter(1) == 'b'
    assert manager._int_to_letter(25) == 'z'
    assert manager._int_to_letter(26) == 'aa'
    print("✅ 整数到字母转换测试通过")

    # 测试任务span创建
    task1 = manager.create_task_span("task_001")
    assert task1.hierarchical_id == 'a'
    assert task1.level == 0
    assert task1.span_type == 'task'
    print("✅ 任务span创建测试通过")

    # 测试代理span创建
    agent1 = manager.create_agent_span("ChatAgent")
    assert agent1.hierarchical_id == 'a.1'
    assert agent1.level == 1
    assert agent1.span_type == 'agent'
    print("✅ 代理span创建测试通过")

    # 测试操作span创建
    llm1 = manager.create_operation_span("llm", "gpt-4")
    assert llm1.hierarchical_id == 'a.1.1'
    assert llm1.level == 2
    assert llm1.span_type == 'llm'
    print("✅ 操作span创建测试通过")

    # 测试错误条件
    manager.clear_stack()
    try:
        manager.create_agent_span("TestAgent")
        assert False, "应该抛出ValueError"
    except ValueError:
        print("✅ 错误条件测试通过")


if __name__ == "__main__":
    test_basic_functionality()
    test_integration_scenario()
    print("\n🎉 所有测试通过！层次化span管理器工作正常。")