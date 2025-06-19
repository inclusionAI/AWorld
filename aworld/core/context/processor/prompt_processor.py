# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import time
from dataclasses import dataclass
from typing import Dict, Any, List

from aworld.config.conf import ContextRuleConfig
from aworld.core.context.base import Context, AgentContext
from aworld.core.context.processor.prompt_compressor import PromptCompressor, CompressionType
from aworld.core.context.processor.chunk_processor import ChunkProcessor, MessageChunk, MessageType
from aworld.logs.util import Color, color_log, logger
from aworld.models.utils import num_tokens_from_messages, truncate_tokens_from_messages


@dataclass
class MessagesProcessingResult:
    """Processing result"""
    original_token_len: int
    processing_token_len: int
    original_messages_len: int
    processing_messaged_len: int
    processing_time: float
    method_used: str
    processed_messages: List[Dict[str, Any]]

@dataclass
class ContextProcessingResult:
    """Context processing result"""
    processed_messages: List[Dict[str, Any]]
    processed_tool_results: List[str]
    statistics: Dict[str, Any]
    chunk_info: Dict[str, Any] = None

@dataclass
class CompressionDecision:
    """Compression decision result"""
    should_compress: bool
    compression_type: CompressionType
    reason: str
    token_count: int


class PromptProcessor:
    """Agent context processor, processes context according to context_rule configuration"""
    
    def __init__(self, agent_context: AgentContext):
        self.context_rule = agent_context.context_rule
        self.agent_context = agent_context
        self.compress_pipeline = None
        self.chunk_pipeline = None
        self._init_pipelines()
    
    def _init_pipelines(self):
        """Initialize processing pipelines"""
        if self.context_rule and self.context_rule.llm_compression_config and self.context_rule.llm_compression_config.enabled:
            # Initialize message splitting and compression pipeline
            self.chunk_pipeline = ChunkProcessor(
                name="ContextChunkProcessor",
                enable_compression=True,
                enable_chunking=True,
                preserve_order=True,
                merge_consecutive=True,
                compression_types=["llm_based", "map_reduce"],
                default_compression_type="llm_based"
            )
            
            # Initialize compression pipeline with both LLM and MapReduce compressors
            self.compress_pipeline = PromptCompressor(
                compression_types=[CompressionType.LLM_BASED, CompressionType.MAP_REDUCE],
                configs={
                    CompressionType.LLM_BASED: {
                        "name": "ContextLLMProcessor",
                        "llm_service": None,  # Will be set when needed
                        "max_tokens": 1000,
                        "temperature": 0.3
                    },
                    CompressionType.MAP_REDUCE: {
                        "name": "ContextMapReduceProcessor",
                        "llm_service": None,  # Will be set when needed
                        "chunk_size": 2000,
                        "overlap": 200,
                        "task_type": "summarize"
                    }
                }
            )
    
    def get_max_tokens(self):
        return self.agent_context.context_usage.total_context_length * self.context_rule.optimization_config.max_token_budget_ratio

    def is_out_of_context(self, messages: List[Dict[str, Any]],
                          is_last_message_in_memory: bool) -> bool:
        # Calculate based on historical message length to determine if threshold is reached, this is a rough statistic
        current_usage = self.agent_context.context_usage
        real_used = current_usage.used_context_length
        if not is_last_message_in_memory:
            real_used += self._count_tokens_from_message(messages[-1])
        return real_used > self.get_max_tokens()

    def _count_tokens_from_messages(self, messages: List[Dict[str, Any]]) -> int:
        """Calculate token count for messages using utils.py method"""
        return num_tokens_from_messages(messages, model=self.agent_context.model_config.model_type)

    def _count_tokens_from_message(self, msg: Dict[str, Any]) -> int:
        """Calculate token count for single message using utils.py method"""
        # Convert single message to list format for num_tokens_from_messages
        return num_tokens_from_messages([msg], model=self.agent_context.model_config.model_type)

    def _count_chunk_tokens(self, chunk: MessageChunk) -> int:
        """Calculate token count for a chunk"""
        return num_tokens_from_messages(chunk.messages, model=self.agent_context.model_config.model_type)
    
    def _count_content_tokens(self, content: str) -> int:
        """Calculate token count for content string"""
        return num_tokens_from_messages(content, model=self.agent_context.model_config.model_type)

    def _truncate_tokens_from_messages(self, content: str, max_tokens: int, keep_both_sides: bool = False) -> str:
        """Calculate token count for messages using utils.py method"""
        return truncate_tokens_from_messages(content, max_tokens, keep_both_sides, model=self.agent_context.model_config.model_type)

    def decide_compression_strategy(self, chunk: MessageChunk) -> CompressionDecision:
        """
        Decide compression strategy based on chunk token length
        
        Args:
            chunk: Message chunk to analyze
            
        Returns:
            CompressionDecision with compression strategy
        """
        if not self.context_rule.llm_compression_config.enabled:
            return CompressionDecision(
                should_compress=False,
                compression_type=CompressionType.LLM_BASED,
                reason="Compression disabled in config",
                token_count=0
            )
        
        token_count = self._count_chunk_tokens(chunk)
        trigger_compress_length = self.context_rule.llm_compression_config.trigger_compress_token_length
        trigger_mapreduce_length = self.context_rule.llm_compression_config.trigger_mapreduce_compress_token_length
        
        # No compression needed
        if token_count < trigger_compress_length:
            return CompressionDecision(
                should_compress=False,
                compression_type=CompressionType.LLM_BASED,
                reason=f"Token count {token_count} below threshold {trigger_compress_length}",
                token_count=token_count
            )
        
        # Use MapReduce for very long content
        elif token_count >= trigger_mapreduce_length:
            return CompressionDecision(
                should_compress=True,
                compression_type=CompressionType.MAP_REDUCE,
                reason=f"Token count {token_count} exceeds MapReduce threshold {trigger_mapreduce_length}",
                token_count=token_count
            )
        
        # Use LLM compression for medium length content
        else:
            return CompressionDecision(
                should_compress=True,
                compression_type=CompressionType.LLM_BASED,
                reason=f"Token count {token_count} between thresholds {trigger_compress_length}-{trigger_mapreduce_length}",
                token_count=token_count
            )

    def decide_content_compression_strategy(self, content: str) -> CompressionDecision:
        """
        Decide compression strategy based on content token length
        
        Args:
            content: Content string to analyze
            
        Returns:
            CompressionDecision with compression strategy
        """
        if not self.context_rule.llm_compression_config.enabled:
            return CompressionDecision(
                should_compress=False,
                compression_type=CompressionType.LLM_BASED,
                reason="Compression disabled in config",
                token_count=0
            )
        
        token_count = self._count_content_tokens(content)
        trigger_compress_length = self.context_rule.llm_compression_config.trigger_compress_token_length
        trigger_mapreduce_length = self.context_rule.llm_compression_config.trigger_mapreduce_compress_token_length
        
        # No compression needed
        if token_count < trigger_compress_length:
            return CompressionDecision(
                should_compress=False,
                compression_type=CompressionType.LLM_BASED,
                reason=f"Token count {token_count} below threshold {trigger_compress_length}",
                token_count=token_count
            )
        
        # Use MapReduce for very long content
        elif token_count >= trigger_mapreduce_length:
            return CompressionDecision(
                should_compress=True,
                compression_type=CompressionType.MAP_REDUCE,
                reason=f"Token count {token_count} exceeds MapReduce threshold {trigger_mapreduce_length}",
                token_count=token_count
            )
        
        # Use LLM compression for medium length content
        else:
            return CompressionDecision(
                should_compress=True,
                compression_type=CompressionType.LLM_BASED,
                reason=f"Token count {token_count} between thresholds {trigger_compress_length}-{trigger_mapreduce_length}",
                token_count=token_count
            )

    def should_compress_conversation(self, messages: List[Dict[str, Any]]) -> bool:
        """Determine whether conversation compression is needed (legacy method for compatibility)"""
        if not self.context_rule.llm_compression_config.enabled:
            return False
        
        # Create temporary chunk for decision
        temp_chunk = MessageChunk(
            message_type=MessageType.TEXT,
            messages=messages,
            metadata={}
        )
        
        decision = self.decide_compression_strategy(temp_chunk)
        return decision.should_compress
    
    def should_compress_tool_result(self, result: str) -> bool:
        """Determine whether tool result compression is needed (legacy method for compatibility)"""
        if not self.context_rule.llm_compression_config.enabled:
            return False
        
        decision = self.decide_content_compression_strategy(result)
        return decision.should_compress
    
    def process_message_chunks(self, 
                              chunks: List[MessageChunk], 
                              base_metadata: Dict[str, Any] = None) -> List[MessageChunk]:
        """
        Process message chunk list, apply different processing strategies for different types of chunks
        
        Args:
            chunks: Message chunk list
            base_metadata: Base metadata
            
        Returns:
            Processed message chunk list
        """
        processed_chunks = []
        
        for chunk in chunks:
            try:
                if chunk.message_type == MessageType.TEXT:
                    # Process text message chunks
                    processed_chunk = self._process_text_chunk(chunk, base_metadata)
                elif chunk.message_type == MessageType.TOOL:
                    # Process tool message chunks
                    processed_chunk = self._process_tool_chunk(chunk, base_metadata)
                else:
                    # Unknown type, keep as is
                    processed_chunk = chunk
                    logger.warning(f"Unknown message chunk type: {chunk.message_type}")
                
                processed_chunks.append(processed_chunk)
                
            except Exception as e:
                logger.error(f"Processing message chunk failed: {e}")
                # Keep original chunk on failure
                processed_chunks.append(chunk)
        
        return processed_chunks
    
    def _process_text_chunk(self, 
                           chunk: MessageChunk, 
                           base_metadata: Dict[str, Any] = None) -> MessageChunk:
        """Process text message chunks with intelligent compression strategy selection"""
        # Decide compression strategy based on chunk length
        decision = self.decide_compression_strategy(chunk)
        
        if not decision.should_compress:
            logger.debug(f"Skipping text chunk compression: {decision.reason}")
            return chunk
        
        try:
            # Use appropriate compression pipeline based on decision
            if self.compress_pipeline:
                processed_messages = []
                
                for message in chunk.messages:
                    content = message.get("content", "")
                    if not content or not isinstance(content, str):
                        processed_messages.append(message)
                        continue
                    
                    logger.info(f'Processing text chunk with {decision.compression_type.value} compression '
                              f'(tokens: {decision.token_count}, reason: {decision.reason})')
                    
                    # Use selected compression type
                    compression_result = self.compress_pipeline.compress(
                        content, 
                        metadata={
                            "message_role": message.get("role", "unknown"),
                            "chunk_token_count": decision.token_count,
                            "compression_reason": decision.reason
                        },
                        compression_type=decision.compression_type
                    )
                    
                    # Create processed message
                    processed_message = message.copy()
                    processed_message["content"] = compression_result.compressed_content
                    processed_messages.append(processed_message)
                
                # Update chunk metadata
                updated_metadata = chunk.metadata.copy()
                updated_metadata.update({
                    "processed": True,
                    "compression_applied": True,
                    "compression_type": decision.compression_type.value,
                    "compression_reason": decision.reason,
                    "original_token_count": decision.token_count,
                    "processing_method": f"{decision.compression_type.value}_compression",
                    "original_message_count": len(chunk.messages),
                    "processed_message_count": len(processed_messages)
                })
                
                return MessageChunk(
                    message_type=chunk.message_type,
                    messages=processed_messages,
                    metadata=updated_metadata
                )
            
            # If no pipeline available, return original chunk
            logger.warning("Compression pipeline unavailable, skipping text chunk compression")
            return chunk
            
        except Exception as e:
            logger.warning(f"Text chunk compression failed: {e}")
            return chunk
    
    def _process_tool_chunk(self, 
                           chunk: MessageChunk, 
                           base_metadata: Dict[str, Any] = None) -> MessageChunk:
        """Process tool message chunks with intelligent compression strategy selection"""
        try:
            processed_messages = []
            
            for message in chunk.messages:
                content = message.get("content", "")
                
                # Decide compression strategy for this content
                decision = self.decide_content_compression_strategy(content)
                
                if decision.should_compress:
                    logger.info(f'Processing tool chunk with {decision.compression_type.value} compression '
                              f'(tokens: {decision.token_count}, reason: {decision.reason})')
                    
                    # Use selected compression type
                    compression_result = self.compress_pipeline.compress(
                        content,
                        metadata={
                            "tool_name": message.get("name", "unknown_tool"),
                            "message_role": message.get("role", "tool"),
                            "content_token_count": decision.token_count,
                            "compression_reason": decision.reason
                        },
                        compression_type=decision.compression_type
                    )
                    
                    # Create processed message
                    processed_message = message.copy()
                    processed_message["content"] = compression_result.compressed_content
                    processed_messages.append(processed_message)
                else:
                    # Messages that don't need compression are kept as is
                    logger.debug(f"Skipping tool content compression: {decision.reason}")
                    processed_messages.append(message)
            
            # Update chunk metadata with compression info
            updated_metadata = chunk.metadata.copy()
            updated_metadata.update({
                "processed": True,
                "tool_compression_applied": True,
                "processing_method": "intelligent_compression",
                "original_message_count": len(chunk.messages),
                "processed_message_count": len(processed_messages)
            })
            
            return MessageChunk(
                message_type=chunk.message_type,
                messages=processed_messages,
                metadata=updated_metadata
            )
            
        except Exception as e:
            logger.warning(f"Tool chunk compression failed: {e}")
            return chunk

    def truncate_messages(self, messages: List[Dict[str, Any]], context: Context) -> MessagesProcessingResult:
        """Truncate messages based on _truncate_input_messages_roughly logic"""
        start_time = time.time()
        original_messages_len = len(messages)
        original_token_len = self._count_tokens_from_messages(messages)
        
        if not self.context_rule.optimization_config.enabled:
            processing_time = time.time() - start_time
            return MessagesProcessingResult(
                original_token_len=original_token_len,
                processing_token_len=original_token_len,
                original_messages_len=original_messages_len,
                processing_messaged_len=original_messages_len,
                processing_time=processing_time,
                method_used="no_optimization",
                processed_messages=messages
            )
        
        if not self.is_out_of_context(messages=messages, is_last_message_in_memory=False):
            processing_time = time.time() - start_time
            return MessagesProcessingResult(
                original_token_len=original_token_len,
                processing_token_len=original_token_len,
                original_messages_len=original_messages_len,
                processing_messaged_len=original_messages_len,
                processing_time=processing_time,
                method_used="within_context_limit",
                processed_messages=messages
            )
        
        max_tokens = self.get_max_tokens()
        
        # Check system message count (at most one, and must be the first)
        system_messages = [m for m in messages if m.get("role") == "system"]

        # Group messages by conversation turns
        turns = []
        for m in messages:
            if m.get("role") == "system":
                continue
            elif m.get("role") == "user":
                turns.append([m])
            else:
                if turns:
                    turns[-1].append(m)
                else:
                    raise Exception('The input messages (excluding the system message) must start with a user message.')

        def _truncate_message(msg: Dict[str, Any], max_tokens: int, keep_both_sides: bool = False):
            """Truncate single message using utils.py method"""
            content = msg.get("content", "")
            if isinstance(content, str):
                # Use utils.py for token counting and truncation
                truncated_content = self._truncate_tokens_from_messages(content, max_tokens, keep_both_sides)
            else:
                # Handle complex content formats
                if isinstance(content, list):
                    text_parts = []
                    for item in content:
                        if isinstance(item, dict) and item.get("text"):
                            text_parts.append(item["text"])
                        elif isinstance(item, str):
                            text_parts.append(item)
                    if not text_parts:
                        return None
                    text = '\n'.join(text_parts)
                else:
                    text = str(content)
                truncated_content = self._truncate_tokens_from_messages(text, max_tokens, keep_both_sides)
            
            new_msg = msg.copy()
            new_msg["content"] = truncated_content
            return new_msg
        
        # Process system messages
        if messages and messages[0].get("role") == "system":
            sys_msg = messages[0]
            available_token = max_tokens - self._count_tokens_from_message(sys_msg)
        else:
            sys_msg = None
            available_token = max_tokens
        
        # Process messages from back to front, keep the latest conversations
        token_cnt = 0
        new_messages = []
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "system":
                continue
            
            cur_token_cnt = self._count_tokens_from_message(messages[i])
            if cur_token_cnt <= available_token:
                new_messages = [messages[i]] + new_messages
                available_token -= cur_token_cnt
            else:
                # Try to truncate message
                if (messages[i].get("role") == "user") and (i != len(messages) - 1):
                    # Truncate user message (not the last one)
                    _msg = _truncate_message(messages[i], max_tokens=available_token)
                    if _msg:
                        new_messages = [_msg] + new_messages
                    break
                elif messages[i].get("role") == "function":
                    # Truncate function message, keep both ends
                    _msg = _truncate_message(messages[i], max_tokens=available_token, keep_both_sides=True)
                    if _msg:
                        new_messages = [_msg] + new_messages
                    else:
                        break
                else:
                    # Cannot truncate, record token count and exit
                    token_cnt = (max_tokens - available_token) + cur_token_cnt
                    break
        
        # Re-add system message
        if sys_msg is not None:
            new_messages = [sys_msg] + new_messages
        
        # Calculate processed statistics
        processing_time = time.time() - start_time
        processing_token_len = self._count_tokens_from_messages(new_messages)
        processing_messaged_len = len(new_messages)
        
        return MessagesProcessingResult(
            original_token_len=original_token_len,
            processing_token_len=processing_token_len,
            original_messages_len=original_messages_len,
            processing_messaged_len=processing_messaged_len,
            processing_time=processing_time,
            method_used="truncate_messages",
            processed_messages=new_messages
        )

    def compress_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.context_rule.llm_compression_config.enabled:
            return messages
        # 1. Re-split processed messages
        final_chunk_result = self.chunk_pipeline.split_messages(messages)

        # 2. Process each chunk
        processed_chunks = self.process_message_chunks(final_chunk_result.chunks)
        
        # 3. Re-merge messages
        return self.chunk_pipeline.merge_chunks(processed_chunks)

    def process_messages(self, messages: List[Dict[str, Any]], context: Context) -> ContextProcessingResult:
        """Process complete context, return processing results and statistics"""
        start_time = time.time()

        # 1. Content compression
        compressed_messages = self.compress_messages(messages)
        
        # 1. Content length limit
        truncated_result = self.truncate_messages(compressed_messages, context)
        truncated_messages = truncated_result.processed_messages
        
        total_time = time.time() - start_time

        color_log(f"\nContext processing statistics: "
                   f"\nOriginal message count={truncated_result.original_messages_len}"
                   f"\nProcessed message count={truncated_result.processing_messaged_len}"
                   f"\nMax context length max_context_len={self.get_max_tokens()} = {self.agent_context.context_usage.total_context_length} * {self.context_rule.optimization_config.max_token_budget_ratio}"
                   f"\nOriginal token count={truncated_result.original_token_len}"
                   f"\nProcessed token count={truncated_result.processing_token_len}"
                   f"\nTruncation processing time={truncated_result.processing_time:.3f}s"
                   f"\nTotal processing time={total_time:.3f}s"
                   f"\nMethod used={truncated_result.method_used}"
                   f"\ntruncated_messages={truncated_messages}",
                   color=Color.pink,)

        return ContextProcessingResult(
            processed_messages=truncated_messages,
            processed_tool_results=None,
            statistics={
                "total_processing_time": total_time,
                "original_message_count": len(messages),
                "truncated_message_count": len(truncated_messages),
            },
        ) 

