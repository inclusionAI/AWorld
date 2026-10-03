"""LocalSandbox coding tools and cross-session queries, without an LLM service."""

import asyncio
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld.core.agent import Agent, Skill
from aworld.core.agent.messages import AssistantMessage, ToolCall, ToolResultMessage
from aworld.core.sandbox import LocalSandbox
from aworld.core.session import InMemorySessionStore, create_session
from aworld.core.tool import default_tools


class CodingModel:
    async def complete(self, request):
        if isinstance(request.messages[-1], ToolResultMessage):
            return AssistantMessage("Local file created, read, and checked with bash.")
        return AssistantMessage(tool_calls=(
            ToolCall("write-note", "write", {"path": "notes/design.md", "content": "Context owns history.\n"}),
            ToolCall("read-note", "read", {"path": "notes/design.md"}),
            ToolCall("check-note", "bash", {"command": "wc -l notes/design.md"}),
        ))


class ReaderModel:
    async def complete(self, request):
        last = request.messages[-1]
        if not isinstance(last, ToolResultMessage):
            return AssistantMessage(tool_calls=(ToolCall("find-session", "session_query", {
                "query": {"metadata": {"project": "coding-demo"}, "text": "Context"}
            }),))
        if last.name == "session_query":
            source_id = last.content["matches"][0]["session_id"]
            return AssistantMessage(tool_calls=(ToolCall("read-session", "read_session", {"session_id": source_id}),))
        return AssistantMessage(f"Read {last.content['total_entries']} history entries from another session.")


async def main():
    store = InMemorySessionStore()
    with tempfile.TemporaryDirectory(prefix="aworld-tools-demo-") as directory:
        tools = default_tools(sandbox=LocalSandbox(directory))
        writer = await create_session(agent=Agent(model=CodingModel(), tools=tools, skills=[
            Skill("notes", "Write and verify design notes", "Read files after writing them.")
        ]), store=store, metadata={"project": "coding-demo"})
        reader = await create_session(agent=Agent(model=ReaderModel(), tools=tools), store=store)
        try:
            for session, input in ((writer, "Write a Context design note"), (reader, "Find and read that session")):
                result = await (await session.submit(input)).result()
                if result.error:
                    raise RuntimeError(result.error.message)
                print(result.output)
            print((Path(directory) / "notes/design.md").read_text().strip())
        finally:
            await writer.close()
            await reader.close()


if __name__ == "__main__":
    asyncio.run(main())
