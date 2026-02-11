# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import json
import traceback
from pathlib import Path
from typing import Dict, Any, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.tools.utils import build_observation
from ..utils import logger

CAST_CODER = "CAST_CODER"


class CAstCoderAction(ToolAction):
    """Definition of CAst Patch tool supported actions."""


    GENERATE_SNAPSHOT = ToolActionInfo(
        name="generate_snapshot",
        input_params={
            "target_dir": ParamInfo(
                name="target_dir",
                type="string",
                required=True,
                desc="Target directory path to create snapshot from"
            ),
            "version": ParamInfo(
                name="version",
                type="string",
                required=False,
                desc="Version string for the snapshot (e.g., 'v0', 'v1'), default is 'v0'"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed generation information"
            )
        },
        desc="Generate compressed snapshot of target directory"
    )

    DEPLOY_PATCHES = ToolActionInfo(
        name="deploy_patches",
        input_params={
            "patch_content": ParamInfo(
                name="patch_content",
                type="string",
                required=True,
                desc="Patch content to deploy"
            ),
            "source_dir": ParamInfo(
                name="source_dir",
                type="string",
                required=True,
                desc="Source directory path"
            ),
            "version": ParamInfo(
                name="version",
                type="string",
                required=False,
                desc="Version string for the patch (e.g., 'v0', 'v1'), default is 'v0'"
            ),
            "validate_syntax": ParamInfo(
                name="validate_syntax",
                type="boolean",
                required=False,
                desc="Whether to validate syntax after deployment"
            ),
            "strict_validation": ParamInfo(
                name="strict_validation",
                type="boolean",
                required=False,
                desc="Whether to enable strict context validation (default: True)"
            ),
            "max_context_mismatches": ParamInfo(
                name="max_context_mismatches",
                type="integer",
                required=False,
                desc="Maximum allowed context mismatches before failing (default: 0)"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed deployment information"
            )
        },
        desc="Deploy patches from patch content to source directory in-place with optional validation"
    )

    DEPLOY_OPS = ToolActionInfo(
        name="deploy_ops",
        input_params={
            "operations_json": ParamInfo(
                name="operations_json",
                type="string",
                required=True,
                desc="JSON格式的操作指令，支持insert、replace、delete三种操作类型"
            ),
            "source_dir": ParamInfo(
                name="source_dir",
                type="string",
                required=True,
                desc="Source directory path"
            ),
            "version": ParamInfo(
                name="version",
                type="string",
                required=False,
                desc="Version string for the operation (e.g., 'v0', 'v1'), default is 'v0'"
            ),
            "strict_validation": ParamInfo(
                name="strict_validation",
                type="boolean",
                required=False,
                desc="Whether to enable strict validation (default: True)"
            ),
            "max_context_mismatches": ParamInfo(
                name="max_context_mismatches",
                type="integer",
                required=False,
                desc="Maximum allowed context mismatches before failing (default: 0)"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed deployment information"
            )
        },
        desc="Deploy code changes based on JSON operation instructions (insert/replace/delete)"
    )

    SEARCH_REPLACE = ToolActionInfo(
        name="search_replace",
        input_params={
            "operation_json": ParamInfo(
                name="operation_json",
                type="string",
                required=True,
                desc="JSON format precise search and replace operation instruction, format: {\"operation\": {\"type\": \"search_replace\", \"file_path\": \"relative_path\", \"search\": \"code_to_search\", \"replace\": \"replacement_code\", \"exact_match_only\": true}}"
            ),
            "source_dir": ParamInfo(
                name="source_dir",
                type="string",
                required=True,
                desc="Source directory path"
            ),
            "show_details": ParamInfo(
                name="show_details",
                type="boolean",
                required=False,
                desc="Whether to show detailed operation information (default: True)"
            )
        },
        desc="Perform precise search and replace operations using exact matching only - no fuzzy matching allowed for guaranteed code accuracy"
    )


