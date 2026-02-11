# coding: utf-8
import json
import logging
import os
from typing import List, Dict, Any

from aworld.config import StorageConfig
from aworld.core.storage.base import DataItem
from aworld.core.storage.condition import Condition, ConditionFilter
from aworld.core.storage.data import Data, DataBlock
from aworld.dataset import TrajectoryStorage

logger = logging.getLogger(__name__)


def serialize_for_json(obj):
    """
    Recursively serialize objects to JSON-serializable format.
    Supports Pydantic BaseModel and objects with to_dict or model_dump methods.
    
    Args:
        obj: Object to serialize
        
    Returns:
        JSON-serializable object (dict, list, or basic types)
    """
    # Handle objects with to_dict method
    if hasattr(obj, 'to_dict'):
        result = obj.to_dict()
        # Recursively process nested objects
        if isinstance(result, dict):
            return {k: serialize_for_json(v) for k, v in result.items()}
        elif isinstance(result, (list, tuple)):
            return [serialize_for_json(item) for item in result]
        return result
    # Handle Pydantic BaseModel or objects with model_dump method
    elif hasattr(obj, 'model_dump'):
        return obj.model_dump()
    # Handle dictionaries
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    # Handle lists and tuples
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    # Basic types are returned directly
    return obj


class TrajectoryJSONEncoder(json.JSONEncoder):
    """
    JSON encoder for serializing objects containing Pydantic models like TrajectoryItem.
    
    Usage:
        json.dumps(data, cls=TrajectoryJSONEncoder, ensure_ascii=False)
        
    Or preprocess with serialize_for_json function:
        json.dumps(serialize_for_json(data), ensure_ascii=False)
    """
    def default(self, obj):
        # Handle Pydantic BaseModel
        if hasattr(obj, 'model_dump'):
            result = obj.model_dump()
            # Recursively process nested Pydantic objects
            return serialize_for_json(result)
        # Handle objects with to_dict method
        elif hasattr(obj, 'to_dict'):
            result = obj.to_dict()
            # Recursively process nested objects
            return serialize_for_json(result)
        # Handle other non-serializable objects by converting to string
        return str(obj)


class FileTrajectoryStorage(TrajectoryStorage):
    """Simple file-based trajectory storage."""

    def __init__(self, base_dir: str = "data/trajectories"):
        super().__init__(StorageConfig(name="file_trajectory_storage"))
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self._blocks: Dict[str, DataBlock] = {}
        self._data: Dict[str, Dict[str, Data]] = {}

    def _get_file_path(self, block_id: str) -> str:
        return os.path.join(self.base_dir, f"{block_id}.json")

    def _serialize(self, obj):
        """Recursively serialize objects for JSON."""
        return serialize_for_json(obj)

    def _save_block(self, block_id: str):
        data = {k: self._serialize(v) for k, v in self._data.get(block_id, {}).items()}
        with open(self._get_file_path(block_id), 'w') as f:
            json.dump(data, f, ensure_ascii=False, default=str)

    def _load_block(self, block_id: str) -> Dict[str, Data]:
        path = self._get_file_path(block_id)
        if os.path.exists(path):
            with open(path, 'r') as f:
                raw = json.load(f)
            return {k: Data(**v) for k, v in raw.items()}
        return {}

    def backend(self):
        return self.base_dir

    async def delete_all(self):
        self._blocks.clear()
        self._data.clear()
        for f in os.listdir(self.base_dir):
            if f.endswith('.json'):
                os.remove(os.path.join(self.base_dir, f))

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        if block_id in self._blocks and not overwrite:
            return False
        self._blocks[block_id] = DataBlock(id=block_id)
        self._data.setdefault(block_id, {})
        return True

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        if block_id not in self._blocks:
            return not exists
        del self._blocks[block_id]
        self._data.pop(block_id, None)
        path = self._get_file_path(block_id)
        if os.path.exists(path):
            os.remove(path)
        return True

    async def get_block(self, block_id: str) -> DataBlock:
        return self._blocks.get(block_id)

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        bid = block_id or getattr(data, 'block_id', None) or 'default'
        if bid not in self._data:
            await self.create_block(bid)
        if data.id in self._data[bid] and not overwrite:
            return False
        self._data[bid][data.id] = data
        self._save_block(bid)
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        bid = block_id or getattr(data, 'block_id', None) or 'default'
        if bid not in self._data:
            if exists:
                return False
            await self.create_block(bid)
        self._data[bid][data.id] = data
        self._save_block(bid)
        return True

    async def delete_data(self, data_id: str, data: DataItem = None, block_id: str = None, exists: bool = False) -> bool:
        bid = block_id or (getattr(data, 'block_id', None) if data else None) or 'default'
        if bid not in self._data or data_id not in self._data[bid]:
            return not exists
        del self._data[bid][data_id]
        self._save_block(bid)
        return True

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        all_data = []
        for block_data in self._data.values():
            all_data.extend(block_data.values())
        if condition:
            return ConditionFilter(condition).filter(all_data, condition)
        return all_data

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        if block_id:
            if block_id not in self._data:
                self._data[block_id] = self._load_block(block_id)
            return list(self._data.get(block_id, {}).values())
        all_data = []
        for block_data in self._data.values():
            all_data.extend(block_data.values())
        return all_data

    async def size(self, condition: Condition = None) -> int:
        return len(await self.select_data(condition))


def format_trajectory_to_string(trajectory: List[Dict[str, Any]]) -> str:
    """
    Format a structured trajectory list into a readable string for LLM prompts.
    """
    if not trajectory:
        return "No trajectory data available."

    result = []
    for i, item in enumerate(trajectory):
        role = item.get("role", "unknown")
        content = item.get("content", "")
        # Handle complex content (e.g. tool calls) if necessary
        # For now assume simple role/content structure or adapting from SAR

        # Check for SAR format or simple chat format
        if "state" in item and "action" in item:
            # SAR Format
            state_input = item.get("state", {}).get("input", "")
            action_content = item.get("action", {}).get("content", "")
            reward = item.get("reward", {})
            result.append(f"Step {i+1}:")
            result.append(f"  Input: {state_input}")
            result.append(f"  Action: {action_content}")
            if reward:
                result.append(f"  Result: {reward}")
        else:
            # Simple format
            result.append(f"[{role}] {content}")

    return "\n".join(result)


# Global instance for easy access
_storage_instance = None


def get_storage(base_dir) -> FileTrajectoryStorage:
    """
    Get or create global storage instance.
    
    Args:
        base_dir: Base directory path for storing trajectories
    
    Returns:
        FileTrajectoryStorage instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = FileTrajectoryStorage(
            base_dir=base_dir
        )
    return _storage_instance

