# coding: utf-8
# Copyright (c) 2025 inclusionAI.
"""
Context Tool - Session and Memory Management Tool

This tool provides functionality to manage and query conversation contexts (sessions).
It reads from the filesystem-based memory storage to provide information about:
- Available sessions
- Session statistics
- Memory items within sessions
"""

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from aworld.config import ToolConfig
from aworld.core.common import ToolActionInfo, ParamInfo, ActionResult, Observation, ActionModel
from aworld.core.context.amni import AmniContext
from aworld.core.event.base import Message
from aworld.core.tool.action import ToolAction
from aworld.core.tool.base import AsyncTool, ToolFactory
from aworld.logs.util import logger
from aworld.tools.utils import build_observation

CONTEXT_TOOL = "CONTEXT_TOOL"

@dataclass
class SessionInfo:
    """Session information data class"""
    session_id: str
    file_path: str
    total_items: int
    memory_types: Dict[str, int]
    agents: List[str]
    tasks: List[str]
    first_created: Optional[str]
    last_updated: Optional[str]
    file_size_kb: float
    current: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return asdict(self)


class ContextAction(ToolAction):
    """Context management actions"""

    LIST_SESSIONS = ToolActionInfo(
        name="list_sessions",
        input_params={
            "memory_root": ParamInfo(
                name="memory_root",
                type="string",
                required=False,
                desc="Memory root directory (default: ~/.aworld/memory or AWORLD_MEMORY_ROOT env var)"
            ),
            "limit": ParamInfo(
                name="limit",
                type="integer",
                required=False,
                desc="Maximum number of sessions to return (default: 10, set to 0 or negative for all sessions)"
            )
        },
        desc="List recent conversation sessions with basic statistics (default: latest 10 sessions)"
    )


