from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from inspect import isawaitable
from typing import Any

from aworld.runner import Runners

from aworld_gateway.channels.dingding.types import DingdingBridgeResult


class AworldDingdingBridge:
    def __init__(self, registry_cls: Any = None, executor_cls: Any = None) -> None:
        if registry_cls is None:
            from aworld_cli.core.agent_registry import LocalAgentRegistry

            registry_cls = LocalAgentRegistry
        if executor_cls is None:
            from aworld_cli.executors.local import LocalAgentExecutor

            executor_cls = LocalAgentExecutor

        self._registry_cls = registry_cls
        self._executor_cls = executor_cls

    async def run(
        self,
        agent_id: str,
        session_id: str,
        text: str,
        on_text_chunk: Callable[[str], Any] | None = None,
    ) -> DingdingBridgeResult:
        agent = self._registry_cls.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        swarm = await agent.get_swarm(None)
        executor = self._executor_cls(
            swarm=swarm,
            context_config=getattr(agent, "context_config", None),
            session_id=session_id,
            hooks=getattr(agent, "hooks", None),
        )

        try:
            chunks: list[str] = []
            async for chunk in self._stream_text(
                executor=executor,
                text=text,
                session_id=session_id,
            ):
                chunks.append(chunk)
                if on_text_chunk is not None:
                    callback_result = on_text_chunk(chunk)
                    if isawaitable(callback_result):
                        await callback_result
            return DingdingBridgeResult(text="".join(chunks).strip())
        finally:
            cleanup = getattr(executor, "cleanup_resources", None)
            if cleanup is not None:
                cleanup_result = cleanup()
                if isawaitable(cleanup_result):
                    await cleanup_result

    async def _stream_text(
        self,
        *,
        executor: Any,
        text: str,
        session_id: str,
    ) -> AsyncIterator[str]:
        task = await executor._build_task(text, session_id=session_id)
        outputs = Runners.streamed_run_task(task)

        async for output in outputs.stream_events():
            chunk = self._extract_text(output)
            if chunk:
                yield chunk

    @staticmethod
    def _extract_text(output: Any) -> str:
        content = getattr(output, "content", None)
        if isinstance(content, str):
            return content

        payload = getattr(output, "payload", None)
        if isinstance(payload, str):
            return payload

        data = getattr(output, "data", None)
        if isinstance(data, str):
            return data
        if data is not None:
            data_content = getattr(data, "content", None)
            if isinstance(data_content, str):
                return data_content

        source = getattr(output, "source", None)
        if source is not None:
            source_content = getattr(source, "content", None)
            if isinstance(source_content, str):
                return source_content

        return ""