@ToolFactory.register(
    name=CAST_CODER,
    desc=CAST_CODER,
    supported_action=CAstCoderAction
)
class CAstCoderTool(AsyncTool):
    """CAst Patch Tool integrating PatchCollectionTool, PatchFileGenerationTool, and PatchDeploymentTool."""

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Initialize CAst Patch Tool."""
        super(CAstCoderTool, self).__init__(conf, **kwargs)
        self._repo_map = None
        self._patches = None
        self._patch_content = None
        self.init()

    def init(self) -> None:
        """Initialize tool components."""
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
                                 ability=CAstCoderAction.GENERATE_SNAPSHOT.value.name), {}

    async def close(self) -> None:
        """Close tool."""
        self._repo_map = None
        self._patches = None
        self._patch_content = None

    async def finished(self) -> bool:
        """Check if tool is finished."""
        return True


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
                logger.info(f"CAstPatchTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(action_name=action_name, tool_name=self.name())

                if action_name == CAstCoderAction.GENERATE_SNAPSHOT.value.name:
                    # Generate snapshot
                    target_dir = Path(action.params.get("target_dir"))
                    version = action.params.get("version", "v0")
                    show_details = action.params.get("show_details", True)

                    # Generate compressed snapshot and get file path
                    snapshot_path = self.acast.generate_snapshot(
                        target_dir=target_dir,
                        version=version
                    )

                    result = {
                        "snapshot_path": str(snapshot_path),
                        "version": version,
                        "target_dir": str(target_dir)
                    }

                    if show_details:
                        logger.info(f"Generated snapshot: {snapshot_path} (version: {version})")

                    action_result.content = json.dumps(result, ensure_ascii=False, default=str)
                    action_result.success = True
                    action_results.append(action_result)
                elif action_name == CAstCoderAction.DEPLOY_PATCHES.value.name:
                    # 部署patch
                    patch_content = action.params.get("patch_content")
                    source_dir = Path(action.params.get("source_dir"))
                    version = action.params.get("version", "v0")
                    validate_syntax = action.params.get("validate_syntax", True)
                    strict_validation = action.params.get("strict_validation", True)
                    max_context_mismatches = action.params.get("max_context_mismatches", 0)
                    show_details = action.params.get("show_details", True)

                    if not patch_content:
                        raise ValueError("patch_content is required")

                    # 使用ACast原地更新并部署patch，启用严格验证
                    try:
                        target_dir = self.acast.deploy_dmp(
                            source_dir=source_dir,
                            patch_content=patch_content,
                            version=version,
                            strict_validation=strict_validation,
                            max_context_mismatches=max_context_mismatches
                        )

                        result = {
                            "target_dir": str(target_dir),
                            "version": version,
                            "patch_file": str(target_dir / f"ast_integration_{version}.patch"),
                            "validation_mode": "strict" if strict_validation else "lenient",
                            "max_allowed_mismatches": max_context_mismatches
                        }

                        if show_details:
                            logger.info(f"Patch deployed successfully to: {target_dir}")

                        action_result.content = json.dumps(result, ensure_ascii=False, default=str)
                        action_result.success = True

                    except Exception as validation_error:
                        # 如果是验证错误，提供详细的错误信息
                        if "上下文验证失败" in str(validation_error):
                            error_result = {
                                "error": "Context validation failed",
                                "error_message": str(validation_error),
                                "suggestions": [
                                    "使用CAST_ANALYSIS.layered_recall重新获取最新的文件内容",
                                    "基于验证后的实际文件内容重新生成patch",
                                    "确保diff中的上下文行与实际文件完全匹配"
                                ],
                                "validation_mode": "strict" if strict_validation else "lenient",
                                "max_allowed_mismatches": max_context_mismatches
                            }
                            action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                            action_result.success = False
                            action_result.error = str(validation_error)
                        else:
                            # 其他类型的错误
                            raise validation_error

                    action_results.append(action_result)
                elif action_name == CAstCoderAction.DEPLOY_OPS.value.name:
                    # 部署JSON操作指令
                    operations_json = action.params.get("operations_json")
                    source_dir = Path(action.params.get("source_dir"))
                    version = action.params.get("version", "v0")
                    strict_validation = action.params.get("strict_validation", True)
                    max_context_mismatches = action.params.get("max_context_mismatches", 0)
                    show_details = action.params.get("show_details", True)

                    if not operations_json:
                        raise ValueError("operations_json is required")

                    if not source_dir.exists():
                        raise ValueError(f"Source directory does not exist: {source_dir}")

                    # 使用ACast的deploy_operations方法直接处理JSON操作
                    try:
                        target_dir = self.acast.deploy_ops(
                            operations_json=operations_json,
                            source_dir=source_dir,
                            version=version,
                            strict_validation=strict_validation,
                            max_context_mismatches=max_context_mismatches
                        )

                        result = {
                            "target_dir": str(target_dir),
                            "version": version,
                            "operations_applied": True,
                            "validation_mode": "strict" if strict_validation else "lenient",
                            "max_allowed_mismatches": max_context_mismatches
                        }

                        if show_details:
                            logger.info(f"JSON操作已成功部署到: {target_dir}")

                        action_result.content = json.dumps(result, ensure_ascii=False, default=str)
                        action_result.success = True

                    except Exception as deployment_error:
                        # 处理部署错误
                        error_result = {
                            "error": "Operations deployment failed",
                            "error_message": str(deployment_error),
                            "suggestions": [
                                "检查JSON格式是否正确",
                                "确认文件路径和行号是否有效",
                                "验证操作指令的完整性"
                            ],
                            "validation_mode": "strict" if strict_validation else "lenient",
                            "max_allowed_mismatches": max_context_mismatches
                        }
                        action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                        action_result.success = False
                        action_result.error = str(deployment_error)

                    action_results.append(action_result)
                elif action_name == CAstCoderAction.SEARCH_REPLACE.value.name:
                    # 搜索替换操作
                    operation_json = action.params.get("operation_json")
                    source_dir = Path(action.params.get("source_dir"))
                    show_details = action.params.get("show_details", True)

                    if not operation_json:
                        raise ValueError("operation_json is required")

                    if not source_dir.exists():
                        raise ValueError(f"Source directory does not exist: {source_dir}")

                    # Use ACast's search_replace_operation method to handle search and replace
                    try:
                        result = self.acast.search_replace_operation(
                            source_dir=source_dir,
                            operation_json=operation_json
                        )

                        if result.get("success", False):
                            response_result = {
                                "success": True,
                                "modified": result.get("modified", False),
                                "file_affected": result.get("file_path", ""),
                                "operation_type": "search_replace",
                                "fuzzy_match_used": result.get("fuzzy_match_used", False),
                                "match_strategy": result.get("match_strategy", "unknown")
                            }

                            if show_details:
                                logger.info(f"Search and replace operation successful: {source_dir}")
                                if result.get("modified"):
                                    logger.info(f"File modified: {result.get('file_path', 'unknown')}")

                            action_result.content = json.dumps(response_result, ensure_ascii=False, default=str)
                            action_result.success = True

                        else:
                            # Search and replace failed
                            error_result = {
                                "success": False,
                                "error": result.get("error", "Precise search and replace operation failed"),
                                "suggestions": [
                                    "Check if search text exactly matches the target file (including whitespace, indentation, etc.)",
                                    "Confirm the file path is correct",
                                    "Ensure the search text is identical to the code in the file",
                                    "Verify line endings and encoding format consistency"
                                ]
                            }
                            action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                            action_result.success = False
                            action_result.error = result.get("error", "Precise search and replace operation failed")

                    except Exception as search_replace_error:
                        # Handle search and replace errors
                        error_result = {
                            "success": False,
                            "error": f"Precise search and replace operation exception: {str(search_replace_error)}",
                            "suggestions": [
                                "Check if JSON format is correct",
                                "Confirm operation field contains necessary parameters",
                                "Verify file path is valid",
                                "Ensure search and replace text format is completely correct"
                            ]
                        }
                        action_result.content = json.dumps(error_result, ensure_ascii=False, default=str)
                        action_result.success = False
                        action_result.error = str(search_replace_error)

                    action_results.append(action_result)
                else:
                    raise ValueError(f"Unsupported action: {action_name}")

        except Exception as e:
            logger.error(f"CAstPatchTool|do_step error: {traceback.format_exc()}")
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
