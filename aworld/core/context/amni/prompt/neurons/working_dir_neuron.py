from typing import List

from amnicontext import ApplicationContext
from . import Neuron


class WorkingDirNeuron(Neuron):
    """处理工作目录相关属性的Neuron"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """格式化工作目录信息"""
        await context.load_working_dir()

        if not context._working_dir or not context._working_dir.inner_attachments:
            return []

        working_dir_context = []
        for i, attachment in enumerate(context._working_dir.inner_attachments, 1):
            item = (
                f" <working_dir_attachment>\n"
                f"  <attachment_id>{i}</attachment_id>\n"
                f"  <filename>{attachment.filename}</filename>\n"
                f"  <file_path>{context.working_dir_root}/{attachment.filename}</file_path>\n"
                f"  <mime_type>{attachment.mime_type}</mime_type>\n"
            )

            # 添加额外的元数据信息（如果存在）
            if hasattr(attachment, 'metadata') and attachment.metadata:
                if 'repository_key' in attachment.metadata:
                    item += f"  <repository_key>{attachment.metadata['repository_key']}</repository_key>\n"
                if 'uploaded_at' in attachment.metadata:
                    item += f"  <uploaded_at>{attachment.metadata['uploaded_at']}</uploaded_at>\n"

            item += f" </working_dir_attachment>\n"
            working_dir_context.append(item)

        return working_dir_context

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """组合工作目录信息"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<working_files_list>\n" + "\n".join(items) + "</working_files_list>\n"
