# coding: utf-8
"""
学习策略抽象接口

用于定义学习工作流的构建策略，类似于奖励策略的抽象接口。
"""

import abc
from typing import Any

from aworld.core.agent.swarm import Swarm
from aworld.core.context.base import Context
from aworld.experimental.metalearning.reward.base import RewardFunction


class LearningStrategy(abc.ABC):
    """
    学习策略抽象接口
    
    所有学习策略必须实现此接口，提供统一的学习任务执行接口。
    """
    
    @abc.abstractmethod
    def __call__(self, reward_function: RewardFunction, context: Context, task_content: str = "analyze trajectory",
            tmp_file_path: str = None) -> Any:
        """
        执行学习任务
        
        Args:
            context: 上下文对象，包含 session_id、task_id 等信息
            task_content: 任务内容，默认为 "analyze trajectory"
        
        Returns:
            Any: 学习任务执行结果
        """
        pass

