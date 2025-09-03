# coding: utf-8
import abc
import sqlite3
import time
from threading import Lock
from typing import Optional, Dict, Any

from aworld.core.singleton import InheritanceSingleton
from aworld.logs.util import logger


class TaskStatus:
    INIT = "init"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CancellationStore(abc.ABC):
    @abc.abstractmethod
    def register(self, task_id: str, status: str = TaskStatus.INIT):
        pass

    @abc.abstractmethod
    def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        pass

    def cancel(self, task_id: str, reason: Optional[str] = None):
        self.set_status(task_id, TaskStatus.CANCELLED, reason)

    @abc.abstractmethod
    def is_cancelled(self, task_id: str) -> bool:
        pass

    @abc.abstractmethod
    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        pass


class InMemoryCancellationStore(CancellationStore):
    def __init__(self):
        self._lock = Lock()
        self._status: Dict[str, Dict[str, Any]] = {}

    def register(self, task_id: str, status: str = TaskStatus.INIT):
        with self._lock:
            self._status.setdefault(task_id, {
                "status": status,
                "reason": None,
                "updated_at": time.time(),
            })

    def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        with self._lock:
            self._status[task_id] = {
                "status": status,
                "reason": reason,
                "updated_at": time.time(),
            }

    def is_cancelled(self, task_id: str) -> bool:
        with self._lock:
            info = self._status.get(task_id)
            return bool(info and info.get("status") == TaskStatus.CANCELLED)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            return self._status.get(task_id)


class RedisCancellationStore(CancellationStore):
    def __init__(self, *, host: str = "127.0.0.1", port: int = 6379, db: int = 0,
                 password: Optional[str] = None, prefix: str = "aworld:cancellation:"):
        try:
            import redis  # type: ignore
        except Exception as e:
            raise RuntimeError("redis-py 未安装，无法使用 RedisCancellationStore") from e
        self._redis = redis.StrictRedis(host=host, port=port, db=db, password=password, decode_responses=True)
        self._prefix = prefix

    def _key(self, task_id: str) -> str:
        return f"{self._prefix}{task_id}"

    def register(self, task_id: str, status: str = TaskStatus.INIT):
        key = self._key(task_id)
        self._redis.hsetnx(key, "status", status)
        self._redis.hsetnx(key, "updated_at", time.time())

    def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        key = self._key(task_id)
        data = {"status": status, "updated_at": time.time()}
        if reason is not None:
            data["reason"] = reason
        self._redis.hset(key, mapping=data)

    def is_cancelled(self, task_id: str) -> bool:
        key = self._key(task_id)
        return self._redis.hget(key, "status") == TaskStatus.CANCELLED

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        key = self._key(task_id)
        values = self._redis.hgetall(key)
        return values or None


class SQLiteCancellationStore(CancellationStore):
    def __init__(self, file: str = "/tmp/aworld_cancellation.db"):
        self._conn = sqlite3.connect(file, check_same_thread=False)
        self._init_table()

    def _init_table(self):
        cur = self._conn.cursor()
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS cancellation (
                task_id TEXT PRIMARY KEY,
                status TEXT,
                reason TEXT,
                updated_at REAL
            );
            """
        )
        self._conn.commit()

    def register(self, task_id: str, status: str = TaskStatus.INIT):
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO cancellation(task_id, status, reason, updated_at)
            VALUES(?, ?, NULL, ?)
            ON CONFLICT(task_id) DO NOTHING
            """,
            (task_id, status, time.time())
        )
        self._conn.commit()

    def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        cur = self._conn.cursor()
        cur.execute(
            """
            INSERT INTO cancellation(task_id, status, reason, updated_at)
            VALUES(?, ?, ?, ?)
            ON CONFLICT(task_id) DO UPDATE SET
                status=excluded.status,
                reason=excluded.reason,
                updated_at=excluded.updated_at
            """,
            (task_id, status, reason, time.time())
        )
        self._conn.commit()

    def is_cancelled(self, task_id: str) -> bool:
        cur = self._conn.cursor()
        cur.execute("SELECT status FROM cancellation WHERE task_id=?", (task_id,))
        row = cur.fetchone()
        return bool(row and row[0] == TaskStatus.CANCELLED)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        cur = self._conn.cursor()
        cur.execute("SELECT status, reason, updated_at FROM cancellation WHERE task_id=?", (task_id,))
        row = cur.fetchone()
        if not row:
            return None
        return {"status": row[0], "reason": row[1], "updated_at": row[2]}


class CancellationRegistry(InheritanceSingleton):
    """任务状态中心，对外暴露统一 API，内部可插拔存储实现。"""

    def __init__(self, store: CancellationStore = None):
        self._store: CancellationStore = store or InMemoryCancellationStore()

    def use_store(self, store: CancellationStore):
        self._store = store or InMemoryCancellationStore()

    # proxy methods
    def register(self, task_id: str, status: str = TaskStatus.INIT):
        self._store.register(task_id, status)

    def set_status(self, task_id: str, status: str, reason: Optional[str] = None):
        self._store.set_status(task_id, status, reason)

    def cancel(self, task_id: str, reason: Optional[str] = None):
        self._store.cancel(task_id, reason)

    def is_cancelled(self, task_id: str) -> bool:
        return self._store.is_cancelled(task_id)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._store.get(task_id)


def build_cancellation_store(conf: Optional[Dict[str, Any]]) -> CancellationStore:
    """根据配置构建 CancellationStore。

    示例配置：
    {
        "backend": "redis",  # memory | redis | sqlite
        "redis": {"host": "127.0.0.1", "port": 6379, "db": 0, "password": null, "prefix":"aworld:cancellation:"},
        "sqlite": {"file": "/tmp/aworld_cancellation.db"}
    }
    """
    conf = conf or {}
    backend = (conf.get("backend") or "memory").lower()
    try:
        if backend == "redis":
            params = conf.get("redis") or {}
            return RedisCancellationStore(**params)
        if backend in ("sqlite", "db", "database"):
            params = conf.get("sqlite") or {}
            return SQLiteCancellationStore(**params)
        return InMemoryCancellationStore()
    except Exception as e:
        logger.warning(f"build_cancellation_store fallback to memory, cause: {e}")
        return InMemoryCancellationStore()


