import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Optional, Union, Tuple

# Import base processor
try:
    from .base_processor import BaseProcessor
except ImportError:
    from aworld.core.context.processor.base_processor import BaseProcessor

# Import compressor modules - use conditional import to handle relative import issues
try:
    from .prompt_compressor import PromptCompressor, CompressionType, CompressionResult
except ImportError:
    # If relative import fails, try absolute import
    try:
        from aworld.core.context.processor.prompt_compressor import PromptCompressor, CompressionType, CompressionResult
    except ImportError:
        # If both fail, define placeholder classes
        logger = logging.getLogger(__name__)
        logger.warning("Unable to import compressor module, compression functionality will be unavailable")
        
        class CompressionType:
            RULE_BASED = "rule_based"
            STATISTICAL = "statistical"
            LLM_BASED = "llm_based"
            TFIDF_BASED = "tfidf_based"

        class CompressionResult:
            def __init__(self, original_content, compressed_content, compression_ratio, metadata, compression_type):
                self.original_content = original_content
                self.compressed_content = compressed_content
                self.compression_ratio = compression_ratio
                self.metadata = metadata
                self.compression_type = compression_type
        
        class PromptCompressor:
            def __init__(self, *args, **kwargs):
                pass
            def compress(self, *args, **kwargs):
                return None
            def compress_batch(self, *args, **kwargs):
                return []
            def get_compression_stats(self, *args, **kwargs):
                return {}

logger = logging.getLogger(__name__)


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


