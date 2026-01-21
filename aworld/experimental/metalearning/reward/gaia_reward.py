# coding: utf-8
"""
GAIA 奖励函数

用于评估轨迹执行结果与 GAIA 验证数据集中标准答案的匹配程度。
"""

import json
import re
from difflib import SequenceMatcher
from typing import Dict, List
from typing import Union

from aworld.core.context.base import Context
from aworld.experimental.metalearning.reward.base import RewardFunction, RewardResult
from aworld.logs.util import logger


class GaiaMatchRewardFunction(RewardFunction):
    """
    GAIA 匹配奖励策略实现类
    """

    async def __call__(
        self,
        context: Context,
        validation_file_path: str,
        traj_file_path: str,
        tmp_file_path: str
    ) -> RewardResult:
        # 实现实际的奖励计算逻辑
        if not traj_file_path or not validation_file_path:
            logger.warning("轨迹文件或验证数据集文件未下载成功，无法计算奖励")
            return RewardResult(score=0.0, reasoning="Trajectory or validation dataset file not downloaded successfully")

        try:
            # 读取轨迹数据
            traj_data = self._load_traj_data(traj_file_path)
            if not traj_data:
                logger.warning("轨迹数据为空，无法计算奖励")
                return RewardResult(score=0.0, reasoning="Trajectory data is empty")

            # 读取验证数据集
            validation_data = self._load_validation_data(validation_file_path)
            if not validation_data:
                logger.warning("验证数据集为空，无法计算奖励")
                return RewardResult(score=0.0, reasoning="Validation dataset is empty")

            # 从轨迹中提取query
            traj_query = self._extract_query_from_traj(traj_data)
            if not traj_query:
                logger.warning("无法从轨迹中提取query")
                return RewardResult(score=0.0, reasoning="Unable to extract query from trajectory")

            # 从轨迹中提取最后输出
            traj_output = self._extract_final_output_from_traj(traj_data)
            if not traj_output:
                logger.warning("无法从轨迹中提取最终输出")
                return RewardResult(score=0.0, reasoning="Unable to extract final output from trajectory")

            # 在validation数据中找到匹配的记录
            matched_record = self._find_matching_validation_record(validation_data, traj_query)
            if not matched_record:
                logger.warning(f"未找到匹配的验证记录，query: {traj_query[:100]}...")
                return RewardResult(score=0.0, reasoning="No matching validation record found")

            # 获取标准答案
            ground_truth = matched_record.get('Final answer', '')
            if not ground_truth:
                logger.warning("匹配的验证记录中没有Final answer字段")
                return RewardResult(score=0.0, reasoning="No 'Final answer' field in matched validation record")

            # 计算匹配分数
            score, reasoning = self._calculate_match_score(traj_output, ground_truth)
            logger.info(f"奖励计算完成 - Query: {traj_query[:50]}..., 轨迹输出: {traj_output[:50]}..., 标准答案: {ground_truth[:50]}..., 分数: {score}")

            return RewardResult(
                score=score,
                traj_output=traj_output,
                ground_truth=ground_truth,
                reasoning=reasoning
            )

        except Exception as e:
            logger.error(f"计算奖励时出错: {e}")
            return RewardResult(score=0.0, reasoning=f"Error calculating reward: {e}")

    def _load_traj_data(self, file_path: str) -> Union[List[Dict], Dict]:
        """加载轨迹数据文件（支持JSON和JSONL格式）"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                if file_path.endswith('.jsonl'):
                    # JSONL格式：每行一个JSON对象
                    data = []
                    for line in f:
                        line = line.strip()
                        if line:
                            data.append(json.loads(line))
                    return data if len(data) > 1 else (data[0] if data else {})
                else:
                    # JSON格式
                    return json.load(f)
        except Exception as e:
            logger.error(f"加载轨迹数据失败: {e}")
            return None

    def _load_validation_data(self, file_path: str) -> List[Dict]:
        """加载验证数据集（JSONL格式）"""
        try:
            data = []
            with open(file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        data.append(json.loads(line))
            return data
        except Exception as e:
            logger.error(f"加载验证数据集失败: {e}")
            return []

    def _extract_query_from_traj(self, traj_data) -> str:
        """从轨迹数据中提取query（messages里第一条user的content）"""
        if isinstance(traj_data, str):
            traj_data = json.loads(traj_data)
        try:
            messages = traj_data[-1].get('state').get('messages')
            for msg in messages:
                if msg.get('role') == 'user':
                    return str(msg.get('content'))
        except Exception as e:
            logger.error(f"提取query失败: {e}")
            return ""

    def _extract_final_output_from_traj(self, traj_data: Union[List[Dict], Dict]) -> str:
        """从轨迹数据中提取最终输出（messages最后一条assistant的content）"""
        if isinstance(traj_data, str):
            traj_data = json.loads(traj_data)
        try:
            messages = traj_data[-1].get('state').get('messages')
            i = len(messages) - 1
            while i >= 0:
                msg = messages[i]
                if msg.get('role') == 'assistant':
                    content = str(msg.get('content'))
                    # 如果内容是<answer>...</answer>格式，去掉标签
                    match = re.search(r'<answer>(.*?)</answer>', content.strip(), flags=re.DOTALL)
                    if match:
                        return match.group(1)
                    return content
                i -= 1
        except Exception as e:
            logger.error(f"提取query失败: {e}")
            return ""

    def _find_matching_validation_record(self, validation_data: List[Dict], query: str) -> Dict:
        """在验证数据集中找到Query匹配的记录（支持Question和Query字段）"""
        query_normalized = query.strip().lower()
        for record in validation_data:
            # 同时支持"Question"和"Query"字段
            question = record.get('Question', '') or record.get('Query', '')
            if question and question.strip().lower() == query_normalized:
                return record
        return None

    def _calculate_match_score(self, traj_output: str, standard_answer: str) -> tuple[float, str]:
        """计算匹配分数（0-1之间）并返回理由"""
        if not traj_output or not standard_answer:
            return 0.0, "Empty input or answer"

        traj_output = traj_output.strip()
        standard_answer = standard_answer.strip()

        # 完全匹配
        if traj_output == standard_answer:
            return 1.0, "Perfect match"

        # 忽略大小写的完全匹配
        if traj_output.lower() == standard_answer.lower():
            return 0.95, "Case-insensitive perfect match"

        # 检查标准答案是否包含在轨迹输出中（或相反）
        traj_lower = traj_output.lower()
        answer_lower = standard_answer.lower()
        if answer_lower in traj_lower or traj_lower in answer_lower:
            return 0.8, "Partial match (one contains the other)"

        # 使用序列相似度计算
        similarity = SequenceMatcher(None, traj_lower, answer_lower).ratio()

        # 将相似度映射到0-1范围，但设置最低阈值
        if similarity >= 0.9:
            return 0.9, f"High similarity ({similarity:.2f})"
        elif similarity >= 0.7:
            return 0.7, f"Good similarity ({similarity:.2f})"
        elif similarity >= 0.5:
            return 0.5, f"Moderate similarity ({similarity:.2f})"
        else:
            final_score = max(0.0, similarity * 0.5)
            return final_score, f"Low similarity ({similarity:.2f})"


def gen_simple_message_reward_function(user_message: str):
    """
    生成一个简单的reward_function，直接将user_message返回

    Args:
        user_message: 要返回的用户消息

    Returns:
        RewardFunction: 一个RewardFunction实例，其__call__方法直接返回user_message
    """
    async def _reward_call(self, context, validation_file_path=None, traj_file_path=None, tmp_file_path=None):
        return RewardResult(
            score=0.0,
            traj_output=str(user_message) if not isinstance(user_message, list) else str(user_message),
            ground_truth="",
            reasoning=f"Simple reward function: directly return user_message: {user_message}"
        )

    return type('SimpleMessageReward', (RewardFunction,), {'__call__': _reward_call})()


gaia_match_reward = GaiaMatchRewardFunction()

