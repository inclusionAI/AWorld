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

# todo(ck.hsq): add other implementation of CancellationStore, such as redis, sqlite


class CancellationRegistry(InheritanceSingleton):
    """Task status registry.

    Exposes a unified API with pluggable storage backends.
    """

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
    """Build a CancellationStore from configuration, only support memory backend for now."""
    return InMemoryCancellationStore()


