import time
import asyncio
from pydantic import BaseModel
from typing import Optional, List
from aworld.core.event.base import Message
from enum import Enum
from abc import ABC, abstractmethod, ABCMeta
from aworld.core.agent.base import is_agent_by_name
from aworld.core.tool.tool_desc import is_tool_by_name
from aworld.core.singleton import InheritanceSingleton, SingletonMeta
from aworld.core.event.base import Constants
from aworld.logs.util import logger


class RunNodeBusiType(Enum):
    AGENT = 'AGENT'
    TOOL = 'TOOL'
    TASK = 'TASK'
    TOOL_CALLBACK = 'TOOL_CALLBACK'

    @staticmethod
    def from_message_category(category: str) -> 'RunNodeBusiType':
        if category == Constants.AGENT:
            return RunNodeBusiType.AGENT
        if category == Constants.TOOL:
            return RunNodeBusiType.TOOL
        if category == Constants.TASK:
            return RunNodeBusiType.TASK
        if category == Constants.TOOL_CALLBACK:
            return RunNodeBusiType.TOOL_CALLBACK
        return None


class RunNodeStatus(Enum):
    INIT = 'INIT'
    RUNNING = 'RUNNING'
    BREAKED = 'BREAKED'
    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    TIMEOUT = 'TIMEOUT'


class HandleResult(BaseModel):
    name: str = None
    status: RunNodeStatus = None
    result_msg: Optional[str] = None
    result: Optional[Message] = None


class RunNode(BaseModel):
    # {busi_id}_{busi_type}
    node_id: Optional[str] = None
    busi_type: str = None
    busi_id: str = None
    session_id: str = None
    msg_id: Optional[str] = None  # input message id
    # busi_id of node that send the input message
    msg_from: Optional[str] = None
    parent_node_id: Optional[str] = None
    status: RunNodeStatus = None
    result_msg: Optional[str] = None
    results: Optional[List[HandleResult]] = None
    create_time: Optional[float] = None
    execute_time: Optional[float] = None
    end_time: Optional[float] = None


class StateStorage(ABC):
    @abstractmethod
    def get(self, node_id: str) -> RunNode:
        pass

    @abstractmethod
    def insert(self, node: RunNode):
        pass

    @abstractmethod
    def update(self, node: RunNode):
        pass

    @abstractmethod
    def query(self, session_id: str) -> List[RunNode]:
        pass


class StateStorageMeta(SingletonMeta, ABCMeta):
    pass


class InMemoryStateStorage(StateStorage, InheritanceSingleton, metaclass=StateStorageMeta):
    '''
    In memory state storage
    '''

    def __init__(self, max_session=1000):
        self._max_session = max_session
        self._nodes = {}  # {node_id: RunNode}
        self._ordered_session_ids = []
        self._session_nodes = {}  # {session_id: [RunNode, RunNode]}

    def get(self, node_id: str) -> RunNode:
        return self._nodes.get(node_id)

    def insert(self, node: RunNode):
        if node.session_id not in self._ordered_session_ids:
            self._ordered_session_ids.append(node.session_id)
            self._session_nodes.update({node.session_id: []})
        if node.node_id not in self._nodes:
            self._nodes.update({node.node_id: node})
            self._session_nodes[node.session_id].append(node)

        if len(self._ordered_session_ids) > self._max_session:
            oldest_session_id = self._ordered_session_ids.pop(0)
            session_nodes = self._session_nodes.pop(oldest_session_id)
            for node in session_nodes:
                self._nodes.pop(node.node_id)
        # logger.info(f"storage nodes: {self._nodes}")

    def update(self, node: RunNode):
        self._nodes[node.node_id] = node

    def query(self, session_id: str, msg_id: str = None) -> List[RunNode]:
        session_nodes = self._session_nodes.get(session_id, [])
        if msg_id:
            return [node for node in session_nodes if node.msg_id == msg_id]
        return session_nodes


