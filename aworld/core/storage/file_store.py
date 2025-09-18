# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import hashlib
import json
from pathlib import Path
from typing import List

import aiofiles

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition, ConditionFilter
from aworld.core.storage.data import Data
from aworld.logs.util import logger


class FileConfig(StorageConfig):
    name: str = "file"
    root_dir: str = "."


class FileStorage(Storage[DataItem]):
    """Simple local file-based storage.
    TODO: add index

    Layout: root_dir/ -> block_id/ -> meta.json
                                   -> data/ -> data_id.json

    Note: Does not support data sharding, structuring, etc.
    """

    def __init__(self, conf: FileConfig = None):
        if not conf:
            conf = FileConfig()
        super().__init__(conf)
        self._blocks_dir = Path(conf.root_dir).resolve()
        self._blocks_dir.mkdir(parents=True, exist_ok=True)

    def backend(self):
        return self

    async def delete_all(self):
        """Delete ALL data under the root directory (use with caution)."""
        if self._blocks_dir.exists():
            for child in self._blocks_dir.glob("**/*"):
                try:
                    if child.is_file():
                        child.unlink(missing_ok=True)
                except Exception as err:
                    logger.warning(f"delete_all: failed to remove file {child}: {err}")
            # Remove empty directories bottom-up
            for dir_path in sorted([p for p in self._blocks_dir.glob("**/*") if p.is_dir()], key=lambda p: len(str(p)),
                                   reverse=True):
                try:
                    dir_path.rmdir()
                except Exception:
                    pass

    def _block_dir(self, block_id: str) -> Path:
        return self._blocks_dir / block_id

    def _block_meta_path(self, block_id: str) -> Path:
        return self._block_dir(block_id) / "meta.json"

    def _data_dir(self, block_id: str) -> Path:
        return self._block_dir(block_id) / "data"

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        block_id = str(block_id)
        self._block_dir(block_id).mkdir(parents=True, exist_ok=True)
        self._data_dir(block_id).mkdir(parents=True, exist_ok=True)

        block_meta = self._block_meta_path(block_id)
        if block_meta.exists() and not overwrite:
            return False

        meta = DataBlock(id=block_id)
        try:
            async with aiofiles.open(block_meta, "w", encoding="utf-8") as f:
                await f.write(
                    json.dumps(obj={"id": meta.id, "create_at": meta.create_at, "meta_info": meta.meta_info},
                               ensure_ascii=False)
                )
            return True
        except Exception as e:
            logger.error(f"create_block: failed to write meta for {block_id}: {e}")
            return False

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        block_dir = self._block_dir(str(block_id))
        if not block_dir.exists():
            return exists
        try:
            for child in block_dir.glob("**/*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            # Remove empty dirs
            for dir_path in sorted([p for p in block_dir.glob("**/*") if p.is_dir()], key=lambda p: len(str(p)),
                                   reverse=True):
                try:
                    dir_path.rmdir()
                except Exception:
                    pass
            block_dir.rmdir()
            return True
        except Exception as e:
            logger.error(f"delete_block: failed for {block_id}: {e}")
            return False

    async def get_block(self, block_id: str) -> DataBlock:
        block_id = str(block_id)
        block_meta = self._block_meta_path(block_id)
        if not block_meta.exists():
            await self.create_block(block_id, overwrite=False)
        if block_meta.exists():
            try:
                with open(block_meta, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return DataBlock(id=data.get("id", block_id), create_at=data.get("create_at", 0.0),
                                 meta_info=data.get("meta_info", {}))
            except Exception as e:
                logger.warning(f"get_block: failed to read meta for {block_id}, err={e}")
        return DataBlock(id=block_id)

    def _data_path(self, block_id: str, data_id: str) -> Path:
        return self._data_dir(block_id) / f"{data_id}.json"

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        data_id = data.id if hasattr(data, "id") else hashlib.md5(data.model_dump_json().encode()).hexdigest()
        await self.create_block(block_id, overwrite=False)
        path = self._data_path(block_id, data_id)
        if path.exists() and not overwrite:
            logger.warning(f"create_data: exists and overwrite=False, block_id={block_id}, id={data_id}")
            return False

        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data.model_dump(), ensure_ascii=False))
            logger.info(f"create_data: {data_id} store to the {path}")
            return True
        except Exception as e:
            logger.warning(f"create_data: failed for block={block_id}, id={data_id}, err={e}")
            return False

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        data_id = data.id if hasattr(data, "id") else hashlib.md5(data.model_dump_json().encode()).hexdigest()
        path = self._data_path(block_id, data_id)
        if not path.exists() and not exists:
            return await self.create_data(data, block_id=block_id, overwrite=True)

        try:
            async with aiofiles.open(path, "w", encoding="utf-8") as f:
                await f.write(json.dumps(data.model_dump(), ensure_ascii=False))
            logger.info(f"update_data: {data_id} overwrite store to the {path}")
            return True
        except Exception as e:
            logger.warning(f"update_data: failed for block={block_id}, id={data_id}, err={e}")
            return False

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        # data_id can not None
        data_id = data_id if data_id else hashlib.md5(data.model_dump_json().encode()).hexdigest()
        block_id = str(block_id)
        path = self._data_path(block_id, data_id)
        if not path.exists():
            return exists

        try:
            path.unlink(missing_ok=True)
            logger.info(f"delete_data: {path} deleted")
            return True
        except Exception as e:
            logger.warning(f"delete_data: failed for block={block_id}, id={data_id}, err={e}")
            return False

    def _load_all_data_in_block(self, block_id: str) -> List[DataItem]:
        items: List[DataItem] = []
        data_dir = self._data_dir(block_id)
        if not data_dir.exists():
            return items

        for fp in data_dir.glob("*.json"):
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                item = Data.from_dict(data)
                # BaseModel
                if not item.value:
                    item = data
                elif not item.block_id:
                    item.block_id = block_id
                items.append(item)
            except Exception as e:
                logger.warning(f"failed to parse {fp}: {e}")
        return items

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        # Scan all blocks
        items: List[DataItem] = []
        for block_meta in self._blocks_dir.glob("*/meta.json"):
            block_id = block_meta.parent.name
            items.extend(self._load_all_data_in_block(block_id))
        return ConditionFilter(condition).filter(items, condition)

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        return self._load_all_data_in_block(block_id)

    async def size(self, condition: Condition = None) -> int:
        return len(await self.select_data(condition))
