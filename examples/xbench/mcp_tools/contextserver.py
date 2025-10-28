import logging
import re
import sys
import traceback
import uuid
from typing import Union

from mcp.server import FastMCP
from mcp.types import TextContent

from aworld.core.context.amni.retrieval.graph.factory import graph_db_factory
from aworld.core.context.amni.worksapces import workspace_repo
from aworld.output import Artifact, ArtifactType

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

mcp = FastMCP("amnicontext-server")

@mcp.tool(description="Retrieve knowledge artifact content by knowledge ID from the current session workspace")
async def get_knowledge(
        knowledge_id: str,
        session_id: str = None
) ->Union[str, TextContent]:
    """
    Retrieve the content of a knowledge artifact from the current session workspace.
    
    This tool fetches a knowledge artifact by its ID and returns its content.
    
    Args:
        knowledge_id (str): The unique identifier of the knowledge artifact to retrieve
        session_id (str): The session ID to identify the workspace
        
    Returns:
        str: The content of the knowledge artifact, or an error message if not found
        
    Raises:
        None: Returns error message string instead of raising exceptions
    """
    logger.info(f"🔍 Retrieving knowledge artifact: knowledge_id={knowledge_id}, session_id={session_id}")
    
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        logger.info(f"✅ Workspace retrieved successfully for session: {session_id}")
        
        artifact = workspace.get_artifact(knowledge_id)
        if not artifact:
            logger.warning(f"⚠️ Knowledge artifact not found: knowledge_id={knowledge_id}, session_id={session_id}")
            return f"Not found knowledge#{knowledge_id}"
        
        logger.info(f"✅ Knowledge artifact retrieved successfully: knowledge_id={knowledge_id}, content_length={len(artifact.content)}")
        content = artifact.content
        if len(artifact.content) > 15000:
            content = artifact.content[:15000] + "\n\n, TIPS: content is too long, only return 15000 char's, please use get_knowledge_chunk tool get next content"
        search_output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": content
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=content,
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )
        
    except Exception as e:
        logger.error(f"❌ Error retrieving knowledge artifact: knowledge_id={knowledge_id}, session_id={session_id}, error={str(e)}, trace is {traceback.format_exc()}")
        return f"Error retrieving knowledge artifact: {str(e)}"

# @mcp.tool(description="Retrieve a specific chunk of knowledge artifact by knowledge ID and chunk index")
async def get_knowledge_chunk(
        knowledge_id: str, 
        chunk_index: int,
        session_id = None
) -> Union[str, TextContent]:
    """
    Retrieve a specific chunk of a knowledge artifact from the current session workspace.
    
    This tool fetches a particular chunk of a knowledge artifact by its ID and chunk index.
    Useful for accessing large knowledge artifacts in smaller, manageable pieces.
    
    Args:
        knowledge_id (str): The unique identifier of the knowledge artifact
        chunk_index (int): The index of the specific chunk to retrieve (zero-based)
        session_id (str): The session ID to identify the workspace
        
    Returns:
        str: The content of the specified chunk, or an error message if not found
        
    Raises:
        None: Returns error message string instead of raising exceptions
    """
    logger.info(f"🔍 Retrieving knowledge chunk: knowledge_id={knowledge_id}, chunk_index={chunk_index}, session_id={session_id}")
    
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        logger.info(f"✅ Workspace retrieved successfully for session: {session_id}")
        
        chunk = await workspace.get_artifact_chunk(knowledge_id, chunk_index=chunk_index)
        if not chunk:
            logger.warning(f"⚠️ Knowledge chunk not found: knowledge_id={knowledge_id}, chunk_index={chunk_index}, session_id={session_id}")
            return f"Not found knowledge#{knowledge_id}, chunk_index={chunk_index}"

        logger.info(f"✅ Knowledge chunk retrieved successfully: knowledge_id={knowledge_id}, chunk_index={chunk_index}, content_length={len(chunk.content)} , trace is {traceback.format_exc()}")
        search_output_dict = {
            "artifact_type": "TEXT",
            "artifact_data": chunk.content
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=chunk.content,
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )

    except Exception as e:
        logger.error(f"❌ Error retrieving knowledge chunk: knowledge_id={knowledge_id}, chunk_index={chunk_index}, session_id={session_id}, error={str(e)}")
        return f"Error retrieving knowledge chunk: {str(e)}"

