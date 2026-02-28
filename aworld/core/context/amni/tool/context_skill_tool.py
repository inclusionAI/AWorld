# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import traceback
from pathlib import Path
from typing import Any, Dict, Tuple

from aworld.config import ToolConfig
from aworld.core.common import Observation, ActionModel, ActionResult, ToolActionInfo, ParamInfo
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import ToolFactory, AsyncTool
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_SKILL = "SKILL"


class ContextExecuteAction(ToolAction):
    """Agent Skills Support. Definition of Context visit and setting supported action."""

    ACTIVE_SKILL = ToolActionInfo(
        name="active_skill",
        input_params={"skill_name": ParamInfo(name="skill_name",
                                              type="str",
                                              required=True,
                                              desc="name of the skill to be activated")},
        desc="dynamically insert a specified and pre-designed skill to help you to conduct the current task, while the current tools cannot handle the current task professionally")

    OFFLOAD_SKILL = ToolActionInfo(
        name="offload_skill",
        input_params={"skill_name": ParamInfo(name="skill_name",
                                              type="str",
                                              required=True,
                                              desc="name of the skill to be offloaded")},
        desc="dynamically delete a particular skill from your current context to save memory")

    READ_SKILL_FILE = ToolActionInfo(
        name="read_skill_file",
        input_params={
            "skill_name": ParamInfo(name="skill_name",
                                   type="str",
                                   required=True,
                                   desc="name of the skill"),
            "file_path": ParamInfo(name="file_path",
                                  type="str",
                                  required=True,
                                  desc="relative file path within the skill directory")
        },
        desc="read a file associated with a skill")

    LIST_SKILL_FILE_TREE = ToolActionInfo(
        name="list_skill_file_tree",
        input_params={
            "skill_name": ParamInfo(name="skill_name",
                                   type="str",
                                   required=True,
                                   desc="name of the skill")
        },
        desc="list the complete file tree structure of a skill directory")

    LIST_SKILL_DIRECTORY = ToolActionInfo(
        name="list_skill_directory",
        input_params={
            "skill_name": ParamInfo(name="skill_name",
                                   type="str",
                                   required=True,
                                   desc="name of the skill"),
            "dir_path": ParamInfo(name="dir_path",
                                 type="str",
                                 required=False,
                                 desc="relative directory path within the skill directory, defaults to root if not specified")
        },
        desc="list files and subdirectories in a specified directory of a skill")


@ToolFactory.register(name=CONTEXT_SKILL,
                      desc=CONTEXT_SKILL,
                      supported_action=ContextExecuteAction)
