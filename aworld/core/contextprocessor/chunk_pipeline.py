# coding: utf-8
# Copyright (c) 2025 inclusionAI.

"""
Chunk Pipeline - Message chunking processing pipeline

Responsible for splitting and grouping mixed-type messages according to prompt_type
"""

import time
from typing import Dict, Any, List, Union, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from aworld.logs.util import logger


class MessageType(Enum):
    """Message type enum"""
    TEXT = "text"  # system, user, assistant messages
    TOOL = "tool"  # tool messages
    UNKNOWN = "unknown"


@dataclass
class MessageChunk:
    """Message chunk containing messages of the same type"""
    message_type: MessageType
    messages: List[Dict[str, Any]]
    metadata: Dict[str, Any]
    
    def __len__(self):
        return len(self.messages)
    
    def is_empty(self):
        return len(self.messages) == 0


@dataclass
class ChunkResult:
    """Chunking result"""
    chunks: List[MessageChunk]
    total_messages: int
    processing_time: float
    metadata: Dict[str, Any]


class ChunkPipeline:
    """Message chunking pipeline"""
    
    def __init__(self, 
                 preserve_order: bool = True,
                 merge_consecutive: bool = True):
        """
        Initialize chunking pipeline
        
        Args:
            preserve_order: Whether to preserve original message order
            merge_consecutive: Whether to merge consecutive messages of the same type
        """
        self.preserve_order = preserve_order
        self.merge_consecutive = merge_consecutive
        self.stats = {
            "total_processed": 0,
            "total_chunks_created": 0,
            "processing_time": 0.0
        }
    
    def classify_message(self, message: Dict[str, Any]) -> MessageType:
        """
        Classify single message
        
        Args:
            message: OpenAI format message
            
        Returns:
            Message type
        """
        role = message.get("role", "")
        
        if role in ["system", "user", "assistant"]:
            return MessageType.TEXT
        elif role == "tool":
            return MessageType.TOOL
        else:
            logger.warning(f"Unknown message role: {role}")
            return MessageType.UNKNOWN
    
    def split_messages(self, 
                      messages: List[Dict[str, Any]], 
                      metadata: Dict[str, Any] = None) -> ChunkResult:
        """
        Split message list by type into chunks, and merge messages of the same type into strings
        
        Args:
            messages: OpenAI format message list
            metadata: Metadata
            
        Returns:
            Chunking result
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
            msg_type = self.classify_message(message)
            
            # If it's a new type or not merging consecutive messages
            if (current_chunk_type != msg_type or 
                not self.merge_consecutive):
                
                # Save current chunk (if it has content)
                if current_chunk_messages:
                    chunk_metadata = (metadata or {}).copy()
                    chunk_metadata.update({
                        "chunk_index": len(chunks),
                        "start_message_index": i - len(current_chunk_messages),
                        "end_message_index": i - 1,
                        "message_count": len(current_chunk_messages),
                        "original_messages": current_chunk_messages.copy()  # Save original messages
                    })
                    
                    # Merge messages into string based on message type
                    if current_chunk_type == MessageType.TEXT:
                        # Use _messages_to_string to merge TEXT type messages
                        merged_content = self._messages_to_string(current_chunk_messages)
                        # Create single merged message
                        merged_message = {
                            "role": "merged_text",
                            "content": merged_content,
                            "original_count": len(current_chunk_messages)
                        }
                        chunk_messages = [merged_message]
                    elif current_chunk_type == MessageType.TOOL:
                        # Use _tool_messages_to_string to merge TOOL type messages
                        merged_content = self._tool_messages_to_string(current_chunk_messages)
                        # Create single merged message
                        merged_message = {
                            "role": "merged_tool",
                            "content": merged_content,
                            "original_count": len(current_chunk_messages)
                        }
                        chunk_messages = [merged_message]
                    else:
                        # Keep unknown types as is
                        chunk_messages = current_chunk_messages.copy()
                    
                    chunks.append(MessageChunk(
                        message_type=current_chunk_type,
                        messages=chunk_messages,
                        metadata=chunk_metadata
                    ))
                
                # Start new chunk
                current_chunk_type = msg_type
                current_chunk_messages = [message]
            else:
                # Add to current chunk
                current_chunk_messages.append(message)
        
        # Process last chunk
        if current_chunk_messages:
            chunk_metadata = (metadata or {}).copy()
            chunk_metadata.update({
                "chunk_index": len(chunks),
                "start_message_index": len(messages) - len(current_chunk_messages),
                "end_message_index": len(messages) - 1,
                "message_count": len(current_chunk_messages),
                "original_messages": current_chunk_messages.copy()  # Save original messages
            })
            
            # Merge messages into string based on message type
            if current_chunk_type == MessageType.TEXT:
                # Use _messages_to_string to merge TEXT type messages
                merged_content = self._messages_to_string(current_chunk_messages)
                # Create single merged message
                merged_message = {
                    "role": "merged_text",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            elif current_chunk_type == MessageType.TOOL:
                # Use _tool_messages_to_string to merge TOOL type messages
                merged_content = self._tool_messages_to_string(current_chunk_messages)
                # Create single merged message
                merged_message = {
                    "role": "merged_tool",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            else:
                # Keep unknown types as is
                chunk_messages = current_chunk_messages.copy()
            
            chunks.append(MessageChunk(
                message_type=current_chunk_type,
                messages=chunk_messages,
                metadata=chunk_metadata
            ))
        
        processing_time = time.time() - start_time
        
        # Update statistics
        self.stats["total_processed"] += len(messages)
        self.stats["total_chunks_created"] += len(chunks)
        self.stats["processing_time"] += processing_time
        
        # Build result metadata
        result_metadata = (metadata or {}).copy()
        result_metadata.update({
            "chunk_count": len(chunks),
            "text_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.TEXT),
            "tool_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.TOOL),
            "unknown_chunks": sum(1 for chunk in chunks if chunk.message_type == MessageType.UNKNOWN),
            "preserve_order": self.preserve_order,
            "merge_consecutive": self.merge_consecutive,
            "processing_time": processing_time,
            "string_merge_applied": True  # Mark that string merging has been applied
        })
        
        logger.debug(f"Message splitting completed: {len(messages)} messages -> {len(chunks)} chunks (string merging applied)")
        
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
        Merge processed chunks back into message list, and split string format messages back into multiple messages
        
        Args:
            chunks: Message chunk list
            preserve_type_order: Whether to preserve type order
            
        Returns:
            Merged message list
        """
        if not chunks:
            return []
        
        if preserve_type_order and self.preserve_order:
            # Merge in original order
            sorted_chunks = sorted(chunks, key=lambda x: x.metadata.get("chunk_index", 0))
        else:
            # Merge by type grouping (text first, then tools)
            text_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TEXT]
            tool_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TOOL]
            unknown_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.UNKNOWN]
            sorted_chunks = text_chunks + tool_chunks + unknown_chunks
        
        merged_messages = []
        for chunk in sorted_chunks:
            chunk_messages = []
            
            for message in chunk.messages:
                # Check if this is a merged message that needs to be split
                if message.get("role") == "merged_text":
                    # This is a merged TEXT type message that needs to be split
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        # Use _string_to_messages to split
                        split_messages = self._string_to_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        # If no original message info, parse string directly
                        split_messages = self._string_to_messages(merged_content, [])
                        chunk_messages.extend(split_messages)
                        
                elif message.get("role") == "merged_tool":
                    # This is a merged TOOL type message that needs to be split
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        # Use _string_to_tool_messages to split
                        split_messages = self._string_to_tool_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        # If no original message info, parse string directly
                        split_messages = self._string_to_tool_messages(merged_content, "")
                        chunk_messages.extend(split_messages)
                        
                else:
                    # Regular messages are added directly
                    chunk_messages.append(message)
            
            merged_messages.extend(chunk_messages)
        
        return merged_messages
    
    def get_chunks_by_type(self, 
                          chunks: List[MessageChunk], 
                          message_type: MessageType) -> List[MessageChunk]:
        """
        Get all chunks of specified type
        
        Args:
            chunks: Message chunk list
            message_type: Message type
            
        Returns:
            List of chunks of specified type
        """
        return [chunk for chunk in chunks if chunk.message_type == message_type]
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get processing statistics"""
        return self.stats.copy()
    
    def reset_statistics(self):
        """Reset statistics"""
        self.stats = {
            "total_processed": 0,
            "total_chunks_created": 0,
            "processing_time": 0.0
        }

    @staticmethod
    def _messages_to_string(messages: List[Dict[str, str]]) -> str:
        """Convert OpenAI message format to string"""
        content_parts = []
        for msg in messages:
            role = msg.get('role', 'user')
            content = msg.get('content', '')
            content_parts.append(f"[{role.upper()}]: {content}")
        return "\n".join(content_parts)
    
    @staticmethod
    def _string_to_messages(content: str, # Processed prompt, is a string
                            messages: List[Dict[str, str]] # Pre-processed prompt
                            ) -> List[Dict[str, str]]:
        # Restore all tool_calls from assistant replies
        tool_calls = []
        if messages:
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls") is not None:
                    tool_calls += msg["tool_calls"]

        """Convert string to OpenAI message format"""
        result_messages = []
        lines = content.split('\n')
        current_role = 'user'
        current_content = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # Save previous message
                if current_content:
                    result_messages.append({
                        'role': current_role,
                        'content': '\n'.join(current_content).strip()
                    })
                    current_content = []
                
                # Parse new role
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

        # Save last message
        if current_content:
            result_messages.append({
                'role': current_role,
                'content': '\n'.join(current_content).strip(),
            })
        final_messages = result_messages if result_messages else [{'role': 'user', 'content': content}]

        # Append tool_calls results
        if tool_calls is not None and len(tool_calls) > 0:
            tool_call_chunk = {
                'role': 'assistant',
                'content': None,
                'tool_calls': tool_calls
            }
            final_messages.append(tool_call_chunk)

        return final_messages

    def _tool_messages_to_string(self, messages: List[Dict[str, str]]) -> str:
        """Convert tool message format to string"""
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
        """Convert string to tool message format"""
        messages = []
        lines = content.split('\n')
        current_role = 'tool'
        current_content = []
        current_tool_call_id = ''
        current_name = ''
    
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # Save previous message
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
    
                # Parse new role and tool information
                role_end = line.find(']:')
                role_part = line[1:role_end]
                content_part = line[role_end + 2:].strip()
    
                if role_part.startswith('TOOL:'):
                    # Parse tool message format: [TOOL:name:tool_call_id]
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
    
        # Save last message
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
    
        # If no messages parsed, return original format
        if not messages and isinstance(original_prompt, list):
            return original_prompt
        elif not messages:
            return [{'role': 'tool', 'content': content}]
    
        return messages


class AdvancedChunkPipeline(ChunkPipeline):
    """Advanced message chunking pipeline, supports more complex chunking strategies"""
    
    def __init__(self, 
                 preserve_order: bool = True,
                 merge_consecutive: bool = True,
                 max_chunk_size: int = None,
                 split_by_tool_name: bool = False):
        """
        Initialize advanced chunking pipeline
        
        Args:
            preserve_order: Whether to preserve original message order
            merge_consecutive: Whether to merge consecutive messages of the same type
            max_chunk_size: Maximum chunk size (number of messages)
            split_by_tool_name: Whether to further split tool messages by tool name
        """
        super().__init__(preserve_order, merge_consecutive)
        self.max_chunk_size = max_chunk_size
        self.split_by_tool_name = split_by_tool_name
    
    def split_messages(self, 
                      messages: List[Dict[str, Any]], 
                      metadata: Dict[str, Any] = None) -> ChunkResult:
        """
        Advanced message splitting, supports splitting by size and tool name
        """
        # First perform basic splitting
        basic_result = super().split_messages(messages, metadata)
        
        if not self.max_chunk_size and not self.split_by_tool_name:
            return basic_result
        
        # Further process chunks
        refined_chunks = []
        
        for chunk in basic_result.chunks:
            if chunk.message_type == MessageType.TOOL and self.split_by_tool_name:
                # Split tool messages by tool name
                tool_chunks = self._split_tool_chunk_by_name(chunk)
                refined_chunks.extend(tool_chunks)
            elif self.max_chunk_size and len(chunk) > self.max_chunk_size:
                # Split large chunks by size
                size_chunks = self._split_chunk_by_size(chunk)
                refined_chunks.extend(size_chunks)
            else:
                refined_chunks.append(chunk)
        
        # Update result
        basic_result.chunks = refined_chunks
        basic_result.metadata["refined_chunk_count"] = len(refined_chunks)
        basic_result.metadata["split_by_tool_name"] = self.split_by_tool_name
        basic_result.metadata["max_chunk_size"] = self.max_chunk_size
        
        return basic_result
    
    def _split_tool_chunk_by_name(self, chunk: MessageChunk) -> List[MessageChunk]:
        """Split tool chunk by tool name"""
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
        """Split chunk by size"""
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