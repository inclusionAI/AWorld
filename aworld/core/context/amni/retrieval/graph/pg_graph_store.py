import asyncio
import itertools
import json
import ssl
import traceback
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Union

import asyncpg
from asyncpg import Connection

from .base_graph_store import BaseGraphStore
from .base import GraphDBConfig
from aworld.logs.util import logger


@dataclass
class PostgreSQLDB:
    """PostgreSQL数据库连接管理"""
    
    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None
        self._initialization_lock = asyncio.Lock()
        self._is_initialized = False
    
    async def initdb(self):
        """初始化数据库连接池"""
        async with self._initialization_lock:
            if self._is_initialized:
                return
                
            try:
                # 构建连接参数
                connection_params = {
                    "host": self.config.get("host", "localhost"),
                    "port": self.config.get("port", 5432),
                    "user": self.config.get("user", "aworldcore"),
                    "password": self.config.get("password", "123456"),
                    "database": self.config.get("database", "aworldcore"),
                }
                
                # SSL配置
                if self.config.get("ssl_mode"):
                    connection_params["ssl"] = self._create_ssl_context()
                
                # 创建连接池
                self.pool = await asyncpg.create_pool(
                    **connection_params,
                    min_size=1,
                    max_size=10,
                    command_timeout=120,  # 增加命令超时时间
                    server_settings={
                        'application_name': 'aworldcore_graph_store',
                        'tcp_keepalives_idle': '600',
                        'tcp_keepalives_interval': '30',
                        'tcp_keepalives_count': '3',
                    },
                    # 连接超时设置
                    timeout=30,  # 连接超时30秒
                    max_queries=50000,  # 最大查询数
                    max_inactive_connection_lifetime=300.0,  # 非活跃连接最大生存时间
                )
                
                # 配置AGE扩展
                async with self.pool.acquire() as connection:
                    await self.configure_age_extension(connection)
                
                self._is_initialized = True
                    
            except Exception as e:
                self._is_initialized = False
                raise Exception(f"Failed to initialize PostgreSQL database: {e}")
    
    def _create_ssl_context(self) -> ssl.SSLContext | None:
        """创建SSL上下文"""
        try:
            ssl_mode = self.config.get("ssl_mode", "prefer")
            if ssl_mode == "disable":
                return None
            
            context = ssl.create_default_context()
            if ssl_mode == "require":
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE
            
            return context
        except Exception:
            return None
    
    @staticmethod
    async def configure_age_extension(connection: Connection) -> None:
        """配置Apache AGE扩展"""
        try:
            # 创建AGE扩展
            await connection.execute("CREATE EXTENSION IF NOT EXISTS age;")
            
            # 设置搜索路径
            await connection.execute("SET search_path = ag_catalog, public;")
            
        except Exception as e:
            # 如果AGE扩展不可用，记录警告但继续
            print(f"Warning: Apache AGE extension not available: {e}")
    
    async def query(self, sql: str, params: List[Any] = None, multirows: bool = False, 
                   with_age: bool = False, graph_name: str = None) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """执行查询"""
        if not self.pool:
            raise Exception("Database pool not initialized")
        
        connection = None
        try:
            connection = await self.pool.acquire()
            # 对于查询操作，使用只读事务
            async with connection.transaction(readonly=True):
                if with_age and graph_name:
                    # 设置AGE搜索路径
                    await connection.execute(f"SET search_path = ag_catalog, public;")
                
                if multirows:
                    rows = await connection.fetch(sql, *(params or []))
                    return [dict(row) for row in rows] if rows else []
                else:
                    row = await connection.fetchrow(sql, *(params or []))
                    return dict(row) if row else None
        except Exception as e:
            # 确保连接被正确释放
            raise e
        finally:
            if connection:
                try:
                    await self.pool.release(connection)
                except Exception as release_error:
                    print(f"Warning: Error releasing connection: {release_error}")
    
    async def execute(self, sql: str, data: Dict[str, Any] = None, upsert: bool = False, 
                     ignore_if_exists: bool = False, with_age: bool = False, graph_name: str = None):
        """执行SQL语句"""
        if not self.pool:
            raise Exception("Database pool not initialized")
        
        connection = None
        try:
            connection = await self.pool.acquire()
            # 开始事务
            async with connection.transaction():
                if with_age and graph_name:
                    # 设置AGE搜索路径
                    await connection.execute(f"SET search_path = ag_catalog, public;")
                
                try:
                    if data:
                        # Apache AGE 的 cypher 函数需要将参数作为单个字典传递
                        result = await connection.fetch(sql, data)
                    else:
                        result = await connection.fetch(sql)
                    
                    # 返回查询结果
                    return [dict(row) for row in result] if result else []
                except Exception as e:
                    if ignore_if_exists and "already exists" in str(e).lower():
                        # 忽略"已存在"错误
                        pass
                    else:
                        raise e
        except Exception as e:
            # 确保连接被正确释放
            raise e
        finally:
            if connection:
                try:
                    await self.pool.release(connection)
                except Exception as release_error:
                    print(f"Warning: Error releasing connection: {release_error}")
    
    async def close(self):
        """关闭连接池"""
        if self.pool:
            try:
                # 等待所有连接完成
                await asyncio.sleep(0.1)
                await self.pool.close()
                self._is_initialized = False
            except Exception as e:
                print(f"Warning: Error closing database pool: {e}")
            finally:
                self.pool = None


