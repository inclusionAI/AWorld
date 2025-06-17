# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
Chunk Pipeline - 消息拆分处理管道

负责将混合类型的消息按照 prompt_type 进行拆分和分组处理
"""

import time
from typing import Dict, Any, List, Union, Optional, Tuple
from dataclasses import dataclass
from enum import Enum
import logging

from aworld.logs.util import logger


logger = logging.getLogger(__name__)


class MessageType(Enum):
    """Message type enum"""
    TEXT = "text"  # system, user, assistant 消息
    TOOL = "tool"  # tool 消息
    SYSTEM = "system"
    UNKNOWN = "unknown"


@dataclass
class MessageChunk:
    """Message chunk data structure"""
    message_type: MessageType
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    
    def __len__(self):
        return len(self.messages)
    
    def is_empty(self):
        return len(self.messages) == 0


@dataclass
class ChunkResult:
    """Chunk processing result"""
    chunks: List[MessageChunk]
    total_messages: int
    processing_time: float
    metadata: Dict[str, Any]


class ChunkPipeline:
    """Message chunking pipeline - responsible for splitting and merging messages"""
    
    def __init__(self, 
                 preserve_order: bool = True,
                 merge_consecutive: bool = True):
        """
        Initialize chunk pipeline
        
        Args:
            preserve_order: Whether to preserve message order
            merge_consecutive: Whether to merge consecutive messages of the same type
        """
        self.preserve_order = preserve_order
        self.merge_consecutive = merge_consecutive
        self.stats = {
            "total_processed": 0,
            "total_chunks_created": 0,
            "processing_time": 0.0
        }
    
    def _determine_message_type(self, message: Dict[str, Any]) -> MessageType:
        """Determine message type"""
        role = message.get("role", "").lower()
        
        if role == "system":
            return MessageType.SYSTEM
        elif role in ["function", "tool"]:
            return MessageType.TOOL
        elif role in ["user", "assistant"]:
            return MessageType.TEXT
        else:
            logger.warning(f"Unknown message role: {role}")
            return MessageType.UNKNOWN
    
    def split_messages(self, 
                      messages: List[Dict[str, Any]], 
                      metadata: Dict[str, Any] = None) -> ChunkResult:
        """
        Split messages into chunks
        
        Args:
            messages: Message list
            metadata: Metadata
            
        Returns:
            Chunk result
        """
        start_time = time.time()
        
        if not messages:
            return ChunkResult(
                chunks=[],
                total_messages=0,
                processing_time=0.0,
                metadata=metadata or {}
            )
        
        chunks = []
        current_chunk_type = None
        current_chunk_messages = []
        
        for i, message in enumerate(messages):
            msg_type = self._determine_message_type(message)
            
            # If merge_consecutive is enabled and types match, add to current chunk
            if (self.merge_consecutive and 
                current_chunk_type == msg_type and 
                current_chunk_messages):
                current_chunk_messages.append(message)
            else:
                # If there's a current chunk, save it
                if current_chunk_messages:
                    chunk_metadata = (metadata or {}).copy()
                    chunk_metadata.update({
                        "chunk_index": len(chunks),
                        "start_message_index": i - len(current_chunk_messages),
                        "end_message_index": i - 1,
                        "message_count": len(current_chunk_messages),
                        "original_messages": current_chunk_messages.copy()  # 保存原始消息
                    })
                    
                    # 根据消息类型将消息合并为字符串
                    if current_chunk_type == MessageType.TEXT:
                        # 使用 _messages_to_string 合并 TEXT 类型消息
                        merged_content = self._messages_to_string(current_chunk_messages)
                        # 创建单个合并消息
                        merged_message = {
                            "role": "merged_text",
                            "content": merged_content,
                            "original_count": len(current_chunk_messages)
                        }
                        chunk_messages = [merged_message]
                    elif current_chunk_type == MessageType.TOOL:
                        # 使用 _tool_messages_to_string 合并 TOOL 类型消息
                        merged_content = self._tool_messages_to_string(current_chunk_messages)
                        # 创建单个合并消息
                        merged_message = {
                            "role": "merged_tool",
                            "content": merged_content,
                            "original_count": len(current_chunk_messages)
                        }
                        chunk_messages = [merged_message]
                    else:
                        # 未知类型保持原样
                        chunk_messages = current_chunk_messages.copy()
                    
                    chunks.append(MessageChunk(
                        message_type=current_chunk_type,
                        messages=chunk_messages,
                        metadata=chunk_metadata
                    ))
                
                # Start new chunk
                current_chunk_messages = [message]
                current_chunk_type = msg_type
        
        # Add last chunk
        if current_chunk_messages:
            chunk_metadata = (metadata or {}).copy()
            chunk_metadata.update({
                "chunk_index": len(chunks),
                "start_message_index": len(messages) - len(current_chunk_messages),
                "end_message_index": len(messages) - 1,
                "message_count": len(current_chunk_messages),
                "original_messages": current_chunk_messages.copy()  # 保存原始消息
            })
            
            # 根据消息类型将消息合并为字符串
            if current_chunk_type == MessageType.TEXT:
                # 使用 _messages_to_string 合并 TEXT 类型消息
                merged_content = self._messages_to_string(current_chunk_messages)
                # 创建单个合并消息
                merged_message = {
                    "role": "merged_text",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            elif current_chunk_type == MessageType.TOOL:
                # 使用 _tool_messages_to_string 合并 TOOL 类型消息
                merged_content = self._tool_messages_to_string(current_chunk_messages)
                # 创建单个合并消息
                merged_message = {
                    "role": "merged_tool",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            else:
                # 未知类型保持原样
                chunk_messages = current_chunk_messages.copy()
            
            chunks.append(MessageChunk(
                message_type=current_chunk_type,
                messages=chunk_messages,
                metadata=chunk_metadata
            ))
        
        processing_time = time.time() - start_time
        
        # 更新统计
        self.stats["total_processed"] += len(messages)
        self.stats["total_chunks_created"] += len(chunks)
        self.stats["processing_time"] += processing_time
        
        # 构建结果元数据
        result_metadata = (metadata or {}).copy()
        result_metadata.update({
            "chunk_count": len(chunks),
            "text_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.TEXT),
            "tool_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.TOOL),
            "unknown_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.UNKNOWN),
            "preserve_order": self.preserve_order,
            "merge_consecutive": self.merge_consecutive,
            "processing_time": processing_time,
            "string_merge_applied": True  # 标记已应用字符串合并
        })
        
        logger.debug(f"消息拆分完成: {len(messages)} 条消息 -> {len(chunks)} 个块 (已应用字符串合并)")
        
        return ChunkResult(
            chunks=chunks,
            total_messages=len(messages),
            processing_time=processing_time,
            metadata=result_metadata
        )
    
    def merge_chunks(self, 
                    chunks: List[MessageChunk], 
                    preserve_type_order: bool = True) -> List[Dict[str, Any]]:
        """
        Merge chunks back into message list
        
        Args:
            chunks: Chunk list
            preserve_type_order: Whether to preserve type order
            
        Returns:
            Message list
        """
        if not chunks:
            return []
        
        if preserve_type_order and self.preserve_order:
            # 按原始顺序合并
            sorted_chunks = sorted(chunks, key=lambda x: x.metadata.get("chunk_index", 0))
        else:
            # 按类型分组合并（先文本后工具）
            text_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TEXT]
            tool_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TOOL]
            unknown_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.UNKNOWN]
            sorted_chunks = text_chunks + tool_chunks + unknown_chunks
        
        merged_messages = []
        for chunk in sorted_chunks:
            chunk_messages = []
            
            for message in chunk.messages:
                # 检查是否是合并的消息，需要拆分
                if message.get("role") == "merged_text":
                    # 这是合并的 TEXT 类型消息，需要拆分
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        # 使用 _string_to_messages 拆分
                        split_messages = self._string_to_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        # 如果没有原始消息信息，直接解析字符串
                        split_messages = self._string_to_messages(merged_content, [])
                        chunk_messages.extend(split_messages)
                        
                elif message.get("role") == "merged_tool":
                    # 这是合并的 TOOL 类型消息，需要拆分
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        # 使用 _string_to_tool_messages 拆分
                        split_messages = self._string_to_tool_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        # 如果没有原始消息信息，直接解析字符串
                        split_messages = self._string_to_tool_messages(merged_content, "")
                        chunk_messages.extend(split_messages)
                        
                else:
                    # 普通消息直接添加
                    chunk_messages.append(message)
            
            merged_messages.extend(chunk_messages)
        
        return merged_messages
    
    def get_chunks_by_type(self, 
                          chunks: List[MessageChunk], 
                          message_type: MessageType) -> List[MessageChunk]:
        """
        获取指定类型的所有块
        
        Args:
            chunks: 消息块列表
            message_type: 消息类型
            
        Returns:
            指定类型的块列表
        """
        return [chunk for chunk in chunks if chunk.message_type == message_type]
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取处理统计信息"""
        return self.stats.copy()
    
    def reset_statistics(self):
        """重置统计信息"""
        self.stats = {
            "total_processed": 0,
            "total_chunks_created": 0,
            "processing_time": 0.0
        }

    @staticmethod
    def _messages_to_string(messages: List[Dict[str, str]]) -> str:
        """将OpenAI消息格式转换为字符串"""
        content_parts = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            content_parts.append(f"[{role.upper()}]: {content}")
        return "\n".join(content_parts)
    
    @staticmethod
    def _string_to_messages(content: str, # 处理后prompt，是一个字符串
                            messages: List[Dict[str, str]] # 处理前prompt
                            ) -> List[Dict[str, str]]:
        # 还原所有assistant回复的tool_calls
        tool_calls = []
        if messages:
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls") is not None:
                    tool_calls += msg["tool_calls"]

        """将字符串转换为OpenAI消息格式"""
        result_messages = []
        lines = content.split('\n')
        current_role = 'user'
        current_content = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # 保存之前的消息
                if current_content:
                    result_messages.append({
                        'role': current_role,
                        'content': '\n'.join(current_content).strip()
                    })
                    current_content = []
                
                # 解析新的角色
                role_end = line.find(']:')
                role = line[1:role_end].lower()
                if role in ['system', 'user', 'assistant']:
                    current_role = role
                    content_part = line[role_end + 2:].strip()
                    if content_part:
                        current_content.append(content_part)
                else:
                    current_content.append(line)
            else:
                current_content.append(line)

        # 保存最后的消息
        if current_content:
            result_messages.append({
                'role': current_role,
                'content': '\n'.join(current_content).strip(),
            })
        final_messages = result_messages if result_messages else [{'role': 'user', 'content': content}]

        # 拼接tool_calls结果
        if tool_calls is not None and len(tool_calls) > 0:
            tool_call_chunk = {
                'role': 'assistant',
                'content': None,
                'tool_calls': tool_calls
            }
            final_messages.append(tool_call_chunk)

        return final_messages

    def _tool_messages_to_string(self, messages: List[Dict[str, str]]) -> str:
        """将tool消息格式转换为字符串"""
        content_parts = []
        for msg in messages:
            role = msg.get('role', 'tool')
            content = msg.get('content', '')
            tool_call_id = msg.get('tool_call_id', '')
            name = msg.get('name', '')
    
            if role == 'tool':
                header = f"[TOOL:{name}:{tool_call_id}]"
            else:
                header = f"[{role.upper()}]"
    
            content_parts.append(f"{header}: {content}")
        return "\n".join(content_parts)
    
    def _string_to_tool_messages(self, content: str, original_prompt: Union[str, List[Dict[str, str]]]) -> List[Dict[str, str]]:
        """将字符串转换为tool消息格式"""
        messages = []
        lines = content.split('\n')
        current_role = 'tool'
        current_content = []
        current_tool_call_id = ''
        current_name = ''
    
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # 保存之前的消息
                if current_content:
                    msg = {
                        'role': current_role,
                        'content': '\n'.join(current_content).strip()
                    }
                    if current_role == 'tool':
                        if current_tool_call_id:
                            msg['tool_call_id'] = current_tool_call_id
                        if current_name:
                            msg['name'] = current_name
                    messages.append(msg)
                    current_content = []
    
                # 解析新的角色和工具信息
                role_end = line.find(']:')
                role_part = line[1:role_end]
                content_part = line[role_end + 2:].strip()
    
                if role_part.startswith('TOOL:'):
                    # 解析工具消息格式: [TOOL:name:tool_call_id]
                    current_role = 'tool'
                    tool_parts = role_part.split(':')
                    if len(tool_parts) >= 2:
                        current_name = tool_parts[1]
                    if len(tool_parts) >= 3:
                        current_tool_call_id = tool_parts[2]
                else:
                    current_role = role_part.lower()
                    current_tool_call_id = ''
                    current_name = ''
    
                if content_part:
                    current_content.append(content_part)
            else:
                current_content.append(line)
    
        # 保存最后的消息
        if current_content:
            msg = {
                'role': current_role,
                'content': '\n'.join(current_content).strip()
            }
            if current_role == 'tool':
                if current_tool_call_id:
                    msg['tool_call_id'] = current_tool_call_id
                if current_name:
                    msg['name'] = current_name
            messages.append(msg)
    
        # 如果没有解析出消息，返回原始格式
        if not messages and isinstance(original_prompt, list):
            return original_prompt
        elif not messages:
            return [{'role': 'tool', 'content': content}]
    
        return messages


