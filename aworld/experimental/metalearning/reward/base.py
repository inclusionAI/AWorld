import abc
from typing import Any, List, Dict, Union, Optional
from dataclasses import dataclass

from aworld.core.context.base import Context


@dataclass
class RewardResult:
    """奖励计算结果"""
    score: float
    traj_output: str
    ground_truth: str
    reasoning: str

    def __init__(self, score: float, traj_output: str = "", ground_truth: str = "", reasoning: str = ""):
        self.score = score
        self.traj_output = traj_output
        self.ground_truth = ground_truth
        self.reasoning = reasoning


class RewardFunction(abc.ABC):
    """
    奖励策略抽象接口
    
    所有奖励策略必须实现此接口，提供统一的奖励计算接口。
    """

    @abc.abstractmethod
    async def __call__(
            self,
            context: Context,
            validation_file_path: str,
            traj_file_path: str,
            tmp_file_path: str
    ) -> RewardResult:
        """
        计算奖励分数

        Args:
            traj_validation_dateset: 验证数据集，用于评估轨迹正确性
            running_traj: 运行时的轨迹数据，可以是 List[TrajectoryItem] 或 List[Dict]

        Returns:
            RewardResult: 包含分数、轨迹输出、标准答案和理由的奖励结果
        """
        pass

