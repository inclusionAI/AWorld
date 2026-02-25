# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.tools.utils import build_observation
from ..utils import logger

CAST_ANALYSIS = "CAST_ANALYSIS"


class CAstAnalysisAction(ToolAction):
    """Definition of CAst Analysis tool supported actions."""

    ANALYZE_REPOSITORY = ToolActionInfo(
        name="analyze_repository",
        input_params={
            "root_path": ParamInfo(
                name="root_path",
                type="string",
                required=True,
                desc="Repository root directory path"
            ),
            "ignore_patterns": ParamInfo(
                name="ignore_patterns",
                type="array",
                required=False,
                desc="Ignore patterns for files"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed analysis information"
            )
        },
        desc="Analyze the repository and build the three-tier index: L1 logic (project structure, call/dependency graph, heatmap), L2 skeleton (signatures and docstrings only, no body), L3 implementation (full source on-demand). Returns logic_layer always; skeleton_layer and implementation_layer when within length limits."
    )

    SEARCH_AST = ToolActionInfo(
        name="search_ast",
        input_params={
            "root_path": ParamInfo(
                name="root_path",
                type="string",
                required=True,
                desc="Repository root directory path"
            ),
            "user_query": ParamInfo(
                name="user_query",
                type="string",
                required=True,
                desc="User query for implementation code recall (supports regular expressions)"
            ),
            "max_tokens": ParamInfo(
                name="max_tokens",
                type="integer",
                required=False,
                desc="Maximum tokens for context recall"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed recall information"
            )
        },
        desc="Retrieve skeleton_layer and implementation_layer to gain insights into implementation details of the repository."
    )


