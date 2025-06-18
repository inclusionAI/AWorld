
from pydantic import BaseModel
from typing import Optional, List
from aworld.core.event.base import Message
from enum import Enum
from abc import ABC, abstractmethod
from aworld.core.agent.base import is_agent_by_name
from aworld.core.tool.tool_desc import is_tool_by_name


class RunNodeBusiType(Enum):
    AGENT = 'AGENT'
    TOOL = 'TOOL'
    TASK = 'TASK'


class RunNodeStatus(Enum):
    INIT = 'INIT'
    RUNNING = 'RUNNING'
    BREAKED = 'BREAKED'
    SUCCESS = 'SUCCESS'
    FAILED = 'FAILED'
    TIMEOUNT = 'TIMEOUNT'


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
    result: Optional[Message] = None


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


class InMemoryStateStorage(StateStorage):
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

    def update(self, node: RunNode):
        self._nodes[node.node_id] = node

    def query(self, session_id: str, msg_id: str = None) -> List[RunNode]:
        session_nodes = self._session_nodes.get(session_id, [])
        if msg_id:
            return [node for node in session_nodes if node.msg_id == msg_id]
        return session_nodes


class RuntimeStateManager:
    '''
    Runtime state manager
    '''

    def __init__(self, storage: StateStorage = InMemoryStateStorage()):
        self.storage = storage

    def create_node(self,
                    busi_type: RunNodeBusiType,
                    busi_id: str,
                    session_id: str,
                    msg_id: str = None,
                    msg_from: str = None) -> RunNode:
        '''
            create node and insert to storage
        '''
        node = self._find_node(busi_type, busi_id)
        if node:
            raise Exception(
                f"node already exist, busi_type: {busi_type}, busi_id: {busi_id}")
        parent_node_id = None
        if msg_from:
            busi_type = self._judge_msg_from_busi_type(msg_from)
            parent_node_id = self._build_node_id(busi_type, msg_from)
        node = RunNode(node_id=self._build_node_id(busi_type, busi_id),
                       busi_type=busi_type,
                       busi_id=busi_id,
                       session_id=session_id,
                       msg_id=msg_id,
                       msg_from=msg_from,
                       parent_node_id=parent_node_id,
                       status=RunNodeStatus.INIT)
        self.storage.insert(node)
        return node

    def run_node(self, busi_type: RunNodeBusiType, busi_id: str):
        '''
            set node status to RUNNING and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.status = RunNodeStatus.RUNNING
        self.storage.update(node)

    def save_result(self,
                    busi_type: str,
                    busi_id: str,
                    message: Message):
        '''
            save node execute result and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.result = message
        self.storage.update(node)

    def break_node(self, busi_type, busi_id):
        '''
            set node status to BREAKED and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.status = RunNodeStatus.BREAKED
        self.storage.update(node)

    def run_succeed(self, busi_type,  busi_id,  result: Message = None):
        '''
            set node status to SUCCESS and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.status = RunNodeStatus.SUCCESS
        node.result = result
        self.storage.update(node)

    def run_failed(self, busi_type, busi_id, result: Message = None):
        '''
            set node status to FAILED and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.status = RunNodeStatus.FAILED
        node.result = result
        self.storage.update(node)

    def run_timeout(self, busi_type, busi_id):
        '''
            set node status to TIMEOUNT and update to storage
        '''
        node = self._node_exist(busi_type, busi_id)
        node.status = RunNodeStatus.TIMEOUNT
        self.storage.update(node)

    def get_node(self, busi_type: RunNodeBusiType, busi_id: str) -> RunNode:
        '''
            get node from storage
        '''
        return self._find_node(busi_type, busi_id)

    def get_nodes(self, session_id: str) -> List[RunNode]:
        '''
            get nodes from storage
        '''
        return self.storage.query(session_id)

    def _node_exist(self, busi_type: RunNodeBusiType, busi_id: str):
        node = self._find_node(busi_type, busi_id)
        if not node:
            raise Exception(
                f"node not found, busi_type: {busi_type}, busi_id: {busi_id}")
        return node

    def _find_node(self, busi_type: RunNodeBusiType, busi_id: str):
        return self.storage.get(self._build_node_id(busi_type, busi_id))

    def _build_node_id(self, busi_type: RunNodeBusiType, busi_id: str) -> str:
        return f"{busi_id}_{busi_type.value}"

    def _judge_msg_from_busi_type(self, msg_from: str) -> RunNodeBusiType:
        '''
        judge msg_from busi_type
        '''
        if is_agent_by_name(msg_from):
            return RunNodeBusiType.AGENT
        if is_tool_by_name(msg_from):
            return RunNodeBusiType.TOOL
        return RunNodeBusiType.TASK