class ClientManager:
    """客户端管理器 - 改进的资源管理"""
    _instances: Dict[str, Any] = {"db": None, "ref_count": 0, "_lock": asyncio.Lock(), "_shutdown": False}

    @classmethod
    async def get_client(cls, config: dict[str, Any]) -> PostgreSQLDB:
        """获取数据库客户端"""
        async with cls._instances["_lock"]:
            if cls._instances["_shutdown"]:
                raise RuntimeError("ClientManager is shutting down")
                
            if cls._instances["db"] is None:
                cls._instances["db"] = PostgreSQLDB(config)
                await cls._instances["db"].initdb()
            
            cls._instances["ref_count"] += 1
            return cls._instances["db"]
    
    @classmethod
    async def reset_client(cls):
        """重置数据库客户端连接"""
        async with cls._instances["_lock"]:
            if cls._instances["db"]:
                try:
                    await cls._instances["db"].close()
                except Exception as e:
                    logger.warning(f"Error closing database client during reset: {e}")
                finally:
                    cls._instances["db"] = None
                    cls._instances["ref_count"] = 0
    
    @classmethod
    async def release_client(cls, db: PostgreSQLDB):
        """释放数据库客户端"""
        async with cls._instances["_lock"]:
            if cls._instances["ref_count"] > 0:
                cls._instances["ref_count"] -= 1
                
            if cls._instances["ref_count"] <= 0 and cls._instances["db"]:
                try:
                    await cls._instances["db"].close()
                except Exception as e:
                    print(f"Warning: Error closing database client: {e}")
                finally:
                    cls._instances["db"] = None
                    cls._instances["ref_count"] = 0
    
    @classmethod
    async def shutdown(cls):
        """强制关闭所有连接"""
        async with cls._instances["_lock"]:
            cls._instances["_shutdown"] = True
            if cls._instances["db"]:
                try:
                    await cls._instances["db"].close()
                except Exception as e:
                    print(f"Warning: Error during shutdown: {e}")
                finally:
                    cls._instances["db"] = None
                    cls._instances["ref_count"] = 0


