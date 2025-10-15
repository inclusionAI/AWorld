from typing import List

from ... import ApplicationContext
from ..formatter.task_formatter import TaskFormatter
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="task", desc="Task history neuron", prio=1)
class TaskHistoryNeuron(Neuron):
    """处理任务历史、当前任务信息和计划相关属性的Neuron"""

    async def format_current_task_info(self, context: ApplicationContext) -> List[str]:
        """格式化当前任务相关信息"""
        items = []

        # 任务ID
        task_id = context.task_id
        if task_id:
            items.append(f"  <task_id>{task_id}</task_id>")

        # 任务输入
        task_input = context.task_input
        if task_input:
            items.append(f"  <task_input>{task_input}</task_input>")

        # 任务输出
        task_output = context.task_output
        if task_output:
            items.append(f"  <task_output>{task_output}</task_output>")

        # # 任务状态
        # task_status = context.task_status
        # if task_status:
        #     items.append(f"<task_status>{task_status}</task_status>")

        # # 任务输入对象
        # task_input_object = context.task_input_object
        # if task_input_object:
        #     items.append(f"<task_input_object>{task_input_object}</task_input_object>")
        #
        # # 任务输出对象
        # task_output_object = context.task_output_object
        # if task_output_object:
        #     items.append(f"<task_output_object>{task_output_object}</task_output_object>")

        # 原始用户输入
        origin_user_input = context.origin_user_input
        if origin_user_input:
            items.append(f"  <origin_user_input>{origin_user_input}</origin_user_input>")

        # origin_task_input = context.get("origin_task_input")
        # if origin_task_input:
        #     items.append(f"<origin_task_input>{origin_task_input}</origin_task_input>")

        return items

    async def format_plan_info(self, context: ApplicationContext) -> List[str]:
        """格式化计划信息"""
        task_contents = []
        for index, sub_task in enumerate(context.sub_task_list, 1):
            task_content = sub_task.input.task_content.strip()
            if task_content:
                task_contents.append(f"<step{index}>{task_content}</step{index}>")
        return task_contents

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化所有任务相关信息"""
        items = []
        
        # 添加当前任务信息
        current_task_items = await self.format_current_task_info(context)
        if current_task_items:
            items.extend(current_task_items)
        
        # 添加计划信息
        plan_items = await self.format_plan_info(context)
        if plan_items:
            items.extend(plan_items)
        
        # 添加任务历史信息
        history_items = await TaskFormatter.format_task_history(context)
        if history_items:
            items.extend(history_items)
        
        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合所有任务相关信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        # 分别格式化各个部分
        current_task_info = await self.format_current_task_info(context)
        plan_info = await self.format_plan_info(context)
        task_history = await TaskFormatter.format_task_history(context)
        
        result_parts = []
        
        # 当前任务信息
        if current_task_info:
            result_parts.append("<current_task_info>\n" + "\n".join(current_task_info) + "\n</current_task_info>")
        
        # 计划信息
        if plan_info:
            result_parts.append("<plan_task_list>\n" + "\n".join(plan_info) + "\n</plan_task_list>")
        
        # 任务历史
        if task_history:
            result_parts.append(f"<task_history>Task Execution Steps:\n {task_history} \n\n</task_history>")
        
        return "\n".join(result_parts) + "\n"
