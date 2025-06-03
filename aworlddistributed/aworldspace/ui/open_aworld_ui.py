import json
import logging
from dataclasses import dataclass
from typing import Any

from aworld.output import MessageOutput, WorkSpace, AworldUI, get_observer, Artifact, Output
from aworld.output.base import StepOutput, ToolResultOutput
from aworld.output.storage.artifact_repository import CommonEncoder
from aworld.output.utils import consume_content
from socketio import AsyncServer

from aworldspace.ui.ui_template import tool_call_template


class OpenWebuiUIBase(AworldUI):
    metadata: dict
    event_emitter: Any
    sio: AsyncServer

    def __init__(self, metadata: dict, event_emitter: Any, sio: AsyncServer, **kwargs):
        """
        Initialize OpenWebuiUIBase
        """
        super().__init__(**kwargs)
        self.metadata = metadata
        self.event_emitter = event_emitter
        self.sio = sio  

    # open_webui.socket.main.get_event_emitter
    async def emit_line(self):
        await self.event_emitter(
            {"type": "message", "data": {"content": "\n\n----\n\n"}}
        )

    async def emit_message(self, message: str):
        await self.event_emitter(
            {"type": "message", "data": {"content": message}}
        )

    async def emit_replace(self, message: str):
        await self.event_emitter(
            {"type": "replace", "data": {"content": message}}
        )

    async def emit_status(self, level: str, message: str, done: bool):
        await self.event_emitter(
            {
                "type": "status",
                "data": {
                    "status": "complete" if done else "in_progress",
                    "level": level,
                    "description": message,
                    "done": done,
                },
            }
        )

    def get_event_call(self, request_info):
        async def __event_call_print__(data):
            logging.info(data)

        async def __event_caller__(event_data):
            if not request_info or not "session_id" in request_info:
                return None
            response = await self.sio.emit(
                "artifacts-events",
                {
                    "chat_id": request_info.get("chat_id", None),
                    "message_id": request_info.get("message_id", None),
                    "data": event_data,
                },
                to=request_info["session_id"],
            )
            return response

        if not self.sio:
            return __event_call_print__
        return __event_caller__

    def register_observer(self, workspace_id):
        observer = get_observer()
        observer.register_create_handler(
            self.send_artifact_msg_ws, instance=self, workspace_id=workspace_id
        )

    def unregister_observer(self, workspace_id):
        """
        TODO
        :param workspace_id:
        :return:
        """
        observer = get_observer()
        observer.un_register_create_handler(
            self.send_artifact_msg_ws, workspace_id=workspace_id
        )
        logging.info(f"unregister_observer #{workspace_id}")

    async def send_artifact_msg_ws(self, artifact: Artifact):
        print(
            f"send_artifact_msg_ws start: artifact = {artifact.artifact_id}:{artifact.to_dict()}"
        )

        if not self.metadata:
            print(f"send_artifact_msg_ws failed; metadata is none")

        event_caller = self.get_event_call(self.metadata)

        # Start processing chat completion in background
        res = await event_caller(
            {
                "type": "artifacts:create",
                "data": json.dumps(artifact, ensure_ascii=False, cls=CommonEncoder),
            }
        )
        print(f"send_artifact_msg_ws success: result = {res}")


@dataclass
class OpenAworldUI(AworldUI):
    chat_id: str
    workspace: WorkSpace = None

    def __init__(self, chat_id: str = None,
                 workspace: WorkSpace = None,
                 **kwargs):
        """
        Initialize OpenWebuiAworldUI
        Args:
            chat_id: chat identifier
            workspace: workspace instance
        """
        super().__init__(**kwargs)
        self.chat_id = chat_id
        self.workspace = workspace


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
        tool_data = tool_call_template.format(
            tool_type = output.tool_type,
            tool_name = output.tool_name,
            function_name= output.origin_tool_call.function.name,
            function_arguments=await self.json_parse(output.origin_tool_call.function.arguments),
            function_result=await self.parse_tool_output(output.data)
        )

        return tool_data

    async def parse_tool_output(self, tool_result):
        def _wrap_line(line: str) -> str:
            line = line.replace("<think>", "<_think_>")
            line = line.replace("</think>", "<_think_/>")
            return f"{line}\n"
        if isinstance(tool_result, str):
            tool_result = await self.json_parse(tool_result)
            tool_result = tool_result.replace("```", "``")
            tool_result = _wrap_line(tool_result)
            return tool_result
        return tool_result

    async def json_parse(self, json_str):
        try:
            function_result = json.dumps(json.loads(json_str), indent=2,
                                         ensure_ascii=False)
        except Exception:
            function_result = json_str
        return function_result

    async def step(self, output: StepOutput):
        emptyLine = "\n\n----\n\n"
        if output.status == "START":
            # await self.emit_message(step_loading_template.format(data = output.name))
            return f"{output.name} 🛫START \n\n"
        elif output.status == "FINISHED":
            return f"{output.name} 🛬FINISHED {emptyLine}"
        elif output.status == "FAILED":
            return f"{output.name} 💥FAILED: reason is {output.data} {emptyLine}"
        else:
            return f"{output.name} ❓❓❓UNKNOWN#{output.status} {emptyLine}"

    async def custom_output(self, output: Output):
        return output.data







