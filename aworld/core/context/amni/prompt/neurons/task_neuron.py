from typing import List

from ... import ApplicationContext
from ..formatter.task_formatter import TaskFormatter
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="task", desc="Task history neuron", prio=1)
class TaskHistoryNeuron(Neuron):
    """Neuron for handling task history, current task information and plan related properties"""

    async def format_current_task_info(self, context: ApplicationContext) -> List[str]:
        """Format current task related information"""
        items = []

        # Task ID
        task_id = context.task_id
        if task_id:
            items.append(f"  <task_id>{task_id}</task_id>")

        # Task input
        task_input = context.task_input
        if task_input:
            items.append(f"  <task_input>{task_input}</task_input>")

        # Task output
        task_output = context.task_output
        if task_output:
            items.append(f"  <task_output>{task_output}</task_output>")


        # Original user input
        origin_user_input = context.origin_user_input
        if origin_user_input:
            items.append(f"  <origin_user_input>{origin_user_input}</origin_user_input>")

        todo_info = await context.get_todo_info()
        if todo_info:
            items.append(" <todo_info description=\"Todo information Help You Tracking the Global Task\">\n" + todo_info + "\n</todo_info>\n")

        return items

    async def format_plan_info(self, context: ApplicationContext) -> List[str]:
        """Format plan information"""
        task_contents = []
        for index, sub_task in enumerate(context.sub_task_list, 1):
            task_content = sub_task.input.task_content.strip()
            if task_content:
                task_contents.append(f"<step{index}>{task_content}</step{index}>")
        return task_contents

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format all task related information"""
        items = []
        
        # Add current task information
        current_task_items = await self.format_current_task_info(context)
        if current_task_items:
            items.extend(current_task_items)
        
        # Add plan information
        plan_items = await self.format_plan_info(context)
        if plan_items:
            items.extend(plan_items)
        
        # Add task history information
        history_items = await TaskFormatter.format_task_history(context)
        if history_items:
            items.extend(history_items)
        
        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine all task related information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        # Format each part separately
        current_task_info = await self.format_current_task_info(context)
        plan_info = await self.format_plan_info(context)
        task_history = await TaskFormatter.format_task_history(context)
        
        result_parts = []
        
        # Current task information
        if current_task_info:
            result_parts.append("<global_task_info description=\"Current task information Help You Understand the Current Task\">\n" + "\n".join(current_task_info) + "\n</global_task_info>")
        
        # Plan information
        if plan_info:
            result_parts.append("<plan_task_list>\n" + "\n".join(plan_info) + "\n</plan_task_list>")
        
        # Task history
        if task_history:
            result_parts.append(f"<task_history>Task Execution Steps:\n {task_history} \n\n</task_history>")
        
        return "\n".join(result_parts) + "\n"
