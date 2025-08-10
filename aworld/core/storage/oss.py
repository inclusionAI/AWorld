# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List, Union

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition


class OssConfig(StorageConfig):
    access_id: str
    access_key: str
    endpoint: str
    bucket: str


class OssStorage(Storage):
    def __init__(self, conf: OssConfig):
        import oss2
        
        super().__init__(conf)
        self.auth = oss2.Auth(conf.access_id, conf.access_key)
        self.bucket = oss2.Bucket(self.auth, conf.endpoint, conf.bucket)

    def backend(self):
        return self.auth

    def _get_bucket(self, bucket: str = None):
        import oss2
        return oss2.Bucket(self.auth, self.conf.endpoint, bucket) if bucket else self.bucket

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = data.block_id if data.block_id else block_id
        self._get_bucket().put_object(f"{block_id}_{data.id}", data)
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = data.block_id if data.block_id else block_id
        self._get_bucket().put_object(f"{block_id}_{data.id}", data)
        return True

    async def delete_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = data.block_id if data.block_id else block_id
        self._get_bucket().delete_object(f"{block_id}_{data.id}")
        return True

    async def get_data(self, block_id: str = None) -> List[DataItem]:
        return self._get_bucket().list_objects(block_id)

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        pass

    async def size(self, condition: Condition = None) -> int:
        return len(await self.select_data(condition))
