import json
from typing import AsyncGenerator
import uuid
import logging
from aworld.cmd.data_model import BaseAWorldAgent, ChatCompletionRequest
from aworld.cmd.utils.agent_ui_parser import (
    AWorldWebAgentUI,
    BaseToolResultParser,
    ToolCard,
    ToolResultParserFactory,
)
from aworld.output.artifact import ArtifactType
from aworld.output.base import ToolResultOutput
from aworld.output.ui.base import AworldUI
from aworld.output.workspace import WorkSpace
from aworld.runner import Runners
from .deepresearch_agent import Pipeline

logger = logging.getLogger(__name__)


class DeepResearchSearchToolResultParser(BaseToolResultParser):
    async def parse(self, output: ToolResultOutput, workspace: WorkSpace):
        tool_card = ToolCard.from_tool_result(output)

        query = ""
        try:
            args = json.loads(tool_card.arguments)
            query = args.get("query")
            # aworld search server
            if not query:
                query = args.get("query_list")
        except Exception:
            pass

        result_items = []
        try:
            results = json.loads(tool_card.results)
            result_items = results.get("message", {}).get("results", [])
            # aworld search server return url, not link
            if result_items and isinstance(result_items, list):
                for item in result_items:
                    if not item.get("link", None) and item.get("url", None):
                        item["link"] = item.get("url")
        except Exception:
            pass

        if len(result_items) > 0:
            tool_card.results = ""

        tool_card.card_type = "tool_call_card_link_list"
        tool_card.card_data = {
            "title": "🔎 Gaia Search",
            "query": query,
            "search_items": result_items,
        }

        artifact_id = str(uuid.uuid4())
        await workspace.create_artifact(
            artifact_type=ArtifactType.WEB_PAGES,
            artifact_id=artifact_id,
            content=result_items,
            metadata={
                "query": query,
            },
        )
        tool_card.artifacts.append(
            {
                "artifact_type": ArtifactType.WEB_PAGES.value,
                "artifact_id": artifact_id,
            }
        )

        return f"""
\n\n**🔎 Gaia Search**\n\n
```tool_card
{json.dumps(tool_card.model_dump(), ensure_ascii=False, indent=2)}
```\n
"""


class CustomToolResultParserFactory(ToolResultParserFactory):
    def get_parser(self, tool_type: str, tool_name: str):
        if tool_name in ("search_server", "search"):
            return DeepResearchSearchToolResultParser()
        return super().get_parser(tool_type, tool_name)


class AWorldAgent(BaseAWorldAgent):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.pipeline = Pipeline()

    def name(self):
        return "Deep Research Agent"

    def description(self):
        return "Deep Research Agent"

    async def run(self, prompt: str = None, request: ChatCompletionRequest = None):
        if prompt is None and request is not None:
            prompt = request.messages[-1].content

        swarm = await self.pipeline.build_swarm(request)

        task = await self.pipeline.build_task(swarm, prompt)

        rich_ui = AWorldWebAgentUI(
            session_id=self.session_id,
            workspace=WorkSpace.from_local_storages(workspace_id=self.session_id),
            tool_result_parser_factory=CustomToolResultParserFactory(),
        )
        async for output in Runners.streamed_run_task(task).stream_events():
            logger.info(f"Gaia Agent Ouput: {output}")
            res = await AworldUI.parse_output(output, rich_ui)
            for item in res if isinstance(res, list) else [res]:
                if isinstance(item, AsyncGenerator):
                    async for sub_item in item:
                        if sub_item and str(sub_item).strip():
                            yield sub_item
                else:
                    if item and str(item).strip():
                        yield item
