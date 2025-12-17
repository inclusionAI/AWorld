# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Any, Dict, Tuple, Optional

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation
from aworld.output import Artifact, ArtifactType

CONTEXT_KNOWLEDGE = "KNOWLEDGE"


class ContextKnowledgeAction(ToolAction):
    """Agent Knowledge Support. Definition of Context knowledge operations."""

    GET_KNOWLEDGE_BY_ID = ToolActionInfo(
        name="get_knowledge_by_id",
        input_params={
            "knowledge_id": ParamInfo(
                name="knowledge_id",
                type="string",
                required=True,
                desc="The unique identifier of the knowledge artifact to retrieve"
            )
        },
        desc="Retrieve knowledge artifact content by knowledge ID from the current session workspace"
    )

    GET_KNOWLEDGE_BY_LINES = ToolActionInfo(
        name="get_knowledge_by_lines",
        input_params={
            "knowledge_id": ParamInfo(
                name="knowledge_id",
                type="string",
                required=True,
                desc="The unique identifier of the knowledge artifact"
            ),
            "start_line": ParamInfo(
                name="start_line",
                type="integer",
                required=True,
                desc="The starting line number (1-based, inclusive)"
            ),
            "end_line": ParamInfo(
                name="end_line",
                type="integer",
                required=True,
                desc="The ending line number (1-based, inclusive)"
            )
        },
        desc="Retrieve specific lines of a knowledge artifact. Useful for accessing specific portions of large knowledge artifacts."
    )

    GREP_KNOWLEDGE = ToolActionInfo(
        name="grep_knowledge",
        input_params={
            "knowledge_id": ParamInfo(
                name="knowledge_id",
                type="string",
                required=True,
                desc="The unique identifier of the knowledge artifact"
            ),
            "pattern": ParamInfo(
                name="pattern",
                type="string",
                required=True,
                desc="The search pattern (supports regular expressions)"
            ),
            "ignore_case": ParamInfo(
                name="ignore_case",
                type="boolean",
                required=False,
                desc="Whether to perform case-insensitive search (default: False)"
            ),
            "context_before": ParamInfo(
                name="context_before",
                type="integer",
                required=False,
                desc="Number of lines to show before each match (default: 0)"
            ),
            "context_after": ParamInfo(
                name="context_after",
                type="integer",
                required=False,
                desc="Number of lines to show after each match (default: 0)"
            ),
            "max_results": ParamInfo(
                name="max_results",
                type="integer",
                required=False,
                desc="Maximum number of matching lines to return (default: 100)"
            )
        },
        desc="Search for pattern in knowledge content using grep-like functionality. Supports regular expressions and context lines."
    )

    LIST_KNOWLEDGE_INFO = ToolActionInfo(
        name="list_knowledge_info",
        input_params={
            "limit": ParamInfo(
                name="limit",
                type="integer",
                required=False,
                desc="Maximum number of knowledge artifacts to return (default: 100)"
            ),
            "offset": ParamInfo(
                name="offset",
                type="integer",
                required=False,
                desc="Offset for pagination (default: 0)"
            )
        },
        desc="List all knowledge artifacts (actions_info) from the current workspace with their IDs and summaries"
    )

    ADD_KNOWLEDGE = ToolActionInfo(
        name="add_knowledge",
        input_params={
            "knowledge_content": ParamInfo(
                name="knowledge_content",
                type="string",
                required=True,
                desc="The content of the knowledge artifact"
            ),
            "content_summary": ParamInfo(
                name="content_summary",
                type="string",
                required=True,
                desc="The summary of the knowledge artifact"
            )
        },
        desc="Add a knowledge artifact to the current session workspace. Useful for saving important information."
    )

    UPDATE_KNOWLEDGE = ToolActionInfo(
        name="update_knowledge",
        input_params={
            "knowledge_id": ParamInfo(
                name="knowledge_id",
                type="string",
                required=True,
                desc="The ID of the knowledge artifact to update"
            ),
            "knowledge_content": ParamInfo(
                name="knowledge_content",
                type="string",
                required=True,
                desc="The updated content of the knowledge artifact"
            ),
            "content_summary": ParamInfo(
                name="content_summary",
                type="string",
                required=True,
                desc="The updated summary of the knowledge artifact"
            )
        },
        desc="Update an existing knowledge artifact in the current session workspace"
    )

    SEARCH_KNOWLEDGE = ToolActionInfo(
        name="search_knowledge",
        input_params={
            "user_query": ParamInfo(
                name="user_query",
                type="string",
                required=True,
                desc="The search query string for semantic search"
            ),
            "top_k": ParamInfo(
                name="top_k",
                type="integer",
                required=False,
                desc="Number of results to return (default: None, uses default)"
            )
        },
        desc="Search knowledge using semantic search. Returns relevant knowledge artifacts based on the query."
    )

    GET_KNOWLEDGE_CHUNK = ToolActionInfo(
        name="get_knowledge_chunk",
        input_params={
            "knowledge_id": ParamInfo(
                name="knowledge_id",
                type="string",
                required=True,
                desc="The unique identifier of the knowledge artifact"
            ),
            "chunk_index": ParamInfo(
                name="chunk_index",
                type="integer",
                required=True,
                desc="The index of the specific chunk to retrieve (zero-based)"
            )
        },
        desc="Get a specific chunk from a knowledge artifact. Useful for accessing large knowledge artifacts in smaller pieces."
    )


