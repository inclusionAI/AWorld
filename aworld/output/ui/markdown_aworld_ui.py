import json
import uuid
from dataclasses import dataclass

from pydantic import Field

from aworld.output import (
    MessageOutput,
    AworldUI,
    Output,
    Artifact,
    ArtifactType,
    WorkSpace,
    SearchOutput,
)
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.ui.template import tool_card_template
from aworld.output.utils import consume_content


@dataclass
class MarkdownAworldUI(AworldUI):

    session_id: str = Field(default="", description="session id")
    workspace: WorkSpace = Field(default=None, description="workspace")
    cur_agent_name: str = Field(default=None, description="cur agent name")

    def __init__(self, session_id: str = None, task_id: str = None, workspace: WorkSpace = None, **kwargs):
        """
        Initialize MarkdownAworldUI
        Args:"""
        super().__init__(**kwargs)
        self.session_id = session_id
        self.workspace = workspace
        self.task_id = task_id

    async def message_output(self, __output__: MessageOutput):
        """
        Returns an async generator that yields each message item.
        """
        # Sentinel object for queue completion
        _SENTINEL = object()

        async def async_generator():
            async def __log_item(item):
                await queue.put(item)

            from asyncio import Queue
            queue = Queue()

            async def consume_all():
                # Consume all relevant generators
                if __output__.reason_generator or __output__.response_generator:
                    if __output__.reason_generator:
                        await consume_content(__output__.reason_generator, __log_item)
                    if __output__.response_generator:
                        await consume_content(__output__.response_generator, __log_item)
                else:
                    await consume_content(__output__.reasoning, __log_item)
                    await consume_content(__output__.response, __log_item)
                # Only after all are done, put the sentinel
                await queue.put(_SENTINEL)

            # Start the consumer in the background
            import asyncio

            consumer_task = asyncio.create_task(consume_all())

            while True:
                item = await queue.get()
                if item is _SENTINEL:
                    break
                yield item
            await consumer_task  # Ensure background task is finished

        return async_generator()

    async def tool_result(self, output: ToolResultOutput):
        """
        tool_result
        """
        output = await self.pre_process_tool_output(output)

        custom_md_output = await self.gen_custom_output(output)

        artifacts = await self.parse_tool_artifacts(output.metadata)

        tool_card_content = {
            "type": "mcp",
            "custom_output": custom_md_output,
            "tool_name": output.tool_name,
            "function_name": output.origin_tool_call.function.name,
            "function_arguments": output.origin_tool_call.function.arguments,
            "artifacts": artifacts,
        }
        tool_data = tool_card_template.format(
            tool_card_content=json.dumps(tool_card_content, indent=2)
        )

        return tool_data

    async def gen_custom_output(self, output):
        """
        hook for custom output
        """
        custom_output = f"{output.tool_name}#{output.origin_tool_call.function.name}"
        if output.tool_name == "aworld-playwright" and output.origin_tool_call.function.name == "browser_navigate":
            custom_output = f"🔍 search `{json.loads(output.origin_tool_call.function.arguments)['url']}`"
        if output.tool_name == "aworldsearch-server" and output.origin_tool_call.function.name == "search":
            custom_output = f"🔍 search keywords: {' '.join(json.loads(output.origin_tool_call.function.arguments)['query_list'])}"
        return custom_output

    async def json_parse(self, json_str):
        try:
            function_result = json.dumps(
                json.loads(json_str), indent=2, ensure_ascii=False
            )
        except Exception:
            function_result = json_str
        return function_result

    async def step(self, output: StepOutput):
        emptyLine = "\n\n----\n\n"
        if output.status == "START":
            if self.cur_agent_name == output.name:
                return f"{emptyLine}"
            self.cur_agent_name = output.name
            return f"\n\n🤖 {output.show_name}: \n\n"
        elif output.status == "FINISHED":
            return f"{emptyLine}"
        elif output.status == "FAILED":
            return f"\n\n{output.name} 💥FAILED: reason is {output.data} {emptyLine}"
        else:
            return f"\n\n{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    async def custom_output(self, output: Output):
        return output.data

    async def parse_tool_artifacts(self, metadata):
        result = []
        if not metadata:
           return result

        # screenshots
        if metadata.get('screenshots') and isinstance(metadata.get('screenshots'), list) and len(
                metadata.get('screenshots')) > 0:
            for index, screenshot in enumerate(metadata.get('screenshots')):
                image_artifact = Artifact(artifact_id=str(uuid.uuid4()), artifact_type=ArtifactType.IMAGE,
                                          content=screenshot.get('ossPath'))
                await self.workspace.add_artifact(image_artifact)
                result.append({
                    "artifact_type": "IMAGE",
                    "artifact_id": image_artifact.artifact_id
                })

        # web_pages
        elif metadata.get("artifact_type") in ["WEB_PAGES"]  :
            data_dict = metadata.get("artifact_data")
            data_dict['task_id'] = self.task_id
            search_output = SearchOutput.from_dict(data_dict)
            artifact_id = str(uuid.uuid4())
            await self.workspace.create_artifact(
                artifact_type=ArtifactType.WEB_PAGES,
                artifact_id=artifact_id,
                content=search_output,
                metadata={
                    "query": search_output.query,
                }
            )
            result.append({
                "artifact_type": metadata.get("artifact_type"),
                "artifact_id": artifact_id
            })
        elif metadata.get("artifact_type") in ["MARKDOWN", "TEXT"]  :
            artifact_id = str(uuid.uuid4())
            await self.workspace.create_artifact(
                artifact_type=metadata.get("artifact_type"),
                artifact_id=artifact_id,
                content=metadata.get("artifact_data"),
                metadata={
                }
            )
            result.append({
                "artifact_type": metadata.get("artifact_type"),
                "artifact_id": artifact_id
            })

        return result

    async def pre_process_tool_output(self, output):
        return output
