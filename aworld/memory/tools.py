"""
Memory tools for agent use.
Provides memory_search and memory_get tools.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class MemorySearchTool:
    """Tool for searching memory."""

    def __init__(self, manager):
        """Initialize tool with memory manager."""
        self.manager = manager

    async def search(
        self,
        query: str,
        max_results: int = 6,
        min_score: float = 0.35,
    ) -> List[Dict[str, Any]]:
        """
        Search memory for relevant information.
        
        Args:
            query: Search query
            max_results: Maximum number of results to return
            min_score: Minimum similarity score (0-1)
        
        Returns:
            List of search results with path, text, score, and line numbers
        """
        try:
            results = await self.manager.search(
                query=query,
                max_results=max_results,
                min_score=min_score,
            )
            return results
        except Exception as e:
            logger.error(f"Memory search failed: {e}", exc_info=True)
            return []

    def get_tool_definition(self) -> Dict[str, Any]:
        """Get tool definition for agent."""
        return {
            "name": "memory_search",
            "description": "Search memory files (MEMORY.md and memory/*.md) for relevant information about prior work, decisions, preferences, or context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query describing what you're looking for",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of results to return (default: 6)",
                        "default": 6,
                    },
                    "min_score": {
                        "type": "number",
                        "description": "Minimum similarity score 0-1 (default: 0.35)",
                        "default": 0.35,
                    },
                },
                "required": ["query"],
            },
        }


class MemoryGetTool:
    """Tool for retrieving specific memory content."""

    def __init__(self, workspace_dir: str):
        """Initialize tool with workspace directory."""
        self.workspace_dir = workspace_dir

    async def get(
        self,
        path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Get specific content from a memory file.
        
        Args:
            path: File path
            start_line: Starting line number (optional)
            end_line: Ending line number (optional)
        
        Returns:
            Dictionary with path, content, and line numbers
        """
        try:
            from pathlib import Path
            
            file_path = Path(self.workspace_dir) / path
            
            if not file_path.exists():
                return {"error": f"File not found: {path}"}
            
            with open(file_path, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            if start_line is not None and end_line is not None:
                # Get specific lines
                content = ''.join(lines[start_line:end_line + 1])
            else:
                # Get entire file
                content = ''.join(lines)
            
            return {
                "path": path,
                "content": content,
                "start_line": start_line,
                "end_line": end_line,
                "total_lines": len(lines),
            }
        except Exception as e:
            logger.error(f"Memory get failed: {e}", exc_info=True)
            return {"error": str(e)}

    def get_tool_definition(self) -> Dict[str, Any]:
        """Get tool definition for agent."""
        return {
            "name": "memory_get",
            "description": "Retrieve specific content from a memory file by path and optional line range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to workspace (e.g., 'MEMORY.md' or 'memory/project.md')",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Starting line number (0-indexed, optional)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Ending line number (0-indexed, optional)",
                    },
                },
                "required": ["path"],
            },
        }


def create_memory_tools(manager, workspace_dir: str) -> Dict[str, Any]:
    """
    Create memory tools for agent use.
    
    Args:
        manager: Memory manager instance
        workspace_dir: Workspace directory path
    
    Returns:
        Dictionary of tool name to tool instance
    """
    return {
        "memory_search": MemorySearchTool(manager),
        "memory_get": MemoryGetTool(workspace_dir),
    }