@ToolFactory.register(
    name=CAST_ANALYSIS,
    desc=CAST_ANALYSIS,
    supported_action=CAstAnalysisAction
)
class CAstAnalysisTool(AsyncTool):
    """CAst Analysis Tool integrating FileParsingTool, RepositoryAnalysisTool, and LayeredRecallTool."""

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Initialize CAst Analysis Tool."""
        super(CAstAnalysisTool, self).__init__(conf, **kwargs)
        self._repo_map = None
        from aworld.experimental.cast import ACast

        self.acast = ACast()

        self.initialized = True
        logger.info("CAst Analysis Tool initialized")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)

        await self.close()
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=CAstAnalysisAction.ANALYZE_REPOSITORY.value.name), {}

    async def close(self) -> None:
        """Close tool."""
        self._repo_map = None

    async def finished(self) -> bool:
        """Check if tool is finished."""
        return True

    def _gather_repository_stats(self, repo_map, show_details: bool = True) -> Dict[str, Any]:
        """Collect repository statistics"""
        if not repo_map or not hasattr(repo_map, 'files'):
            return {
                "total_files": 0,
                "total_symbols": 0,
                "total_references": 0,
                "language_distribution": {},
                "symbol_type_distribution": {},
                "file_size_distribution": {
                    "small": 0,
                    "medium": 0,
                    "large": 0
                }
            }

        stats = {
            "total_files": len(repo_map.files),
            "total_symbols": 0,
            "total_references": 0,
            "language_distribution": {},
            "symbol_type_distribution": {},
            "file_size_distribution": {
                "small": 0,  # < 100 lines
                "medium": 0,  # 100-500 lines
                "large": 0  # > 500 lines
            }
        }

        if show_details:
            logger.info(f"\n📊 Repository Statistics:")
            logger.info(f"   Files analyzed: {stats['total_files']}")

        # Iterate through all files to collect statistics
        for file_info in repo_map.files.values():
            # Count symbols and references
            if hasattr(file_info, 'symbols') and file_info.symbols:
                stats["total_symbols"] += len(file_info.symbols)

                # Count symbol type distribution
                for symbol in file_info.symbols:
                    symbol_type = symbol.symbol_type.name if hasattr(symbol.symbol_type, 'name') else str(
                        symbol.symbol_type)
                    stats["symbol_type_distribution"][symbol_type] = stats["symbol_type_distribution"].get(symbol_type,
                                                                                                           0) + 1

            if hasattr(file_info, 'references') and file_info.references:
                stats["total_references"] += len(file_info.references)

            # Count language distribution
            if hasattr(file_info, 'language'):
                language = file_info.language
                stats["language_distribution"][language] = stats["language_distribution"].get(language, 0) + 1

            # Count file size distribution
            if hasattr(file_info, 'line_count'):
                line_count = file_info.line_count
                if line_count < 100:
                    stats["file_size_distribution"]["small"] += 1
                elif line_count < 500:
                    stats["file_size_distribution"]["medium"] += 1
                else:
                    stats["file_size_distribution"]["large"] += 1

        if show_details:
            logger.info(f"   Total symbols: {stats['total_symbols']}")
            logger.info(f"   Total references: {stats['total_references']}")

            # Show language distribution
            if stats["language_distribution"]:
                logger.info(f"   Language distribution:")
                for lang, count in sorted(stats["language_distribution"].items(), key=lambda x: x[1], reverse=True):
                    percentage = count / stats["total_files"] * 100
                    logger.info(f"     • {lang}: {count} files ({percentage:.1f}%)")

            # Show major symbol types
            if stats["symbol_type_distribution"]:
                logger.info(f"   Major symbol types:")
                top_symbol_types = sorted(stats["symbol_type_distribution"].items(), key=lambda x: x[1], reverse=True)[
                                   :5]
                for symbol_type, count in top_symbol_types:
                    percentage = count / stats["total_symbols"] * 100 if stats["total_symbols"] > 0 else 0
                    logger.info(f"     • {symbol_type}: {count} ({percentage:.1f}%)")

            # Show file size distribution
            if any(stats["file_size_distribution"].values()):
                logger.info(f"   File size distribution:")
                logger.info(f"     • Small files(<100 lines): {stats['file_size_distribution']['small']}")
                logger.info(f"     • Medium files(100-500 lines): {stats['file_size_distribution']['medium']}")
                logger.info(f"     • Large files(>500 lines): {stats['file_size_distribution']['large']}")

        return stats

    def _extract_implementation_sections(self, lines: List[str]) -> List[str]:
        """Extract implementation-related sections from context lines"""
        implementation_sections = []
        current_section = []

        for line in lines:
            if line.startswith('#'):
                if current_section:
                    implementation_sections.append('\n'.join(current_section))
                current_section = [line]
            else:
                current_section.append(line)

        if current_section:
            implementation_sections.append('\n'.join(current_section))

        return implementation_sections

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
                logger.info(f"CAstAnalysisTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())

            if action_name == CAstAnalysisAction.ANALYZE_REPOSITORY.value.name:
                # Analyze entire repository
                root_path = Path(action.params.get("root_path"))
                ignore_patterns = action.params.get("ignore_patterns", ['__pycache__', '*.pyc', '.git'])
                show_details = action.params.get("show_details", True)

                logger.info(f"Repository-level analysis - Path: {root_path}")

                if show_details:
                    logger.info(f"\n🏗️ Repository-level Analysis")
                    logger.info("-" * 40)

                try:
                    # Use ACast to analyze entire directory
                    repo_map = self.acast.analyze(
                        root_path=root_path,
                        ignore_patterns=ignore_patterns,
                        record_name=Path(root_path).name
                    )

                    # Save complete repo_map for later use (contains implementation layer)
                    self._repo_map = repo_map

                    # Return logic_layer always; include skeleton_layer/implementation_layer only if length is short enough
                    from dataclasses import replace
                    from aworld.experimental.cast.models import (
                        ImplementationLayer,
                        SkeletonLayer,
                    )
                    # Length thresholds (chars): only include layer in return when under threshold
                    SKELETON_MAX_CHARS = 80_000
                    IMPL_MAX_CHARS = 100_000
                    skeleton_len = sum(
                        len(s) for s in repo_map.skeleton_layer.file_skeletons.values()
                    )
                    impl_len = sum(
                        len(s.content or "")
                        for node in repo_map.implementation_layer.code_nodes.values()
                        for s in node.symbols
                    )
                    repo_map_for_return = replace(
                        repo_map,
                        skeleton_layer=repo_map.skeleton_layer
                        if skeleton_len <= SKELETON_MAX_CHARS
                        else SkeletonLayer(
                            file_skeletons={},
                            symbol_signatures={},
                            line_mappings={},
                        ),
                        implementation_layer=repo_map.implementation_layer
                        if impl_len <= IMPL_MAX_CHARS
                        else ImplementationLayer(code_nodes={}),
                    )

                    # Analyze statistics
                    analysis_stats = self._gather_repository_stats(repo_map, show_details)

                    result = {
                        "root_path": str(root_path),
                        "ignore_patterns": ignore_patterns,
                        "repository_map": repo_map_for_return.to_dict(),  # Use to_dict() to convert to JSON serializable object
                        "analysis_stats": analysis_stats,
                        "analysis_success": True,
                        "analysis_time": datetime.now().isoformat()
                    }

                    logger.info(
                        f"Repository analysis completed - Files: {analysis_stats['total_files']}, Symbols: {analysis_stats['total_symbols']}"
                    )

                except Exception as e:
                    error_msg = f"Repository analysis failed: {str(e)}"
                    logger.error(f"Repository analysis failed: {error_msg}")

                    result = {
                        "root_path": str(root_path),
                        "ignore_patterns": ignore_patterns,
                        "repository_map": None,
                        "analysis_stats": None,
                        "analysis_success": False,
                        "error": error_msg,
                        "analysis_time": datetime.now().isoformat()
                    }

                action_result.content = json.dumps(result, ensure_ascii=False, indent=2)
                action_result.success = result.get("analysis_success", False)
                action_results.append(action_result)
            elif action_name == CAstAnalysisAction.SEARCH_AST.value.name:
                # Only recall implementation layer code
                root_path = Path(action.params.get("root_path"))
                user_query = action.params.get("user_query", "How to integrate AST analysis capability for DocCodeAgent")
                max_tokens = action.params.get("max_tokens", 8000)
                show_details = action.params.get("show_details", True)

                try:
                    # Use ACast to recall only implementation layer
                    context = self.acast.search_ast(
                        repo_map=None,
                        user_query=user_query,
                        max_tokens=max_tokens,
                        context_layers=["implementation"],
                        record_name=Path(root_path).name
                    )

                    result = {
                        "success": True,
                        "user_query": user_query,
                        "context": context,
                        "context_length": len(context) if context else 0,
                        "recall_time": datetime.now().isoformat()
                    }

                    logger.info(f"Implementation layer recall completed - Context length: {len(context) if context else 0} characters")

                    if show_details:
                        logger.info(f"\n🎯 Implementation Layer Recall")
                        logger.info("=" * 60)
                        logger.info(f"💭 User query: {user_query}")
                        for value in context.values():
                            logger.info(f"📏 Context length: {len(value) if value else 0} characters")

                except Exception as e:
                    error_msg = f"Recall failed: {str(e)} {traceback.format_exc()}"
                    logger.error(f"Implementation layer recall failed: {error_msg}")
                    result = {
                        "success": False,
                        "error": error_msg,
                        "context": ""
                    }

                action_result.content = json.dumps(result, ensure_ascii=False, indent=2)
                action_result.success = result.get("success", False)
                action_results.append(action_result)
            else:
                raise ValueError(f"Unsupported action: {action_name}")

        except Exception as e:
            logger.error(f"CAstAnalysisTool|do_step error: {traceback.format_exc()}")
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

        observation = build_observation(
            observer=self.name(),
            ability=action_name,
            action_result=action_results
        )

        self.step_finished = True
        return (observation, reward, len(fail_error) > 0, len(fail_error) > 0, info)
