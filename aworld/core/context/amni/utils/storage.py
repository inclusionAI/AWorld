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
    递归序列化对象为 JSON 可序列化的格式。
    支持 Pydantic BaseModel、具有 to_dict 或 model_dump 方法的对象。
    
    Args:
        obj: 要序列化的对象
        
    Returns:
        JSON 可序列化的对象（dict、list 或基本类型）
    """
    # 处理 Pydantic BaseModel 或具有 model_dump 方法的对象
    # 处理具有 to_dict 方法的对象
    if hasattr(obj, 'to_dict'):
        result = obj.to_dict()
        # 递归处理嵌套对象
        if isinstance(result, dict):
            return {k: serialize_for_json(v) for k, v in result.items()}
        elif isinstance(result, (list, tuple)):
            return [serialize_for_json(item) for item in result]
        return result
    elif hasattr(obj, 'model_dump'):
        return obj.model_dump()
    # 处理字典
    elif isinstance(obj, dict):
        return {k: serialize_for_json(v) for k, v in obj.items()}
    # 处理列表和元组
    elif isinstance(obj, (list, tuple)):
        return [serialize_for_json(item) for item in obj]
    # 基本类型直接返回
    return obj


class TrajectoryJSONEncoder(json.JSONEncoder):
    """
    JSON 编码器，用于序列化包含 TrajectoryItem 等 Pydantic 模型的对象。
    
    使用方法：
        json.dumps(data, cls=TrajectoryJSONEncoder, ensure_ascii=False)
        
    或者使用 serialize_for_json 函数预处理：
        json.dumps(serialize_for_json(data), ensure_ascii=False)
    """
    def default(self, obj):
        # 处理 Pydantic BaseModel
        if hasattr(obj, 'model_dump'):
            result = obj.model_dump()
            # 递归处理嵌套的 Pydantic 对象
            return serialize_for_json(result)
        # 处理具有 to_dict 方法的对象
        elif hasattr(obj, 'to_dict'):
            result = obj.to_dict()
            # 递归处理嵌套对象
            return serialize_for_json(result)
        # 处理其他无法序列化的对象，转换为字符串
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
        oss_prefix: Prefix path in OSS bucket for storing trajectories
        access_key_id: OSS access key ID (or from env: OSS_ACCESS_KEY_ID)
        access_key_secret: OSS access key secret (or from env: OSS_ACCESS_KEY_SECRET)
        endpoint: OSS endpoint (or from env: OSS_ENDPOINT)
        bucket_name: OSS bucket name (or from env: OSS_BUCKET_NAME)
        enable_export: Whether to enable OSS export
    
    Returns:
        OssTrajectoryStorage instance
    """
    global _storage_instance
    if _storage_instance is None:
        _storage_instance = FileTrajectoryStorage(
            base_dir=base_dir
        )
    return _storage_instance