@mcp.tool(description="Retrieve knowledge content by line range from the current session workspace")
async def get_knowledge_by_lines(
        knowledge_id: str,
        start_line: int,
        end_line: int,
        session_id: str = None
) -> Union[str, TextContent]:
    """
    Retrieve specific lines of a knowledge artifact from the current session workspace.
    
    This tool fetches a knowledge artifact by its ID and returns content between specified line numbers.
    Useful for accessing specific portions of large knowledge artifacts.
    
    Args:
        knowledge_id (str): The unique identifier of the knowledge artifact
        start_line (int): The starting line number (1-based, inclusive)
        end_line (int): The ending line number (1-based, inclusive)
        session_id (str): The session ID to identify the workspace
        
    Returns:
        Union[str, TextContent]: The content of the specified line range, or an error message if not found
        
    Raises:
        None: Returns error message string instead of raising exceptions
        
    Example:
        get_knowledge_by_lines(knowledge_id="knowledge_123", start_line=10, end_line=20, session_id="session_456")
    """
    logger.info(f"🔍 Retrieving knowledge by lines: knowledge_id={knowledge_id}, start_line={start_line}, end_line={end_line}, session_id={session_id}")
    
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        logger.info(f"✅ Workspace retrieved successfully for session: {session_id}")
        
        artifact = workspace.get_artifact(knowledge_id)
        if not artifact:
            logger.warning(f"⚠️ Knowledge artifact not found: knowledge_id={knowledge_id}, session_id={session_id}")
            return f"Not found knowledge#{knowledge_id}"
        
        # Split content into lines
        lines = artifact.content.split('\n')
        total_lines = len(lines)
        
        # Validate line numbers (1-based indexing)
        if start_line < 1 or end_line < 1:
            error_msg = f"Line numbers must be positive. Got start_line={start_line}, end_line={end_line}"
            logger.warning(f"⚠️ {error_msg}")
            return error_msg
        
        if start_line > total_lines:
            error_msg = f"start_line ({start_line}) exceeds total lines ({total_lines})"
            logger.warning(f"⚠️ {error_msg}")
            return error_msg
        
        if start_line > end_line:
            error_msg = f"start_line ({start_line}) must be less than or equal to end_line ({end_line})"
            logger.warning(f"⚠️ {error_msg}")
            return error_msg
        
        # Adjust end_line if it exceeds total lines
        actual_end_line = min(end_line, total_lines)
        
        # Extract lines (convert to 0-based indexing)
        selected_lines = lines[start_line - 1:actual_end_line]
        content = '\n'.join(selected_lines)
        
        logger.info(f"✅ Knowledge lines retrieved successfully: knowledge_id={knowledge_id}, lines={start_line}-{actual_end_line}, content_length={len(content)}")
        
        search_output_dict = {
            "artifact_type": "TEXT",
            "artifact_data": content,
            "total_lines": total_lines,
            "start_line": start_line,
            "end_line": actual_end_line,
            "requested_end_line": end_line
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=f"Lines {start_line}-{actual_end_line} of {total_lines} (knowledge_id: {knowledge_id}):\n\n{content}",
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )
        
    except Exception as e:
        logger.error(f"❌ Error retrieving knowledge by lines: knowledge_id={knowledge_id}, start_line={start_line}, end_line={end_line}, session_id={session_id}, error={str(e)}, trace={traceback.format_exc()}")
        return f"Error retrieving knowledge by lines: {str(e)}"