class ContextSkillTool(AsyncTool):
    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        super(ContextSkillTool, self).__init__(conf, **kwargs)
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
                                 ability=ContextExecuteAction.ACTIVE_SKILL.value.name), {}

    def _resolve_skill_file_path(self, skill_path: str, file_path: str) -> Path:
        """
        Resolve the absolute file path from skill path and relative file path.
        
        Args:
            skill_path: The skill's root directory path (from skill_path field in skill config)
            file_path: Relative file path within the skill directory
            
        Returns:
            Resolved absolute Path object
            
        Example:
            >>> tool = ContextSkillTool(...)
            >>> skill_path = "/path/to/skills/ppt_theme/SKILL.md"
            >>> file_path = "forms/style_map.json"
            >>> resolved = tool._resolve_skill_file_path(skill_path, file_path)
            >>> # Result: Path("/path/to/skills/ppt_theme/forms/style_map.json")
        """
        # Get the skill directory (parent of SKILL.md)
        skill_dir = Path(skill_path).parent if skill_path else None
        if not skill_dir:
            raise ValueError(f"Invalid skill_path: {skill_path}")
        
        # Handle @ prefix in file_path (remove it if present)
        if file_path.startswith("@"):
            # Extract path after @
            file_path = file_path[1:]
            # If it starts with skill name, remove it
            if file_path.startswith(skill_dir.name + "/"):
                file_path = file_path[len(skill_dir.name) + 1:]
        
        # Resolve the full path
        full_path = (skill_dir / file_path).resolve()
        
        # Security check: ensure the resolved path is within skill directory
        try:
            full_path.relative_to(skill_dir.resolve())
        except ValueError:
            raise ValueError(f"File path {file_path} is outside skill directory")
        
        return full_path

    def _get_skill_directory(self, skill_path: str) -> Path:
        """
        Get the skill directory path from skill_path.
        
        Args:
            skill_path: The skill's SKILL.md file path
            
        Returns:
            Path object pointing to the skill directory
            
        Example:
            >>> skill_path = "/path/to/skills/ppt_theme/SKILL.md"
            >>> skill_dir = tool._get_skill_directory(skill_path)
            >>> # Result: Path("/path/to/skills/ppt_theme")
        """
        skill_dir = Path(skill_path).parent if skill_path else None
        if not skill_dir:
            raise ValueError(f"Invalid skill_path: {skill_path}")
        return skill_dir.resolve()

    def _build_file_tree(self, root_path: Path, prefix: str = "", is_last: bool = True, max_depth: int = 10, current_depth: int = 0, is_root: bool = False) -> str:
        """
        Build a text-based file tree representation.
        
        Args:
            root_path: Root directory path to build tree from
            prefix: Prefix string for tree visualization
            is_last: Whether this is the last item in its parent
            max_depth: Maximum depth to traverse (prevents infinite recursion)
            current_depth: Current depth level
            is_root: Whether this is the root node
            
        Returns:
            File tree as formatted string
            
        Example:
            >>> tree = tool._build_file_tree(Path("/path/to/skill"), is_root=True)
            >>> # Returns formatted tree string
        """
        if current_depth > max_depth:
            return ""
        
        lines = []
        if root_path.is_file():
            # File node
            lines.append(f"{prefix}{'└── ' if is_last else '├── '}{root_path.name}")
        else:
            # Directory node
            if is_root:
                # Root directory shows just the name
                lines.append(f"{root_path.name}/")
            else:
                lines.append(f"{prefix}{'└── ' if is_last else '├── '}{root_path.name}/")
            
            # Get children and sort: directories first, then files
            try:
                children = sorted(root_path.iterdir(), key=lambda p: (p.is_file(), p.name))
                if not children:
                    return "\n".join(lines)
                
                # Filter out hidden files/directories (starting with .)
                children = [c for c in children if not c.name.startswith('.')]
                
                for i, child in enumerate(children):
                    is_last_child = (i == len(children) - 1)
                    if is_root:
                        extension = ""
                        child_prefix = prefix
                    else:
                        extension = "    " if is_last else "│   "
                        child_prefix = prefix + extension
                    child_tree = self._build_file_tree(child, child_prefix, is_last_child, max_depth, current_depth + 1, is_root=False)
                    if child_tree:
                        lines.append(child_tree)
            except PermissionError:
                if is_root:
                    lines.append("[Permission Denied]")
                else:
                    lines.append(f"{prefix}{'    ' if is_last else '│   '}[Permission Denied]")
        
        return "\n".join(lines)

    async def _list_skill_file_tree(self, skill_name: str, namespace: str, context: AmniContext) -> str:
        """
        Generate file tree structure for a skill directory.
        
        Args:
            skill_name: Name of the skill
            namespace: Agent namespace
            context: AmniContext instance
            
        Returns:
            File tree structure as formatted string
            
        Example:
            >>> tree = await tool._list_skill_file_tree("ppt_theme", "agent1", context)
        """
        logger.info(f"📁 ContextSkillTool|_list_skill_file_tree: skill_name={skill_name}, namespace={namespace}")
        
        # Get skill configuration
        skill_config = await context.get_skill(skill_name, namespace)
        if not skill_config:
            raise ValueError(f"Skill '{skill_name}' not found in namespace '{namespace}'")
        
        skill_path = skill_config.get("skill_path", "")
        if not skill_path:
            raise ValueError(f"Skill '{skill_name}' has no skill_path configured")
        
        # Get skill directory
        skill_dir = self._get_skill_directory(skill_path)
        
        if not skill_dir.exists():
            raise FileNotFoundError(f"Skill directory not found: {skill_dir}")
        
        # Build file tree
        tree_str = self._build_file_tree(skill_dir, is_root=True)
        logger.info(f"✅ ContextSkillTool|_list_skill_file_tree: Successfully generated file tree for {skill_name}")
        return tree_str

    async def _list_skill_directory(self, skill_name: str, dir_path: str, namespace: str, context: AmniContext) -> str:
        """
        List files and subdirectories in a specified directory of a skill.
        
        Args:
            skill_name: Name of the skill
            dir_path: Relative directory path within the skill directory (empty for root)
            namespace: Agent namespace
            context: AmniContext instance
            
        Returns:
            Formatted string listing directory contents
            
        Example:
            >>> contents = await tool._list_skill_directory("ppt_theme", "sub_skills", "agent1", context)
        """
        logger.info(f"📂 ContextSkillTool|_list_skill_directory: skill_name={skill_name}, dir_path={dir_path}, namespace={namespace}")
        
        # Get skill configuration
        skill_config = await context.get_skill(skill_name, namespace)
        if not skill_config:
            raise ValueError(f"Skill '{skill_name}' not found in namespace '{namespace}'")
        
        skill_path = skill_config.get("skill_path", "")
        if not skill_path:
            raise ValueError(f"Skill '{skill_name}' has no skill_path configured")
        
        # Get skill directory
        skill_dir = self._get_skill_directory(skill_path)
        
        # Resolve target directory path
        if dir_path:
            # Handle @ prefix if present
            if dir_path.startswith("@"):
                dir_path = dir_path[1:]
                if dir_path.startswith(skill_dir.name + "/"):
                    dir_path = dir_path[len(skill_dir.name) + 1:]
            target_dir = (skill_dir / dir_path).resolve()
        else:
            target_dir = skill_dir
        
        # Security check: ensure the resolved path is within skill directory
        try:
            target_dir.relative_to(skill_dir.resolve())
        except ValueError:
            raise ValueError(f"Directory path {dir_path} is outside skill directory")
        
        # Check if directory exists
        if not target_dir.exists():
            raise FileNotFoundError(f"Directory not found: {target_dir} (skill: {skill_name}, relative path: {dir_path})")
        
        if not target_dir.is_dir():
            raise ValueError(f"Path is not a directory: {target_dir}")
        
        # List directory contents
        try:
            items = sorted(target_dir.iterdir(), key=lambda p: (p.is_file(), p.name))
            # Filter out hidden files/directories
            items = [item for item in items if not item.name.startswith('.')]
            
            if not items:
                result = f"Directory '{dir_path if dir_path else skill_name}' is empty."
            else:
                lines = [f"Directory: {dir_path if dir_path else skill_name}/"]
                lines.append("")
                lines.append("Contents:")
                
                directories = []
                files = []
                for item in items:
                    if item.is_dir():
                        directories.append(f"  📁 {item.name}/")
                    else:
                        files.append(f"  📄 {item.name}")
                
                if directories:
                    lines.append("  Directories:")
                    lines.extend(directories)
                if files:
                    lines.append("  Files:")
                    lines.extend(files)
                
                result = "\n".join(lines)
            
            logger.info(f"✅ ContextSkillTool|_list_skill_directory: Successfully listed directory {dir_path if dir_path else 'root'} for skill {skill_name}")
            return result
        except PermissionError as e:
            raise ValueError(f"Permission denied: {target_dir}")

    async def _read_skill_file(self, skill_name: str, file_path: str, namespace: str, context: AmniContext) -> str:
        """
        Read file content from a skill's directory.
        
        Args:
            skill_name: Name of the skill
            file_path: Relative file path within the skill directory
            namespace: Agent namespace
            context: AmniContext instance
            
        Returns:
            File content as string
            
        Example:
            >>> content = await tool._read_skill_file("ppt_theme", "forms/style_map.json", "agent1", context)
        """
        logger.info(f"📖 ContextSkillTool|_read_skill_file: skill_name={skill_name}, file_path={file_path}, namespace={namespace}")
        
        # Get skill configuration
        skill_config = await context.get_skill(skill_name, namespace)
        if not skill_config:
            raise ValueError(f"Skill '{skill_name}' not found in namespace '{namespace}'")
        
        skill_path = skill_config.get("skill_path", "")
        if not skill_path:
            raise ValueError(f"Skill '{skill_name}' has no skill_path configured")
        
        # Resolve file path
        full_path = self._resolve_skill_file_path(skill_path, file_path)
        
        # Check if file exists
        if not full_path.exists():
            raise FileNotFoundError(f"File not found: {full_path} (skill: {skill_name}, relative path: {file_path})")
        
        if not full_path.is_file():
            raise ValueError(f"Path is not a file: {full_path}")
        
        # Read file content
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                content = f.read()
            logger.info(f"✅ ContextSkillTool|_read_skill_file: Successfully read file {full_path} ({len(content)} characters)")
            return content
        except UnicodeDecodeError:
            # Try other common encodings
            for encoding in ['gbk', 'gb2312', 'latin-1']:
                try:
                    with open(full_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    logger.info(f"✅ ContextSkillTool|_read_skill_file: Successfully read file {full_path} with encoding {encoding} ({len(content)} characters)")
                    return content
                except UnicodeDecodeError:
                    continue
            # If all text encodings fail, read as binary and return error message
            raise ValueError(f"Failed to decode file {full_path} as text with common encodings")
        except Exception as e:
            logger.error(f"❌ ContextSkillTool|_read_skill_file: Error reading file {full_path}: {e}")
            raise

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
                                        ability=ContextExecuteAction.ACTIVE_SKILL.value.name)
        info = {}
        try:
            if not actions:
                raise ValueError("actions is empty")
            if not isinstance(message.context, AmniContext):
                raise ValueError("context is not AmniContext")

            for action in actions:
                logger.info(f"CONTEXTTool|do_step: {action}")
                action_name = action.action_name
                if action_name == ContextExecuteAction.ACTIVE_SKILL.value.name:
                    skill_name = action.params.get("skill_name", "")
                    if not skill_name:
                        raise ValueError("skill name invalid")
                    result = await message.context.active_skill(skill_name, namespace=action.agent_name)
                    if not result:
                        raise ValueError("active skill failed")
                    observation.content = result
                    observation.action_result.append(
                        ActionResult(is_done=True,
                                     success=True,
                                     content=f"{result}",
                                     keep=False))
                elif action_name == ContextExecuteAction.OFFLOAD_SKILL.value.name:
                    skill_name = action.params.get("skill_name", "")
                    if not skill_name:
                        raise ValueError("skill name invalid")
                    result = await message.context.offload_skill(skill_name, namespace=action.action_name)
                    if not result:
                        raise ValueError("offload skill failed")
                    observation.content = result
                    observation.action_result.append(
                        ActionResult(is_done=True,
                                     success=True,
                                     content=f"{result}",
                                     keep=False))
                elif action_name == ContextExecuteAction.READ_SKILL_FILE.value.name:
                    skill_name = action.params.get("skill_name", "")
                    file_path = action.params.get("file_path", "")
                    if not skill_name:
                        raise ValueError("skill name invalid")
                    if not file_path:
                        raise ValueError("file_path invalid")
                    try:
                        result = await self._read_skill_file(skill_name, file_path, action.agent_name, message.context)
                        # Empty file content is valid, so we don't check if result is empty
                        observation.content = result
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=True,
                                         content=f"{result}",
                                         keep=False))
                    except FileNotFoundError as e:
                        # Extract relative path from error message for cleaner error reporting
                        error_msg = f"文件不存在: skill={skill_name}, file_path={file_path}"
                        logger.warn(f"📖 ContextSkillTool|read_skill_file: {error_msg}, full_path={str(e)}")
                        observation.content = error_msg
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,
                                         content=error_msg,
                                         error=error_msg,
                                         keep=False))
                    except ValueError as e:
                        # Handle other errors like skill not found, path issues, etc.
                        error_msg = str(e)
                        logger.warn(f"📖 ContextSkillTool|read_skill_file: {error_msg}")
                        observation.content = error_msg
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,
                                         content=error_msg,
                                         error=error_msg,
                                         keep=False))
                elif action_name == ContextExecuteAction.LIST_SKILL_FILE_TREE.value.name:
                    skill_name = action.params.get("skill_name", "")
                    if not skill_name:
                        raise ValueError("skill name invalid")
                    try:
                        result = await self._list_skill_file_tree(skill_name, action.agent_name, message.context)
                        observation.content = result
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=True,
                                         content=f"{result}",
                                         keep=False))
                    except (ValueError, FileNotFoundError) as e:
                        error_msg = str(e)
                        logger.warn(f"📁 ContextSkillTool|list_skill_file_tree: {error_msg}")
                        observation.content = error_msg
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=False,
                                         content=error_msg,
                                         error=error_msg,
                                         keep=False))
                elif action_name == ContextExecuteAction.LIST_SKILL_DIRECTORY.value.name:
                    skill_name = action.params.get("skill_name", "")
                    dir_path = action.params.get("dir_path", "")
                    if not skill_name:
                        raise ValueError("skill name invalid")
                    try:
                        result = await self._list_skill_directory(skill_name, dir_path, action.agent_name, message.context)
                        observation.content = result
                        observation.action_result.append(
                            ActionResult(is_done=True,
                                         success=True,
                                         content=f"{result}",
                                         keep=False))
                    except (ValueError, FileNotFoundError) as e:
                        error_msg = str(e)
                        logger.warn(f"📂 ContextSkillTool|list_skill_directory: {error_msg}")
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
            logger.warn(f"CONTEXTTool|failed do_step: {traceback.format_exc()}")
        finally:
            self.step_finished = True
        info["exception"] = fail_error
        info.update(kwargs)
        return (observation, reward, kwargs.get("terminated", False),
                kwargs.get("truncated", False), info)