class AdvancedChunkPipeline(ChunkPipeline):
    """高级消息拆分管道，支持更复杂的拆分策略"""
    
    def __init__(self, 
                 preserve_order: bool = True,
                 merge_consecutive: bool = True,
                 max_chunk_size: int = None,
                 split_by_tool_name: bool = False):
        """
        初始化高级拆分管道
        
        Args:
            preserve_order: 是否保持消息原始顺序
            merge_consecutive: 是否合并连续的同类型消息
            max_chunk_size: 最大块大小（消息数量）
            split_by_tool_name: 是否按工具名称进一步拆分tool消息
        """
        super().__init__(preserve_order, merge_consecutive)
        self.max_chunk_size = max_chunk_size
        self.split_by_tool_name = split_by_tool_name
    
    def split_messages(self, 
                      messages: List[Dict[str, Any]], 
                      metadata: Dict[str, Any] = None) -> ChunkResult:
        """
        高级消息拆分，支持按大小和工具名称拆分
        """
        # 先进行基础拆分
        basic_result = super().split_messages(messages, metadata)
        
        if not self.max_chunk_size and not self.split_by_tool_name:
            return basic_result
        
        # 进一步处理块
        refined_chunks = []
        
        for chunk in basic_result.chunks:
            if chunk.message_type == MessageType.TOOL and self.split_by_tool_name:
                # 按工具名称拆分tool消息
                tool_chunks = self._split_tool_chunk_by_name(chunk)
                refined_chunks.extend(tool_chunks)
            elif self.max_chunk_size and len(chunk) > self.max_chunk_size:
                # 按大小拆分大块
                size_chunks = self._split_chunk_by_size(chunk)
                refined_chunks.extend(size_chunks)
            else:
                refined_chunks.append(chunk)
        
        # 更新结果
        basic_result.chunks = refined_chunks
        basic_result.metadata["refined_chunk_count"] = len(refined_chunks)
        basic_result.metadata["split_by_tool_name"] = self.split_by_tool_name
        basic_result.metadata["max_chunk_size"] = self.max_chunk_size
        
        return basic_result
    
    def _split_tool_chunk_by_name(self, chunk: MessageChunk) -> List[MessageChunk]:
        """按工具名称拆分tool块"""
        if chunk.message_type != MessageType.TOOL:
            return [chunk]
        
        tool_groups = {}
        for message in chunk.messages:
            tool_name = message.get("name", "unknown_tool")
            if tool_name not in tool_groups:
                tool_groups[tool_name] = []
            tool_groups[tool_name].append(message)
        
        sub_chunks = []
        for i, (tool_name, messages) in enumerate(tool_groups.items()):
            sub_metadata = chunk.metadata.copy()
            sub_metadata.update({
                "tool_name": tool_name,
                "sub_chunk_index": i,
                "parent_chunk_index": chunk.metadata.get("chunk_index", 0)
            })
            
            sub_chunks.append(MessageChunk(
                message_type=MessageType.TOOL,
                messages=messages,
                metadata=sub_metadata
            ))
        
        return sub_chunks
    
    def _split_chunk_by_size(self, chunk: MessageChunk) -> List[MessageChunk]:
        """按大小拆分块"""
        if len(chunk) <= self.max_chunk_size:
            return [chunk]
        
        sub_chunks = []
        messages = chunk.messages
        
        for i in range(0, len(messages), self.max_chunk_size):
            sub_messages = messages[i:i + self.max_chunk_size]
            sub_metadata = chunk.metadata.copy()
            sub_metadata.update({
                "sub_chunk_index": len(sub_chunks),
                "parent_chunk_index": chunk.metadata.get("chunk_index", 0),
                "size_split": True
            })
            
            sub_chunks.append(MessageChunk(
                message_type=chunk.message_type,
                messages=sub_messages,
                metadata=sub_metadata
            ))
        
        return sub_chunks 