@ToolFactory.register(name=CONTEXT_KNOWLEDGE,
                      desc=CONTEXT_KNOWLEDGE,
                      supported_action=ContextKnowledgeAction)
class ContextKnowledgeTool(AsyncTool):
    """Tool for managing knowledge operations in context."""
    
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextKnowledgeTool, self).__init__(conf, **kwargs)
        self.cur_observation = None
        self.content = None
        self.keyframes = []
        self.init()
        self.step_finished = True

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)
        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=ContextKnowledgeAction.GET_KNOWLEDGE_BY_ID.value.name), {}

    def init(self) -> None:
        """Initialize the tool."""
        self.initialized = True

    async def close(self) -> None:
        """Close the tool."""
        pass

    async def finished(self) -> bool:
        """Check if the tool step is finished."""
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        """
        Execute knowledge actions.
        
        Supported actions:
        - get_knowledge_by_id: Get knowledge by ID
        - get_knowledge_by_lines: Get knowledge by line range
        - grep_knowledge: Search pattern in knowledge
        - list_knowledge_info: List all knowledge artifacts
        - add_knowledge: Add new knowledge artifact
        - update_knowledge: Update existing knowledge artifact
        - search_knowledge: Semantic search knowledge
        - get_knowledge_chunk: Get specific chunk from knowledge
        """
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ContextKnowledgeAction.GET_KNOWLEDGE_BY_ID.value.name)
        info = {}
        
        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            for action in actions:
                logger.info(f"CONTEXTKnowledgeTool|do_step: {action}")
                action_name = action.action_name
                namespace = action.agent_name if hasattr(action, 'agent_name') else "default"
                
                if action_name == ContextKnowledgeAction.GET_KNOWLEDGE_BY_ID.value.name:
                    knowledge_id = action.params.get("knowledge_id", "")
                    if not knowledge_id:
                        raise ValueError("knowledge_id is required")
                    
                    artifact = await message.context.knowledge_service.get_knowledge_by_id(knowledge_id, namespace)
                    if not artifact:
                        result = f"❌ Knowledge artifact not found: {knowledge_id}"
                    else:
                        content = artifact.content if isinstance(artifact.content, str) else str(artifact.content)
                        # Truncate if too long
                        if len(content) > 15000:
                            content = content[:15000] + "\n\n⚠️ Content is too long, only showing first 15000 characters. Use get_knowledge_by_lines or get_knowledge_chunk for more."
                        result = f"📚 Knowledge #{knowledge_id}:\n\n{content}"
                    
                elif action_name == ContextKnowledgeAction.GET_KNOWLEDGE_BY_LINES.value.name:
                    knowledge_id = action.params.get("knowledge_id", "")
                    start_line = action.params.get("start_line")
                    end_line = action.params.get("end_line")
                    
                    if not knowledge_id or start_line is None or end_line is None:
                        raise ValueError("knowledge_id, start_line, and end_line are required")
                    
                    content = await message.context.knowledge_service.get_knowledge_by_lines(
                        knowledge_id, start_line, end_line, namespace
                    )
                    if content is None:
                        result = f"❌ Failed to retrieve lines {start_line}-{end_line} from knowledge #{knowledge_id}"
                    else:
                        result = content
                    
                elif action_name == ContextKnowledgeAction.GREP_KNOWLEDGE.value.name:
                    knowledge_id = action.params.get("knowledge_id", "")
                    pattern = action.params.get("pattern", "")
                    ignore_case = action.params.get("ignore_case", False)
                    context_before = action.params.get("context_before", 0)
                    context_after = action.params.get("context_after", 0)
                    max_results = action.params.get("max_results", 100)
                    
                    if not knowledge_id or not pattern:
                        raise ValueError("knowledge_id and pattern are required")
                    
                    content = await message.context.knowledge_service.grep_knowledge(
                        knowledge_id, pattern, ignore_case, context_before, context_after, max_results, namespace
                    )
                    if content is None:
                        result = f"❌ Failed to grep pattern '{pattern}' in knowledge #{knowledge_id}"
                    else:
                        result = content
                    
                elif action_name == ContextKnowledgeAction.LIST_KNOWLEDGE_INFO.value.name:
                    limit = action.params.get("limit", 100)
                    offset = action.params.get("offset", 0)
                    
                    knowledge_list = await message.context.knowledge_service.list_knowledge_info(limit, offset, namespace)
                    if knowledge_list is None:
                        result = "❌ Failed to list knowledge info"
                    elif len(knowledge_list) == 0:
                        result = "📋 No knowledge artifacts found in the workspace."
                    else:
                        result_lines = [f"📚 Knowledge Artifacts List (Total: {len(knowledge_list)})\n"]
                        result_lines.append("=" * 80)
                        result_lines.append("")
                        for item in knowledge_list:
                            result_lines.append(f"{item['index']}. 📝 Knowledge ID: {item['knowledge_id']}")
                            result_lines.append(f"   📄 Summary: {item['summary']}")
                            if item.get('task_id') and item['task_id'] != 'N/A':
                                result_lines.append(f"   🔖 Task ID: {item['task_id']}")
                            result_lines.append("")
                        result_lines.append("=" * 80)
                        result_lines.append("\n💡 Tips:")
                        result_lines.append("   • Use get_knowledge_by_id(knowledge_id) to retrieve full content")
                        result_lines.append("   • Use grep_knowledge(knowledge_id, pattern) to search within knowledge")
                        result_lines.append("   • Use get_knowledge_by_lines(knowledge_id, start_line, end_line) to get specific lines")
                        result = "\n".join(result_lines)
                    
                elif action_name == ContextKnowledgeAction.ADD_KNOWLEDGE.value.name:
                    knowledge_content = action.params.get("knowledge_content", "")
                    content_summary = action.params.get("content_summary", "")
                    
                    if not knowledge_content or not content_summary:
                        raise ValueError("knowledge_content and content_summary are required")
                    
                    import uuid
                    artifact = Artifact(
                        artifact_id=f"actions_info_{str(uuid.uuid4())}",
                        artifact_type=ArtifactType.TEXT,
                        content=knowledge_content,
                        metadata={
                            "context_type": "actions_info",
                            "task_id": getattr(message.context, 'task_id', None),
                            "summary": content_summary
                        }
                    )
                    await message.context.knowledge_service.add_knowledge(artifact, namespace, index=False)
                    result = f"✅ Knowledge added successfully\n📝 Knowledge ID: {artifact.artifact_id}\n📄 Summary: {content_summary}\n💡 Use get_knowledge_by_id({artifact.artifact_id}) to retrieve content"
                    
                elif action_name == ContextKnowledgeAction.UPDATE_KNOWLEDGE.value.name:
                    knowledge_id = action.params.get("knowledge_id", "")
                    knowledge_content = action.params.get("knowledge_content", "")
                    content_summary = action.params.get("content_summary", "")
                    
                    if not knowledge_id or not knowledge_content or not content_summary:
                        raise ValueError("knowledge_id, knowledge_content, and content_summary are required")
                    
                    # Get existing artifact to preserve metadata
                    existing_artifact = await message.context.knowledge_service.get_knowledge_by_id(knowledge_id, namespace)
                    if not existing_artifact:
                        raise ValueError(f"Knowledge artifact not found: {knowledge_id}")
                    
                    # Update content and summary
                    existing_artifact.content = knowledge_content
                    existing_artifact.metadata['summary'] = content_summary
                    
                    await message.context.knowledge_service.update_knowledge(existing_artifact, namespace)
                    result = f"✅ Knowledge updated successfully\n📝 Knowledge ID: {knowledge_id}\n📄 Summary: {content_summary}"
                    
                elif action_name == ContextKnowledgeAction.SEARCH_KNOWLEDGE.value.name:
                    user_query = action.params.get("user_query", "")
                    top_k = action.params.get("top_k")
                    
                    if not user_query:
                        raise ValueError("user_query is required")
                    
                    search_results = await message.context.knowledge_service.search_knowledge(
                        user_query, top_k, None, namespace
                    )
                    if not search_results:
                        result = f"🔍 No knowledge found for query: '{user_query}'"
                    else:
                        result_lines = [f"🔍 Search Results for: '{user_query}'\n"]
                        result_lines.append("=" * 80)
                        result_lines.append("")
                        for idx, doc in enumerate(search_results.docs[:top_k or 10], start=1):
                            result_lines.append(f"{idx}. 📝 Knowledge ID: {doc.id}")
                            result_lines.append(f"   📊 Relevance Score: {doc.score:.3f}")
                            content_preview = doc.content[:200] + "..." if len(doc.content) > 200 else doc.content
                            result_lines.append(f"   📄 Content Preview: {content_preview}")
                            result_lines.append("")
                        result_lines.append("=" * 80)
                        result_lines.append(f"\n💡 Use get_knowledge_by_id(knowledge_id) to retrieve full content")
                        result = "\n".join(result_lines)
                    
                elif action_name == ContextKnowledgeAction.GET_KNOWLEDGE_CHUNK.value.name:
                    knowledge_id = action.params.get("knowledge_id", "")
                    chunk_index = action.params.get("chunk_index")
                    
                    if not knowledge_id or chunk_index is None:
                        raise ValueError("knowledge_id and chunk_index are required")
                    
                    chunk = await message.context.knowledge_service.get_knowledge_chunk(knowledge_id, chunk_index)
                    if not chunk:
                        result = f"❌ Chunk not found: knowledge_id={knowledge_id}, chunk_index={chunk_index}"
                    else:
                        result = f"📄 Chunk #{chunk_index} from Knowledge #{knowledge_id}:\n\n{chunk.content}"
                    
                else:
                    raise ValueError(f"Unknown action: {action_name}")

                observation.content = result
                observation.action_result.append(
                    ActionResult(is_done=True,
                                 success=True,
                                 content=f"{result}",
                                 keep=False))
            reward = 1.
            
        except Exception as e:
            fail_error = str(e)
            logger.warning(f"CONTEXTKnowledgeTool|failed do_step: {traceback.format_exc()}")
            observation.content = f"❌ Error: {fail_error}"
            observation.action_result.append(
                ActionResult(is_done=True,
                             success=False,
                             content=f"Error: {fail_error}",
                             keep=False))
        finally:
            self.step_finished = True
            
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

