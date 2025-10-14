from typing import Optional, Dict, Any, List
from datetime import datetime

from aworld.memory.models import (
    MemoryMessage, MemorySystemMessage, MemoryHumanMessage, MemoryAIMessage, 
    MemoryToolMessage, MessageMetadata
)
from aworld.models.model_response import ToolCall


def from_dict_to_memory_message(data: Dict[str, Any]) -> Optional[MemoryMessage]:
    """
    根据 role 将 dict 转换为 MemoryMessage
    
    Args:
        data: 包含消息数据的字典
        
    Returns:
        MemoryMessage 对象，如果转换失败则返回 None
    """
    if not data:
        return None
    
    # 提取基本信息
    content = data.get('content', '')
    metadata = data.get('metadata', {})
    role = metadata.get('role', '')

    # 基础数据
    base_data = {
        'id': data.get('id'),
        'created_at': data.get('created_at'),
        'updated_at': data.get('updated_at'),
        'tags': data.get('tags', []),
        'version': data.get('version'),
        'deleted': data.get('deleted', False)
    }
    
    # 根据 role 创建对应的 MemoryMessage
    if role == 'system':
        return MemorySystemMessage(
            content=content,
            metadata=MessageMetadata(**metadata),
            **base_data
        )
    elif role == 'user':
        return MemoryHumanMessage(
            content=content,
            metadata=MessageMetadata(**metadata),
            **base_data
        )
    elif role == 'assistant':
        # 处理 tool_calls
        tool_calls = []
        tool_calls_data = metadata.get('tool_calls', [])
        for tool_call_data in tool_calls_data:
            if isinstance(tool_call_data, dict):
                tool_call = ToolCall.from_dict(tool_call_data)
                if tool_call:
                    tool_calls.append(tool_call)
        
        return MemoryAIMessage(
            content=content,
            tool_calls=tool_calls,
            metadata=MessageMetadata(**metadata),
            **base_data
        )
    elif role == 'tool':
        return MemoryToolMessage(
            content=content,
            tool_call_id=metadata.get('tool_call_id'),
            status=metadata.get('status', 'success'),
            metadata=MessageMetadata(**metadata),
            **base_data
        )
    else:
        # 默认返回 MemoryMessage
        return MemoryMessage(
            content=content,
            metadata=metadata,
            **base_data
        )
