# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
Cast Search Tool
================

Encapsulates ACast's search functionality, providing a unified search interface.
Supports Grep content search, Glob file matching, and Read file reading.
"""

import json
import traceback
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation
from ..searchers.engine import SearchResult

CAST_SEARCH = "CAST_SEARCH"


class CastSearchAction(ToolAction):
    """Definition of Cast Search tool supported actions."""

    GREP_SEARCH = ToolActionInfo(
        name="grep_search",
        input_params={
            "pattern": ParamInfo(
                name="pattern",
                type="string",
                required=True,
                desc="Regular expression search pattern"
            ),
            "path": ParamInfo(
                name="path",
                type="string",
                required=False,
                desc="Search path, uses root path if None"
            ),
            "case_sensitive": ParamInfo(
                name="case_sensitive",
                type="boolean",
                required=False,
                desc="Whether to be case sensitive (default: False)"
            ),
            "context_lines": ParamInfo(
                name="context_lines",
                type="integer",
                required=False,
                desc="Number of context lines (default: 0)"
            ),
            "max_results": ParamInfo(
                name="max_results",
                type="integer",
                required=False,
                desc="Maximum number of results (default: 100)"
            ),
            "include_patterns": ParamInfo(
                name="include_patterns",
                type="array",
                required=False,
                desc="List of file patterns to include"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed search information (default: True)"
            )
        },
        desc="Execute content search using regular expression pattern"
    )

    GLOB_SEARCH = ToolActionInfo(
        name="glob_search",
        input_params={
            "pattern": ParamInfo(
                name="pattern",
                type="string",
                required=True,
                desc="File pattern (e.g., '*.py', 'src/**/*.js')"
            ),
            "path": ParamInfo(
                name="path",
                type="string",
                required=False,
                desc="Search path, uses root path if None"
            ),
            "max_depth": ParamInfo(
                name="max_depth",
                type="integer",
                required=False,
                desc="Maximum search depth"
            ),
            "search_hidden": ParamInfo(
                name="search_hidden",
                type="boolean",
                required=False,
                desc="Whether to search hidden files (default: True)"
            ),
            "follow_symlinks": ParamInfo(
                name="follow_symlinks",
                type="boolean",
                required=False,
                desc="Whether to follow symbolic links (default: True)"
            ),
            "max_results": ParamInfo(
                name="max_results",
                type="integer",
                required=False,
                desc="Maximum number of results (default: 100)"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed search information (default: True)"
            )
        },
        desc="Execute file pattern matching search"
    )

    READ_FILE = ToolActionInfo(
        name="read_file",
        input_params={
            "file_path": ParamInfo(
                name="file_path",
                type="string",
                required=True,
                desc="File path to read"
            ),
            "limit": ParamInfo(
                name="limit",
                type="integer",
                required=False,
                desc="Line limit for reading (default: 2000)"
            ),
            "offset": ParamInfo(
                name="offset",
                type="integer",
                required=False,
                desc="Starting line offset (default: 0)"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed read information (default: True)"
            )
        },
        desc="Read file content"
    )


@ToolFactory.register(
    name=CAST_SEARCH,
    desc=CAST_SEARCH,
    supported_action=CastSearchAction
)
class CastSearchTool(AsyncTool):
    """Cast Search Tool integrating ACast's search functionality."""

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Initialize Cast Search Tool."""
        super(CastSearchTool, self).__init__(conf, **kwargs)
        self._root_path = None
        self.init()

    def init(self) -> None:
        """Initialize tool components."""
        from aworld.experimental.cast import ACast

        self.acast = ACast()
        self.initialized = True
        logger.info("Cast Search Tool initialized")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=CastSearchAction.GREP_SEARCH.value.name), {}

    async def close(self) -> None:
        """Close tool."""
        self._root_path = None

    async def finished(self) -> bool:
        """Check if tool is finished."""
        return True

    def set_root_path(self, path: Union[str, Path]):
        """
        Set search root path

        Args:
            path: Root path
        """
        path = Path(path)
        if not path.exists():
            raise ValueError(f"Specified path does not exist: {path}")

        self.acast.set_search_root_path(path)
        self._root_path = path
        logger.info(f"Search root path set to: {path}")

    async def do_step(
            self,
            actions: list[ActionModel],
            message: Message = None,
            **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        self.step_finished = False
        reward = 0.
        fail_error = ""
        action_results = []
        info = {}

        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            for action in actions:
                logger.info(f"CastSearchTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())

                if action_name == CastSearchAction.GREP_SEARCH.value.name:
                    # Grep search
                    pattern = action.params.get("pattern")
                    path = action.params.get("path")
                    case_sensitive = action.params.get("case_sensitive", False)
                    context_lines = action.params.get("context_lines", 0)
                    max_results = action.params.get("max_results", 100)
                    include_patterns = action.params.get("include_patterns")
                    show_details = action.params.get("show_details", True)

                    if not pattern:
                        raise ValueError("pattern is required")

                    try:
                        result = await self._grep_search(
                            pattern=pattern,
                            path=path,
                            case_sensitive=case_sensitive,
                            context_lines=context_lines,
                            max_results=max_results,
                            include_patterns=include_patterns
                        )

                        result_data = {
                            "search_type": "grep",
                            "pattern": pattern,
                            "total_count": result.total_count,
                            "match_count": len(result.matches),
                            "truncated": result.truncated,
                            "execution_time": result.execution_time,
                            "matches": result.matches[:10] if len(result.matches) > 10 else result.matches,
                            "output": result.output
                        }

                        if show_details:
                            logger.info(
                                f"Grep search completed: pattern='{pattern}', found {result.total_count} results")

                        action_result.content = json.dumps(result_data, ensure_ascii=False, default=str)
                        action_result.success = True

                    except Exception as e:
                        error_result = {
                            "error": "Grep search failed",
                            "error_message": str(e),
                            "suggestions": [
                                "检查正则表达式模式是否正确",
                                "确认搜索路径是否存在",
                                "验证参数格式是否正确"
                            ]
                        }
                        action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                        action_result.success = False
                        action_result.error = str(e)

                    action_results.append(action_result)

                elif action_name == CastSearchAction.GLOB_SEARCH.value.name:
                    # Glob search
                    pattern = action.params.get("pattern")
                    path = action.params.get("path")
                    max_depth = action.params.get("max_depth")
                    search_hidden = action.params.get("search_hidden", True)
                    follow_symlinks = action.params.get("follow_symlinks", True)
                    max_results = action.params.get("max_results", 100)
                    show_details = action.params.get("show_details", True)

                    if not pattern:
                        raise ValueError("pattern is required")

                    try:
                        result = await self._glob_search(
                            pattern=pattern,
                            path=path,
                            max_depth=max_depth,
                            search_hidden=search_hidden,
                            follow_symlinks=follow_symlinks,
                            max_results=max_results
                        )

                        result_data = {
                            "search_type": "glob",
                            "pattern": pattern,
                            "total_count": result.total_count,
                            "match_count": len(result.matches),
                            "truncated": result.truncated,
                            "execution_time": result.execution_time,
                            "matches": result.matches[:10] if len(result.matches) > 10 else result.matches,
                            "output": result.output
                        }

                        if show_details:
                            logger.info(f"Glob search completed: pattern='{pattern}', found {result.total_count} files")

                        action_result.content = json.dumps(result_data, ensure_ascii=False, default=str)
                        action_result.success = True

                    except Exception as e:
                        error_result = {
                            "error": "Glob search failed",
                            "error_message": str(e),
                            "suggestions": [
                                "检查文件模式是否正确",
                                "确认搜索路径是否存在",
                                "验证参数格式是否正确"
                            ]
                        }
                        action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                        action_result.success = False
                        action_result.error = str(e)

                    action_results.append(action_result)

                elif action_name == CastSearchAction.READ_FILE.value.name:
                    # Read file
                    file_path = action.params.get("file_path")
                    limit = action.params.get("limit", 2000)
                    offset = action.params.get("offset", 0)
                    show_details = action.params.get("show_details", True)

                    if not file_path:
                        raise ValueError("file_path is required")

                    try:
                        result = await self._read_file(
                            file_path=file_path,
                            limit=limit,
                            offset=offset
                        )

                        result_data = {
                            "search_type": "read",
                            "file_path": str(file_path),
                            "total_count": result.total_count,
                            "match_count": len(result.matches),
                            "truncated": result.truncated,
                            "execution_time": result.execution_time,
                            "output": result.output
                        }

                        if show_details:
                            logger.info(f"File read completed: {file_path}, read {len(result.matches)} lines")

                        action_result.content = json.dumps(result_data, ensure_ascii=False, default=str)
                        action_result.success = True

                    except Exception as e:
                        error_result = {
                            "error": "File read failed",
                            "error_message": str(e),
                            "suggestions": [
                                "检查文件路径是否正确",
                                "确认文件是否存在",
                                "验证文件权限是否足够"
                            ]
                        }
                        action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                        action_result.success = False
                        action_result.error = str(e)

                    action_results.append(action_result)

                else:
                    raise ValueError(f"Unsupported action: {action_name}")

        except Exception as e:
            logger.error(f"CastSearchTool|do_step error: {traceback.format_exc()}")
            fail_error = str(e)
            reward = -1.0
            # Create failed action results for all actions
            for action in actions:
                action_result = ActionResult(
                    action_name=action.action_name,
                    tool_name=self.name(),
                    success=False,
                    error=str(e)
                )
                action_results.append(action_result)

        # Get action_name from the last action or default
        action_name = actions[0].action_name if actions else CastSearchAction.GREP_SEARCH.value.name

        observation = build_observation(
            observer=self.name(),
            ability=action_name,
            action_result=action_results
        )

        self.step_finished = True
        return (observation, reward, len(fail_error) > 0, len(fail_error) > 0, info)

    async def _grep_search(self,
                           pattern: str,
                           path: Optional[Union[str, Path]] = None,
                           case_sensitive: bool = False,
                           context_lines: int = 0,
                           max_results: int = 100,
                           include_patterns: Optional[List[str]] = None,
                           **kwargs) -> SearchResult:
        """
        Execute content search

        Args:
            pattern: Regular expression search pattern
            path: Search path, uses root path if None
            case_sensitive: Whether to be case sensitive
            context_lines: Number of context lines
            max_results: Maximum number of results
            include_patterns: List of file patterns to include
            **kwargs: Other search parameters

        Returns:
            SearchResult object containing search results

        Examples:
            >>> tool = CastSearchTool()
            >>> result = tool.grep_search("def.*function", case_sensitive=True)
            >>> print(f"Found {len(result.matches)} matches")
        """
        try:
            result = await self.acast.grep(
                pattern=pattern,
                path=path,
                case_sensitive=case_sensitive,
                context_lines=context_lines,
                max_results=max_results,
                include_patterns=include_patterns,
                **kwargs
            )

            logger.info(f"Grep search completed: pattern='{pattern}', found {result.total_count} results")
            return result

        except Exception as e:
            logger.error(f"Grep search failed: {e}")
            raise

    async def _glob_search(self,
                           pattern: str,
                           path: Optional[Union[str, Path]] = None,
                           max_depth: Optional[int] = None,
                           search_hidden: bool = True,
                           follow_symlinks: bool = True,
                           max_results: int = 100,
                           **kwargs) -> SearchResult:
        """
        Execute file pattern matching search

        Args:
            pattern: File pattern (e.g., "*.py", "src/**/*.js")
            path: Search path, uses root path if None
            max_depth: Maximum search depth
            search_hidden: Whether to search hidden files
            follow_symlinks: Whether to follow symbolic links
            max_results: Maximum number of results
            **kwargs: Other search parameters

        Returns:
            SearchResult object containing matched file paths

        Examples:
            >>> tool = CastSearchTool()
            >>> result = tool.glob_search("*.py", max_depth=2)
            >>> for match in result.matches:
            ...     print(match['path'])
        """
        try:
            result = await self.acast.glob(
                pattern=pattern,
                path=path,
                max_depth=max_depth,
                search_hidden=search_hidden,
                follow_symlinks=follow_symlinks,
                max_results=max_results,
                **kwargs
            )

            logger.info(f"Glob search completed: pattern='{pattern}', found {result.total_count} files")
            return result

        except Exception as e:
            logger.error(f"Glob search failed: {e}")
            raise

    async def _read_file(self,
                         file_path: Union[str, Path],
                         limit: int = 2000,
                         offset: int = 0,
                         **kwargs) -> SearchResult:
        """
        Read file content

        Args:
            file_path: File path
            limit: Line limit for reading
            offset: Starting line offset
            **kwargs: Other read parameters

        Returns:
            SearchResult object containing file content

        Examples:
            >>> tool = CastSearchTool()
            >>> result = tool.read_file("src/main.py", limit=50)
            >>> print(result.output)
        """
        try:
            result = await self.acast.read(
                file_path=file_path,
                limit=limit,
                offset=offset,
                **kwargs
            )

            file_path_str = str(file_path)
            logger.info(f"File read completed: {file_path_str}, read {len(result.matches)} lines")
            return result

        except Exception as e:
            logger.error(f"File read failed: {e}")
            raise


# Convenience functions (using internal helper class for direct usage)
class _CastSearchHelper:
    """Helper class for convenience functions that don't require ToolConfig."""

    def __init__(self):
        from aworld.experimental.cast import ACast
        self.acast = ACast()

    def grep_search(self, pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
        """Execute content search."""
        return self.acast.grep(pattern=pattern, path=path, **kwargs)

    def glob_search(self, pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
        """Execute file pattern matching search."""
        return self.acast.glob(pattern=pattern, path=path, **kwargs)

    def read_file(self, file_path: Union[str, Path], **kwargs) -> SearchResult:
        """Read file content."""
        return self.acast.read(file_path=file_path, **kwargs)


def quick_grep(pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
    """
    Quick content search convenience function

    Args:
        pattern: Search pattern
        path: Search path
        **kwargs: Other parameters

    Returns:
        Search results
    """
    helper = _CastSearchHelper()
    return helper.grep_search(pattern, path, **kwargs)


def quick_glob(pattern: str, path: Optional[Union[str, Path]] = None, **kwargs) -> SearchResult:
    """
    Quick file matching convenience function

    Args:
        pattern: File pattern
        path: Search path
        **kwargs: Other parameters

    Returns:
        Search results
    """
    helper = _CastSearchHelper()
    return helper.glob_search(pattern, path, **kwargs)


def quick_read(file_path: Union[str, Path], **kwargs) -> SearchResult:
    """
    Quick file reading convenience function

    Args:
        file_path: File path
        **kwargs: Other parameters

    Returns:
        File content
    """
    helper = _CastSearchHelper()
    return helper.read_file(file_path, **kwargs)
