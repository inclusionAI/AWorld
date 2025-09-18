# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import List

from aworld.config import StorageConfig
from aworld.core.storage.base import Storage, DataItem, DataBlock
from aworld.core.storage.condition import Condition, ConditionFilter


class OssConfig(StorageConfig):
    name: str = "oss"
    access_id: str
    access_key: str
    endpoint: str
    bucket: str


class OssStorage(Storage):
    def __init__(self, conf: OssConfig):
        from aworld.utils.import_package import import_package
        import_package("oss2")
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
        block_id = str(block_id)
        self._get_bucket().put_object(f"{block_id}_{data.id}", data)
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        block_id = data.block_id if data.block_id else block_id
        block_id = str(block_id)
        self._get_bucket().put_object(f"{block_id}_{data.id}", data)
        return True

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        block_id = str(block_id)
        self._get_bucket().delete_object(f"{block_id}_{data_id}")
        return True

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        return self._get_bucket().list_objects(block_id)

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        res = self._get_bucket().list_objects()
        return ConditionFilter(condition).filter(res, condition)

    async def size(self, condition: Condition = None) -> int:
        return len(await self.select_data(condition))

    async def delete_all(self):
        # unsupported
        return

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        # unsupported
        return False

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        # unsupported
        return False

    async def get_block(self, block_id: str) -> DataBlock:
        # unsupported
        return None
