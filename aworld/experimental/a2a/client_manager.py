import threading
import asyncio
import httpx
import atexit
import time
import abc
from typing import Generic, TypeVar

from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from aworld.experimental.a2a.config import ClientConfig
from aworld.logs.util import logger

ClientType = TypeVar('ClientType')
ConfigType = TypeVar('ConfigType')


class ThreadSafeManager(Generic[ClientType, ConfigType]):
    _instance_lock = threading.RLock()
    _global_instance = None

    @classmethod
    def get_instance(cls, config: ConfigType = None):
        """
        get the singleton instance of ThreadSafeManager.
        if the instance doesn't exist and config is provided,
        create a new instance.
        """
        with cls._instance_lock:
            if cls._global_instance is None and config is not None:
                cls._global_instance = cls(config)
            return cls._global_instance

    def __init__(self, config: ConfigType):
        """
        initialize the ThreadSafeManager instance.
        """
        # prevent direct instantiation from outside
        with self._instance_lock:
            if ThreadSafeManager._global_instance is not None:
                raise RuntimeError("use get_instance() to get the singleton instance")

        self._config = config
        self._local = threading.local()
        self._instances = {}  # client instance registry
        self._thread_registry = {}
        self._cleanup_lock = threading.RLock()

        # register the global cleanup function,
        # which will be called when the program exits
        atexit.register(self._cleanup_all)

        # initialize the registry with the main thread
        main_thread = threading.current_thread()
        self._thread_registry[main_thread.ident] = main_thread

        # start the thread monitor daemon thread,
        # which periodically checks for and cleans up dead threads
        self._shutdown_flag = False
        self._monitor_thread = threading.Thread(
            target=self._thread_monitor,
            daemon=True
        )
        self._monitor_thread.start()

    @abc.abstractmethod
    def _create_client(self) -> ClientType:
        """create ClientType instance"""
        pass

    def get_client(self) -> ClientType:
        """
        get ClientType instance for the current thread,
        create a new one if it doesn't exist.
        """
        thread_id = threading.get_ident()
        thread = threading.current_thread()

        # register the thread to the registry
        with self._cleanup_lock:
            self._thread_registry[thread_id] = thread

        # if the thread doesn't have a client instance, create one
        if not hasattr(self._local, '_client') or self._local._client is None:
            client = self._create_client()
            self._local._client = client
            with self._cleanup_lock:
                self._instances[thread_id] = client
        return self._local._client

    def release_client(self):
        thread_id = threading.get_ident()
        self._cleanup_thread(thread_id)

    def _cleanup_thread(self, thread_id: int):
        """clean up A2AClient instance for a specific thread"""
        with self._cleanup_lock:
            client = self._instances.pop(thread_id, None)
            # remove the thread from the registry
            self._thread_registry.pop(thread_id, None)

        if client and hasattr(client, 'close'):
            try:
                asyncio.run(client.close())
                logger.debug(f"A2AClient for thread {thread_id} closed successfully")
            except Exception as e:
                logger.warning(f"Error closing A2AClient for thread {thread_id}: {e}")

    def _cleanup_all(self):
        """clean up all A2AClient instances"""
        logger.debug("Cleaning up all A2AClient instances...")
        with self._cleanup_lock:
            thread_ids = list(self._instances.keys())

        for thread_id in thread_ids:
            self._cleanup_thread(thread_id)

        logger.debug("All A2AClient instances cleaned up")

    def _thread_monitor(self):
        """
        monitor thread to check and clean up resources for dead threads
        """
        while not self._shutdown_flag:
            try:
                self._check_and_cleanup_dead_threads()
            except Exception as e:
                logger.error(f"Error in thread monitor: {e}")

            # check and clean up resources for dead threads every 5 seconds
            time.sleep(5)

    def _check_and_cleanup_dead_threads(self):
        """
        check and clean up resources for dead threads
        """
        dead_thread_ids = []

        with self._cleanup_lock:
            # copy the thread registry to avoid modification during iteration
            thread_registry_copy = dict(self._thread_registry)

        # check which threads have died
        for thread_id, thread in thread_registry_copy.items():
            # skip the main thread to avoid double cleanup on exit
            if thread_id == threading.main_thread().ident:
                continue

            # check if the thread is still alive
            if not thread.is_alive():
                dead_thread_ids.append(thread_id)

        # clean up resources for dead threads
        for thread_id in dead_thread_ids:
            logger.debug(f"Cleaning up resources for dead thread {thread_id}")
            self._cleanup_thread(thread_id)

    def shutdown(self):
        """
        close the manager and stop the monitor thread
        """
        self._shutdown_flag = True

        # wait for the monitor thread to exit
        if hasattr(self, '_monitor_thread') and self._monitor_thread.is_alive():
            self._monitor_thread.join(timeout=1.0)

        # clean up all client instances
        self._cleanup_all()


class WrapperedA2AClientFactory(A2AClientFactory):

    def __init__(self, httpx_client: httpx.AsyncClient, **kwargs):
        super().__init__(**kwargs)
        self._httpx_client = httpx_client

    @property
    def https_client(self) -> httpx.AsyncClient:
        return self._httpx_client


class A2AClientManager(ThreadSafeManager[A2AClientFactory, ClientConfig]):

    def __init__(self, config: ClientConfig):
        super().__init__(config)

    def _create_client(self) -> A2AClientFactory:
        # create A2AClient instance
        _httpx_client = httpx.AsyncClient(timeout=self._config.timeout)
        a2a_client_config = A2AClientConfig(
            streaming=self._config.streaming,
            polling=self._config.polling,
            httpx_client=_httpx_client,
            supported_transports=self._config.supported_transports,
            grpc_channel_factory=self._config.grpc_channel_factory,
            use_client_preference=self._config.use_client_preference,
            accepted_output_modes=self._config.accepted_output_modes,
            push_notification_configs=self._config.push_notification_configs,
        )
        return WrapperedA2AClientFactory(
            config=a2a_client_config,
            consumers=self._config.consumers,
            httpx_client=_httpx_client,
        )
