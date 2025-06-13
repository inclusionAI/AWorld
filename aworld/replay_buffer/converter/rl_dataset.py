from typing import List, Any
from aworld.replay_buffer.base import DataRow, Converter
from aworld.logs.util import logger
from collections import defaultdict
import json

class RLDatasetConverter(Converter):
    """
    RLDatasetConverter is a converter that converts task experience to RL dataset row.
    """
    def to_dataset_row(self, task_experience: List[DataRow]) -> List[Any]:
        """根据trace结构将task_experience转为RL dataset row
        Args:
            task_experience: list[DataRow]
        Returns:
            list[Any]
        """
        datas = []
        if not task_experience:
            return datas
        # 构建span_id到row的映射，以及parent_id到children的映射
        # 找到所有step节点（span_name以'step_'开头）
        span_id2row = {}
        parent2children = defaultdict(list)
        step_nodes = []
        step_ids = set()
        for row in task_experience:
            ext_info = getattr(row, 'ext_info', {})
            span_id = ext_info.get('span_id')
            parent_id = ext_info.get('parent_id')
            if span_id is not None:
                span_id2row[span_id] = row
            if parent_id is not None:
                parent2children[parent_id].append(row)
            span_name = ext_info.get('span_name', '')
            if span_name.startswith('step_'):
                row_id = getattr(row, 'id', None)
                if row_id is not None and row_id not in step_ids:
                    step_nodes.append(row)
                    step_ids.add(row_id)

        def collect_descendants(row):
            """递归收集所有子孙节点"""
            ext_info = getattr(row, 'ext_info', {})
            span_id = ext_info.get('span_id')
            descendants = []
            for child in parent2children.get(span_id, []):
                descendants.append(child)
                descendants.extend(collect_descendants(child))
            return descendants

        task_group = {}
        for step_node in step_nodes:
            # 收集所有子孙节点
            descendants = collect_descendants(step_node)
            # 找AGENT节点
            agent_node = next((r for r in descendants if getattr(r, 'ext_info', {}).get('run_type') == 'AGENT'), None)
            # 找LLM节点
            llm_node = next((r for r in descendants if getattr(r, 'ext_info', {}).get('run_type') == 'LLM'), None)
            if not agent_node:
                continue  # 必须有agent节点
            exp_data = agent_node.exp_data
            # 组装Experience
            new_exp = type(exp_data)(
                state=exp_data.state,
                actions=exp_data.actions,
                reward_t=exp_data.reward_t,
                adv_t=exp_data.adv_t,
                v_t=exp_data.v_t,
                messages=llm_node.exp_data.messages if llm_node else exp_data.messages
            )
            # 组装DataRow
            new_row = DataRow(
                exp_meta=step_node.exp_meta,
                exp_data=new_exp
            )
            task_id = step_node.exp_meta.task_id
            task_group.setdefault(task_id, []).append(new_row)

        for task_id, rows in task_group.items():
            rows.sort(key=lambda x: x.exp_meta.execute_time)
            datas.append(rows)

        return datas