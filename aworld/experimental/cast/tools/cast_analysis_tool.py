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
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

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
        desc="Analyze the entire repository and generate RepositoryMap"
    )

    RECALL_IMPL = ToolActionInfo(
        name="recall_impl",
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
        desc="Recall implementation layer code based on user query. Logic and skeleton layers are already returned by analyze_repository interface."
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
        """收集仓库统计信息"""
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
                "small": 0,  # < 100 行
                "medium": 0,  # 100-500 行
                "large": 0  # > 500 行
            }
        }

        if show_details:
            print(f"\n📊 仓库统计信息:")
            print(f"   分析的文件数: {stats['total_files']}")

        # 遍历所有文件收集统计信息
        for file_info in repo_map.files.values():
            # 统计符号和引用
            if hasattr(file_info, 'symbols') and file_info.symbols:
                stats["total_symbols"] += len(file_info.symbols)

                # 统计符号类型分布
                for symbol in file_info.symbols:
                    symbol_type = symbol.symbol_type.name if hasattr(symbol.symbol_type, 'name') else str(
                        symbol.symbol_type)
                    stats["symbol_type_distribution"][symbol_type] = stats["symbol_type_distribution"].get(symbol_type,
                                                                                                           0) + 1

            if hasattr(file_info, 'references') and file_info.references:
                stats["total_references"] += len(file_info.references)

            # 统计语言分布
            if hasattr(file_info, 'language'):
                language = file_info.language
                stats["language_distribution"][language] = stats["language_distribution"].get(language, 0) + 1

            # 统计文件大小分布
            if hasattr(file_info, 'line_count'):
                line_count = file_info.line_count
                if line_count < 100:
                    stats["file_size_distribution"]["small"] += 1
                elif line_count < 500:
                    stats["file_size_distribution"]["medium"] += 1
                else:
                    stats["file_size_distribution"]["large"] += 1

        if show_details:
            print(f"   总符号数: {stats['total_symbols']}")
            print(f"   总引用数: {stats['total_references']}")

            # 显示语言分布
            if stats["language_distribution"]:
                print(f"   语言分布:")
                for lang, count in sorted(stats["language_distribution"].items(), key=lambda x: x[1], reverse=True):
                    percentage = count / stats["total_files"] * 100
                    print(f"     • {lang}: {count} 文件 ({percentage:.1f}%)")

            # 显示主要符号类型
            if stats["symbol_type_distribution"]:
                print(f"   主要符号类型:")
                top_symbol_types = sorted(stats["symbol_type_distribution"].items(), key=lambda x: x[1], reverse=True)[
                                   :5]
                for symbol_type, count in top_symbol_types:
                    percentage = count / stats["total_symbols"] * 100 if stats["total_symbols"] > 0 else 0
                    print(f"     • {symbol_type}: {count} ({percentage:.1f}%)")

            # 显示文件大小分布
            if any(stats["file_size_distribution"].values()):
                print(f"   文件大小分布:")
                print(f"     • 小文件(<100行): {stats['file_size_distribution']['small']}")
                print(f"     • 中文件(100-500行): {stats['file_size_distribution']['medium']}")
                print(f"     • 大文件(>500行): {stats['file_size_distribution']['large']}")

        return stats

    def _extract_implementation_sections(self, lines: List[str]) -> List[str]:
        """从上下文行中提取实现相关的章节"""
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
                # 分析整个仓库
                root_path = Path(action.params.get("root_path"))
                ignore_patterns = action.params.get("ignore_patterns", ['__pycache__', '*.pyc', '.git'])
                show_details = action.params.get("show_details", True)

                logger.info(f"仓库级分析 - 路径: {root_path}")

                if show_details:
                    print(f"\n🏗️ 仓库级分析")
                    print("-" * 40)

                try:
                    # 使用 ACast 分析整个目录
                    repo_map = self.acast.analyze(
                        root_path=root_path,
                        ignore_patterns=ignore_patterns,
                        record_name=Path(root_path).name
                    )

                    # 保存完整的 repo_map 供后续使用（包含实现层）
                    self._repo_map = repo_map

                    # 创建不包含实现层的副本用于返回（ANALYZE_REPOSITORY 不返回实现层）
                    from dataclasses import replace
                    from aworld.experimental.cast.models import ImplementationLayer
                    repo_map_without_impl = replace(
                        repo_map,
                        implementation_layer=ImplementationLayer(
                            code_nodes={}
                        )
                    )

                    # 分析统计信息
                    analysis_stats = self._gather_repository_stats(repo_map, show_details)

                    result = {
                        "root_path": str(root_path),
                        "ignore_patterns": ignore_patterns,
                        "repository_map": repo_map_without_impl,  # 不包含实现层
                        "analysis_stats": analysis_stats,
                        "analysis_success": True,
                        "analysis_time": datetime.now().isoformat()
                    }

                    logger.info(
                        f"仓库分析完成 - 文件数: {analysis_stats['total_files']}, 符号数: {analysis_stats['total_symbols']}"
                    )

                except Exception as e:
                    error_msg = f"仓库分析失败: {str(e)}"
                    logger.error(f"仓库分析失败: {error_msg}")

                    result = {
                        "root_path": str(root_path),
                        "ignore_patterns": ignore_patterns,
                        "repository_map": None,
                        "analysis_stats": None,
                        "analysis_success": False,
                        "error": error_msg,
                        "analysis_time": datetime.now().isoformat()
                    }

                action_result.content = json.dumps(result, ensure_ascii=False, default=str)
                action_result.success = result.get("analysis_success", False)
                action_results.append(action_result)
            elif action_name == CAstAnalysisAction.RECALL_IMPL.value.name:
                # 只召回实现层代码
                root_path = Path(action.params.get("root_path"))
                user_query = action.params.get("user_query", "如何为DocCodeAgent集成AST分析能力")
                max_tokens = action.params.get("max_tokens", 8000)
                show_details = action.params.get("show_details", True)

                try:
                    # 使用 ACast 只召回实现层
                    context = self.acast.recall(
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

                    logger.info(f"实现层召回完成 - 上下文长度: {len(context) if context else 0} 字符")

                    if show_details:
                        print(f"\n🎯 实现层召回")
                        print("=" * 60)
                        print(f"💭 用户查询: {user_query}")
                        for value in context.values():
                            print(f"📏 上下文长度: {len(value) if value else 0} 字符")

                except Exception as e:
                    error_msg = f"召回失败: {str(e)} {traceback.format_exc()}"
                    logger.error(f"实现层召回失败: {error_msg}")
                    result = {
                        "success": False,
                        "error": error_msg,
                        "context": ""
                    }

                action_result.content = json.dumps(result, ensure_ascii=False, default=str)
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
