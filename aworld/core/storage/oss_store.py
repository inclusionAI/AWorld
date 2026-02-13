# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import json
from typing import List, Tuple

from oss2.models import ListObjectsResult

from aworld.core.storage.base import DataItem, DataBlock, Storage
from aworld.core.storage.condition import Condition, ConditionFilter
from aworld.core.storage.data import Data
from aworld.core.storage.file_store import FileConfig
from aworld.logs.util import logger
from aworld.utils.serialized_util import to_serializable


class OssConfig(FileConfig):
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
        key, content = await self._encode_data(data, block_id)
        self._get_bucket().put_object(key, content)
        logger.info(f"Data key={key}")
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        return await self.create_data(data, block_id)

    async def _encode_data(self, data: DataItem, block_id: str = None) -> Tuple[str, bytes]:
        block_id = data.block_id if data.block_id else block_id
        block_id = str(block_id)
        data_id = data.id
        if self.conf.record_value_only and isinstance(data, Data):
            data = data.value

        if isinstance(data, str):
            content = data.encode('utf-8')
        else:
            content = json.dumps(to_serializable(data), ensure_ascii=False).encode('utf-8')
        return f"{block_id}/{data_id}", content

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        block_id = str(block_id)
        self._get_bucket().delete_object(f"{block_id}/{data_id}")
        return True

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        res: ListObjectsResult = self._get_bucket().list_objects(block_id)
        obj_list = res.object_list
        results = []
        for obj in obj_list:
            data = await self.get_data_item(block_id, obj.key)
            if data:
                data.meta_info = {"size": obj.size, "last_modified": obj.last_modified, "etag": obj.etag}
                results.append(data)

        return results

    async def get_data_item(self, block_id: str = None, data_id: str = None) -> DataItem:
        if data_id:
            if self._get_bucket().object_exists(f"{block_id}/{data_id}"):
                res = self._get_bucket().get_object(f"{block_id}/{data_id}")
                data = res.read()
                return Data(value=data)
            else:
                return None
        else:
            res = await self.get_data_items(block_id)
            return res[0] if res else None

    async def list_items(self, block_id: str = None) -> List[str]:
        block_id = str(block_id)
        res: ListObjectsResult = self._get_bucket().list_objects(block_id)
        obj_list = res.object_list
        return [obj.key for obj in obj_list]

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
        block_id = str(block_id)
        self._get_bucket().delete_object(f"{block_id}")
        return True

    async def get_block(self, block_id: str) -> DataBlock:
        # unsupported
        return None
