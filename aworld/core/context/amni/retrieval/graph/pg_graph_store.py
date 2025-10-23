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
    """PostgreSQL database connection management"""
    
    def __init__(self, config: dict[str, Any], **kwargs: Any):
        self.config = config
        self.pool: Optional[asyncpg.Pool] = None
        self._initialization_lock = asyncio.Lock()
        self._is_initialized = False
    
    async def initdb(self):
        """Initialize database connection pool"""
        async with self._initialization_lock:
            if self._is_initialized:
                return
                
            try:
                # Build connection parameters
                connection_params = {
                    "host": self.config.get("host", "localhost"),
                    "port": self.config.get("port", 5432),
                    "user": self.config.get("user", "aworldcore"),
                    "password": self.config.get("password", "123456"),
                    "database": self.config.get("database", "aworldcore"),
                }
                
                # SSL configuration
                if self.config.get("ssl_mode"):
                    connection_params["ssl"] = self._create_ssl_context()
                
                # Create connection pool
                self.pool = await asyncpg.create_pool(
                    **connection_params,
                    min_size=1,
                    max_size=10,
                    command_timeout=120,  # Increase command timeout
                    server_settings={
                        'application_name': 'aworldcore_graph_store',
                        'tcp_keepalives_idle': '600',
                        'tcp_keepalives_interval': '30',
                        'tcp_keepalives_count': '3',
                    },
                    # Connection timeout settings
                    timeout=30,  # Connection timeout 30 seconds
                    max_queries=50000,  # Maximum queries
                    max_inactive_connection_lifetime=300.0,  # Maximum inactive connection lifetime
                )
                
                # Configure AGE extension
                async with self.pool.acquire() as connection:
                    await self.configure_age_extension(connection)
                
                self._is_initialized = True
                    
            except Exception as e:
                self._is_initialized = False
                raise Exception(f"Failed to initialize PostgreSQL database: {e}")
    
    def _create_ssl_context(self) -> ssl.SSLContext | None:
        """Create SSL context"""
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
        """Configure Apache AGE extension"""
        try:
            # Create AGE extension
            await connection.execute("CREATE EXTENSION IF NOT EXISTS age;")
            
            # Set search path
            await connection.execute("SET search_path = ag_catalog, public;")
            
        except Exception as e:
            # If AGE extension is not available, log warning but continue
            print(f"Warning: Apache AGE extension not available: {e}")
    
    async def query(self, sql: str, params: List[Any] = None, multirows: bool = False, 
                   with_age: bool = False, graph_name: str = None) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """Execute query"""
        if not self.pool:
            raise Exception("Database pool not initialized")
        
        connection = None
        try:
            connection = await self.pool.acquire()
            # For query operations, use read-only transaction
            async with connection.transaction(readonly=True):
                if with_age and graph_name:
                    # Set AGE search path
                    await connection.execute(f"SET search_path = ag_catalog, public;")
                
                if multirows:
                    rows = await connection.fetch(sql, *(params or []))
                    return [dict(row) for row in rows] if rows else []
                else:
                    row = await connection.fetchrow(sql, *(params or []))
                    return dict(row) if row else None
        except Exception as e:
            # Ensure connection is properly released
            raise e
        finally:
            if connection:
                try:
                    await self.pool.release(connection)
                except Exception as release_error:
                    print(f"Warning: Error releasing connection: {release_error}")
    
    async def execute(self, sql: str, data: Dict[str, Any] = None, upsert: bool = False, 
                     ignore_if_exists: bool = False, with_age: bool = False, graph_name: str = None):
        """Execute SQL statement"""
        if not self.pool:
            raise Exception("Database pool not initialized")
        
        connection = None
        try:
            connection = await self.pool.acquire()
            # Begin transaction
            async with connection.transaction():
                if with_age and graph_name:
                    # Set AGE search path
                    await connection.execute(f"SET search_path = ag_catalog, public;")
                
                try:
                    if data:
                        # Apache AGE's cypher function requires parameters to be passed as a single dictionary
                        result = await connection.fetch(sql, data)
                    else:
                        result = await connection.fetch(sql)
                    
                    # Return query result
                    return [dict(row) for row in result] if result else []
                except Exception as e:
                    if ignore_if_exists and "already exists" in str(e).lower():
                        # Ignore "already exists" error
                        pass
                    else:
                        raise e
        except Exception as e:
            # Ensure connection is properly released
            raise e
        finally:
            if connection:
                try:
                    await self.pool.release(connection)
                except Exception as release_error:
                    print(f"Warning: Error releasing connection: {release_error}")
    
    async def close(self):
        """Close connection pool"""
        if self.pool:
            try:
                # Wait for all connections to complete
                await asyncio.sleep(0.1)
                await self.pool.close()
                self._is_initialized = False
            except Exception as e:
                print(f"Warning: Error closing database pool: {e}")
            finally:
                self.pool = None


