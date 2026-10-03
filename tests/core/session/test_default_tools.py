"""Exercise real temporary files/processes and injected physical carriers."""

import asyncio
import os
from pathlib import Path
import shlex
import tempfile
import unittest

from aworld.core.agent import Agent, Skill
from aworld.core.agent.messages import AssistantMessage, ToolCall
from aworld.core.sandbox import LocalSandbox
from aworld.core.sandbox.local import MAX_BYTES
from aworld.core.session import RunStatus, create_session
from aworld.core.tool import Tool, ToolExecutionError, ToolRegistry, default_tools


class Model:
    def __init__(self, responses=()):
        self.responses = iter(responses)
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return next(self.responses)


class DefaultToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sandbox = LocalSandbox(self.root)
        self.registry = ToolRegistry(default_tools(sandbox=self.sandbox))

    async def test_read_write_nested_path_overwrite_empty_and_pagination(self):
        await self.registry.execute("write", {"path": "nested/a.txt", "content": "一\n二\n三\n"}, None)
        first = await self.registry.execute("read", {"path": "nested/a.txt", "limit": 2}, None)
        self.assertEqual(first["content"], "一\n二\n")
        self.assertTrue(first["truncated"])
        self.assertEqual(first["next_offset"], 3)
        last = await self.registry.execute("read", {"path": "nested/a.txt", "offset": 3}, None)
        self.assertEqual(last["content"], "三\n")
        self.assertIsNone(last["next_offset"])
        await self.registry.execute("write", {"path": "nested/a.txt", "content": ""}, None)
        self.assertEqual((self.root / "nested/a.txt").read_text(), "")
        self.assertFalse(list((self.root / "nested").glob(".aworld-write-*")))

    async def test_read_caps_large_line_and_rejects_binary_fifo_and_bad_offsets(self):
        (self.root / "big").write_text("中" * MAX_BYTES)
        value = await self.registry.execute("read", {"path": "big"}, None)
        self.assertLessEqual(len(value["content"].encode()), MAX_BYTES)
        self.assertTrue(value["line_truncated"])
        self.assertEqual(value["next_offset"], 1)
        (self.root / "binary").write_bytes(b"a\0b")
        os.mkfifo(self.root / "fifo")
        for path in ("binary", "fifo"):
            with self.assertRaises(ValueError):
                await asyncio.wait_for(self.registry.execute("read", {"path": path}, None), 1)
        for offset in (0, True, -1):
            with self.assertRaises(ValueError):
                await self.registry.execute("read", {"path": "big", "offset": offset}, None)
        with self.assertRaises(ValueError):
            await self.registry.execute("read", {"path": "binary", "offset": 3}, None)

    async def test_bash_cwd_stdout_stderr_and_structured_failure(self):
        result = await self.registry.execute("bash", {"command": "pwd; printf out; printf err >&2"}, None)
        self.assertIn(str(self.root), result["output"])
        self.assertIn("outerr", result["output"])
        self.assertEqual(result["exit_code"], 0)
        with self.assertRaises(ToolExecutionError) as raised:
            await self.registry.execute("bash", {"command": "printf failure; exit 7"}, None)
        self.assertEqual(raised.exception.result["exit_code"], 7)
        self.assertEqual(raised.exception.result["output"], "failure")

    async def test_bash_output_tail_has_complete_log(self):
        result = await self.registry.execute("bash", {"command": "for ((i=0;i<3000;i++)); do echo line-$i; done"}, None)
        log = Path(result["full_output_path"])
        self.addCleanup(log.unlink, missing_ok=True)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["output"].splitlines()), 2000)
        self.assertIn("line-2999", result["output"])
        self.assertIn("line-0\n", log.read_text())

    async def test_timeout_covers_process_that_closed_its_output(self):
        result = await asyncio.wait_for(self.sandbox.bash("exec 1>&- 2>&-; sleep 20", .05), 2)
        self.assertTrue(result["timed_out"])
        self.assertNotEqual(result["exit_code"], 0)

    async def test_cancel_run_stops_shell_and_background_descendant(self):
        model = Model([AssistantMessage(tool_calls=(ToolCall("shell", "bash", {
            "command": "echo $$ > shell.pid; (sleep 1; echo leaked > leak) & wait"
        }),))])
        session = await create_session(agent=Agent(model=model, tools=self.registry))
        run = await session.submit("start")
        async def ready():
            while not (self.root / "shell.pid").exists():
                await asyncio.sleep(.01)
        await asyncio.wait_for(ready(), 2)
        pid = int((self.root / "shell.pid").read_text())
        await run.cancel()
        self.assertEqual((await asyncio.wait_for(run.result(), 2)).status, RunStatus.CANCELLED)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)
        await asyncio.sleep(1.05)
        self.assertFalse((self.root / "leak").exists())
        await session.close()

    async def test_default_capabilities_custom_disable_and_skill_binding(self):
        async def marker(arguments, context):
            return "ok"
        skill = Skill("coding", "Follow coding rules", "Use small edits.", tools=(Tool("marker", "Mark", {}, marker),))
        default = Model([AssistantMessage("done")])
        session = await create_session(agent=Agent(model=default, skills=[skill]))
        await (await session.submit("hello")).result()
        self.assertEqual([tool.name for tool in default.requests[0].tools], ["read", "write", "bash", "read_session", "search_sessions", "session_query", "marker"])
        self.assertIn("Use small edits.", default.requests[0].system_prompt)
        empty = Model([AssistantMessage("done")])
        session = await create_session(agent=Agent(model=empty, tools=[]))
        await (await session.submit("hello")).result()
        self.assertEqual(empty.requests[0].tools, ())
        with self.assertRaises(ValueError):
            Agent(model=Model(), tools=self.registry, skills=[Skill("duplicate", "dup", tools=tuple(self.registry))])

    async def test_sandbox_injection_and_registry_selection(self):
        class Carrier:
            async def read(self, path, offset, limit):
                return [path, offset, limit]
            async def write(self, path, content):
                return "remote write"
            async def bash(self, command, timeout):
                return {"output": "remote", "exit_code": 0}
        registry = ToolRegistry(default_tools(sandbox=Carrier())).select("read", "bash")
        self.assertEqual(registry.names, ("read", "bash"))
        self.assertEqual(await registry.execute("read", {"path": "a"}, None), ["a", 1, 2000])
        with self.assertRaises(LookupError):
            registry.get("write")
        with self.assertRaises(ValueError):
            registry.extend([registry.get("read")])
        detached = registry.schemas()
        detached[0].parameters["changed"] = True
        self.assertNotIn("changed", registry.schemas()[0].parameters)

    async def test_bash_failed_result_remains_visible_to_model(self):
        model = Model([AssistantMessage(tool_calls=(ToolCall("fail", "bash", {"command": "echo details; exit 2"}),)), AssistantMessage("repaired")])
        session = await create_session(agent=Agent(model=model, tools=self.registry))
        self.assertEqual((await (await session.submit("repair")).result()).output, "repaired")
        result = model.requests[1].messages[-1]
        self.assertTrue(result.is_error)
        self.assertEqual(result.content["exit_code"], 2)
        self.assertIn("details", result.content["output"])


if __name__ == "__main__":
    unittest.main()
