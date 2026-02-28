# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Knowledge Service - Manages knowledge artifacts and search operations.

This service abstracts knowledge management operations from ApplicationContext,
providing a clean interface for adding, updating, retrieving, and searching knowledge.
"""
import abc
from typing import Optional, List, Dict, Any

from aworld.core.context.amni.retrieval.embeddings import SearchResults
from aworld.core.context.amni.state.common import WorkingState
from aworld.logs.util import logger
from aworld.output import Artifact, ArtifactType


class IKnowledgeService(abc.ABC):
    """Interface for knowledge management operations."""
    
    @abc.abstractmethod
    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index: bool = True) -> None:
        """
        Add a single knowledge artifact.
        
        Args:
            knowledge: The artifact to add
            namespace: Namespace for storage
            index: Whether to build index for the artifact
        """
        pass
    
    @abc.abstractmethod
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index: bool = True) -> None:
        """
        Add multiple knowledge artifacts in batch.
        
        Args:
            knowledge_list: List of artifacts to add
            namespace: Namespace for storage
            build_index: Whether to build index for artifacts
        """
        pass
    
    @abc.abstractmethod
    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        """
        Update an existing knowledge artifact.
        
        Args:
            knowledge: The artifact to update
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """
        Delete a knowledge artifact by ID.
        
        Args:
            knowledge_id: ID of the artifact to delete
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> Optional[Artifact]:
        """
        Get a knowledge artifact by ID.
        
        Args:
            knowledge_id: ID of the artifact
            namespace: Namespace for storage
            
        Returns:
            Artifact if found, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Any]:
        """
        Get a specific chunk from a knowledge artifact.
        
        Args:
            knowledge_id: ID of the artifact
            chunk_index: Index of the chunk
            
        Returns:
            Chunk if found, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def get_knowledge_by_lines(self, knowledge_id: str, start_line: int, end_line: int, namespace: str = "default") -> Optional[str]:
        """
        Get knowledge content by line range.
        
        Args:
            knowledge_id: ID of the artifact
            start_line: Starting line number (1-based, inclusive)
            end_line: Ending line number (1-based, inclusive)
            namespace: Namespace for retrieval
            
        Returns:
            Content string of the specified line range, or None if not found
        """
        pass
    
    @abc.abstractmethod
    async def grep_knowledge(self, knowledge_id: str, pattern: str, ignore_case: bool = False, 
                            context_before: int = 0, context_after: int = 0, max_results: int = 100,
                            namespace: str = "default") -> Optional[str]:
        """
        Search for pattern in knowledge content using grep-like functionality.
        
        Args:
            knowledge_id: ID of the artifact
            pattern: Search pattern (supports regular expressions)
            ignore_case: Whether to perform case-insensitive search
            context_before: Number of lines to show before each match
            context_after: Number of lines to show after each match
            max_results: Maximum number of matching lines to return
            namespace: Namespace for search
            
        Returns:
            Search results string with matching lines and context, or None if not found
        """
        pass
    
    @abc.abstractmethod
    async def list_knowledge_info(self, limit: int = 100, offset: int = 0, namespace: str = "default") -> Optional[List[Dict[str, Any]]]:
        """
        List all knowledge artifacts (actions_info) from workspace.
        
        Args:
            limit: Maximum number of knowledge artifacts to return
            offset: Offset for pagination
            namespace: Namespace for retrieval
            
        Returns:
            List of knowledge artifacts with their IDs and summaries, or None if error
        """
        pass
    
    @abc.abstractmethod
    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter: Dict[str, Any] = None, namespace: str = "default") -> Optional[SearchResults]:
        """
        Search knowledge using semantic search.
        
        Args:
            user_query: Search query string
            top_k: Number of results to return
            search_filter: Additional search filters
            namespace: Namespace for search
            
        Returns:
            SearchResults if workspace is available, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def offload_by_workspace(self, artifacts: List[Artifact], namespace: str = "default", biz_id: str = None) -> str:
        """
        Offload artifacts to workspace (context offloading).
        
        Args:
            artifacts: List of artifacts to offload
            namespace: Namespace for storage
            biz_id: Business ID for grouping artifacts
            
        Returns:
            Offload context string
        """
        pass
    
    @abc.abstractmethod
    async def load_context_by_workspace(self, search_filter: Dict[str, Any] = None, namespace: str = "default", 
                                        top_k: int = 20, load_content: bool = True, load_index: bool = True, 
                                        search_by_index: bool = True) -> str:
        """
        Load knowledge context from workspace.
        
        Args:
            search_filter: Search filter dictionary
            namespace: Namespace for search
            top_k: Number of results to return
            load_content: Whether to load chunk content
            load_index: Whether to load chunk indices
            search_by_index: Whether to search by index
            
        Returns:
            Formatted knowledge context string
        """
        pass
    
    @abc.abstractmethod
    async def build_knowledge_context(self, namespace: str = "default", search_filter: Dict[str, Any] = None, top_k: int = 20) -> str:
        """
        Build knowledge context string.
        
        Args:
            namespace: Namespace for search
            search_filter: Search filter dictionary
            top_k: Number of results to return
            
        Returns:
            Formatted knowledge context string
        """
        pass
    
    @abc.abstractmethod
    async def get_todo_info(self) -> str:
        """
        Get todo information from workspace.
        
        Returns:
            Formatted todo information string, or "Todo is Empty" if no todo found
        """
        pass
    
    @abc.abstractmethod
    async def add_todo(self, todo_content: str, namespace: str = "default") -> None:
        """
        Add or update todo content in workspace.
        
        Args:
            todo_content: The todo content to add or update
            namespace: Namespace for storage
        """
        pass
    
    @abc.abstractmethod
    async def get_todo(self, namespace: str = "default") -> Optional[str]:
        """
        Get todo content from workspace.
        
        Args:
            namespace: Namespace for retrieval
            
        Returns:
            Todo content string if found, None otherwise
        """
        pass
    
    @abc.abstractmethod
    async def get_actions_info(self, namespace: str = "default") -> str:
        """
        Get actions information from workspace.
        
        Args:
            namespace: Namespace for retrieval
            
        Returns:
            Formatted actions information string
        """
        pass
    
    @abc.abstractmethod
    async def add_task_output(self, output_artifact: Artifact, namespace: str = "default", index: bool = True) -> None:
        """
        Add a task output artifact to task state and workspace.
        
        Args:
            output_artifact: The artifact to add as task output
            namespace: Namespace for storage
            index: Whether to build index for the artifact
        """
        pass


