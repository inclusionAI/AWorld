"""
File-system based memory storage implementation (Session-based NDJSON)

This implementation persists memories using a session-based approach similar to OpenClaw.
Each session has its own file containing newline-delimited JSON (NDJSON) records.

Directory structure:
  memory_root/
    ├── index/
    │   └── id_map.json        # Maps memory_id -> session_id
    └── sessions/
        ├── {session_id}.jsonl # NDJSON file for the session
        └── ...

Design principles:
- Session-Centric: Optimized for retrieving conversation history.
- Append-Only (mostly): Adding memories is fast.
- NDJSON: Standard format for log-structured data.
"""

import json
import time
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Set

from pydantic import BaseModel

from aworld.core.memory import MemoryStore
from aworld.memory.models import (
    MemoryItem, MemoryAIMessage, MemoryHumanMessage, MemorySummary,
    MemorySystemMessage, MemoryToolMessage, MessageMetadata,
    UserProfile, AgentExperience, ConversationSummary, Fact
)
from aworld.models.model_response import ToolCall
from aworld.logs.util import digest_logger


class FileSystemMemoryStore(MemoryStore):
    """
    File-system based memory storage implementation using Session-based NDJSON files.
    """

    def __init__(self, memory_root: str = "./data/aworld_memory"):
        """
        Initialize filesystem memory storage.

        Args:
            memory_root: Root directory path for memory storage.
        """
        self.memory_root = Path(memory_root)
        self.sessions_dir = self.memory_root / "sessions"
        self.index_dir = self.memory_root / "index"
        self.id_map_file = self.index_dir / "id_map.json"
        
        # In-memory cache for ID mapping
        self._id_map: Dict[str, str] = {}
        self._id_map_dirty = False

    def _init_storage(self) -> None:
        """Initialize storage directories and load index."""
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.index_dir.mkdir(parents=True, exist_ok=True)

        if self.id_map_file.exists():
            try:
                content = self.id_map_file.read_text(encoding='utf-8')
                self._id_map = json.loads(content)
            except Exception:
                self._id_map = {}
        else:
            self._id_map = {}

    def _save_id_map(self) -> None:
        """Persist the ID map to disk."""
        if self._id_map_dirty:
            self.id_map_file.write_text(
                json.dumps(self._id_map, ensure_ascii=False, indent=2),
                encoding='utf-8'
            )
            self._id_map_dirty = False

    def _get_session_id(self, item: MemoryItem) -> str:
        """Extract session_id from MemoryItem or use default."""
        # Check metadata for session_id
        if item.metadata:
            # metadata can be a dict or an object
            if isinstance(item.metadata, dict):
                return item.metadata.get("session_id") or "default"
            elif hasattr(item.metadata, "session_id"):
                return getattr(item.metadata, "session_id") or "default"
        return "default"

    def _get_session_path(self, session_id: str) -> Path:
        """Get the file path for a session."""
        # Sanitize session_id to be safe for filenames if necessary
        safe_id = "".join(c for c in session_id if c.isalnum() or c in ('-', '_')).strip()
        if not safe_id:
            safe_id = "default"
        return self.sessions_dir / f"{safe_id}.jsonl"

    def _serialize_content(self, content: Any) -> str:
        """Serialize `content` as a JSON string."""
        if content is None:
            return ""
        if isinstance(content, (dict, list, str, int, float, bool)):
            return json.dumps(content, ensure_ascii=False)
        if isinstance(content, BaseModel):
            return content.model_dump_json()
        return json.dumps(content, ensure_ascii=False, default=str)

    def _deserialize_content(self, content_str: str) -> Any:
        """Deserialize JSON string back into Python object."""
        if not content_str:
            return None
        try:
            return json.loads(content_str)
        except json.JSONDecodeError:
            return content_str

    def _memory_item_to_dict(self, item: MemoryItem) -> Dict[str, Any]:
        """Convert `MemoryItem` into a JSON-serializable dict."""
        return {
            "id": item.id,
            "content": self._serialize_content(item.content),
            "created_at": item.created_at or datetime.now().isoformat(),
            "updated_at": item.updated_at or datetime.now().isoformat(),
            "metadata": item.metadata,
            "tags": item.tags,
            "memory_type": item.memory_type,
            "version": item.version,
            "deleted": item.deleted
        }

    def _dict_to_memory_item(self, data: Dict[str, Any]) -> Optional[MemoryItem]:
        """Convert a dict into a `MemoryItem` instance."""
        if not data:
            return None

        memory_meta = data.get("metadata", {})
        role = memory_meta.get('role')
        memory_type = data.get("memory_type")

        base_data = {
            'id': data['id'],
            'created_at': data.get('created_at'),
            'updated_at': data.get('updated_at'),
            'tags': data.get('tags', []),
            'version': data.get('version', 1),
            'deleted': data.get('deleted', False)
        }

        content = self._deserialize_content(data.get('content', ''))

        # Build the appropriate `MemoryItem` subtype based on type/role.
        if role == 'system':
            return MemorySystemMessage(
                content=content,
                metadata=MessageMetadata(**memory_meta),
                **base_data
            )
        elif role == 'user':
            return MemoryHumanMessage(
                metadata=MessageMetadata(**memory_meta),
                content=content,
                **base_data
            )
        elif memory_type == 'summary':
            if not content or not isinstance(content, str):
                return None
            item_ids = memory_meta.get('item_ids', [])
            return MemorySummary(
                item_ids=item_ids,
                summary=content,
                metadata=MessageMetadata(**memory_meta),
                **base_data
            )
        elif role == 'assistant':
            tool_calls_jsons = memory_meta.get('tool_calls', [])
            tool_calls = [ToolCall.from_dict(tc) for tc in tool_calls_jsons]
            return MemoryAIMessage(
                content=content,
                tool_calls=tool_calls,
                metadata=MessageMetadata(**memory_meta),
                **base_data
            )
        elif role == 'tool':
            return MemoryToolMessage(
                tool_call_id=memory_meta.get('tool_call_id'),
                content=content,
                status=memory_meta.get('status', 'success'),
                metadata=MessageMetadata(**memory_meta),
                **base_data
            )
        elif memory_type == 'fact':
            if not content or not isinstance(content, dict):
                return None
            return Fact(
                content=content,
                user_id=memory_meta.get('user_id'),
                metadata=memory_meta,
                **base_data
            )
        elif memory_type == 'user_profile':
            if not content or not isinstance(content, dict):
                return None
            return UserProfile(
                key=content.get('key'),
                value=content.get('value'),
                user_id=memory_meta.get('user_id'),
                metadata=memory_meta,
                **base_data
            )
        elif memory_type == 'agent_experience':
            if not content or not isinstance(content, dict):
                return None
            return AgentExperience(
                skill=content.get('skill'),
                actions=content.get('actions'),
                agent_id=memory_meta.get('agent_id'),
                metadata=memory_meta,
                **base_data
            )
        elif memory_type == 'conversation_summary':
            if not content or not isinstance(content, str):
                return None
            return ConversationSummary(
                user_id=memory_meta.get('user_id'),
                session_id=memory_meta.get('session_id'),
                summary=content,
                metadata=MessageMetadata(**memory_meta),
                **base_data
            )
        else:
            return MemoryItem(
                content=content,
                metadata=memory_meta,
                memory_type=memory_type,
                **base_data
            )

    def _read_session_file(self, session_id: str) -> List[Dict[str, Any]]:
        """Read all items from a session file."""
        path = self._get_session_path(session_id)
        if not path.exists():
            return []
        
        items = []
        try:
            with open(path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except Exception:
            return []
        return items

    def _append_to_session_file(self, session_id: str, data: Dict[str, Any]) -> None:
        """Append a single item to the session file."""
        path = self._get_session_path(session_id)
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(data, ensure_ascii=False) + '\n')

    def _rewrite_session_file(self, session_id: str, items: List[Dict[str, Any]]) -> None:
        """Rewrite the session file with the given items."""
        path = self._get_session_path(session_id)
        # Write to temp file first then rename for atomicity
        temp_path = path.with_suffix('.tmp')
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                for item in items:
                    f.write(json.dumps(item, ensure_ascii=False) + '\n')
            temp_path.replace(path)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    def _matches_filters(self, data: Dict[str, Any], filters: Dict[str, Any]) -> bool:
        """Check whether a memory item matches the given filters."""
        if not filters:
            return not data.get('deleted', False)

        # Deleted items never match.
        if data.get('deleted', False):
            return False

        metadata = data.get('metadata', {})
        memory_type = data.get('memory_type')

        for key, value in filters.items():
            if value is None:
                continue

            if key in ['user_id', 'agent_id', 'session_id', 'task_id', 'agent_name', 'tool_call_id']:
                if metadata.get(key) != value:
                    return False
            elif key == 'memory_type':
                if isinstance(value, list):
                    if memory_type not in value:
                        return False
                else:
                    if memory_type != value:
                        return False

        return True

    def _log_timing(
        self,
        op_name: str,
        start_time: float,
        *,
        success: bool,
        exc: Optional[BaseException] = None,
        extra: str = "",
    ) -> None:
        """Emit a structured timing log for a `MemoryStore` interface call."""
        duration = round(time.perf_counter() - start_time, 6)
        status = "success" if success else "failed"
        msg = f"memory_store|{op_name}|{duration}|{status}"
        if extra:
            msg += f"|{extra}"
        if exc is not None:
            msg += f"|error={type(exc).__name__}"
        digest_logger.info(msg)

    def add(self, memory_item: MemoryItem) -> None:
        """Add a new memory item."""
        start_time = time.perf_counter()
        extra = f"id={memory_item.id}"
        try:
            self._init_storage()
            
            session_id = self._get_session_id(memory_item)
            data = self._memory_item_to_dict(memory_item)
            
            self._append_to_session_file(session_id, data)
            
            # Update index
            self._id_map[memory_item.id] = session_id
            self._id_map_dirty = True
            self._save_id_map()
            
            self._log_timing("add", start_time, success=True, extra=extra)
        except Exception as exc:
            self._log_timing("add", start_time, success=False, exc=exc, extra=extra)
            raise

    def get(self, memory_id: str) -> Optional[MemoryItem]:
        """Get a memory item by id."""
        start_time = time.perf_counter()
        extra = f"id={memory_id}"
        try:
            self._init_storage()
            
            session_id = self._id_map.get(memory_id)
            if not session_id:
                # Fallback: scan all sessions? Or just return None.
                # For performance, we rely on the index.
                return None
            
            items = self._read_session_file(session_id)
            for item in items:
                if item['id'] == memory_id:
                    if item.get('deleted', False):
                        return None
                    return self._dict_to_memory_item(item)
            
            return None
        except Exception as exc:
            self._log_timing("get", start_time, success=False, exc=exc, extra=extra)
            raise

    def get_first(self, filters: Dict[str, Any] = None) -> Optional[MemoryItem]:
        """Get the first matching memory item (ascending by created time)."""
        start_time = time.perf_counter()
        extra = f"filters={len(filters) if filters else 0}"
        
        try:
            self._init_storage()
            
            # Optimization: If session_id is in filters, only check that file
            target_sessions = []
            if filters and 'session_id' in filters:
                target_sessions = [filters['session_id']]
            else:
                # List all session files
                target_sessions = [p.stem for p in self.sessions_dir.glob("*.jsonl")]
            
            all_matches = []
            for session_id in target_sessions:
                items = self._read_session_file(session_id)
                for item in items:
                    if self._matches_filters(item, filters):
                        all_matches.append(item)
            
            all_matches.sort(key=lambda x: x.get('created_at', ''))
            
            result = self._dict_to_memory_item(all_matches[0]) if all_matches else None
            self._log_timing("get_first", start_time, success=True, extra=extra)
            return result
        except Exception as exc:
            self._log_timing("get_first", start_time, success=False, exc=exc, extra=extra)
            raise

    def total_rounds(self, filters: Dict[str, Any] = None) -> int:
        """Get the total number of matching memory items."""
        start_time = time.perf_counter()
        extra = f"filters={len(filters) if filters else 0}"
        
        try:
            self._init_storage()
            
            target_sessions = []
            if filters and 'session_id' in filters and filters['session_id'] is not None:
                target_sessions = [filters['session_id']]
            else:
                target_sessions = [p.stem for p in self.sessions_dir.glob("*.jsonl")]
                
            count = 0
            for session_id in target_sessions:
                items = self._read_session_file(session_id)
                for item in items:
                    if self._matches_filters(item, filters):
                        count += 1
                        
            self._log_timing("total_rounds", start_time, success=True, extra=extra)
            return count
        except Exception as exc:
            self._log_timing("total_rounds", start_time, success=False, exc=exc, extra=extra)
            raise

    def get_all(self, filters: Dict[str, Any] = None) -> List[MemoryItem]:
        """Get all matching memory items (ascending by created time)."""
        start_time = time.perf_counter()
        extra = f"filters={len(filters) if filters else 0}"
        
        try:
            self._init_storage()
            
            if filters and 'session_id' in filters and filters['session_id'] is not None:
                target_sessions = [filters['session_id']]
            else:
                target_sessions = [p.stem for p in self.sessions_dir.glob("*.jsonl")]
            
            all_matches = []
            for session_id in target_sessions:
                items = self._read_session_file(session_id)
                for item in items:
                    if self._matches_filters(item, filters):
                        all_matches.append(item)
            
            all_matches.sort(key=lambda x: x.get('created_at', ''))
            
            result = [self._dict_to_memory_item(data) for data in all_matches]
            self._log_timing("get_all", start_time, success=True, extra=extra)
            return result
        except Exception as exc:
            self._log_timing("get_all", start_time, success=False, exc=exc, extra=extra)
            raise

    def get_last_n(self, last_rounds: int, filters: Dict[str, Any] = None) -> List[MemoryItem]:
        """
        Get the last N matching memory items.
        """
        start_time = time.perf_counter()
        extra = f"last_rounds={last_rounds},filters={len(filters) if filters else 0}"
        
        try:
            self._init_storage()
            
            # Optimization: If session_id is provided, we can just read that file
            target_sessions = []
            if filters and 'session_id' in filters and filters['session_id'] is not None:
                target_sessions = [filters['session_id']]
            else:
                target_sessions = [p.stem for p in self.sessions_dir.glob("*.jsonl")]
            
            all_matches = []
            for session_id in target_sessions:
                items = self._read_session_file(session_id)
                for item in items:
                    if self._matches_filters(item, filters):
                        all_matches.append(item)
            
            # Sort by created time (descending)
            all_matches.sort(key=lambda x: x.get('created_at', ''), reverse=True)
            
            # Take top N
            selected = all_matches[:last_rounds]
            
            # Reverse back to ascending
            selected.reverse()
            
            result = [self._dict_to_memory_item(data) for data in selected]
            self._log_timing("get_last_n", start_time, success=True, extra=extra)
            return result
        except Exception as exc:
            self._log_timing("get_last_n", start_time, success=False, exc=exc, extra=extra)
            raise

    def update(self, memory_item: MemoryItem) -> None:
        """Update an existing memory item."""
        start_time = time.perf_counter()
        extra = f"id={memory_item.id}"
        try:
            self._init_storage()
            
            session_id = self._id_map.get(memory_item.id)
            if not session_id:
                # Try to guess from item if not in index (e.g. migration case or corruption)
                session_id = self._get_session_id(memory_item)
            
            items = self._read_session_file(session_id)
            updated = False
            new_items = []
            
            for item in items:
                if item['id'] == memory_item.id:
                    # Update timestamp
                    memory_item.updated_at = datetime.now().isoformat()
                    new_items.append(self._memory_item_to_dict(memory_item))
                    updated = True
                else:
                    new_items.append(item)
            
            if updated:
                self._rewrite_session_file(session_id, new_items)
            else:
                # Item not found in the expected session file. 
                # Could be a consistency issue. Treat as add or error?
                # Standard MemoryStore update usually assumes existence.
                pass

            self._log_timing("update", start_time, success=True, extra=extra)
        except Exception as exc:
            self._log_timing("update", start_time, success=False, exc=exc, extra=extra)
            raise

    def delete(self, memory_id: str) -> None:
        """Soft-delete a memory item (mark as deleted)."""
        start_time = time.perf_counter()
        extra = f"id={memory_id}"
        try:
            self._init_storage()
            
            session_id = self._id_map.get(memory_id)
            if not session_id:
                return 
            
            items = self._read_session_file(session_id)
            updated = False
            
            for item in items:
                if item['id'] == memory_id:
                    item['deleted'] = True
                    item['updated_at'] = datetime.now().isoformat()
                    updated = True
                    break
            
            if updated:
                self._rewrite_session_file(session_id, items)
                
            self._log_timing("delete", start_time, success=True, extra=extra)
        except Exception as exc:
            self._log_timing("delete", start_time, success=False, exc=exc, extra=extra)
            raise

    def delete_items(self, message_types: List[str], session_id: str, task_id: str,
                     filters: Dict[str, Any] = None) -> None:
        """Batch soft-delete specified memory item types."""
        filters = filters or {}
        filters['memory_type'] = message_types
        filters['session_id'] = session_id
        filters['task_id'] = task_id

        start_time = time.perf_counter()
        extra = f"types={len(message_types)},session_id={session_id},task_id={task_id}"
        try:
            self._init_storage()
            
            # If session_id is provided (it is required by signature), we only check that file
            target_sessions = [session_id]
            
            for sid in target_sessions:
                items = self._read_session_file(sid)
                if not items: 
                    continue
                    
                updated = False
                for item in items:
                    if self._matches_filters(item, filters):
                        item['deleted'] = True
                        item['updated_at'] = datetime.now().isoformat()
                        updated = True
                
                if updated:
                    self._rewrite_session_file(sid, items)

            self._log_timing("delete_items", start_time, success=True, extra=extra)
        except Exception as exc:
            self._log_timing("delete_items", start_time, success=False, exc=exc, extra=extra)
            raise

    def history(self, memory_id: str) -> Optional[List[MemoryItem]]:
        """
        Get the history of a memory item.
        """
        start_time = time.perf_counter()
        extra = f"id={memory_id}"
        try:
            result: Optional[List[MemoryItem]] = None
            self._log_timing("history", start_time, success=True, extra=extra)
            return result
        except Exception as exc:
            self._log_timing("history", start_time, success=False, exc=exc, extra=extra)
            raise

    def close(self) -> None:
        """Close the storage."""
        start_time = time.perf_counter()
        try:
            self._save_id_map()
            self._log_timing("close", start_time, success=True)
        except Exception as exc:
            self._log_timing("close", start_time, success=False, exc=exc)
            raise

    def __enter__(self):
        """Context manager entry."""
        self._init_storage()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()