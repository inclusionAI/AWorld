"""Run the new Agent + Context + Tool loop without a provider or event bus."""

import argparse
import asyncio
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from aworld.core.agent import Agent
from aworld.core.agent.messages import AssistantMessage, ToolCall, ToolResultMessage, UserMessage
from aworld.core.context import Context
from aworld.core.session import InMemorySessionStore, create_session, load_session
from aworld.core.tool import Tool


class CalculatorModel:
    """A deterministic local model boundary for an executable loop demonstration."""

    async def complete(self, request):
        tail = request.messages[-1]
        if isinstance(tail, ToolResultMessage):
            if tail.is_error:
                return AssistantMessage(f"Tool failed: {tail.content}")
            return AssistantMessage(str(tail.content))
        if not isinstance(tail, UserMessage):
            raise ValueError("Expected a user message or tool result")
        parts = tail.content.split()
        if parts[0] == "add" and len(parts) == 3:
            operands = [int(part) for part in parts[1:]]
        elif parts[0] == "continue" and len(parts) == 2:
            previous = [m.content for m in request.messages if isinstance(m, ToolResultMessage) and not m.is_error]
            if not previous:
                raise ValueError("No previous result")
            operands = [previous[-1], int(parts[1])]
        else:
            raise ValueError("Expected 'add N N' or 'continue N'")
        return AssistantMessage(tool_calls=(ToolCall(
            f"add-{len(request.messages)}", "add", {"operands": operands},
        ),))


async def add(arguments, context):
    await asyncio.sleep(0)
    return sum(arguments["operands"])


def calculator_agent():
    return Agent(model=CalculatorModel(), tools=[Tool(
        "add", "Add integer operands", {"type": "object", "properties": {
            "operands": {"type": "array", "items": {"type": "integer"}},
        }, "required": ["operands"]}, add,
    )])


async def main(context_dir=None):
    storage = None
    if context_dir:
        from aworld.core.context.storage import MemoryStoreAdapter
        from aworld.memory.db import FileSystemMemoryStore
        storage = MemoryStoreAdapter(FileSystemMemoryStore(memory_root=context_dir))
    store = InMemorySessionStore()
    session = await create_session(agent=calculator_agent(), context=Context(storage=storage), store=store)
    for input in ("add 2 3", "continue 4"):
        run = await session.submit(input)
        events = [event.type async for event in run.events()]
        result = await run.result()
        print(f"{input} -> {result.output} ({result.status.value})")
        print("events:", ", ".join(events))
        session = await load_session(session.id, store=store)
    print("context history entries:", len(session.context.history()))
    if context_dir:
        from aworld.core.context.storage import MemoryStoreAdapter
        from aworld.memory.db import FileSystemMemoryStore
        reopened = MemoryStoreAdapter(FileSystemMemoryStore(memory_root=context_dir))
        print("persisted context entries:", len(reopened.read(session.id)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context-dir", help="Persist context history in this directory")
    asyncio.run(main(parser.parse_args().context_dir))
