import json
import uuid
from datetime import datetime
from typing import Dict, List, Any, Optional, Set, Tuple, OrderedDict

from aworld.core.event.base import Message
from aworld.logs.util import logger
from aworld.runners.state_manager import RuntimeStateManager, RunNodeBusiType


class AgentCall:
    """表示一次Agent调用的数据结构"""
    
    def __init__(self, id: str, caller_id: str, callee_id: str, as_tool: bool = False):
        self.caller_id = caller_id
        self.callee_id = callee_id
        self.as_tool = as_tool
        self.timestamp = datetime.now()
        self.call_id = id
        self.status = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "caller_id": self.caller_id,
            "callee_id": self.callee_id,
            "as_tool": self.as_tool,
            "timestamp": self.timestamp.isoformat(),
            "call_id": self.call_id,
            "status": self.status
        }


class CallHierarchyNode:
    """表示调用层次结构中的节点"""
    
    def __init__(self, id: str, agent_id: str, level: int = 0):
        self.id = id
        self.agent_id = agent_id
        self.level = level
        self.children: List[CallHierarchyNode] = []
    
    def add_child(self, node: 'CallHierarchyNode'):
        self.children.append(node)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "agent_id": self.agent_id,
            "level": self.level,
            "children": [child.to_dict() for child in self.children]
        }