class KnowledgeService(IKnowledgeService):
    """
    Knowledge Service implementation.
    
    Manages knowledge artifacts stored in working state and workspace.
    Provides operations for adding, updating, retrieving, and searching knowledge.
    """
    
    def __init__(self, context):
        """
        Initialize KnowledgeService with ApplicationContext.
        
        Args:
            context: ApplicationContext instance that provides workspace and working state access
        """
        self._context = context
    
    def _get_working_state(self, namespace: str = "default") -> Optional[WorkingState]:
        """Get working state for the given namespace."""
        return self._context._get_working_state(namespace)
    
    async def add_knowledge(self, knowledge: Artifact, namespace: str = "default", index: bool = True) -> None:
        """Add a single knowledge artifact."""
        
        
        logger.debug(f"add knowledge #{knowledge.artifact_id} start")
        self._get_working_state(namespace).save_knowledge(knowledge)
        
        # Workspace is optional, only add if initialized
        if self._context._workspace:
            await self._context._workspace.add_artifact(knowledge, index=index)
            logger.info(f"add knowledge to#{knowledge.artifact_id} workspace finished")
        logger.debug(f"add knowledge #{knowledge.artifact_id} finished")
    
    async def add_knowledge_list(self, knowledge_list: List[Artifact], namespace: str = "default", build_index: bool = True) -> None:
        """Add multiple knowledge artifacts in batch."""
        import asyncio
        import time
        
        
        logger.debug(f"add_knowledge_list start")
        
        if knowledge_list:
            logger.debug(f"🧠 Start adding knowledge in batch, total {len(knowledge_list)} items")
            start_time = time.time()
            
            # Batch process all knowledge items concurrently
            await asyncio.gather(*(self.add_knowledge(knowledge, namespace, build_index) for knowledge in knowledge_list))
            elapsed = time.time() - start_time
            logger.info(f"✅ Batch add {len(knowledge_list)} knowledge addition completed, elapsed time: {elapsed:.3f} seconds")
        logger.debug(f"add_knowledge_list end")
    
    async def update_knowledge(self, knowledge: Artifact, namespace: str = "default") -> None:
        """Update an existing knowledge artifact."""
        self._get_working_state(namespace).save_knowledge(knowledge)
        
        # Workspace is optional, only update if initialized
        if self._context._workspace:
            await self._context._workspace.update_artifact(artifact_id=knowledge.artifact_id, content=knowledge.content)
    
    async def delete_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> None:
        """Delete a knowledge artifact by ID."""
        # Implementation depends on workspace and working state
        # This is a placeholder - actual implementation may vary
        working_state = self._get_working_state(namespace)
        if working_state:
            # Remove from working state if there's a method for it
            pass
        
        if self._context._workspace:
            # Delete from workspace if there's a method for it
            pass
    
    async def get_knowledge_by_id(self, knowledge_id: str, namespace: str = "default") -> Optional[Artifact]:
        """Get a knowledge artifact by ID."""
        workspace = await self._context._ensure_workspace()
        return workspace.get_latest_artifact(knowledge_id)
    
    async def get_knowledge_chunk(self, knowledge_id: str, chunk_index: int) -> Optional[Any]:
        """Get a specific chunk from a knowledge artifact.

        Note:
            Chunk-based retrieval is no longer supported. This method is kept for
            backward compatibility and will always return None.
        """
        logger.info(f"get_knowledge_chunk is deprecated, chunk retrieval disabled. "
                    f"knowledge_id={knowledge_id}, chunk_index={chunk_index}")
        return None
    
    async def get_knowledge_by_lines(self, knowledge_id: str, start_line: int, end_line: int, namespace: str = "default") -> Optional[str]:
        """
        Get knowledge content by line range.
        
        Args:
            knowledge_id: ID of the artifact
            start_line: Starting line number (1-based, inclusive)
            end_line: Ending line number (1-based, inclusive)
            namespace: Namespace for retrieval
            
        Returns:
            Content string of the specified line range, or None if not found
        """
        
        
        try:
            workspace = await self._context._ensure_workspace()
            artifact = workspace.get_latest_artifact(knowledge_id)
            
            if not artifact:
                logger.warning(f"⚠️ Knowledge artifact not found: knowledge_id={knowledge_id}")
                return None
            
            # Split content into lines
            content_str = artifact.content if isinstance(artifact.content, str) else str(artifact.content)
            lines = content_str.split('\n')
            total_lines = len(lines)
            
            # Validate line numbers (1-based indexing)
            if start_line < 1 or end_line < 1:
                logger.warning(f"⚠️ Line numbers must be positive. Got start_line={start_line}, end_line={end_line}")
                return None
            
            if start_line > total_lines:
                logger.warning(f"⚠️ start_line ({start_line}) exceeds total lines ({total_lines})")
                return None
            
            if start_line > end_line:
                logger.warning(f"⚠️ start_line ({start_line}) must be less than or equal to end_line ({end_line})")
                return None
            
            # Adjust end_line if it exceeds total lines
            actual_end_line = min(end_line, total_lines)
            
            # Extract lines (convert to 0-based indexing)
            selected_lines = lines[start_line - 1:actual_end_line]
            content = '\n'.join(selected_lines)
            
            logger.info(f"✅ Knowledge lines retrieved: knowledge_id={knowledge_id}, lines={start_line}-{actual_end_line}")
            return f"Lines {start_line}-{actual_end_line} of {total_lines} (knowledge_id: {knowledge_id}):\n\n{content}"
            
        except Exception as e:
            logger.error(f"❌ Error retrieving knowledge by lines: knowledge_id={knowledge_id}, error={str(e)}")
            return None
    
    async def grep_knowledge(self, knowledge_id: str, pattern: str, ignore_case: bool = False, 
                            context_before: int = 0, context_after: int = 0, max_results: int = 100,
                            namespace: str = "default") -> Optional[str]:
        """
        Search for pattern in knowledge content using grep-like functionality.
        
        Args:
            knowledge_id: ID of the artifact
            pattern: Search pattern (supports regular expressions)
            ignore_case: Whether to perform case-insensitive search
            context_before: Number of lines to show before each match
            context_after: Number of lines to show after each match
            max_results: Maximum number of matching lines to return
            namespace: Namespace for search
            
        Returns:
            Search results string with matching lines and context, or None if not found
        """
        import re
        
        
        try:
            workspace = await self._context._ensure_workspace()
            artifact = workspace.get_latest_artifact(knowledge_id)
            
            if not artifact:
                logger.warning(f"⚠️ Knowledge artifact not found: knowledge_id={knowledge_id}")
                return None
            
            # Split content into lines
            content_str = artifact.content if isinstance(artifact.content, str) else str(artifact.content)
            lines = content_str.split('\n')
            total_lines = len(lines)
            
            # Compile regex pattern
            try:
                flags = re.IGNORECASE if ignore_case else 0
                regex = re.compile(pattern, flags)
            except re.error as e:
                logger.warning(f"⚠️ Invalid regex pattern: {str(e)}")
                return None
            
            # Find matching lines
            matches = []
            matched_line_numbers = set()
            
            for line_num, line in enumerate(lines, start=1):
                if regex.search(line):
                    matches.append(line_num)
                    matched_line_numbers.add(line_num)
                    
                    if len(matches) >= max_results:
                        break
            
            if not matches:
                logger.info(f"✅ No matches found for pattern: {pattern}")
                return f"No matches found for pattern '{pattern}' in knowledge#{knowledge_id}"
            
            # Build result with context
            result_lines = []
            lines_to_show = set()
            
            # Collect all lines to show (including context)
            for match_line in matches:
                # Add context before
                for i in range(max(1, match_line - context_before), match_line):
                    lines_to_show.add(i)
                
                # Add matching line
                lines_to_show.add(match_line)
                
                # Add context after
                for i in range(match_line + 1, min(total_lines + 1, match_line + context_after + 1)):
                    lines_to_show.add(i)
            
            # Sort and format output
            sorted_lines = sorted(lines_to_show)
            prev_line = 0
            
            for line_num in sorted_lines:
                # Add separator for gaps
                if prev_line > 0 and line_num > prev_line + 1:
                    result_lines.append("--")
                
                line_content = lines[line_num - 1]
                
                # Mark matching lines with different prefix
                if line_num in matched_line_numbers:
                    prefix = f"{line_num}:"
                    # Highlight matches in the line
                    highlighted_line = regex.sub(lambda m: f"**{m.group(0)}**", line_content)
                    result_lines.append(f"{prefix} {highlighted_line}")
                else:
                    prefix = f"{line_num}-"
                    result_lines.append(f"{prefix} {line_content}")
                
                prev_line = line_num
            
            result_text = "\n".join(result_lines)
            
            # Prepare summary
            summary_text = f"Found {len(matches)} match(es) for pattern '{pattern}' in knowledge#{knowledge_id} ({total_lines} total lines)\n\n{result_text}"
            
            if len(matches) >= max_results:
                summary_text += f"\n\n⚠️ Results limited to {max_results} matches. Use max_results parameter to see more."
            
            logger.info(f"✅ Grep completed: found {len(matches)} matches for pattern '{pattern}'")
            return summary_text
            
        except Exception as e:
            logger.error(f"❌ Error grepping knowledge: knowledge_id={knowledge_id}, pattern={pattern}, error={str(e)}")
            return None
    
    async def list_knowledge_info(self, limit: int = 100, offset: int = 0, namespace: str = "default") -> Optional[List[Dict[str, Any]]]:
        """
        List all knowledge artifacts (actions_info) from workspace.
        
        Args:
            limit: Maximum number of knowledge artifacts to return
            offset: Offset for pagination
            namespace: Namespace for retrieval
            
        Returns:
            List of knowledge artifacts with their IDs and summaries, or None if error
        """
        
        
        try:
            workspace = await self._context._ensure_workspace()
            workspace._load_workspace_data(load_artifact_content=False)
            
            # Query all knowledge artifacts with context_type = "actions_info"
            artifacts = await workspace.query_artifacts(search_filter={
                "context_type": "actions_info"
            })
            
            total_count = len(artifacts)
            logger.info(f"📊 Found {total_count} knowledge artifacts")
            
            if total_count == 0:
                return []
            
            # Apply pagination
            start_idx = offset
            end_idx = min(offset + limit, total_count)
            paginated_artifacts = artifacts[start_idx:end_idx]
            
            # Build result list
            knowledge_list = []
            for idx, artifact in enumerate(paginated_artifacts, start=start_idx + 1):
                knowledge_id = artifact.artifact_id
                summary = artifact.metadata.get('summary', 'No summary available') if hasattr(artifact, 'metadata') and artifact.metadata else 'No summary available'
                task_id = artifact.metadata.get('task_id', 'N/A') if hasattr(artifact, 'metadata') and artifact.metadata else 'N/A'
                
                knowledge_info = {
                    "index": idx,
                    "knowledge_id": knowledge_id,
                    "summary": summary,
                    "task_id": task_id
                }
                knowledge_list.append(knowledge_info)
            
            logger.info(f"✅ Knowledge list retrieved: {len(paginated_artifacts)} items returned")
            return knowledge_list
            
        except Exception as e:
            logger.error(f"❌ Error listing knowledge info: error={str(e)}")
            return None
    
    async def search_knowledge(self, user_query: str, top_k: int = None, search_filter: Dict[str, Any] = None, namespace: str = "default") -> Optional[SearchResults]:
        """Search knowledge using semantic search."""
        if self._context._workspace:
            if not search_filter:
                search_filter = {}
            search_filter = {
                # "type": "knowledge",
                **search_filter
            }
            return await self._context._workspace.search_artifact_chunks(
                user_query=user_query,
                search_filter=search_filter,
                top_k=top_k
            )
        return None
    
    async def offload_by_workspace(self, artifacts: List[Artifact], namespace: str = "default", biz_id: str = None) -> str:
        """Offload artifacts to workspace (context offloading)."""
        import uuid
        
        
        if not artifacts:
            return ""
        
        # 1. add knowledge to workspace (indexing disabled)
        if not biz_id:
            biz_id = str(uuid.uuid4())
        for artifact in artifacts:
            # keep existing metadata and ensure biz_id is set
            artifact.metadata.update({
                "biz_id": biz_id
            })
        await self.add_knowledge_list(artifacts, namespace=namespace, build_index=False)
        
        # Add a strategy: single page should not exceed 40K
        first_content = artifacts[0].content
        if len(artifacts) == 1 and isinstance(first_content, str) and len(first_content) < 40_000:
            logger.info(f"directly return artifacts content: {len(first_content)}")
            return f"{first_content}"
        
        # 2. build lightweight knowledge index without chunk details
        logger.info(f"add artifacts to context: {[artifact.artifact_id for artifact in artifacts]}")
        artifact_context = "This is current tool result, a list of knowledge artifacts:"
        artifact_context += "\n<knowledge_list description='This is a list of knowledge artifacts'>\n"
        
        for artifact in artifacts:
            summary = artifact.summary
            if not summary:
                # fallback: truncate content as summary if it's a string
                if isinstance(artifact.content, str):
                    content_str = artifact.content
                else:
                    content_str = str(artifact.content)

                if len(content_str) > 500:
                    summary = f"{content_str[:500]}... you can use get_knowledge_by_lines(artifact_id, start_line, end_line) or grep_knowledge(artifact_id, pattern, ...) to get more content"
                else:
                    summary = content_str
            
            artifact_context += (
                f"<knowledge id='{artifact.artifact_id}' type='{artifact.artifact_type.name}' desc>\n"
                f"{summary}\n"
                f"</knowledge>\n"
            )
        
        artifact_context += "</knowledge_list>"
        return f"{artifact_context}"
    
    async def load_context_by_workspace(self, search_filter: Dict[str, Any] = None, namespace: str = "default", 
                                        top_k: int = 20, load_content: bool = True, load_index: bool = True, 
                                        search_by_index: bool = True) -> str:
        """Load knowledge context from workspace.

        Note:
            The new implementation no longer relies on chunk-level retrieval.
            It builds a lightweight knowledge index based on artifacts only.
        """
        from aworld.core.context.amni.prompt.prompts import AMNI_CONTEXT_PROMPT

        # Ensure workspace is initialized
        workspace = await self._context._ensure_workspace()

        if not search_filter:
            search_filter = {}

        # Query artifacts directly by metadata filter
        artifacts = await workspace.query_artifacts(search_filter=search_filter)

        knowledge_index_context = ""
        if artifacts:
            lines: list[str] = []
            # only keep top_k artifacts to control context size
            for artifact in artifacts[:top_k]:
                summary = artifact.summary
                if not summary:
                    if isinstance(artifact.content, str):
                        summary = artifact.content[:500]
                    else:
                        summary = str(artifact.content)[:500]

                lines.append(
                    f"<knowledge id='{artifact.artifact_id}' type='{artifact.artifact_type.name}' desc>\n"
                    f"{summary}\n"
                    f"</knowledge>\n"
                )
            knowledge_index_context = "\n".join(lines)

        # No chunk-level context anymore
        knowledge_context = AMNI_CONTEXT_PROMPT["KNOWLEDGE_PART"].format(
            knowledge_index=knowledge_index_context,
            knowledge_chunks=""
        )

        return knowledge_context
    
    async def build_knowledge_context(self, namespace: str = "default", search_filter: Dict[str, Any] = None, top_k: int = 20) -> str:
        """Build knowledge context string."""
        return await self.load_context_by_workspace(search_filter, namespace=namespace, top_k=top_k)
    
    async def get_todo_info(self) -> str:
        """Get todo information from workspace."""
        
        
        workspace = await self._context._ensure_workspace()
        todo_info = (
            "Below is the global task execute todo information, explaining the current progress:\n"
        )
        artifact = workspace.get_latest_artifact(artifact_id=f"session_{self._context.session_id}_todo")
        if not artifact:
            return "Todo is Empty"
        todo_info += f"{artifact.content}"
        return todo_info
    
    async def add_todo(self, todo_content: str, namespace: str = "default") -> None:
        """
        Add or update todo content in workspace.
        
        Args:
            todo_content: The todo content to add or update
            namespace: Namespace for storage
        """
        from aworld.logs.util import logger
        
        workspace = await self._context._ensure_workspace()
        workspace._load_workspace_data()
        
        todo_artifact_id = f"session_{self._context.session_id}_todo"
        existing_artifact = workspace.get_artifact(todo_artifact_id)
        
        if existing_artifact:
            # Update existing todo artifact
            await workspace.update_artifact(artifact_id=todo_artifact_id, content=todo_content)
            logger.info(f"✅ Updated todo artifact: {todo_artifact_id}")
        else:
            # Create new todo artifact
            todo_artifact = Artifact(
                artifact_id=todo_artifact_id,
                artifact_type=ArtifactType.TEXT,
                content=todo_content,
                metadata={
                    "context_type": "todo",
                    "session_id": self._context.session_id,
                    "task_id": self._context.task_id
                }
            )
            await workspace.add_artifact(todo_artifact, index=False)
            logger.info(f"✅ Created todo artifact: {todo_artifact_id}")
    
    async def get_todo(self, namespace: str = "default") -> Optional[str]:
        """
        Get todo content from workspace.
        
        Args:
            namespace: Namespace for retrieval
            
        Returns:
            Todo content string if found, None otherwise
        """

        workspace = await self._context._ensure_workspace()
        
        todo_artifact_id = f"session_{self._context.session_id}_todo"
        artifact = workspace.get_latest_artifact(todo_artifact_id)
        
        if not artifact:
            return None
        
        return artifact.content if isinstance(artifact.content, str) else str(artifact.content)
    
    async def get_actions_info(self, namespace: str = "default") -> str:
        """Get actions information from workspace."""
        workspace = await self._context._ensure_workspace()
        workspace._load_workspace_data(load_artifact_content=False)
        artifacts = await workspace.query_artifacts(search_filter={
            "context_type": "actions_info",
            "task_id": self._context.task_id
        })
        logger.info(f"get_actions_info: {len(artifacts)}")
        actions_info = (
            "\nBelow is the actions information, including both successful and failed experiences, "
            "as well as key knowledge and insights obtained during the process. "
            "\nMake full use of this information:\n"
            "<knowledge_list>"
        )
        for artifact in artifacts:
            actions_info += f"  <knowledge id='{artifact.artifact_id}' summary='{artifact.summary}<'>: </knowledge>\n"
        actions_info += f"\n</knowledge_list>\n\n<tips>\n"
        actions_info += f"you can use get_knowledge(knowledge_id_xxx) to got detail content\n"
        actions_info += f"</tips>\n"
        return actions_info
    
    async def add_task_output(self, output_artifact: Artifact, namespace: str = "default", index: bool = True) -> None:
        """Add a task output artifact to task state and workspace."""
        # Add to task output file index
        self._context.task_state.task_output.add_file(output_artifact.artifact_id, output_artifact.summary)
        
        # Add to workspace if initialized
        if self._context._workspace:
            await self._context._workspace.add_artifact(output_artifact, index=index)

