from typing import List

from ... import ApplicationContext
from . import Neuron
from .neuron_factory import neuron_factory


@neuron_factory.register(name="working_dir", desc="Working directory neuron", prio=8)
class WorkingDirNeuron(Neuron):
    """Neuron for handling working directory related properties"""
    
    async def format_items(self, context: ApplicationContext, namespace: str = None, **kwargs) -> List[str]:
        """Format working directory information"""
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

            # Add additional metadata information (if exists)
            if hasattr(attachment, 'metadata') and attachment.metadata:
                if 'repository_key' in attachment.metadata:
                    item += f"  <repository_key>{attachment.metadata['repository_key']}</repository_key>\n"
                if 'uploaded_at' in attachment.metadata:
                    item += f"  <uploaded_at>{attachment.metadata['uploaded_at']}</uploaded_at>\n"

            item += f" </working_dir_attachment>\n"
            working_dir_context.append(item)

        return working_dir_context

    async def format(self, context: ApplicationContext, items: List[str] = None, namespace: str = None, **kwargs) -> str:
        """Combine working directory information"""
        if not items:
            items = await self.format_items(context, namespace, **kwargs)
        
        return "<working_files_list>\n" + "\n".join(items) + "</working_files_list>\n"
