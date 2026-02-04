"""
AWorld AST Framework - 工具类
============================

提供PageRank计算、缓存管理、文件过滤等工具功能。
"""

from typing import Dict, List, Optional

from aworld.logs.util import logger


class PageRankCalculator:
    """PageRank算法计算器"""

    def __init__(self, damping_factor: float = 0.85, max_iterations: int = 100,
                 tolerance: float = 1e-6):
        self.damping_factor = damping_factor
        self.max_iterations = max_iterations
        self.tolerance = tolerance

    def calculate(self, adjacency_dict: Dict[str, List[str]],
                 personalization: Optional[Dict[str, float]] = None) -> Dict[str, float]:
        """
        计算PageRank分数

        Args:
            adjacency_dict: 邻接字典 {node: [neighbors]}
            personalization: 个性化权重 {node: weight}

        Returns:
            PageRank分数字典
        """
        if not adjacency_dict:
            return {}

        nodes = set(adjacency_dict.keys())
        for neighbors in adjacency_dict.values():
            nodes.update(neighbors)

        nodes = list(nodes)
        n = len(nodes)

        if n == 0:
            return {}

        # 初始化分数
        scores = {node: 1.0 / n for node in nodes}

        # 构建转移矩阵
        transition_matrix = {}
        for node in nodes:
            out_links = adjacency_dict.get(node, [])
            if out_links:
                weight = 1.0 / len(out_links)
                transition_matrix[node] = {link: weight for link in out_links}
            else:
                # 悬挂节点：等概率链接到所有节点
                weight = 1.0 / n
                transition_matrix[node] = {other: weight for other in nodes}

        # 个性化向量
        if personalization:
            total_personalization = sum(personalization.values())
            if total_personalization > 0:
                personalization = {k: v / total_personalization
                                 for k, v in personalization.items()}
        else:
            personalization = {node: 1.0 / n for node in nodes}

        # 迭代计算PageRank
        for iteration in range(self.max_iterations):
            new_scores = {}

            for node in nodes:
                rank = 0.0

                # 来自其他节点的权重传递
                for source, targets in transition_matrix.items():
                    if node in targets:
                        rank += scores[source] * targets[node]

                # 添加阻尼和个性化
                rank = (self.damping_factor * rank +
                       (1 - self.damping_factor) * personalization.get(node, 0))

                new_scores[node] = rank

            # 检查收敛
            diff = sum(abs(new_scores[node] - scores[node]) for node in nodes)
            scores = new_scores

            if diff < self.tolerance:
                logger.debug(f"PageRank收敛于第{iteration + 1}次迭代")
                break
        else:
            logger.warning(f"PageRank在{self.max_iterations}次迭代后未收敛")

        return scores



class TokenCounter:
    """Token计数器（简单实现）"""

    @staticmethod
    def count_tokens(text: str) -> int:
        """简单的token计数（基于单词数量的估算）"""
        # 这是一个简化实现，实际应该使用tiktoken等专业库
        import re
        words = re.findall(r'\b\w+\b', text)
        # 假设平均每个word约为0.75个token
        return int(len(words) * 0.75)

    @staticmethod
    def truncate_to_tokens(text: str, max_tokens: int) -> str:
        """截断文本到指定token数量"""
        if TokenCounter.count_tokens(text) <= max_tokens:
            return text

        lines = text.split('\n')
        result_lines = []
        current_tokens = 0

        for line in lines:
            line_tokens = TokenCounter.count_tokens(line)
            if current_tokens + line_tokens <= max_tokens:
                result_lines.append(line)
                current_tokens += line_tokens
            else:
                break

        return '\n'.join(result_lines)