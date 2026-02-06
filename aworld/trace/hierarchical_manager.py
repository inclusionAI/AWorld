# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
层次化Span管理器

实现层次化ID体系，支持 a -> a.1 -> a.1.1 格式的span ID管理
"""
import threading
from typing import Optional, Dict, List
from contextvars import ContextVar


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
    """
    层次化Span管理器

    负责生成和管理层次化的span ID，实现以下层次结构：
    - Level 0 (Task): a, b, c, d, ...
    - Level 1 (Agent): a.1, a.2, b.1, ...
    - Level 2 (Operation): a.1.1, a.1.2, a.2.1, ...

    使用ContextVar实现线程安全和异步安全的上下文管理
    """

    def __init__(self):
        # 使用ContextVar实现线程和异步安全
        self._span_stack: ContextVar[List[SpanContext]] = ContextVar('span_stack', default=[])
        # 全局任务级别计数器（使用锁保护）
        self._task_counter = 0
        self._task_counter_lock = threading.Lock()
        # 每个层次的子级计数器（存储在上下文中）
        self._child_counters: ContextVar[Dict[str, int]] = ContextVar('child_counters', default={})

    def _get_next_task_id(self) -> str:
        """
        获取下一个任务级别的ID (a, b, c, ...)

        Returns:
            str: 任务级别ID，如 'a', 'b', 'c'
        """
        with self._task_counter_lock:
            task_id = self._int_to_letter(self._task_counter)
            self._task_counter += 1
            return task_id

    @staticmethod
    def _int_to_letter(n: int) -> str:
        """
        将整数转换为字母序列 (0->a, 1->b, ..., 25->z, 26->aa, ...)

        Args:
            n: 整数索引

        Returns:
            str: 字母序列
        """
        result = ""
        while True:
            result = chr(ord('a') + (n % 26)) + result
            n = n // 26
            if n == 0:
                break
            n -= 1  # 调整以支持 aa, ab, ... 序列
        return result

    def _get_next_child_id(self, parent_id: str) -> str:
        """
        获取父级ID下的下一个子级ID

        Args:
            parent_id: 父级层次ID

        Returns:
            str: 子级层次ID，如 'a.1', 'a.2'
        """
        counters = self._child_counters.get()
        if parent_id not in counters:
            counters[parent_id] = 0

        counters[parent_id] += 1
        child_id = f"{parent_id}.{counters[parent_id]}"

        # 更新上下文中的计数器
        self._child_counters.set(counters)
        return child_id

    def create_task_span(self, task_id: str, **metadata) -> SpanContext:
        """
        创建任务级span上下文

        Args:
            task_id: 任务ID
            **metadata: 额外的元数据

        Returns:
            SpanContext: 任务span上下文
        """
        hierarchical_id = self._get_next_task_id()
        span_context = SpanContext(
            hierarchical_id=hierarchical_id,
            level=0,
            span_type='task',
            task_id=task_id,
            **metadata
        )

        # 获取当前栈并添加新的span
        stack = self._span_stack.get().copy()
        stack.append(span_context)
        self._span_stack.set(stack)

        return span_context

    def create_agent_span(self, agent_name: str, **metadata) -> SpanContext:
        """
        创建代理级span上下文

        Args:
            agent_name: 代理名称
            **metadata: 额外的元数据

        Returns:
            SpanContext: 代理span上下文

        Raises:
            ValueError: 如果没有找到父级任务span
        """
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

        # 更新栈
        new_stack = stack.copy()
        new_stack.append(span_context)
        self._span_stack.set(new_stack)

        return span_context

    def create_operation_span(self, operation_type: str, operation_name: str, **metadata) -> SpanContext:
        """
        创建操作级span上下文（LLM/Tool）

        Args:
            operation_type: 操作类型 ('llm' 或 'tool')
            operation_name: 操作名称
            **metadata: 额外的元数据

        Returns:
            SpanContext: 操作span上下文

        Raises:
            ValueError: 如果没有找到父级agent span
        """
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

        # 更新栈
        new_stack = stack.copy()
        new_stack.append(span_context)
        self._span_stack.set(new_stack)

        return span_context

    def pop_span(self) -> Optional[SpanContext]:
        """
        弹出当前span上下文

        Returns:
            Optional[SpanContext]: 被弹出的span上下文，如果栈为空则返回None
        """
        stack = self._span_stack.get()
        if not stack:
            return None

        new_stack = stack[:-1]
        self._span_stack.set(new_stack)

        return stack[-1]

    def get_current_span(self) -> Optional[SpanContext]:
        """
        获取当前span上下文（不弹出）

        Returns:
            Optional[SpanContext]: 当前span上下文，如果栈为空则返回None
        """
        stack = self._span_stack.get()
        return stack[-1] if stack else None

    def get_span_stack(self) -> List[SpanContext]:
        """
        获取当前完整的span栈

        Returns:
            List[SpanContext]: span栈的副本
        """
        return self._span_stack.get().copy()

    def clear_stack(self):
        """清空当前上下文的span栈"""
        self._span_stack.set([])
        self._child_counters.set({})

    def reset(self):
        """重置管理器（主要用于测试）"""
        with self._task_counter_lock:
            self._task_counter = 0
        self.clear_stack()


# 全局单例实例
_global_hierarchical_manager = HierarchicalSpanManager()


def get_hierarchical_manager() -> HierarchicalSpanManager:
    """获取全局层次化span管理器实例"""
    return _global_hierarchical_manager