@mcp.tool(description="Search for pattern in knowledge content using grep-like functionality")
async def grep_knowledge(
        knowledge_id: str,
        pattern: str,
        ignore_case: bool = False,
        context_before: int = 0,
        context_after: int = 0,
        max_results: int = 100,
        session_id: str = None
) -> Union[str, TextContent]:
    """
    Search for a pattern in knowledge artifact content using grep-like functionality.
    
    This tool searches for a pattern (supports regular expressions) in a knowledge artifact
    and returns matching lines with optional context lines.
    
    Args:
        knowledge_id (str): The unique identifier of the knowledge artifact
        pattern (str): The search pattern (supports regular expressions)
        ignore_case (bool): Whether to perform case-insensitive search (default: False)
        context_before (int): Number of lines to show before each match (like grep -B, default: 0)
        context_after (int): Number of lines to show after each match (like grep -A, default: 0)
        max_results (int): Maximum number of matching lines to return (default: 100)
        session_id (str): The session ID to identify the workspace
        
    Returns:
        Union[str, TextContent]: Search results with matching lines and context, or error message
        
    Raises:
        None: Returns error message string instead of raising exceptions
        
    Example:
        grep_knowledge(knowledge_id="doc_123", pattern="error", ignore_case=True, context_after=2)
    """
    logger.info(f"🔍 Grepping knowledge: knowledge_id={knowledge_id}, pattern={pattern}, ignore_case={ignore_case}, session_id={session_id}")
    
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        logger.info(f"✅ Workspace retrieved successfully for session: {session_id}")
        
        artifact = workspace.get_artifact(knowledge_id)
        if not artifact:
            logger.warning(f"⚠️ Knowledge artifact not found: knowledge_id={knowledge_id}, session_id={session_id}")
            return f"Not found knowledge#{knowledge_id}"
        
        # Split content into lines
        lines = artifact.content.split('\n')
        total_lines = len(lines)
        
        # Compile regex pattern
        try:
            flags = re.IGNORECASE if ignore_case else 0
            regex = re.compile(pattern, flags)
        except re.error as e:
            error_msg = f"Invalid regex pattern: {str(e)}"
            logger.warning(f"⚠️ {error_msg}")
            return error_msg
        
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
            return TextContent(
                type="text",
                text=f"No matches found for pattern '{pattern}' in knowledge#{knowledge_id}",
                **{"metadata": {
                    "artifact_type": "TEXT",
                    "matches_found": 0,
                    "pattern": pattern,
                    "total_lines": total_lines
                }}
            )
        
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
        
        search_output_dict = {
            "artifact_type": "TEXT",
            "artifact_data": result_text,
            "matches_found": len(matches),
            "pattern": pattern,
            "total_lines": total_lines,
            "ignore_case": ignore_case,
            "context_before": context_before,
            "context_after": context_after,
            "matched_lines": matches
        }
        
        return TextContent(
            type="text",
            text=summary_text,
            **{"metadata": search_output_dict}
        )
        
    except Exception as e:
        logger.error(f"❌ Error grepping knowledge: knowledge_id={knowledge_id}, pattern={pattern}, error={str(e)}, trace={traceback.format_exc()}")
        return f"Error grepping knowledge: {str(e)}"