class ClientManager:
    """Client manager - improved resource management"""
    _instances: Dict[str, Any] = {"db": None, "ref_count": 0, "_lock": asyncio.Lock(), "_shutdown": False}

    @classmethod
    async def get_client(cls, config: dict[str, Any]) -> PostgreSQLDB:
        """Get database client"""
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
        """Reset database client connection"""
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
        """Release database client"""
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
        """Force close all connections"""
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
    """PostgreSQL graph storage implementation"""

    graph_db_config: GraphDBConfig = field(default=None)
    db: Optional[PostgreSQLDB] = field(default=None)
    graph_name: str = field(default="")
    
    def __init__(self, graph_db_config: dict[str, Any], graph_name: str = "aworld"):
        self.graph_db_config = graph_db_config
        self.graph_name = graph_name

    async def initialize(self):
        """Initialize graph storage"""
        if self.db is None:
            self.db = await ClientManager.get_client(self.graph_db_config)
        
        # Ensure database is initialized
        if not self.db._is_initialized:
            await self.db.initdb()
        
        # Check connection health status
        await self._check_connection_health()
        
        # Create AGE extension and configure graph environment
        async with self.db.pool.acquire() as connection:
            await PostgreSQLDB.configure_age_extension(connection)
        
        # Execute graph initialization statements
        queries = [
            f"SELECT create_graph('{self.graph_name}')",
            f"SELECT create_vlabel('{self.graph_name}', 'base');",
            f"SELECT create_elabel('{self.graph_name}', 'DIRECTED');",
        ]
        
        for query in queries:
            try:
                await self.db.execute(query, with_age=True, graph_name=self.graph_name, ignore_if_exists=True)
            except Exception as e:
                # Ignore "already exists" error
                if "already exists" not in str(e).lower():
                    print(f"Warning: Failed to execute query {query}: {e}")
    
    async def _check_connection_health(self):
        """Check database connection health status"""
        try:
            if self.db and self.db.pool:
                # Try to acquire a connection and execute a simple query
                async with self.db.pool.acquire() as connection:
                    await connection.fetchval("SELECT 1")
                logger.debug("Database connection health check passed")
            else:
                raise Exception("Database pool not available")
        except Exception as e:
            logger.error(f"Database connection health check failed: {e}")
            # If health check fails, try to reinitialize connection
            if self.db:
                try:
                    await self.db.close()
                except:
                    pass
                self.db = None
                # Re-acquire client
                self.db = await ClientManager.get_client(self.graph_db_config)
                await self.db.initdb()
    
    async def finalize(self):
        """Clean up resources"""
        if self.db is not None:
            try:
                await ClientManager.release_client(self.db)
            except Exception as e:
                logger.warning(f"Error releasing database client: {e}")
            finally:
                self.db = None
    
    @staticmethod
    def _record_to_dict(record: asyncpg.Record) -> Dict[str, Any]:
        """Convert AGE query records to dictionary"""
        d = {}
        
        for k in record.keys():
            v = record[k]
            if isinstance(v, str) and "::" in v:
                # Handle AGE type data
                if v.startswith("[") and v.endswith("]"):
                    # Handle array type
                    json_content = v[:v.rfind("::")]
                    type_id = v[v.rfind("::") + 2:]
                    if type_id in ["vertex", "edge"]:
                        try:
                            parsed_data = json.loads(json_content)
                            d[k] = parsed_data
                        except json.JSONDecodeError:
                            d[k] = None
                else:
                    # Handle single object
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
        """Convert property dictionary to Cypher query string"""
        props = []
        for k, v in properties.items():
            prop = f"`{k}`: {json.dumps(v)}"
            props.append(prop)
        
        if _id is not None and "id" not in properties:
            props.append(f"id: {json.dumps(_id)}")
        
        return "{" + ", ".join(props) + "}"
    
    async def _query(self, query: str, readonly: bool = True, upsert: bool = False, 
                    params: Dict[str, Any] = None, max_retries: int = 5) -> List[Dict[str, Any]]:
        """Execute graph query"""
        # Ensure database is initialized
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
                # Check if it's a connection-related error that needs retry
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
                    # Exponential backoff retry, increase wait time
                    wait_time = min(2.0 * (2 ** attempt), 30.0)  # Maximum wait 30 seconds
                    logger.warning(f"Database connection error, retrying ({attempt + 1}/{max_retries}) in {wait_time:.1f}s: {e}")
                    await asyncio.sleep(wait_time)
                    
                    # If it's a connection pool issue, try to reinitialize
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
        """Check if node exists"""
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
        """Check if edge exists"""
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
        """Get node"""
        result = await self.get_nodes_batch(namespace=namespace, node_ids=[node_id])
        if result and node_id in result:
            return result[node_id]
        return None

    
    async def get_edge(self, namespace, source_node_id: str, target_node_id: str) -> Optional[Dict[str, Any]]:
        """Get edge"""
        result = await self.get_edges_batch(namespace, [{"src": source_node_id, "tgt": target_node_id}])
        if result and (source_node_id, target_node_id) in result:
            return result[(source_node_id, target_node_id)]
        return None

    async def upsert_node(self, namespace, node_id: str, node_data: dict[str, str]) -> None:
        # Ensure namespace property is included in node data
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
            # Ensure database connection is healthy
            await self._check_connection_health()
            
            await self._query(query, readonly=False, upsert=True)
            logger.debug(f"Successfully upserted node: {node_id}")

        except Exception as e:
            logger.error(
                f"[{self.graph_name}] POSTGRES, upsert_node error on node_id: `{node_id}`, error: {e}"
            )
            
            # If connection timeout error, try to reinitialize connection
            error_msg = str(e).lower()
            if any(keyword in error_msg for keyword in ["timeout", "connection", "cancelled"]):
                logger.warning(f"Connection issue detected for node {node_id}, attempting to recover...")
                try:
                    # Reset client connection
                    await ClientManager.reset_client()
                    self.db = None
                    await self.initialize()
                    
                    # Retry once
                    await self._query(query, readonly=False, upsert=True)
                    logger.info(f"Successfully recovered and upserted node: {node_id}")
                    return
                except Exception as retry_error:
                    logger.error(f"Failed to recover connection for node {node_id}: {retry_error}")
            
            # Re-raise exception with more context information
            raise Exception(f"Failed to upsert node {node_id}: {e}") from e

    async def upsert_edge(self, namespace, source_node_id: str, target_node_id: str, edge_data: Dict[str, Any]) -> None:
        try:
            """Insert or update edge"""
            # Ensure source and target nodes exist
            source = await self.get_node(source_node_id)
            target = await self.get_node(target_node_id)
            if not source or not target:
                raise ValueError(f"Source or target node does not exist: {source_node_id}, {target_node_id}")

            # Ensure edge data contains namespace property
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
        """Delete multiple nodes"""
        if not node_ids:
            return
        
        # Build string of node ID list
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
        
        # Verify deletion was successful
        await self._verify_nodes_deleted(namespace, node_ids)

    async def remove_edges(self, namespace, edges: List[Tuple[str, str]]) -> None:
        """Delete multiple edges"""
        if not edges:
            return
        
        deleted_count = 0
        # Build delete query for each edge
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
        
        # Verify deletion was successful
        await self._verify_edges_deleted(edges)

    async def get_nodes_batch(self, namespace, node_ids: List[str], batch_size: int = 1000) -> Dict[str, Dict[str, Any]]:
        """Batch get nodes"""
        if not node_ids:
            return {}

        nodes_dict = {}
        
        # Process node IDs in batches
        for i in range(0, len(node_ids), batch_size):
            batch = node_ids[i:i + batch_size]
            
            # Build string of node ID list
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
        """Index completion callback"""
        # PostgreSQL automatically handles persistence
        pass
    
    async def _flush_database_cache(self) -> None:
        """Refresh database cache to ensure deletion takes effect immediately"""
        try:
            async with self.db.pool.acquire() as connection:
                # Force refresh all caches
                await connection.execute("SELECT pg_stat_reset();")
                # Refresh AGE graph cache
                await connection.execute(f"SET search_path = ag_catalog, public;")
                # Execute a simple query to refresh connection
                await connection.fetch("SELECT 1;")
                logger.debug("🔄 Database cache flushed")
        except Exception as e:
            logger.warning(f"⚠️ Failed to flush database cache: {e}")
    
    async def _verify_nodes_deleted(self, namespace, node_ids: List[str]) -> None:
        """Verify nodes have been successfully deleted"""
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
        """Verify edges have been successfully deleted"""
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
        """Get list of node IDs related to specified node, supports multi-level queries
        
        Args:
            node_id: Node ID to query related nodes
            max_depth: Maximum query depth, defaults to 2
            limit: Result count limit, defaults to 10
            
        Returns:
            List[str]: List of related node IDs
        """
        if max_depth <= 0:
            return []
        
        # Build multi-level query Cypher statement
        # Use UNION ALL to merge results from different depths
        depth_queries = []
        
        for depth in range(1, max_depth + 1):
            if depth == 1:
                # Directly connected nodes
                query = f"""
                    SELECT * FROM cypher('{self.graph_name}', $$
                        MATCH (n:base {{id: "{node_id}"}})
                        OPTIONAL MATCH (n)-[r*1..1]-(connected:base)
                        WHERE connected.id IS NOT NULL AND connected.id <> "{node_id}"
                        RETURN DISTINCT connected.id AS related_id
                    $$) AS (related_id text)
                """
            else:
                # Multi-level connected nodes
                query = f"""
                    SELECT * FROM cypher('{self.graph_name}', $$
                        MATCH (n:base {{id: "{node_id}"}})
                        OPTIONAL MATCH (n)-[r*1..{depth}]-(connected:base)
                        WHERE connected.id IS NOT NULL AND connected.id <> "{node_id}"
                        RETURN DISTINCT connected.id AS related_id
                    $$) AS (related_id text)
                """
            depth_queries.append(query)
        
        # Merge query results from all depths
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
            # If multi-level query fails, fallback to single-level query
            return await self._get_related_nodes_fallback(namespace, node_id, limit)
    
    async def _get_related_nodes_fallback(self, namespace, node_id: str, limit: int = 10) -> List[str]:
        """Fallback method: get directly related nodes"""
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
        """Normalize node ID"""
        return str(node_id) if node_id is not None else ""

    async def test_connection(self) -> bool:
        """Test if database connection is normal
        
        Returns:
            bool: Returns True if connection succeeds, False if fails
        """
        try:
            # Ensure database is initialized
            if self.db is None:
                await self.initialize()
            
            # Check if connection pool exists
            if not self.db or not self.db.pool:
                logger.error("Database pool not available")
                return False
            
            # Try to acquire connection and execute simple query
            async with self.db.pool.acquire() as connection:
                # Execute simple health check query
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