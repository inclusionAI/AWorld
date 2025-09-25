# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import multiprocessing
import traceback
import pickle
from typing import Dict, List

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem
from aworld.core.storage.condition import Condition, ConditionFilter
from aworld.core.storage.data import DataBlock
from aworld.logs.util import logger


class MultiProcessConfig(StorageConfig):
    name: str = "multi_process"
    max_capacity: int = 10000


class MultiProcessStorage(Storage):
    def __init__(self, conf: MultiProcessConfig = None):
        if not conf:
            conf = MultiProcessConfig()
        super().__init__(conf)
        manager = multiprocessing.Manager()
        # block_id -> [data1, data2,...]
        self._data: Dict[str, List[str]] = manager.dict()
        self._fifo_queue: List[str] = manager.list()
        self._max_capacity = conf.max_capacity
        self._lock: multiprocessing.Lock = manager.Lock()

    def backend(self):
        return self

    def _save_to_shared_memory(self, datas: List[DataItem], block_id: str):
        serialized_data = pickle.dumps(datas)
        try:
            if not self._data.get(block_id):
                shm = multiprocessing.shared_memory.SharedMemory(create=True, name=block_id, size=len(serialized_data))
                shm.buf[:len(serialized_data)] = serialized_data
                self._data[block_id] = shm.name
                shm.close()
                return

            shm = multiprocessing.shared_memory.SharedMemory(name=block_id, create=False)
            if len(serialized_data) > shm.size:
                shm.close()
                shm.unlink()
                shm = multiprocessing.shared_memory.SharedMemory(create=True, name=block_id, size=len(serialized_data))
                shm.buf[:len(serialized_data)] = serialized_data
                self._data[block_id] = shm.name
            else:
                shm.buf[:len(serialized_data)] = serialized_data
        except FileNotFoundError:
            shm = multiprocessing.shared_memory.SharedMemory(create=True, name=block_id, size=len(serialized_data))
            shm.buf[:len(serialized_data)] = serialized_data
            self._data[block_id] = shm.name
        shm.close()

    def _load_from_shared_memory(self, block_id: str) -> List[DataItem]:
        if block_id not in self._data:
            return []

        try:
            try:
                multiprocessing.shared_memory.SharedMemory(name=block_id, create=False)
            except FileNotFoundError:
                return []

            shm = multiprocessing.shared_memory.SharedMemory(name=block_id)
            datas = pickle.loads(shm.buf.tobytes())
            shm.close()
            return datas
        except Exception as e:
            stack_trace = traceback.format_exc()
            logger.error(f"_load_from_shared_memory error: {e}\nStack trace:\n{stack_trace}")
            return []

    def _delete_from_shared_memory(self, block_id: str, data_id: str = None, data: DataItem = None):
        if block_id not in self._data:
            logger.warning(f"{block_id} not in datas")
            return

        try:
            shm = multiprocessing.shared_memory.SharedMemory(name=block_id)
            if data_id or data:
                datas = pickle.loads(shm.buf.tobytes())
                for data_item in datas:
                    if hasattr(data_item, "id"):
                        if data_item.id == data_id:
                            datas.remove(data_item)
                            break
                    elif data == data_item:
                        datas.remove(data_item)
                        break

                shm.close()
                self._save_to_shared_memory(datas, block_id)
            else:
                shm.close()
                shm.unlink()
                del self._data[block_id]
        except FileNotFoundError:
            print(traceback.format_exc())

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        with self._lock:
            existing_data = self._load_from_shared_memory(block_id)
            existing_data.append(data)
            self._save_to_shared_memory(existing_data, block_id)
            self._fifo_queue.append(block_id)
        return True

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        block_id = str(block_id)
        with self._lock:
            self._delete_from_shared_memory(block_id, data_id=data_id, data=data)
            return True

    async def size(self, condition: Condition = None) -> int:
        return len(await self.select_data(condition))

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        with self._lock:
            datas: List[DataItem] = []
            for key in list(self._data.keys()):
                datas.extend(self._load_from_shared_memory(key))
            if condition:
                datas = ConditionFilter(condition).filter(datas)
            return datas

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        with self._lock:
            return self._load_from_shared_memory(block_id)

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = str(data.block_id if hasattr(data, "block_id") and data.block_id else block_id)
        data_id = None
        if hasattr(data, "id"):
            data_id = data.id
        with self._lock:
            self._delete_from_shared_memory(block_id, data_id=data_id, data=data)
            self._save_to_shared_memory([data], block_id)
        return True

    async def get_block(self, block_id: str) -> DataBlock:
        # unsupported
        return DataBlock(id=str(block_id))

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        with self._lock:
            shm = multiprocessing.shared_memory.SharedMemory(name=str(block_id), create=False)
            shm.close()
            shm.unlink()
            return True

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        try:
            shm = multiprocessing.shared_memory.SharedMemory(name=str(block_id), create=False)
            if overwrite:
                shm.close()
                shm.unlink()
                self._data.pop(block_id)
            return True
        except FileNotFoundError:
            return True

    async def delete_all(self):
        # unsupported
        return