@mcp.tool(description="List all knowledge artifacts from the current workspace")
async def list_knowledge_info(
        limit: int = 100,
        offset: int = 0,
        session_id: str = None,
) -> Union[str, TextContent]:
    """
    List all knowledge artifacts (actions_info) from the current session workspace.
    
    This tool retrieves all knowledge artifacts that contain important information,
    including successful and failed experiences, as well as key knowledge and insights.
    
    Args:
        session_id (str): The session ID to identify the workspace
        limit (int): Maximum number of knowledge artifacts to return (default: 100)
        offset (int): Offset for pagination (default: 0)
        
    Returns:
        Union[str, TextContent]: List of knowledge artifacts with their IDs and summaries, or error message
        
    Raises:
        None: Returns error message string instead of raising exceptions
        
    Example:
        list_knowledge_info(session_id="session_456", limit=50)
    """
    logger.info(f"📋 Listing knowledge info: session_id={session_id}, limit={limit}, offset={offset}")
    
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        logger.info(f"✅ Workspace retrieved successfully for session: {session_id}")
        
        # Load workspace data
        workspace._load_workspace_data()
        
        # Query all knowledge artifacts with context_type = "actions_info"
        artifacts = await workspace.query_artifacts(search_filter={
            "context_type": "actions_info"
        })
        
        total_count = len(artifacts)
        logger.info(f"📊 Found {total_count} knowledge artifacts")
        
        if total_count == 0:
            return TextContent(
                type="text",
                text="No knowledge artifacts found in the workspace.",
                **{"metadata": {
                    "artifact_type": "TEXT",
                    "total_count": 0,
                    "returned_count": 0
                }}
            )
        
        # Apply pagination
        start_idx = offset
        end_idx = min(offset + limit, total_count)
        paginated_artifacts = artifacts[start_idx:end_idx]
        
        # Build result
        result_lines = []
        result_lines.append(f"📚 Knowledge Artifacts List (Total: {total_count}, Showing: {start_idx + 1}-{end_idx})\n")
        result_lines.append("=" * 80)
        result_lines.append("")
        
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
            
            result_lines.append(f"{idx}. 📝 Knowledge ID: {knowledge_id}")
            result_lines.append(f"   📄 Summary: {summary}")
            if task_id != 'N/A':
                result_lines.append(f"   🔖 Task ID: {task_id}")
            result_lines.append("")
        
        result_lines.append("=" * 80)
        result_lines.append("\n💡 Tips:")
        result_lines.append("   • Use get_knowledge(knowledge_id) to retrieve full content")
        result_lines.append("   • Use grep_knowledge(knowledge_id, pattern) to search within knowledge")
        result_lines.append("   • Use get_knowledge_by_lines(knowledge_id, start_line, end_line) to get specific lines")
        
        # Add pagination info
        if total_count > limit:
            result_lines.append(f"\n📄 Pagination: Showing {start_idx + 1}-{end_idx} of {total_count}")
            if end_idx < total_count:
                result_lines.append(f"   ⏭️  Use offset={end_idx} to see the next {min(limit, total_count - end_idx)} items")
        
        result_text = "\n".join(result_lines)
        
        logger.info(f"✅ Knowledge list retrieved successfully: {len(paginated_artifacts)} items returned")
        
        search_output_dict = {
            "artifact_type": "TEXT",
            "artifact_data": result_text,
            "knowledge_list": knowledge_list,
            "total_count": total_count,
            "returned_count": len(paginated_artifacts),
            "limit": limit,
            "offset": offset,
            "has_more": end_idx < total_count
        }
        
        return TextContent(
            type="text",
            text=result_text,
            **{"metadata": search_output_dict}
        )
        
    except Exception as e:
        logger.error(f"❌ Error listing knowledge info: session_id={session_id}, error={str(e)}, trace={traceback.format_exc()}")
        return f"Error listing knowledge info: {str(e)}"


@mcp.tool(description="add important information to the current workspace, you can use tool get_knowledge to got content ")
async def add_knowledge(knowledge_content: str, content_summary:str, session_id: str = None, task_id: str = None) -> Union[str, TextContent]:
    """
    Add an knowledge artifact to the current session workspace.

    This tool adds an knowledge artifact to the current session workspace.
    Useful for adding new knowledge to the workspace.

    Args:
        knowledge_content (str): The content of the knowledge artifact
        content_summary (str): The summary of the knowledge artifact
        session_id (str, optional): The session ID to identify the workspace. Defaults to None.

    Returns:
        str: A message indicating the success and knownledge artifact ID or failure of the operation
    """
    logger.info(f"🔍 Adding artifact: artifact_content={knowledge_content},session_id={session_id}")
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        artifact = Artifact(artifact_id=f"actions_info_task_id{str(uuid.uuid4())}", artifact_type=ArtifactType.TEXT, content=knowledge_content, metadata={
            "context_type": "actions_info",
            "task_id": task_id,
            "summary": content_summary
        })
        await workspace.add_artifact(artifact, index=False)
        search_output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": knowledge_content
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=f"add knowledge success\n knowledge_id: {artifact.artifact_id}\n content_summary: {content_summary}\n you can use tool get_knowledge({artifact.artifact_id}) to got content",
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )
    except Exception as e:
        logger.error(f"❌ Error adding artifact, artifact_content={knowledge_content},session_id={session_id}, error={str(e)}, trace is {traceback.format_exc()}")
        return f"Error adding artifact: {str(e)}"


