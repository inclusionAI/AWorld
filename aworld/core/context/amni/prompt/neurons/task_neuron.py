import time
from typing import List

from ... import ApplicationContext, logger
from ..formatter.task_formatter import TaskFormatter
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="task", desc="Task history neuron", prio=1)
class TaskHistoryNeuron(Neuron):
    """Neuron for handling task history, current task information and plan related properties"""

    async def format_current_task_info(self, context: ApplicationContext) -> List[str]:
        """Format current task related information"""
        start_time = time.perf_counter()
        
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
        if origin_user_input and origin_user_input != task_input:
            items.append(f"  <origin_user_input>{origin_user_input}</origin_user_input>")

        todo_info = await context.get_todo_info()
        if todo_info:
            items.append(" <todo_info description=\"Todo information Help You Tracking the Global Task\">\n" + todo_info + "\n</todo_info>\n")

        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  TaskHistoryNeuron.format_current_task_info() execution time: {elapsed_time:.4f}s")

        return items

    async def format_plan_info(self, context: ApplicationContext) -> List[str]:
        """Format plan information"""
        start_time = time.perf_counter()
        
        task_contents = []
        for index, sub_task in enumerate(context.sub_task_list, 1):
            task_content = sub_task.input.task_content.strip()
            if task_content:
                task_contents.append(f"<step{index}>{task_content}</step{index}>")
        
        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  TaskHistoryNeuron.format_plan_info() execution time: {elapsed_time:.4f}s")
        
        return task_contents

    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format all task related information"""
        start_time = time.perf_counter()
        
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
        history_start_time = time.perf_counter()
        history_items = await TaskFormatter.format_task_history(context)
        if context.get_config() and context.get_config().debug_mode:
            history_elapsed_time = time.perf_counter() - history_start_time
            logger.info(f"⏱️  TaskFormatter.format_task_history() execution time: {history_elapsed_time:.4f}s")
        
        if history_items:
            items.extend(history_items)
        
        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  TaskHistoryNeuron.format_items() execution time: {elapsed_time:.4f}s")
        
        return items

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine all task related information"""
        start_time = time.perf_counter()
        
        if not items:
            items = await self.format_items(context, namespace, **kwargs)

        result = "\n".join(items) + "\n"
        
        # Log execution time if debug mode is enabled
        if context.get_config() and context.get_config().debug_mode:
            elapsed_time = time.perf_counter() - start_time
            logger.info(f"⏱️  TaskHistoryNeuron.format() execution time: {elapsed_time:.4f}s")
        
        return result
