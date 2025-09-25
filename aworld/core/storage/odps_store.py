# coding: utf-8
# Copyright (c) 2025 inclusionAI.
from odps.models import Table
from typing import Any, List, Dict

from aworld.config import StorageConfig
from aworld.core.storage.base import DataBlock, DataItem, Storage
from aworld.core.storage.condition import ConditionBuilder, Condition
from aworld.logs.util import logger


class OdpsConfig(StorageConfig):
    name = "odps"
    table_name: str
    project: str
    endpoint: str
    access_id: str
    access_key: str


class OdpsSQLBuilder(ConditionBuilder):
    def _build_condition(self, condition: Condition) -> str:
        if condition is None:
            return ""

        if "field" in condition and "op" in condition:
            field = condition["field"].split('.')[-1]
            op = condition["op"]
            value = condition.get("value")

            if op == "eq":
                return f"{field} = {self._format_value(value)}"
            elif op == "ne":
                return f"{field} != {self._format_value(value)}"
            elif op == "gt":
                return f"{field} > {self._format_value(value)}"
            elif op == "gte":
                return f"{field} >= {self._format_value(value)}"
            elif op == "lt":
                return f"{field} < {self._format_value(value)}"
            elif op == "lte":
                return f"{field} <= {self._format_value(value)}"
            elif op == "in":
                return f"{field} IN ({self._format_value(value)})"
            elif op == "not_in":
                return f"{field} NOT IN ({self._format_value(value)})"
            elif op == "like":
                return f"{field} LIKE '{value}'"
            elif op == "not_like":
                return f"{field} NOT LIKE '{value}'"
            elif op == "is_null":
                return f"{field} IS NULL"
            elif op == "is_not_null":
                return f"{field} IS NOT NULL"

        elif "and_" in condition:
            return f"({' AND '.join(self._build_condition(c) for c in condition['and_'])})"
        elif "or_" in condition:
            return f"({' OR '.join(self._build_condition(c) for c in condition['or_'])})"

        return ""

    def _format_value(self, value: Any) -> str:
        if isinstance(value, str):
            return f"'{value}'"
        elif isinstance(value, (list, tuple)):
            return ", ".join(self._format_value(v) for v in value)
        return str(value)

    def build(self) -> str:
        return self._build_condition(self.condition)


class OdpsStorage(Storage):
    def __init__(self, conf: OdpsConfig):
        from aworld.utils.import_package import import_package
        import_package("odps")
        from odps import ODPS

        super().__init__(conf)
        self.odps = ODPS(conf.access_id, conf.access_key, conf.project, conf.endpoint)
        self.table_name = conf.table_name

    def backend(self):
        return self.odps

    def _get_table(self, table_name: str = None) -> Table:
        return self.backend().get_table(table_name if table_name else self.table_name)

    def _build_sql(self, condition: Condition, count: bool = False):
        build_sql = OdpsSQLBuilder(condition).build()
        if build_sql:
            build_sql = f" WHERE {build_sql}"
        sql = f"SELECT {'COUNT(*)' if count else '*'} FROM {self.table_name}{build_sql}"
        return sql

    async def create_block(self, block_id: str, overwrite: bool = True) -> bool:
        # block_id is a partition name
        if block_id:
            self._get_table().create_partition(block_id, if_not_exists=overwrite)
            return True
        else:
            logger.warning(f"{block_id} is None")
            return False

    async def delete_block(self, block_id: str, exists: bool = False) -> bool:
        if block_id:
            self._get_table().delete_partition(block_id, if_exists=exists)
            return True
        else:
            logger.warning(f"{block_id} is None")
            return False

    async def get_block(self, block_id: str) -> DataBlock:
        if block_id:
            partition = self._get_table().get_partition(block_id)
            return DataBlock(id=block_id, meta_info={block_id: partition})
        else:
            logger.warning(f"{block_id} is None")
            return DataBlock(id='')

    async def create_data(self, data: DataItem, block_id: str = None, overwrite: bool = True) -> bool:
        block_id = data.block_id if data.block_id else block_id
        block_id = str(block_id)
        if not self._get_table().exist_partition(block_id):
            await self.create_block(block_id)

        self.backend().write_table(block_id, [data.value], partition_cols=[block_id], create_partition=True)
        return True

    async def create_datas(self, data: List[DataItem], block_id: str = None, overwrite: bool = True) -> bool:
        block_id = data[0].block_id if data[0].block_id else block_id
        block_id = str(block_id)
        if not self._get_table().exist_partition(block_id):
            await self.create_block(block_id)

        self.backend().write_table(block_id, [d.value for d in data], partition_cols=[block_id], create_partition=True)
        return True

    async def get_data_items(self, block_id: str = None) -> List[DataItem]:
        block_id = str(block_id)
        df = self._get_table().get_partition(block_id).to_df()
        return df

    async def select_data(self, condition: Condition = None) -> List[DataItem]:
        sql = self._build_sql(condition)
        results = []
        with self.odps.execute_sql(sql).open_reader(tunnel=True) as reader:
            for record in reader:
                results.append(record)
            return results

    async def size(self, condition: Condition = None) -> int:
        sql = self._build_sql(condition, count=True)
        with self.odps.execute_sql(sql).open_reader() as reader:
            return reader[0]["count"]

    async def update_data(self, data: DataItem, block_id: str = None, exists: bool = False) -> bool:
        # unsupported
        return False

    async def delete_data(self,
                          data_id: str = None,
                          data: DataItem = None,
                          block_id: str = None,
                          exists: bool = False) -> bool:
        # unsupported
        return False

    async def delete_all(self):
        # unsupported
        return
