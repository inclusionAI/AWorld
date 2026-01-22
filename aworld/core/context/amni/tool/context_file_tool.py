# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_FILE = "FILE"


class ContextFileAction(ToolAction):
    """Context File Tool Actions. Definition of file reading operations in working directory."""

    READ_FILE = ToolActionInfo(
        name="read_file",
        input_params={
            "file_path": ParamInfo(
                name="file_path",
                type="str",
                required=True,
                desc="Relative file path in working directory, e.g., 'ppt/v1/outline.md' or 'ppt/v1/metadata.json'"
            )
        },
        desc="Read file content from working directory")


@ToolFactory.register(name=CONTEXT_FILE,
                      desc=CONTEXT_FILE,
                      supported_action=ContextFileAction)
class ContextFileTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextFileTool, self).__init__(conf, **kwargs)
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
                                 ability=ContextFileAction.READ_FILE.value.name), {}

    async def _read_file(self, file_path: str, context: AmniContext) -> str:
        """
        Read file content from working directory.
        
        Args:
            file_path: Relative file path in working directory
            context: AmniContext instance
            
        Returns:
            File content as string
            
        Example:
            >>> content = await tool._read_file("ppt/v1/outline.md", context)
        """
        logger.info(f"📖 ContextFileTool|_read_file: file_path={file_path}")
        
        # Load working directory
        await context.load_working_dir()
        
        if not context._working_dir:
            raise ValueError("Working directory not initialized")
        
        # Get file from working directory
        attachment = context._working_dir.get_file(file_path)
        if not attachment:
            raise FileNotFoundError(f"File not found: {file_path}")
        
        # Read file content
        content = attachment.content
        if content is None:
            raise ValueError(f"File content is empty: {file_path}")
        
        # Convert bytes to string if needed
        if isinstance(content, bytes):
            try:
                content = content.decode('utf-8')
            except UnicodeDecodeError:
                # Try other common encodings
                for encoding in ['gbk', 'gb2312', 'latin-1']:
                    try:
                        content = content.decode(encoding)
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    raise ValueError(f"Failed to decode file {file_path} as text with common encodings")
        
        if not isinstance(content, str):
            raise ValueError(f"File content is not text: {file_path}")
        
        logger.info(f"✅ ContextFileTool|_read_file: Successfully read file {file_path} ({len(content)} characters)")
        return content

    def init(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        pass

    async def finished(self) -> bool:
        return self.step_finished

    async def do_step(self, actions: list[ActionModel], message: Message = None, **kwargs) -> Tuple[
        Observation, float, bool, bool, Dict[str, Any]]:
        self.step_finished = False
        reward = 0.
        fail_error = ""
        observation = build_observation(observer=self.name(),
                                        ability=ContextFileAction.READ_FILE.value.name)
        info = {}
        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            for action in actions:
                logger.info(f"ContextFileTool|do_step: {action}")
                action_name = action.action_name
                if action_name == ContextFileAction.READ_FILE.value.name:
                    file_path = action.params.get("file_path", "")
                    if not file_path:
                        raise ValueError("file_path invalid")
                    try:
                        result = await self._read_file(file_path, message.context)
                        # Empty file content is valid, so we don't check if result is empty
                        observation.content = result
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=True,
                                         content=f"{result}",
                                         keep=False))
                    except FileNotFoundError as e:
                        error_msg = f"文件不存在: file_path={file_path}"
                        logger.warn(f"📖 ContextFileTool|read_file: {error_msg}, error={str(e)}")
                        observation.content = error_msg
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,
                                         content=error_msg,
                                         error=error_msg,
                                         keep=False))
                    except ValueError as e:
                        # Handle other errors like file not found, encoding issues, etc.
                        error_msg = str(e)
                        logger.warn(f"📖 ContextFileTool|read_file: {error_msg}")
                        observation.content = error_msg
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,
                                         content=error_msg,
                                         error=error_msg,
                                         keep=False))
                else:
                    raise ValueError("action name invalid")
            reward = 1.
        except Exception as e:
            fail_error = str(e)
            logger.warn(f"ContextFileTool|failed do_step: {traceback.format_exc()}")
        finally:
            self.step_finished = True
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)

