import multiprocessing
import traceback
import pickle
from typing import Dict, List

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem
from aworld.core.storage.condition import Condition, ConditionFilter
from aworld.logs.util import logger


class MultiProcessConfig(StorageConfig):
    max_capacity: int = 10000


class MultiProcessStorage(Storage):
    def __init__(self, conf: MultiProcessConfig):
        super().__init__(conf)
        manager = multiprocessing.Manager()
        self._data: Dict[str, List[str]] = manager.dict()
        self._fifo_queue: List[str] = manager.list()
        self._max_capacity = conf.max_capacity
        self._lock: multiprocessing.Lock = manager.Lock()

    def backend(self):
        return self

    def _save_to_shared_memory(self, data: DataItem):
        serialized_data = pickle.dumps(data)
        data_id = data.id
        try:
            if data_id not in self._data or not self._data[data_id]:
                shm = multiprocessing.shared_memory.SharedMemory(create=True, size=len(serialized_data))
                shm.buf[:len(serialized_data)] = serialized_data
                self._data[data_id] = shm.name
                shm.close()
                return
            shm = multiprocessing.shared_memory.SharedMemory(
                name=self._data[data_id], create=False)
            if len(serialized_data) > shm.size:
                shm.close()
                shm.unlink()
                shm = multiprocessing.shared_memory.SharedMemory(create=True, size=len(serialized_data))
                shm.buf[:len(serialized_data)] = serialized_data
                self._data[data_id] = shm.name
            else:
                shm.buf[:len(serialized_data)] = serialized_data
        except FileNotFoundError:
            shm = multiprocessing.shared_memory.SharedMemory(create=True, size=len(serialized_data))
            shm.buf[:len(serialized_data)] = serialized_data
            self._data[data_id] = shm.name
        shm.close()

    def _load_from_shared_memory(self, data_id):
        if data_id not in self._data or not self._data[data_id]:
            return []

        try:
            try:
                multiprocessing.shared_memory.SharedMemory(name=self._data[data_id], create=False)
            except FileNotFoundError:
                return []

            shm = multiprocessing.shared_memory.SharedMemory(name=self._data[data_id])
            data = pickle.loads(shm.buf.tobytes())
            shm.close()
            return data
        except Exception as e:
            stack_trace = traceback.format_exc()
            logger.error(f"_load_from_shared_memory error: {e}\nStack trace:\n{stack_trace}")
            return []

    def _delete_from_shared_memory(self, data_id):
        if data_id not in self._data or not self._data[data_id]:
            return

        try:
            shm = multiprocessing.shared_memory.SharedMemory(name=self._data[data_id])
            shm.close()
            shm.unlink()
            del self._data[data_id]
        except FileNotFoundError:
            pass

    async def create_data(self, data: DataItem, data_id: str = None, overwrite: bool = True) -> bool:
        data_id = data.id
        with self._lock:
            current_size = sum(len(self._load_from_shared_memory(data_id)) for data_id in self._data.keys())
            while current_size >= self._max_capacity and self._fifo_queue:
                old_id = self._fifo_queue.pop(0)
                if old_id in self._data.keys():
                    current_size -= len(self._load_from_shared_memory(old_id))
                    self._delete_from_shared_memory(old_id)

            existing_data = self._load_from_shared_memory(data_id)
            existing_data.append(data)
            self._save_to_shared_memory(existing_data)
            self._fifo_queue.append(data_id)
        return True

    async def create_datas(self, data: List[DataItem], block_id: str = None, overwrite: bool = True) -> bool:
        with self._lock:
            return await super().create_datas(data, block_id, overwrite)

    async def delete_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        with self._lock:
            self._delete_from_shared_memory(data.id)
            return True

    async def size(self, condition: Condition = None) -> int:
        with self._lock:
            return len(await self.select_data(condition))

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        with self._lock:
            datas = []
            datas.extend(self._load_from_shared_memory(data) for data in self._data.keys())

            if condition:
                datas = ConditionFilter(condition).filter(datas)
            return datas

    async def get_data(self, block_id: str = None) -> List[DataItem]:
        with self._lock:
            return self._load_from_shared_memory(block_id)