@ToolFactory.register(
    name=CONTEXT_TOOL,
    desc=CONTEXT_TOOL,
    supported_action=ContextAction
)
class ContextTool(AsyncTool):
    """
    Tool for managing and querying conversation contexts (sessions)

    This tool provides access to the session-based memory storage, allowing
    users to list sessions, view session details, and query memory items.
    """

    def __init__(self, conf: ToolConfig, **kwargs) -> None:
        """Initialize Context Tool."""
        super(ContextTool, self).__init__(conf, **kwargs)
        self.step_finished = True
        logger.info("Context Tool initialized")

    async def reset(self, *, seed: int | None = None, options: Dict[str, str] | None = None) -> Tuple[
        Observation, dict[str, Any]]:
        await super().reset(seed=seed, options=options)
        self.step_finished = True
        return build_observation(observer=self.name(),
                                 ability=ContextAction.LIST_SESSIONS.value.name), {}

    async def close(self) -> None:
        """Close tool."""
        pass

    async def finished(self) -> bool:
        """Check if tool is finished."""
        return True

    def _get_memory_root(self, memory_root: Optional[str] = None) -> Path:
        """Get memory root directory path"""
        if memory_root:
            root = Path(memory_root)
        else:
            # Use AWORLD_MEMORY_ROOT env var or default
            env_root = os.getenv("AWORLD_MEMORY_ROOT", "~/.aworld/memory")
            root = Path(os.path.expanduser(os.path.expandvars(env_root)))

        return root

    def _get_sessions_dir(self, memory_root: Optional[str] = None) -> Path:
        """Get sessions directory path"""
        root = self._get_memory_root(memory_root)
        return root / "sessions"

    def _read_session_file(self, session_path: Path) -> List[Dict[str, Any]]:
        """Read all items from a session NDJSON file"""
        if not session_path.exists():
            return []

        items = []
        try:
            with open(session_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError as e:
                        logger.warning(f"Failed to parse line in {session_path}: {e}")
                        continue
        except Exception as e:
            logger.error(f"Failed to read session file {session_path}: {e}")
            return []

        return items

    def _analyze_session(self, session_path: Path, current_session_id: Optional[str] = None) -> Optional[SessionInfo]:
        """Analyze a session file and extract statistics"""
        items = self._read_session_file(session_path)
        if not items:
            return None

        # Extract statistics
        memory_types: Dict[str, int] = {}
        agents: set = set()
        tasks: set = set()
        first_created = None
        last_updated = None

        for item in items:
            # Skip deleted items
            if item.get('deleted', False):
                continue

            # Count memory types
            mem_type = item.get('memory_type', 'unknown')
            memory_types[mem_type] = memory_types.get(mem_type, 0) + 1

            # Extract metadata
            metadata = item.get('metadata', {})
            if metadata.get('agent_id'):
                agents.add(metadata['agent_id'])
            if metadata.get('task_id'):
                tasks.add(metadata['task_id'])

            # Track timestamps
            created = item.get('created_at')
            updated = item.get('updated_at')

            if created:
                if not first_created or created < first_created:
                    first_created = created
            if updated:
                if not last_updated or updated > last_updated:
                    last_updated = updated

        # Get file size
        file_size_kb = session_path.stat().st_size / 1024

        # Check if this is the current session
        session_id = session_path.stem
        is_current = (current_session_id is not None and session_id == current_session_id)

        return SessionInfo(
            session_id=session_id,
            file_path=str(session_path),
            total_items=len([i for i in items if not i.get('deleted', False)]),
            memory_types=memory_types,
            agents=sorted(list(agents)),
            tasks=sorted(list(tasks)),
            first_created=first_created,
            last_updated=last_updated,
            file_size_kb=round(file_size_kb, 2),
            current=is_current
        )

    async def _list_sessions(self, params: Dict[str, Any], current_session_id: Optional[str] = None) -> ActionResult:
        """List all available sessions"""
        try:
            memory_root = params.get('memory_root')
            limit = params.get('limit', 10)  # Default to 10 sessions
            sessions_dir = self._get_sessions_dir(memory_root)

            if not sessions_dir.exists():
                return ActionResult(
                    success=False,
                    content=f"Sessions directory not found: {sessions_dir}"
                )

            # Find all session files
            session_files = list(sessions_dir.glob("*.jsonl"))

            if not session_files:
                return ActionResult(
                    success=True,
                    content="No sessions found"
                )

            # Analyze each session
            sessions_info = []
            for session_file in sorted(session_files):
                info = self._analyze_session(session_file, current_session_id)
                if info:
                    sessions_info.append(info.to_dict())

            # Sort by last updated (most recent first)
            sessions_info.sort(
                key=lambda x: x.get('last_updated', ''),
                reverse=True
            )

            # Apply limit (if limit <= 0, return all sessions)
            total_sessions = len(sessions_info)
            if limit and limit > 0:
                sessions_info = sessions_info[:limit]

            result = {
                "total_sessions": total_sessions,
                "returned_sessions": len(sessions_info),
                "memory_root": str(self._get_memory_root(memory_root)),
                "current_session_id": current_session_id,
                "limit": limit,
                "sessions": sessions_info
            }

            return ActionResult(
                success=True,
                content=json.dumps(result, ensure_ascii=False, indent=2)
            )

        except Exception as e:
            logger.error(f"Failed to list sessions: {e}")
            return ActionResult(
                success=False,
                content=f"Error listing sessions: {str(e)}"
            )


    async def do_step(
        self,
        actions: list[ActionModel],
        message: Message = None,
        **kwargs
    ) -> Tuple[Observation, float, bool, bool, Dict[str, Any]]:
        """Execute context tool actions"""
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

            # Get current session ID from context
            current_session_id = getattr(message.context, 'session_id', None)

            for action in actions:
                logger.debug(f"ContextTool|do_step: {action}")
                action_name = action.action_name
                action_result = ActionResult(
                    action_name=action_name,
                    tool_name=self.name()
                )

                if action_name == ContextAction.LIST_SESSIONS.value.name:
                    action_result = await self._list_sessions(action.params, current_session_id)
                    action_result.action_name = action_name
                    action_result.tool_name = self.name()

                else:
                    action_result.success = False
                    action_result.content = f"Unknown action: {action_name}"

                action_results.append(action_result)

        except Exception as e:
            logger.error(f"ContextTool|do_step failed: {e}")
            import traceback
            logger.error(traceback.format_exc())

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

        # Get action_name from the first action or default
        action_name = actions[0].action_name if actions else ContextAction.LIST_SESSIONS.value.name

        observation = build_observation(
            observer=self.name(),
            ability=action_name,
            action_result=action_results
        )

        self.step_finished = True
        return (observation, reward, len(fail_error) > 0, len(fail_error) > 0, info)