@dataclass
class PGGraphStore(BaseGraphStore):
    """PostgreSQL图存储实现"""

    graph_db_config: GraphDBConfig = field(default=None)
    db: Optional[PostgreSQLDB] = field(default=None)
    graph_name: str = field(default="")
    
    def __init__(self, graph_db_config: dict[str, Any], graph_name: str = "aworld"):
        self.graph_db_config = graph_db_config
        self.graph_name = graph_name

    async def initialize(self):
        """初始化图存储"""
        if self.db is None:
            self.db = await ClientManager.get_client(self.graph_db_config)
        
        # 确保数据库已初始化
        if not self.db._is_initialized:
            await self.db.initdb()
        
        # 检查连接健康状态
        await self._check_connection_health()
        
        # 创建AGE扩展和配置图环境
        async with self.db.pool.acquire() as connection:
            await PostgreSQLDB.configure_age_extension(connection)
        
        # 执行图初始化语句
        queries = [
            f"SELECT create_graph('{self.graph_name}')",
            f"SELECT create_vlabel('{self.graph_name}', 'base');",
            f"SELECT create_elabel('{self.graph_name}', 'DIRECTED');",
        ]
        
        for query in queries:
            try:
                await self.db.execute(query, with_age=True, graph_name=self.graph_name, ignore_if_exists=True)
            except Exception as e:
                # 忽略"已存在"错误
                if "already exists" not in str(e).lower():
                    print(f"Warning: Failed to execute query {query}: {e}")
    
    async def _check_connection_health(self):
        """检查数据库连接健康状态"""
        try:
            if self.db and self.db.pool:
                # 尝试获取一个连接并执行简单查询
                async with self.db.pool.acquire() as connection:
                    await connection.fetchval("SELECT 1")
                logger.debug("Database connection health check passed")
            else:
                raise Exception("Database pool not available")
        except Exception as e:
            logger.error(f"Database connection health check failed: {e}")
            # 如果健康检查失败，尝试重新初始化连接
            if self.db:
                try:
                    await self.db.close()
                except:
                    pass
                self.db = None
                # 重新获取客户端
                self.db = await ClientManager.get_client(self.graph_db_config)
                await self.db.initdb()
    
    async def finalize(self):
        """清理资源"""
        if self.db is not None:
            try:
                await ClientManager.release_client(self.db)
            except Exception as e:
                logger.warning(f"Error releasing database client: {e}")
            finally:
                self.db = None
    
    @staticmethod
    def _record_to_dict(record: asyncpg.Record) -> Dict[str, Any]:
        """将AGE查询记录转换为字典"""
        d = {}
        
        for k in record.keys():
            v = record[k]
            if isinstance(v, str) and "::" in v:
                # 处理AGE类型数据
                if v.startswith("[") and v.endswith("]"):
                    # 处理数组类型
                    json_content = v[:v.rfind("::")]
                    type_id = v[v.rfind("::") + 2:]
                    if type_id in ["vertex", "edge"]:
                        try:
                            parsed_data = json.loads(json_content)
                            d[k] = parsed_data
                        except json.JSONDecodeError:
                            d[k] = None
                else:
                    # 处理单个对象
                    json_content = v[:v.rfind("::")]
                    type_id = v[v.rfind("::") + 2:]
                    if type_id in ["vertex", "edge"]:
                        try:
                            parsed_data = json.loads(json_content)
                            d[k] = parsed_data
                        except json.JSONDecodeError:
                            d[k] = None
                    else:
                        d[k] = v
            else:
                d[k] = v
        
        return d
    
    @staticmethod
    def _format_properties(properties: Dict[str, Any], _id: Optional[str] = None) -> str:
        """将属性字典转换为Cypher查询字符串"""
        props = []
        for k, v in properties.items():
            prop = f"`{k}`: {json.dumps(v)}"
            props.append(prop)
        
        if _id is not None and "id" not in properties:
            props.append(f"id: {json.dumps(_id)}")
        
        return "{" + ", ".join(props) + "}"
    
    async def _query(self, query: str, readonly: bool = True, upsert: bool = False, 
                    params: Dict[str, Any] = None, max_retries: int = 5) -> List[Dict[str, Any]]:
        """执行图查询"""
        # 确保数据库已初始化
        if self.db is None:
            await self.initialize()
        
        for attempt in range(max_retries):
            try:
                if readonly:
                    data = await self.db.query(
                        query,
                        list(params.values()) if params else None,
                        multirows=True,
                        with_age=True,
                        graph_name=self.graph_name,
                    )
                else:
                    data = await self.db.execute(
                        query,
                        params,
                        upsert=upsert,
                        with_age=True,
                        graph_name=self.graph_name,
                    )
                
                if data is None:
                    return []
                else:
                    return [self._record_to_dict(d) for d in data]
            
            except Exception as e:
                error_msg = str(e).lower()
                # 检查是否是连接相关的错误，需要重试
                retryable_errors = [
                    "another operation is in progress",
                    "connection is closed",
                    "connection lost",
                    "connection timeout",
                    "connection reset",
                    "timeout",
                    "cancelled",
                    "pool is closed",
                    "connection pool is closed"
                ]
                
                is_retryable = any(keyword in error_msg for keyword in retryable_errors)
                
                if is_retryable and attempt < max_retries - 1:
                    # 指数退避重试，增加等待时间
                    wait_time = min(2.0 * (2 ** attempt), 30.0)  # 最大等待30秒
                    logger.warning(f"Database connection error, retrying ({attempt + 1}/{max_retries}) in {wait_time:.1f}s: {e}")
                    await asyncio.sleep(wait_time)
                    
                    # 如果是连接池问题，尝试重新初始化
                    if "pool" in error_msg and attempt == 1:
                        try:
                            logger.info("Attempting to reinitialize database connection pool...")
                            await self.db.close()
                            self.db = None
                            await self.initialize()
                        except Exception as reinit_error:
                            logger.error(f"Failed to reinitialize database pool: {reinit_error} {traceback.format_exc()}")
                    
                    continue
                else:
                    logger.error(f"Database query failed after {attempt + 1} attempts: {e} {traceback.format_exc()}")
                    raise Exception(f"Error executing graph query: {query}, error: {e}")
 
    async def has_node(self, namespace, node_id: str) -> bool:
        """检查节点是否存在"""
        query = f"""
            SELECT EXISTS (
              SELECT 1
              FROM {self.graph_name}.base
              WHERE ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"id"'::agtype]
                    ) = (to_json($1::text)::text)::agtype
                AND ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"namespace"'::agtype]
                    ) = (to_json($2::text)::text)::agtype
              LIMIT 1
            ) AS node_exists;
        """
        
        params = {"node_id": node_id, "namespace": namespace}
        row = (await self._query(query, params=params))[0]
        return bool(row["node_exists"])

    async def has_edge(self, namespace, source_node_id: str, target_node_id: str) -> bool:
        """检查边是否存在"""
        query = f"""
            WITH a AS (
              SELECT id AS vid
              FROM {self.graph_name}.base
              WHERE ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"id"'::agtype]
                    ) = (to_json($1::text)::text)::agtype
                AND ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"namespace"'::agtype]
                    ) = (to_json($3::text)::text)::agtype
            ),
            b AS (
              SELECT id AS vid
              FROM {self.graph_name}.base
              WHERE ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"id"'::agtype]
                    ) = (to_json($2::text)::text)::agtype
                AND ag_catalog.agtype_access_operator(
                      VARIADIC ARRAY[properties, '"namespace"'::agtype]
                    ) = (to_json($3::text)::text)::agtype
            )
            SELECT EXISTS (
              SELECT 1
              FROM {self.graph_name}."DIRECTED" d
              JOIN a ON d.start_id = a.vid
              JOIN b ON d.end_id   = b.vid
              LIMIT 1
            ) AS edge_exists;
        """
        
        params = {"source_node_id": source_node_id, "target_node_id": target_node_id, "namespace": namespace}
        row = (await self._query(query, params=params))[0]
        return bool(row["edge_exists"])

    async def get_node(self, namespace, node_id: str) -> Optional[dict[str, str]]:
        """获取节点"""
        result = await self.get_nodes_batch(namespace=namespace, node_ids=[node_id])
        if result and node_id in result:
            return result[node_id]
        return None

    
    async def get_edge(self, namespace, source_node_id: str, target_node_id: str) -> Optional[Dict[str, Any]]:
        """获取边"""
        result = await self.get_edges_batch(namespace, [{"src": source_node_id, "tgt": target_node_id}])
        if result and (source_node_id, target_node_id) in result:
            return result[(source_node_id, target_node_id)]
        return None

    async def upsert_node(self, namespace, node_id: str, node_data: dict[str, str]) -> None:
        # 确保namespace属性被包含在节点数据中
        node_data_with_namespace = node_data.copy()
        node_data_with_namespace['namespace'] = namespace
        properties = self._format_properties(node_data_with_namespace)

        query = """SELECT * FROM cypher('%s', $$
                         MERGE (n:base {id: "%s"})
                         SET n += %s
                         RETURN n
                       $$) AS (n agtype)""" % (
            self.graph_name,
            node_id,
            properties,
        )

        try:
            # 确保数据库连接健康
            await self._check_connection_health()
            
            await self._query(query, readonly=False, upsert=True)
            logger.debug(f"Successfully upserted node: {node_id}")

        except Exception as e:
            logger.error(
                f"[{self.graph_name}] POSTGRES, upsert_node error on node_id: `{node_id}`, error: {e}"
            )
            
            # 如果是连接超时错误，尝试重新初始化连接
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ["timeout", "connection", "cancelled"]):
                logger.warning(f"Connection issue detected for node {node_id}, attempting to recover...")
                try:
                    # 重置客户端连接
                    await ClientManager.reset_client()
                    self.db = None
                    await self.initialize()
                    
                    # 重试一次
                    await self._query(query, readonly=False, upsert=True)
                    logger.info(f"Successfully recovered and upserted node: {node_id}")
                    return
                except Exception as retry_error:
                    logger.error(f"Failed to recover connection for node {node_id}: {retry_error}")
            
            # 重新抛出异常，但添加更多上下文信息
            raise Exception(f"Failed to upsert node {node_id}: {e}") from e

    async def upsert_edge(self, namespace, source_node_id: str, target_node_id: str, edge_data: Dict[str, Any]) -> None:
        try:
            """插入或更新边"""
            # 确保源节点和目标节点存在
            source = await self.get_node(source_node_id)
            target = await self.get_node(target_node_id)
            if not source or not target:
                raise ValueError(f"Source or target node does not exist: {source_node_id}, {target_node_id}")

            # 确保边数据包含namespace属性
            edge_data_with_namespace = edge_data.copy()
            edge_data_with_namespace['namespace'] = namespace
            edge_properties = self._format_properties(edge_data_with_namespace)

            query = """SELECT * FROM cypher('%s', $$
                         MATCH (source:base {id: "%s"})
                         WITH source
                         MATCH (target:base {id: "%s"})
                         MERGE (source)-[r:DIRECTED]-(target)
                         SET r += %s
                         RETURN r
                       $$) AS (r agtype)""" % (
                self.graph_name,
                source_node_id,
                target_node_id,
                edge_properties,
            )
            await self._query(query, readonly=False, upsert=True)
        except Exception:
            logger.error(
                f"[{self.graph_name}] POSTGRES, upsert_edge error on edge: `{source_node_id}`-`{target_node_id}` {traceback.format_exc()}"
            )
            raise

    async def remove_nodes(self, namespace, node_ids: List[str] = None) -> None:
        """删除多个节点"""
        if not node_ids:
            return
        
        # 构建节点ID列表的字符串
        node_ids_str = ", ".join([f"'{node_id}'" for node_id in node_ids])
        
        query = f"""
            SELECT * FROM cypher('{self.graph_name}', $$
                MATCH (n:base)
                WHERE n.id IN [{node_ids_str}]
                DETACH DELETE n
                RETURN count(n) as deleted_count
            $$) AS (result agtype);
        """
        
        result = await self._query(query, readonly=False)
        if result:
            deleted_count = result[0].get('result', {}).get('deleted_count', 0)
            logger.info(f"🗑️ Successfully deleted {deleted_count} nodes: {node_ids}")
        
        # 验证删除是否成功
        await self._verify_nodes_deleted(namespace, node_ids)

    async def remove_edges(self, namespace, edges: List[Tuple[str, str]]) -> None:
        """删除多个边"""
        if not edges:
            return
        
        deleted_count = 0
        # 为每个边构建删除查询
        for source_id, target_id in edges:
            query = f"""
                SELECT * FROM cypher('{self.graph_name}', $$
                    MATCH (a:base {{id: $source_id}})-[r:DIRECTED]->(b:base {{id: $target_id}})
                    DELETE r
                    RETURN count(r) as deleted_count
                $$, $1) AS (result agtype);
            """
            
            params = {
                "source_id": source_id,
                "target_id": target_id
            }
            
            result = await self._query(query, readonly=False, params=params)
            if result and result[0].get('result', {}).get('deleted_count', 0) > 0:
                deleted_count += 1
        
        logger.info(f"🗑️ Successfully deleted {deleted_count} edges out of {len(edges)}")
        
        # 验证删除是否成功
        await self._verify_edges_deleted(edges)

    async def get_nodes_batch(self, namespace, node_ids: List[str], batch_size: int = 1000) -> Dict[str, Dict[str, Any]]:
        """批量获取节点"""
        if not node_ids:
            return {}

        nodes_dict = {}
        
        # 分批处理节点ID
        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i:i + batch_size]
            
            # 构建节点ID列表的字符串
            node_ids_str = ", ".join([f"'{node_id}'" for node_id in batch])

            query = f"""
                WITH input(v, ord) AS (
                  SELECT v, ord
                  FROM unnest($1::text[]) WITH ORDINALITY AS t(v, ord)
                ),
                ids(node_id, ord) AS (
                  SELECT (to_json(v)::text)::agtype AS node_id, ord
                  FROM input
                )
                SELECT i.node_id::text AS node_id,
                       b.properties
                FROM {self.graph_name}.base AS b
                JOIN ids i
                  ON ag_catalog.agtype_access_operator(
                       VARIADIC ARRAY[b.properties, '"id"'::agtype]
                     ) = i.node_id
                WHERE ag_catalog.agtype_access_operator(
                       VARIADIC ARRAY[b.properties, '"namespace"'::agtype]
                     ) = (to_json($2::text)::text)::agtype
                ORDER BY i.ord;
            """

            results = await self._query(query, params={"ids": batch, "namespace": namespace})

            for result in results:
                if result["node_id"] and result["properties"]:
                    node_dict = result["properties"]

                    # Process string result, parse it to JSON dictionary
                    if isinstance(node_dict, str):
                        try:
                            node_dict = json.loads(node_dict)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse node string in batch: {node_dict}"
                            )

                    nodes_dict[result["node_id"]] = node_dict
        
        return nodes_dict

    async def get_all_nodes(self, namespace) -> list[dict]:
        """Get all nodes in the graph.

        Returns:
            A list of all nodes, where each node is a dictionary of its properties
        """
        query = f"""SELECT * FROM cypher('{self.graph_name}', $$
                     MATCH (n:base)
                     WHERE n.namespace = '{namespace}'
                     RETURN n
                   $$) AS (n agtype)"""

        results = await self._query(query)
        nodes = []
        for result in results:
            if result["n"]:
                node_dict = result["n"]["properties"]

                # Process string result, parse it to JSON dictionary
                if isinstance(node_dict, str):
                    try:
                        node_dict = json.loads(node_dict)
                    except json.JSONDecodeError:
                        logger.warning(
                            f"[{self.workspace}] Failed to parse node string: {node_dict}"
                        )

                # Add node id (entity_id) to the dictionary for easier access
                node_dict["id"] = node_dict.get("id")
                nodes.append(node_dict)
        return nodes

    async def get_edges_batch(self, namespace, pairs: List[Dict[str, str]], batch_size: int = 500) -> Dict[Tuple[str, str], Dict[str, Any]]:
        """
        Retrieve edge properties for multiple (src, tgt) pairs in one query.
        Get forward and backward edges seperately and merge them before return

        Args:
            pairs: List of dictionaries, e.g. [{"src": "node1", "tgt": "node2"}, ...]
            batch_size: Batch size for the query

        Returns:
            A dictionary mapping (src, tgt) tuples to their edge properties.
        """
        if not pairs:
            return {}

        seen = set()
        uniq_pairs: list[dict[str, str]] = []
        for p in pairs:
            s = p["src"]
            t = p["tgt"]
            key = (s, t)
            if s and t and key not in seen:
                seen.add(key)
                uniq_pairs.append(p)

        edges_dict: dict[tuple[str, str], dict] = {}

        for i in range(0, len(uniq_pairs), batch_size):
            batch = uniq_pairs[i : i + batch_size]

            pairs = [{"src": p["src"], "tgt": p["tgt"]} for p in batch]

            forward_cypher = f"""
                         UNWIND $pairs AS p
                         WITH p.src AS src_eid, p.tgt AS tgt_eid
                         MATCH (a:base {{id: src_eid}})
                         MATCH (b:base {{id: tgt_eid}})
                         MATCH (a)-[r]->(b)
                         RETURN src_eid AS source, tgt_eid AS target, properties(r) AS edge_properties"""
            backward_cypher = f"""
                         UNWIND $pairs AS p
                         WITH p.src AS src_eid, p.tgt AS tgt_eid
                         MATCH (a:base {{id: src_eid}})
                         MATCH (b:base {{id: tgt_eid}})
                         MATCH (a)<-[r]-(b)
                         RETURN src_eid AS source, tgt_eid AS target, properties(r) AS edge_properties"""

            def dollar_quote(s: str, tag_prefix="AGE"):
                s = "" if s is None else str(s)
                for i in itertools.count(1):
                    tag = f"{tag_prefix}{i}"
                    wrapper = f"${tag}$"
                    if wrapper not in s:
                        return f"{wrapper}{s}{wrapper}"

            sql_fwd = f"""
            SELECT * FROM cypher({dollar_quote(self.graph_name)}::name,
                                 {dollar_quote(forward_cypher)}::cstring,
                                 $1::agtype)
              AS (source text, target text, edge_properties agtype)
            """

            sql_bwd = f"""
            SELECT * FROM cypher({dollar_quote(self.graph_name)}::name,
                                 {dollar_quote(backward_cypher)}::cstring,
                                 $1::agtype)
              AS (source text, target text, edge_properties agtype)
            """

            pg_params = {"params": json.dumps({"pairs": pairs}, ensure_ascii=False)}

            forward_results = await self._query(sql_fwd, params=pg_params)
            backward_results = await self._query(sql_bwd, params=pg_params)

            for result in forward_results:
                if result["source"] and result["target"] and result["edge_properties"]:
                    edge_props = result["edge_properties"]

                    # Process string result, parse it to JSON dictionary
                    if isinstance(edge_props, str):
                        try:
                            edge_props = json.loads(edge_props)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse edge properties string: {edge_props}"
                            )
                            continue

                    edges_dict[(result["source"], result["target"])] = edge_props

            for result in backward_results:
                if result["source"] and result["target"] and result["edge_properties"]:
                    edge_props = result["edge_properties"]

                    # Process string result, parse it to JSON dictionary
                    if isinstance(edge_props, str):
                        try:
                            edge_props = json.loads(edge_props)
                        except json.JSONDecodeError:
                            logger.warning(
                                f"Failed to parse edge properties string: {edge_props}"
                            )
                            continue

                    edges_dict[(result["source"], result["target"])] = edge_props

        return edges_dict

    async def index_done_callback(self) -> None:
        """索引完成回调"""
        # PostgreSQL自动处理持久化
        pass
    
    async def _flush_database_cache(self) -> None:
        """刷新数据库缓存，确保删除操作立即生效"""
        try:
            async with self.db.pool.acquire() as connection:
                # 强制刷新所有缓存
                await connection.execute("SELECT pg_stat_reset();")
                # 刷新AGE图缓存
                await connection.execute(f"SET search_path = ag_catalog, public;")
                # 执行一个简单的查询来刷新连接
                await connection.fetch("SELECT 1;")
                logger.debug("🔄 Database cache flushed")
        except Exception as e:
            logger.warning(f"⚠️ Failed to flush database cache: {e}")
    
    async def _verify_nodes_deleted(self, namespace, node_ids: List[str]) -> None:
        """验证节点是否已成功删除"""
        try:
            for node_id in node_ids:
                exists = await self.has_node(namespace, node_id)
                if exists:
                    logger.warning(f"⚠️ Node {node_id} still exists after deletion attempt")
                else:
                    logger.debug(f"✅ Node {node_id} successfully deleted")
        except Exception as e:
            logger.error(f"❌ Error verifying node deletion: {e}")
    
    async def _verify_edges_deleted(self, namespace, edges: List[Tuple[str, str]]) -> None:
        """验证边是否已成功删除"""
        try:
            for source_id, target_id in edges:
                exists = await self.has_edge(namespace=namespace, source_node_id=source_id, target_node_id=target_id)
                if exists:
                    logger.warning(f"⚠️ Edge {source_id}->{target_id} still exists after deletion attempt")
                else:
                    logger.debug(f"✅ Edge {source_id}->{target_id} successfully deleted")
        except Exception as e:
            logger.error(f"❌ Error verifying edge deletion: {e}")


    async def get_node_edges(self, namespace, source_node_id: str) -> Optional[List[Tuple[str, str]]]:
        """
        Retrieves all edges (relationships) for a particular node identified by its label.
        :return: list of dictionaries containing edge information
        """

        query = """SELECT * FROM cypher('%s', $$
                      MATCH (n:base {id: "%s"})
                      OPTIONAL MATCH (n)-[]-(connected:base)
                      RETURN n.id AS source_id, connected.id AS connected_id
                    $$) AS (source_id text, connected_id text)""" % (
            self.graph_name,
            source_node_id,
        )

        results = await self._query(query)
        edges = []
        for record in results:
            source_id = record["source_id"]
            connected_id = record["connected_id"]

            if source_id and connected_id:
                edges.append((source_id, connected_id))

        return edges

    async def get_nodes_edges_batch(self, namespace, node_ids: List[str], batch_size: int = 500) -> Dict[str, List[Tuple[str, str]]]:
        """
        Get all edges (both outgoing and incoming) for multiple nodes in a single batch operation.

        Args:
            node_ids: List of node IDs to get edges for
            batch_size: Batch size for the query

        Returns:
            Dictionary mapping node IDs to lists of (source, target) edge tuples
        """
        if not node_ids:
            return {}

        seen = set()
        unique_ids: list[str] = []
        for nid in node_ids:
            n = nid
            if n and n not in seen:
                seen.add(n)
                unique_ids.append(n)

        edges_norm: dict[str, list[tuple[str, str]]] = {n: [] for n in unique_ids}

        for i in range(0, len(unique_ids), batch_size):
            batch = unique_ids[i : i + batch_size]
            # Format node IDs for the query
            formatted_ids = ", ".join([f'"{n}"' for n in batch])

            outgoing_query = """SELECT * FROM cypher('%s', $$
                         UNWIND [%s] AS node_id
                         MATCH (n:base {id: node_id})
                         OPTIONAL MATCH (n:base)-[]->(connected:base)
                         RETURN node_id, connected.id AS connected_id
                       $$) AS (node_id text, connected_id text)""" % (
                self.graph_name,
                formatted_ids
            )

            incoming_query = """SELECT * FROM cypher('%s', $$
                         UNWIND [%s] AS node_id
                         MATCH (n:base {id: node_id})
                         OPTIONAL MATCH (n:base)<-[]-(connected:base)
                         RETURN node_id, connected.id AS connected_id
                       $$) AS (node_id text, connected_id text)""" % (
                self.graph_name,
                formatted_ids
            )

            outgoing_results = await self._query(outgoing_query)
            incoming_results = await self._query(incoming_query)

            for result in outgoing_results:
                if result["node_id"] and result["connected_id"]:
                    edges_norm[result["node_id"]].append(
                        (result["node_id"], result["connected_id"])
                    )

            for result in incoming_results:
                if result["node_id"] and result["connected_id"]:
                    edges_norm[result["node_id"]].append(
                        (result["connected_id"], result["node_id"])
                    )

        out: dict[str, list[tuple[str, str]]] = {}
        for orig in node_ids:
            n = self._normalize_node_id(orig)
            out[orig] = edges_norm.get(n, [])

        return out
    
    async def get_related_nodes(self, namespace, node_id: str, max_depth: int = 2, limit: int = 10) -> List[str]:
        """获取与指定节点相关的节点ID列表，支持多层级查询
        
        Args:
            node_id: 要查询关联节点的节点ID
            max_depth: 最大查询深度，默认为2
            limit: 返回结果数量限制，默认为10
            
        Returns:
            List[str]: 关联节点ID列表
        """
        if max_depth <= 0:
            return []
        
        # 构建多层级查询的Cypher语句
        # 使用UNION ALL来合并不同深度的结果
        depth_queries = []
        
        for depth in range(1, max_depth + 1):
            if depth == 1:
                # 直接连接的节点
                query = f"""
                    SELECT * FROM cypher('{self.graph_name}', $$
                        MATCH (n:base {{id: "{node_id}"}})
                        OPTIONAL MATCH (n)-[r*1..1]-(connected:base)
                        WHERE connected.id IS NOT NULL AND connected.id <> "{node_id}"
                        RETURN DISTINCT connected.id AS related_id
                    $$) AS (related_id text)
                """
            else:
                # 多层级连接的节点
                query = f"""
                    SELECT * FROM cypher('{self.graph_name}', $$
                        MATCH (n:base {{id: "{node_id}"}})
                        OPTIONAL MATCH (n)-[r*1..{depth}]-(connected:base)
                        WHERE connected.id IS NOT NULL AND connected.id <> "{node_id}"
                        RETURN DISTINCT connected.id AS related_id
                    $$) AS (related_id text)
                """
            depth_queries.append(query)
        
        # 合并所有深度的查询结果
        combined_query = " UNION ALL ".join(depth_queries)
        final_query = f"""
            WITH all_related AS ({combined_query})
            SELECT DISTINCT related_id
            FROM all_related
            WHERE related_id IS NOT NULL
            LIMIT {limit}
        """
        
        try:
            results = await self._query(final_query)
            related_node_ids = []
            
            for result in results:
                if result.get("related_id"):
                    related_node_ids.append(result["related_id"])

            related_nodes = await self.get_nodes_batch(namespace, related_node_ids)
            logger.info(f"PGGraphStore|get_related_nodes|node_id={node_id}|max_depth={max_depth}|limit={limit}|related_node_ids={related_node_ids}|related_nodes={related_nodes}")

            return related_nodes
            
        except Exception as e:
            logger.error(f"Error getting related nodes for {node_id}: {e}")
            # 如果多层级查询失败，回退到单层级查询
            return await self._get_related_nodes_fallback(namespace, node_id, limit)
    
    async def _get_related_nodes_fallback(self, namespace, node_id: str, limit: int = 10) -> List[str]:
        """回退方法：获取直接关联的节点"""
        try:
            edges = await self.get_node_edges(namespace=namespace, source_node_id=node_id)
            if not edges:
                return []
            
            related_node_ids = set()
            for source_id, target_id in edges:
                if source_id != node_id:
                    related_node_ids.add(source_id)
                if target_id != node_id:
                    related_node_ids.add(target_id)
            
            return list(related_node_ids)[:limit]
            
        except Exception as e:
            logger.error(f"Error in fallback method for {node_id}: {e}")
            return []

    def _normalize_node_id(self, node_id: str) -> str:
        """标准化节点ID"""
        return str(node_id) if node_id is not None else ""

    async def test_connection(self) -> bool:
        """测试数据库连接是否正常
        
        Returns:
            bool: 连接成功返回True，失败返回False
        """
        try:
            # 确保数据库已初始化
            if self.db is None:
                await self.initialize()
            
            # 检查连接池是否存在
            if not self.db or not self.db.pool:
                logger.error("Database pool not available")
                return False
            
            # 尝试获取连接并执行简单查询
            async with self.db.pool.acquire() as connection:
                # 执行简单的健康检查查询
                result = await connection.fetchval("SELECT 1")
                if result == 1:
                    logger.debug("Database connection test passed")
                    return True
                else:
                    logger.error("Database connection test failed: unexpected result")
                    return False
                    
        except Exception as e:
            logger.error(f"Database connection test failed: {e}")
            return False