@mcp.tool(description="update existing knowledge if the knowledge is not comprehensive, you can use the tool to update the knowledge")
async def update_knowledge(knowledge_id: str, knowledge_content: str, content_summary:str, session_id: str = None, task_id: str = None) -> Union[str, TextContent]:
    """
    Update an existing knowledge artifact to the current session workspace.

    This tool updates an existing knowledge artifact to the current session workspace.
    Useful for updating existing knowledge to the workspace.

    Args:
        knowledge_id (str): The id of the knowledge artifact
        knowledge_content (str): The content of the knowledge artifact
        content_summary (str): The summary of the knowledge artifact
        session_id (str, optional): The session ID to identify the workspace. Defaults to None.

    Returns:
        str: A message indicating the success and knownledge artifact ID or failure of the operation
    """
    logger.info(f"🔍 Updating artifact: knowledge_id={knowledge_id}, artifact_content={knowledge_content},session_id={session_id}")
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)


        await workspace.update_artifact(artifact_id=knowledge_id, content=knowledge_content)
        search_output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": knowledge_content
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=f"update knowledge success\n knowledge_id: {knowledge_id}\n content_summary: {content_summary}\n you can use tool get_knowledge({knowledge_content}) to got content",
            **{"metadata": search_output_dict}
        )
    except Exception as e:
        logger.error(f"❌ Error updating artifact, knowledge_id={knowledge_id}, artifact_content={knowledge_content},session_id={session_id}, error={str(e)}, trace is {traceback.format_exc()}")
        return f"Error updating artifact: {str(e)}"


@mcp.tool(description="Add a todo to the current session workspace. if exist, update the todo.")
async def add_todo(todo_content: str, session_id: str = None) -> Union[str, TextContent]:
    """
    Add a todo to the current session workspace. if exist, update the todo.
    <examples>
    task: search for a list of football players who scored 16 goals in the 2024-25 season

    todo content is
    ```markdown
    [] get 2024-25 season football scores data
    [] get player from previous step data and get player's goal data
    [] filter player who scored 16 goals
    [] output the result
    ```

    <examples>

    todo_content: is a markdown content, you can use markdown format to describe the todo.
    session_id: is the session id of the current session.
    task_id: is the task id of the current task.
    """
    logger.info(f"🔍 Adding todo: todo_content={todo_content},session_id={session_id}")
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        todo = Artifact(artifact_id=f"session_{session_id}_todo", artifact_type=ArtifactType.TEXT, content=todo_content, metadata={
            "context_type": "todo_info",
            "session_id": session_id
        })
        await workspace.add_artifact(todo, index=False)
        search_output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": todo_content
        }

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=f"add todo success\n todo_id: {todo.artifact_id}\n you can use tool get_todo({todo.artifact_id}) to got content",
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )
    except Exception as e:
        logger.error(f"❌ Error adding todo, todo_content={todo_content},session_id={session_id}, error={str(e)}, trace is {traceback.format_exc()}")
        return f"Error adding todo: {str(e)}"

@mcp.tool(description="Get a todo from the current session workspace.")
async def get_todo(session_id: str = None) -> Union[str, TextContent]:
    """
    Get a todo from the current session workspace.
    """
    logger.info(f"🔍 Getting todo: session_id={session_id}")
    try:
        workspace = await workspace_repo.get_session_workspace(session_id=session_id)
        todo = workspace.get_artifact(f"session_{session_id}_todo")
        if not todo:
            logger.info(f"⚠️ Todo not found: session_id={session_id}")
            return f"todo is empty"
        search_output_dict = {
            "artifact_type": "MARKDOWN",
            "artifact_data": todo.content
        }

        logger.info(f"✅ Todo retrieved successfully:content={todo.content}")

        # Initialize TextContent with additional parameters
        return TextContent(
            type="text",
            text=todo.content,
            **{"metadata": search_output_dict}  # Pass processed data as metadata
        )
    except Exception as e:
        logger.error(f"❌ Error getting todo, session_id={session_id}, error={str(e)}, trace is {traceback.format_exc()}")
        return f"Error getting todo: {str(e)}"


def main():
    from dotenv import load_dotenv

    load_dotenv(override=True)

    logger.info("🚀 Starting MCP amnicontext-server...")
    mcp.run(transport="stdio")


# Make the module callable
def __call__():
    """
    Make the module callable for uvx.
    This function is called when the module is executed directly.
    """
    main()


sys.modules[__name__].__call__ = __call__

if __name__ == "__main__":
    main()