class RuntimeStateManager(InheritanceSingleton):
    '''
    Runtime state manager
    '''

    def __init__(self, storage: StateStorage = InMemoryStateStorage.instance()):
        self.storage = storage

    def create_node(self,
                    busi_type: RunNodeBusiType,
                    busi_id: str,
                    session_id: str,
                    node_id: str = None,
                    parent_node_id: str = None,
                    msg_id: str = None,
                    msg_from: str = None) -> RunNode:
        '''
            create node and insert to storage
        '''
        node_id = node_id or msg_id
        node = self._find_node(node_id)
        if node:
            # raise Exception(f"node already exist, node_id: {node_id}")
            return
        if parent_node_id:
            parent_node = self._find_node(parent_node_id)
            if not parent_node:
                logger.warning(
                    f"parent node not exist, parent_node_id: {parent_node_id}")
        node = RunNode(node_id=node_id,
                       busi_type=busi_type.name,
                       busi_id=busi_id,
                       session_id=session_id,
                       msg_id=msg_id,
                       msg_from=msg_from,
                       parent_node_id=parent_node_id,
                       status=RunNodeStatus.INIT,
                       create_time=time.time())
        self.storage.insert(node)
        return node

    def run_node(self, node_id: str):
        '''
            set node status to RUNNING and update to storage
        '''
        logger.info(f"====== set node {node_id} running =======")
        node = self._node_exist(node_id)
        node.status = RunNodeStatus.RUNNING
        node.execute_time = time.time()
        self.storage.update(node)

    def save_result(self,
                    node_id: str,
                    result: HandleResult):
        '''
            save node execute result and update to storage
        '''
        node = self._node_exist(node_id)
        if not node.results:
            node.results = []
        node.results.append(result)
        self.storage.update(node)

    def break_node(self, node_id):
        '''
            set node status to BREAKED and update to storage
        '''
        node = self._node_exist(node_id)
        node.status = RunNodeStatus.BREAKED
        self.storage.update(node)

    def run_succeed(self,
                    node_id,
                    result_msg=None,
                    results: List[HandleResult] = None):
        '''
            set node status to SUCCESS and update to storage
        '''
        node = self._node_exist(node_id)
        node.status = RunNodeStatus.SUCCESS
        node.result_msg = result_msg
        node.end_time = time.time()
        if results:
            if not node.results:
                node.results = []
            node.results.extend(results)
        logger.info(f"====== run_succeed set node {node_id} succeed: {node} =======")

        self.storage.update(node)

    def run_failed(self,
                   node_id,
                   result_msg=None,
                   results: List[HandleResult] = None):
        '''
            set node status to FAILED and update to storage
        '''
        node = self._node_exist(node_id)
        node.status = RunNodeStatus.FAILED
        node.result_msg = result_msg
        node.end_time = time.time()
        if results:
            if not node.results:
                node.results = []
            node.results.extend(results)
        self.storage.update(node)

    def run_timeout(self,
                    node_id,
                    result_msg=None):
        '''
            set node status to TIMEOUT and update to storage
        '''
        node = self._node_exist(node_id)
        node.status = RunNodeStatus.TIMEOUT
        node.result_msg = result_msg
        self.storage.update(node)

    def get_node(self, node_id: str) -> RunNode:
        '''
            get node from storage
        '''
        return self._find_node(node_id)

    def get_nodes(self, session_id: str) -> List[RunNode]:
        '''
            get nodes from storage
        '''
        return self.storage.query(session_id)

    def _node_exist(self, node_id: str):
        node = self._find_node(node_id)
        if not node:
            raise Exception(f"node not found, node_id: {node_id}")
        return node

    def _find_node(self, node_id: str):
        return self.storage.get(node_id)

    def _judge_msg_from_busi_type(self, msg_from: str) -> RunNodeBusiType:
        '''
        judge msg_from busi_type
        '''
        if is_agent_by_name(msg_from):
            return RunNodeBusiType.AGENT
        if is_tool_by_name(msg_from):
            return RunNodeBusiType.TOOL
        return RunNodeBusiType.TASK

    async def wait_for_node_completion(self, node_id: str, timeout: float = 600.0, interval: float = 1.0) -> RunNode:
        '''Poll for node status until completion or timeout.

        Args:
            node_id: Node ID
            timeout: Timeout threshold in seconds
            interval: Polling interval in seconds

        Returns:
            RunNode: Node object

        Raises:
            Exception: If node does not exist
            TimeoutError: If waiting times out
        '''
        start_time = time.time()
        log_start_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        logger.info(f"wait for node completion: {node_id}, start_time:{log_start_time}")

        while True:
            node = self._find_node(node_id)
            if not node:
                raise Exception(f"Node not found, node_id: {node_id}")

            logger.info(f"====wait_for_node_completion node_id#{node_id}: {node}")

            # Check if node has completed
            if node.status in [RunNodeStatus.SUCCESS, RunNodeStatus.FAILED, RunNodeStatus.BREAKED,
                               RunNodeStatus.TIMEOUT]:
                return node

            # Check if timed out
            if time.time() - start_time > timeout:
                self.run_timeout(node_id, result_msg=f"Waiting for node completion timed out after {timeout} seconds")
                node = self._find_node(node_id)
                return node

            # Wait for the specified interval before polling again
            await asyncio.sleep(interval)


class EventRuntimeStateManager(RuntimeStateManager):

    def __init__(self, storage: StateStorage = InMemoryStateStorage.instance()):
        super().__init__(storage)

    def start_message_node(self, message: Message):
        '''
        create and start node while message handle started.
        '''
        run_node_busi_type = RunNodeBusiType.from_message_category(
            message.category)
        logger.info(
            f"start message node: {message.receiver}, busi_type={run_node_busi_type}, node_id={message.id}")
        if run_node_busi_type:
            self.create_node(
                node_id=message.id,
                busi_type=run_node_busi_type,
                busi_id=message.receiver or "",
                session_id=message.session_id,
                msg_id=message.id,
                msg_from=message.sender)
            self.run_node(message.id)

    def save_message_handle_result(self, name: str, message: Message, result: Message = None):
        '''
        save message handle result
        '''
        run_node_busi_type = RunNodeBusiType.from_message_category(
            message.category)
        if run_node_busi_type:
            if result and result.is_error():
                handle_result = HandleResult(
                    name=name,
                    status=RunNodeStatus.FAILED,
                    result=result)
            elif self.get_node(message.id).status ==RunNodeStatus.RUNNING:
                handle_result = HandleResult(
                    name=name,
                    status=RunNodeStatus.RUNNING,
                    result=result)
            else:
                handle_result = HandleResult(
                    name=name,
                    status=RunNodeStatus.SUCCESS,
                    result=result)
            self.save_result(node_id=message.id, result=handle_result)

    def end_message_node(self, message: Message):
        '''
        end node while message handle finished.
        '''
        run_node_busi_type = RunNodeBusiType.from_message_category(
            message.category)
        if run_node_busi_type:
            node = self._node_exist(node_id=message.id)
            status = RunNodeStatus.SUCCESS
            if node.results:
                for result in node.results:
                    if result.status == RunNodeStatus.FAILED:
                        status = RunNodeStatus.FAILED
                        break
            if status == RunNodeStatus.FAILED:
                self.run_failed(node_id=message.id)
            else:
                self.run_succeed(node_id=message.id)
