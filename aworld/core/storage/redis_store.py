# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from typing import Dict, List

from aworld.config import StorageConfig
from aworld.core.storage.base import DataItem, Storage
from aworld.core.storage.condition import Condition, ConditionBuilder
from aworld.core.storage.data import DataBlock
from aworld.logs.util import logger
from aworld.utils.import_package import import_package

import_package("redis")
import redis
from redis import Redis
from redis.commands.search.field import TextField, NumericField, TagField
from redis.commands.search.index_definition import IndexDefinition, IndexType
from redis.commands.search.query import Query


class RedisConfig(StorageConfig):
    name: str = "redis"
    host: str = 'localhost'
    port: int = 6379
    db: int = 0
    password: str = None
    key_prefix: str = 'AWORLD:RB:'
    index_name: str = 'idx:AWORLD:RB'
    recreate_idx_if_exists = False
    data_schema: Dict[str, type] = {}


class RedisSearchQueryBuilder(ConditionBuilder):
    def _build_condition(self, condition: Condition) -> str:
        if condition is None:
            return ""

        if "field" in condition and "op" in condition:
            field = condition["field"].split('.')[-1]
            op = condition["op"]
            value = condition.get("value")

            if op == "eq":
                return f"@{field}:{{{value}}}"
            elif op == "ne":
                return f"-@{field}:{{{value}}}"
            elif op == "gt":
                return f"@{field}:[{value} +inf]"
            elif op == "gte":
                return f"@{field}:[{value} +inf]"
            elif op == "lt":
                return f"@{field}:[-inf {value}]"
            elif op == "lte":
                return f"@{field}:[-inf {value}]"
            elif op == "in":
                return f"@{field}:{{{'|'.join(str(v) for v in value)}}}"
            elif op == "not_in":
                return f"-@{field}:{{{'|'.join(str(v) for v in value)}}}"
            elif op == "like":
                return f"@{field}:*{value}*"
            elif op == "not_like":
                return f"-@{field}:*{value}*"
            elif op == "is_null":
                return f"-@{field}:*"
            elif op == "is_not_null":
                return f"@{field}:*"
        elif "and_" in condition:
            conditions = [self._build_condition(c) for c in condition["and_"]]
            return " ".join(conditions)
        elif "or_" in condition:
            conditions = [self._build_condition(c) for c in condition["or_"]]
            return f"({'|'.join(conditions)})"
        return ""

    def build(self) -> Query:
        query_str = self._build_condition(self.condition)
        logger.info(f"redis search query: {query_str}")
        return Query(query_str)


class RedisStorage(Storage):
    def __init__(self, conf: RedisConfig):
        super().__init__(conf)
        self._redis = Redis(host=conf.host, port=conf.port, db=conf.db, password=conf.password)
        self._key_prefix = conf.key_prefix
        self._index_name = conf.index_name
        self._recreate_idx_if_exists = conf.recreate_idx_if_exists
        self._create_index(conf.data_schema)

    def backend(self) -> Redis:
        return self._redis

    def _create_index(self, schema: dict):
        try:
            existing_indices = self._redis.execute_command('FT._LIST')
            if self._index_name.encode('utf-8') in existing_indices:
                logger.info(f"Index {self._index_name} already exists")
                if self._recreate_idx_if_exists:
                    self._redis.ft(self._index_name).dropindex()
                    logger.info(f"Index {self._index_name} dropped")
                else:
                    return

            fields = []
            for k, v in schema.items():
                if isinstance(v, (int, float)):
                    fields.append(NumericField(k))
                else:
                    fields.append(TagField(k))

            self._redis.ft(self._index_name).create_index(
                fields=fields,
                definition=IndexDefinition(prefix=[self._key_prefix], index_type=IndexType.HASH)
            )
        except redis.exceptions.ResponseError as e:
            logger.error(f"Create index {self._index_name} failed. {e}")

    def _get_object_key(self, key: str) -> str:
        return f"{self._key_prefix}{key}"

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        key = self._get_object_key(data.id)
        await self.backend().hset(key, mapping=data.to_json())
        return True

    async def create_datas(self, datas: List[DataItem], block_id: str = None, overwrite: bool = True) -> bool:
        pipeline = self._redis.pipeline()
        for data in datas:
            if not data or not data.exp_meta:
                continue
            key = self._get_object_key(data.id)
            await pipeline.hset(key, mapping=data.to_json())
        pipeline.execute()
        return True

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        return await self.create_data(data, block_id, exists)

    async def update_datas(self, data: List[DataItem], block_id: str = None, exists: bool = False) -> bool:
        return await self.create_datas(data, block_id, exists)

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        key = self._get_object_key(data_id)
        await self.backend().delete(key)
        return True

    async def delete_datas(self, datas: List[str], block_id: str = None, overwrite: bool = True) -> bool:
        pipeline = self._redis.pipeline()
        for data_id in datas:
            key = self._get_object_key(data_id)
            await pipeline.delete(key)
        pipeline.execute()
        return True

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        return self._redis.ft(self._index_name).search(Query("*")).docs

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        if not condition:
            result = self._redis.ft(self._index_name).search(Query("*"))
        else:
            query_builder = RedisSearchQueryBuilder(condition)
            query = query_builder.build()
            result = self._redis.ft(self._index_name).search(query)
        return result.docs

    async def size(self, condition: Condition = None) -> int:
        if not condition:
            return self._redis.ft(self._index_name).info()['num_docs']

        query_builder = RedisSearchQueryBuilder(condition)
        query = query_builder.build()
        return self._redis.ft(self._index_name).search(query).total

    async def delete_all(self):
        keys = await self.backend().keys(f"{self._key_prefix}*")
        if keys:
            await self.backend().delete(*keys)

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        # unsupported
        return False

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        # unsupported
        return False

    async def get_block(self, block_id: str) -> DataBlock:
        # unsupported
        return None
