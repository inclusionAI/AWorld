"""
任务格式化工具模块

这个模块包含了任务历史格式化的相关函数，用于避免循环依赖问题。
"""

from ...state import TaskOutput


class TaskFormatter:
    """任务格式化工具类"""
    
    @staticmethod
    async def format_task_history(context) -> str:
        """
        格式化任务历史信息
        
        Args:
            context: ApplicationContext 实例
            
        Returns:
            str: 格式化后的任务历史字符串
        """
        items = []
        sub_tasks = context.task_state.working_state.sub_task_list
        if not sub_tasks:
            return ""

        # Filter out tasks with INIT status
        active_tasks = {sub_task.task_id: sub_task for sub_task in sub_tasks if sub_task.status != 'INIT'}

        if not active_tasks:
            return ""

        for step_number, (task_id, sub_task) in enumerate(active_tasks.items(), 1):
            # Get task result description
            if isinstance(sub_task.result, TaskOutput):
                result_desc = str(sub_task.result.result) if sub_task.result is not None else "No result yet"

                result_files = []
                for file_id, file_summary in sub_task.result.file_index().items():
                    file = context._workspace.get_artifact(file_id)
                    if file:
                        file_content = file.content
                    else:
                        file_content = ""
                    result_files.append({
                        "file_id": file_id,
                        "file_summary": file_summary,
                        "file_content": file_content
                    })

            else:
                result_desc = str(sub_task.result) if sub_task.result is not None else "No result yet"
                result_files = []

            items.append(f"""
    <step_{step_number}>:
        <sub_step_id>{task_id}</sub_step_id>
        <sub_step_goal>{sub_task.input.task_content.strip()}</sub_step_goal>
        <sub_step_status>{sub_task.status}</sub_step_status>
        <sub_step_result>
          <content>{result_desc}</content>
          <actions_info>{sub_task.result.actions_info}</actions_info>
          <todo_plan>{sub_task.result.todo_info}</todo_plan>
          <files>{result_files}</files>
        </sub_step_result>
    </step_{step_number}>
    """)
        
        return items