class AgentCallTracker:
    """跟踪和记录Agent之间的调用关系"""
    
    def __init__(self):
        # 记录直接调用关系
        self.direct_calls: Dict[str, List[AgentCall]] = {}
        # 记录作为工具的调用关系
        self.as_tool_calls: Dict[str, Dict[str, List[AgentCall]]] = dict()
        self.agent_tools_record: Dict[int, Dict[str, AgentCall]] = dict()
        # 记录调用层次结构
        self.call_hierarchy: OrderedDict[int, List[CallHierarchyNode]] = OrderedDict[int, list[CallHierarchyNode]]()
        # 记录每个Agent的级别
        self.agent_levels: Dict[str, int] = {}
        # 记录已处理的消息ID，避免重复处理
        self._processed_messages: Set[str] = set()
        self._track_nodes: Dict[str, CallHierarchyNode] = dict()
    
    def track_call(self, caller_id: str, callee_id: str, as_tool: bool = False, message: Message = None) -> bool:
        """
        记录一次Agent调用
        
        Args:
            caller_id: 调用者ID
            callee_id: 被调用者ID
            as_tool: 是否作为工具调用
            message_id: 消息ID，用于去重
            
        Returns:
            bool: 是否成功记录（如果消息已处理过，返回False）
        """
        message_id = message.id
        if message_id and message_id in self._processed_messages:
            return False
        
        # 创建调用记录
        call = AgentCall(message_id, caller_id, callee_id, as_tool)


        pre_node_id = message.headers.get("pre_message_id")
        if not pre_node_id:
            logger.info(f"{message_id} is root_node of task {message.task_id}")
        pre_node = self._get_track_node(pre_node_id)
        # 更新调用层次结构
        # self._update_call_hierarchy(message_id, pre_node, caller_id, callee_id, as_tool)
        self._update_call_hierarchy(call, pre_node, call_type=message.call_type)


        # 记录直接调用
        if not as_tool:
            if caller_id not in self.direct_calls:
                self.direct_calls[caller_id] = []
            self.direct_calls[caller_id].append(call)
        
        # 记录作为工具的调用
        else:
            if caller_id not in self.as_tool_calls:
                self.as_tool_calls[caller_id] = {}
            if callee_id not in self.as_tool_calls[caller_id]:
                self.as_tool_calls[caller_id][callee_id] = []
            self.as_tool_calls[caller_id][callee_id].append(call)

        # 标记消息为已处理
        if message_id:
            self._processed_messages.add(message_id)
        
        return True

    def _get_track_node(self, node_id) -> CallHierarchyNode | None:
        for level, nodes in self.call_hierarchy.items():
            for node in nodes:
                if node.id == node_id:
                    return node
        state_manager = RuntimeStateManager.instance()
        result_node = state_manager._find_node(node_id)
        if result_node and result_node.busi_type == RunNodeBusiType.TOOL.name:
            parent_message_id = result_node.metadata.get("pre_message_id")
            if parent_message_id:
                for level, nodes in self.call_hierarchy.items():
                    for node in nodes:
                        if node.id == parent_message_id:
                            return node
        return None

    def _insert_track_node(self, node: CallHierarchyNode):
        self._track_nodes[node.id] = node

    # def _update_call_hierarchy(self, id: str, pre_node: CallHierarchyNode, caller_id: str, callee_id: str, as_tool: bool):
    def _update_call_hierarchy(self, call: AgentCall, pre_node: CallHierarchyNode, call_type: str = None):
        """更新调用层次结构"""
        id = call.call_id
        caller_id = call.caller_id
        callee_id = call.callee_id
        as_tool = call.as_tool
        # 确定调用者的级别
        # caller_level = self.agent_levels.get(caller_id, 0)
        caller_level = pre_node.level if pre_node else 0

        # 确定被调用者的级别
        callee_level = caller_level
        agent_tool_call = self.agent_tools_record.get(caller_level, {}).get(caller_id, None)
        logger.info(f"agent_tool_call: {agent_tool_call}")
        if as_tool:
            callee_level = caller_level + 1
            self.agent_tools_record.setdefault(callee_level, {})[callee_id] = call
        elif call_type == "tool_result":
            # 当前message是工具调用结果返回给agent
            callee_level = caller_level
        elif agent_tool_call and agent_tool_call.caller_id == callee_id:
            # 当前callee 是tool_caller，当前message作为tool_result返回到agent
            callee_level = caller_level - 1
            self.agent_tools_record.get(caller_level, {}).pop(caller_id, None)

        if callee_level not in self.call_hierarchy:
            self.call_hierarchy[callee_level] = []
        callee_node = CallHierarchyNode(id, callee_id, callee_level)
        self.call_hierarchy[callee_level].append(callee_node)
        if as_tool and pre_node:  # 只要as_tool就一定会有pre_node
            pre_node.add_child(callee_node)
        
        # # 更新被调用者的级别
        # if callee_id not in self.agent_levels or callee_level > self.agent_levels[callee_id]:
        #     self.agent_levels[callee_id] = callee_level
        #
        # # 更新调用层次结构
        # if caller_id not in self.call_hierarchy:
        #     self.call_hierarchy[caller_id] = CallHierarchyNode(id, caller_id, caller_level)
        #
        # # 检查是否已经存在该子节点
        # for child in self.call_hierarchy[caller_id].children:
        #     if child.agent_id == callee_id:
        #         return
        # # 添加新的子节点
        # caller_level = self.call_hierarchy[caller_id].level
        #
        # if callee_node.level > caller_level:
        #     self.call_hierarchy[caller_id].add_child(callee_node)
        # # 确保被调用者也有一个节点
        # if callee_id not in self.call_hierarchy:
        #     self.call_hierarchy[callee_id] = callee_node

    
    def get_call_graph(self) -> Dict[str, Any]:
        """获取完整的调用关系图"""
        return {
            "direct_calls": {
                caller_id: [call.to_dict() for call in calls]
                for caller_id, calls in self.direct_calls.items()
            },
            "as_tool_calls": {
                caller_id: {
                    callee_id: [call.to_dict() for call in calls]
                    for callee_id, calls in callees.items()
                }
                for caller_id, callees in self.as_tool_calls.items()
            },
            "hierarchy": {
                level: [node.to_dict() for node in nodes]
                for level, nodes in self.call_hierarchy.items()
            },
            # "hierarchy": {
            #     agent_id: node.to_dict()
            #     for agent_id, node in self.call_hierarchy.items()
            #     if node.level == 0  # 只包含根节点
            # },
            "agent_levels": self.agent_levels
        }
    
    def get_agent_level(self, agent_id: str) -> int:
        """获取指定Agent的级别"""
        return self.agent_levels.get(agent_id, 0)
    
    def get_root_agents(self) -> List[str]:
        """获取所有根Agent（级别为0的Agent）"""
        return [agent_id for agent_id, level in self.agent_levels.items() if level == 0]
    
    def get_agent_children(self, agent_id: str) -> List[Tuple[str, bool]]:
        """
        获取指定Agent的所有子Agent
        
        Returns:
            List[Tuple[str, bool]]: (agent_id, as_tool)列表
        """
        children = []
        
        # 添加直接调用的子Agent
        if agent_id in self.direct_calls:
            for call in self.direct_calls[agent_id]:
                children.append((call.callee_id, False))
        
        # 添加作为工具调用的子Agent
        if agent_id in self.as_tool_calls:
            for callee_id, calls in self.as_tool_calls[agent_id].items():
                if calls:  # 确保有调用记录
                    children.append((callee_id, True))
        
        return children
    
    def export_to_json(self, filepath: str):
        """将调用关系导出为JSON文件"""
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(self.get_call_graph(), f, ensure_ascii=False, indent=2) 