class ChunkProcessor(BaseProcessor):
    """
    简单压缩处理器（集成分块功能）
    
    继承自BaseProcessor，专门用于对消息进行压缩处理和分块处理
    集成了原来chunk_pipeline.py的分块功能
    """
    
    def __init__(self, 
                 name: str = "SimpleCompressProcessor",
                 # 压缩相关参数
                 enable_compression: bool = True,
                 compression_types: List[str] = None,
                 compression_configs: Dict[str, Dict[str, Any]] = None,
                 default_compression_type: str = "rule_based",
                 # 分块相关参数
                 enable_chunking: bool = False,
                 preserve_order: bool = True,
                 merge_consecutive: bool = True,
                 max_chunk_size: int = None,
                 split_by_tool_name: bool = False):
        """
        初始化简单压缩处理器
        
        Args:
            name: 处理器名称
            # 压缩参数
            enable_compression: 是否启用压缩
            compression_types: 支持的压缩类型列表
            compression_configs: 压缩器配置
            default_compression_type: 默认压缩类型
            # 分块参数
            enable_chunking: 是否启用分块
            preserve_order: 是否保持原始消息顺序
            merge_consecutive: 是否合并连续的同类型消息
            max_chunk_size: 最大分块大小（消息数量）
            split_by_tool_name: 是否按工具名称进一步分割工具消息
        """
        super().__init__(name)
        
        # 压缩器配置
        self.enable_compression = enable_compression
        self.default_compression_type = CompressionType(default_compression_type)
        
        # 分块器配置
        self.enable_chunking = enable_chunking
        self.preserve_order = preserve_order
        self.merge_consecutive = merge_consecutive
        self.max_chunk_size = max_chunk_size
        self.split_by_tool_name = split_by_tool_name
        
        # 初始化压缩器
        if self.enable_compression:
            if compression_types is None:
                compression_types = ["rule_based", "statistical", "llm_based", "tfidf_based"]
            
            comp_types = [CompressionType(ct) for ct in compression_types]
            self.compressor = PromptCompressor(
                compression_types=comp_types,
                configs={CompressionType(k): v for k, v in (compression_configs or {}).items()}
            )
            self.logger.info(f"压缩器已启用，支持的压缩类型: {compression_types}")
        else:
            self.compressor = None
            self.logger.info("压缩器已禁用")
        
        # 统计信息
        self.stats = {
            # 压缩统计
            "compression": {
                "total_messages_processed": 0,
                "total_messages_compressed": 0,
                "total_original_length": 0,
                "total_compressed_length": 0,
                "compression_results": []
            },
            # 分块统计
            "chunking": {
                "total_processed": 0,
                "total_chunks_created": 0,
                "processing_time": 0.0
            }
        }

    def _process_chunking(self, messages: List[Dict[str, Any]], **kwargs) -> List[Dict[str, Any]]:
        """处理分块逻辑"""
        # 先分块
        chunk_result = self.split_messages(messages, kwargs.get('metadata', {}))
        
        # 再合并回消息列表
        merged_messages = self.merge_chunks(chunk_result.chunks, 
                                          kwargs.get('preserve_type_order', True))
        
        return merged_messages

    def classify_message(self, message: Dict[str, Any]) -> MessageType:
        """
        分类单个消息
        
        Args:
            message: OpenAI格式消息
            
        Returns:
            消息类型
        """
        role = message.get("role", "")
        
        if role in ["system", "user", "assistant"]:
            return MessageType.TEXT
        elif role == "tool":
            return MessageType.TOOL
        else:
            self.logger.warning(f"未知消息角色: {role}")
            return MessageType.UNKNOWN

    def split_messages(self, 
                      messages: List[Dict[str, Any]], 
                      metadata: Dict[str, Any] = None) -> ChunkResult:
        """
        按类型将消息列表分割成块，并将同类型消息合并为字符串
        
        Args:
            messages: OpenAI格式消息列表
            metadata: 元数据
            
        Returns:
            分块结果
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
            
            # 如果是新类型或不合并连续消息
            if (current_chunk_type != msg_type or 
                not self.merge_consecutive):
                
                # 保存当前块（如果有内容）
                if current_chunk_messages:
                    chunk_metadata = (metadata or {}).copy()
                    chunk_metadata.update({
                        "chunk_index": len(chunks),
                        "start_message_index": i - len(current_chunk_messages),
                        "end_message_index": i - 1,
                        "message_count": len(current_chunk_messages),
                        "original_messages": current_chunk_messages.copy()
                    })
                    
                    # 根据消息类型合并消息为字符串
                    if current_chunk_type == MessageType.TEXT:
                        merged_content = self._messages_to_string(current_chunk_messages)
                        merged_message = {
                            "role": "merged_text",
                            "content": merged_content,
                            "original_count": len(current_chunk_messages)
                        }
                        chunk_messages = [merged_message]
                    elif current_chunk_type == MessageType.TOOL:
                        merged_content = self._tool_messages_to_string(current_chunk_messages)
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
                
                # 开始新块
                current_chunk_type = msg_type
                current_chunk_messages = [message]
            else:
                # 添加到当前块
                current_chunk_messages.append(message)
        
        # 处理最后一个块
        if current_chunk_messages:
            chunk_metadata = (metadata or {}).copy()
            chunk_metadata.update({
                "chunk_index": len(chunks),
                "start_message_index": len(messages) - len(current_chunk_messages),
                "end_message_index": len(messages) - 1,
                "message_count": len(current_chunk_messages),
                "original_messages": current_chunk_messages.copy()
            })
            
            # 根据消息类型合并消息为字符串
            if current_chunk_type == MessageType.TEXT:
                merged_content = self._messages_to_string(current_chunk_messages)
                merged_message = {
                    "role": "merged_text",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            elif current_chunk_type == MessageType.TOOL:
                merged_content = self._tool_messages_to_string(current_chunk_messages)
                merged_message = {
                    "role": "merged_tool",
                    "content": merged_content,
                    "original_count": len(current_chunk_messages)
                }
                chunk_messages = [merged_message]
            else:
                chunk_messages = current_chunk_messages.copy()
            
            chunks.append(MessageChunk(
                message_type=current_chunk_type,
                messages=chunk_messages,
                metadata=chunk_metadata
            ))
        
        processing_time = time.time() - start_time
        
        # 更新统计
        self.stats["chunking"]["total_processed"] += len(messages)
        self.stats["chunking"]["total_chunks_created"] += len(chunks)
        self.stats["chunking"]["processing_time"] += processing_time
        
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
            "string_merge_applied": True
        })
        
        self.logger.debug(f"消息分割完成: {len(messages)} 条消息 -> {len(chunks)} 个块（已应用字符串合并）")
        
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
        将处理后的块合并回消息列表，并将字符串格式消息分割回多个消息
        
        Args:
            chunks: 消息块列表
            preserve_type_order: 是否保持类型顺序
            
        Returns:
            合并后的消息列表
        """
        if not chunks:
            return []
        
        if preserve_type_order and self.preserve_order:
            # 按原始顺序合并
            sorted_chunks = sorted(chunks, key=lambda x: x.metadata.get("chunk_index", 0))
        else:
            # 按类型分组合并（文本优先，然后工具）
            text_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TEXT]
            tool_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.TOOL]
            unknown_chunks = [chunk for chunk in chunks if chunk.message_type == MessageType.UNKNOWN]
            sorted_chunks = text_chunks + tool_chunks + unknown_chunks
        
        merged_messages = []
        for chunk in sorted_chunks:
            chunk_messages = []
            
            for message in chunk.messages:
                # 检查是否是需要分割的合并消息
                if message.get("role") == "merged_text":
                    # 这是需要分割的合并TEXT类型消息
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        split_messages = self._string_to_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        split_messages = self._string_to_messages(merged_content, [])
                        chunk_messages.extend(split_messages)
                        
                elif message.get("role") == "merged_tool":
                    # 这是需要分割的合并TOOL类型消息
                    merged_content = message.get("content", "")
                    original_messages = chunk.metadata.get("original_messages", [])
                    
                    if original_messages:
                        split_messages = self._string_to_tool_messages(merged_content, original_messages)
                        chunk_messages.extend(split_messages)
                    else:
                        split_messages = self._string_to_tool_messages(merged_content, "")
                        chunk_messages.extend(split_messages)
                        
                else:
                    # 常规消息直接添加
                    chunk_messages.append(message)
            
            merged_messages.extend(chunk_messages)
        
        return merged_messages

    # 消息转换方法
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
    def _string_to_messages(content: str, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """将字符串转换为OpenAI消息格式"""
        # 恢复所有tool_calls
        tool_calls = []
        if messages:
            for msg in messages:
                if msg.get("role") == "assistant" and msg.get("tool_calls") is not None:
                    tool_calls += msg["tool_calls"]

        result_messages = []
        lines = content.split('\n')
        current_role = 'user'
        current_content = []
        
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # 保存前一个消息
                if current_content:
                    result_messages.append({
                        'role': current_role,
                        'content': '\n'.join(current_content).strip()
                    })
                    current_content = []
                
                # 解析新角色
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

        # 保存最后一个消息
        if current_content:
            result_messages.append({
                'role': current_role,
                'content': '\n'.join(current_content).strip(),
            })
        
        final_messages = result_messages if result_messages else [{'role': 'user', 'content': content}]

        # 添加tool_calls结果
        if tool_calls and len(tool_calls) > 0:
            tool_call_chunk = {
                'role': 'assistant',
                'content': None,
                'tool_calls': tool_calls
            }
            final_messages.append(tool_call_chunk)

        return final_messages

    def _tool_messages_to_string(self, messages: List[Dict[str, str]]) -> str:
        """将工具消息格式转换为字符串"""
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
        """将字符串转换为工具消息格式"""
        messages = []
        lines = content.split('\n')
        current_role = 'tool'
        current_content = []
        current_tool_call_id = ''
        current_name = ''
    
        for line in lines:
            line = line.strip()
            if line.startswith('[') and ']:' in line:
                # 保存前一个消息
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
    
                # 解析新角色和工具信息
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
    
        # 保存最后一个消息
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
    
        # 如果没有解析到消息，返回原始格式
        if not messages and isinstance(original_prompt, list):
            return original_prompt
        elif not messages:
            return [{'role': 'tool', 'content': content}]
    
        return messages
