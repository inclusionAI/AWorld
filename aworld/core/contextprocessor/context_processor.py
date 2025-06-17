# coding: utf-8
# Copyright (c) 2025 inclusionAI.

import time
from dataclasses import dataclass
from typing import Dict, Any, List

from aworld.config.conf import ContextRuleConfig
from aworld.core.context.base import Context, AgentContext
from aworld.core.contextprocessor.chunk_pipeline import ChunkPipeline, MessageType, MessageChunk
from aworld.core.contextprocessor.map_pipeline import MapPipeline
from aworld.core.contextprocessor.tokenization_qwen import tokenizer
from aworld.logs.util import Color, color_log, logger


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


class ContextProcessor:
    """Agent context processor, processes context according to context_rule configuration"""
    
    def __init__(self, context_rule: ContextRuleConfig, currentAgentContext: AgentContext):
        self.context_rule = context_rule
        self.current_agent_context = currentAgentContext
        self.map_pipeline = None
        self.chunk_pipeline = None
        self._init_pipelines()
    
    def _init_pipelines(self):
        """Initialize processing pipelines"""
        if self.context_rule and self.context_rule.llm_compression_config and self.context_rule.llm_compression_config.enabled:
            # Initialize message splitting pipeline
            self.chunk_pipeline = ChunkPipeline(
                preserve_order=True,
                merge_consecutive=True
            )
            # MapPipeline focuses on single message compression
            self.map_pipeline = MapPipeline(
                enable_compression=True,
                compression_types=[self.context_rule.llm_compression_config.compression_type],  # Default to rule-based compression
                default_compression_type="rule_based",
                compression_configs={"llm_based": {
                    "llm_model_name": self.context_rule.summary_model.llm_model_name,
                    "llm_base_url": self.context_rule.summary_model.llm_base_url,
                    "llm_api_key": self.context_rule.summary_model.llm_api_key
                }}
            )
    
    def should_compress_conversation(self, messages: List[Dict[str, Any]]) -> bool:
        """Determine whether conversation compression is needed"""
        if not self.context_rule.llm_compression_config.enabled:
            return False
        return True
        
        # Calculate total length
        total_length = sum(len(str(msg.get("content", ""))) for msg in messages)
        max_length = self.context_rule.llm_conversation_max_history_length
        
        return total_length > max_length
    
    def should_compress_tool_result(self, result: str) -> bool:
        """Determine whether tool result compression is needed"""
        if not self.context_rule.tool_compression_config.enabled:
            return False
        return True
        
        max_length = self.context_rule.tool_result_max_result_length
        return len(result) > max_length
    
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
        """Process text message chunks - use map_pipeline for compression"""
        if not self.should_compress_conversation(chunk.messages):
            return chunk
        
        try:
            # Use map_pipeline to process text messages
            if self.map_pipeline:
                processed_messages = []
                
                for message in chunk.messages:
                    # Prepare metadata
                    message_metadata = (base_metadata or {}).copy()
                    message_metadata.update(chunk.metadata)
                    message_metadata["compression_type"] = self.context_rule.llm_compression_config.compression_type
                    message_metadata["content_type"] = "text_message"
                    logger.info('_process_text_chunk')
                    # Use map_pipeline to process single message
                    result = self.map_pipeline.process_message(message, message_metadata)
                    
                    if result.status.value == "completed":
                        processed_messages.append(result.processed_message)
                    else:
                        # If processing fails, use original message
                        processed_messages.append(result.original_message)
                
                # Update chunk metadata
                updated_metadata = chunk.metadata.copy()
                updated_metadata.update({
                    "processed": True,
                    "compression_applied": True,
                    "processing_method": "map_pipeline",
                    "original_message_count": len(chunk.messages),
                    "processed_message_count": len(processed_messages)
                })
                
                return MessageChunk(
                    message_type=chunk.message_type,
                    messages=processed_messages,
                    metadata=updated_metadata
                )
            
            # If no pipeline available, return original chunk
            logger.warning("Map pipeline unavailable, skipping text chunk compression")
            return chunk
            
        except Exception as e:
            logger.warning(f"Text chunk compression failed: {e}")
            return chunk
    
    def _process_tool_chunk(self, 
                           chunk: MessageChunk, 
                           base_metadata: Dict[str, Any] = None) -> MessageChunk:
        """Process tool message chunks - use map_pipeline for compression"""
        try:
            processed_messages = []
            
            for message in chunk.messages:
                content = message.get("content", "")
                
                # Check if compression is needed
                if self.should_compress_tool_result(content):
                    # Prepare metadata
                    message_metadata = (base_metadata or {}).copy()
                    message_metadata.update(chunk.metadata)
                    message_metadata["compression_type"] = self.context_rule.tool_compression_config.compression_type
                    message_metadata["content_type"] = "tool_result"
                    message_metadata["tool_name"] = message.get("name", "unknown_tool")
                    logger.info('_process_tool_chunk')
                    # Use map_pipeline to process tool message
                    result = self.map_pipeline.process_message(message, message_metadata)
                    
                    if result.status.value == "completed":
                        processed_messages.append(result.processed_message)
                    else:
                        # If processing fails, use original message
                        processed_messages.append(result.original_message)
                else:
                    # Messages that don't need compression are kept as is
                    processed_messages.append(message)
            
            # Update chunk metadata
            updated_metadata = chunk.metadata.copy()
            updated_metadata.update({
                "processed": True,
                "tool_compression_applied": True,
                "processing_method": "map_pipeline",
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
        original_token_len = sum(self._count_tokens(msg) for msg in messages)
        
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
        # if len(system_messages) >= 2:
        #     raise Exception('The input messages must contain no more than one system message. And the system message, if exists, must be the first message.')

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
            """Truncate single message"""
            content = msg.get("content", "")
            if isinstance(content, str):
                truncated_content = tokenizer.truncate(content, max_token=max_tokens, keep_both_sides=keep_both_sides)
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
                truncated_content = tokenizer.truncate(text, max_token=max_tokens, keep_both_sides=keep_both_sides)
            
            new_msg = msg.copy()
            new_msg["content"] = truncated_content
            return new_msg
        
        # Process system messages
        if messages and messages[0].get("role") == "system":
            sys_msg = messages[0]
            available_token = max_tokens - self._count_tokens(sys_msg)
        else:
            sys_msg = None
            available_token = max_tokens
        
        # Process messages from back to front, keep the latest conversations
        token_cnt = 0
        new_messages = []
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "system":
                continue
            
            cur_token_cnt = self._count_tokens(messages[i])
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

        # Check if final result is valid
        # if (sys_msg is not None and len(new_messages) < 2) or (sys_msg is None and len(new_messages) < 1):
        #     raise Exception(
        #         'The input messages exceed the maximum context length ({max_tokens} tokens) after keeping only the system message (if exists) and the latest one user message (around {token_cnt} tokens). \nTo configure the context limit, please specify "max_token_budget_ratio" in the optimization_config. \nExample: max_token_budget_ratio = 0.5',
        #     )
        
        # Calculate processed statistics
        processing_time = time.time() - start_time
        processing_token_len = sum(self._count_tokens(msg) for msg in new_messages)
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

    # def format_as_text_message(self,
    #     msg: Message,
    #     add_upload_info: bool,
    #     lang: Literal['auto', 'en', 'zh'] = 'auto',
    # ) -> Message:
    #     msg = format_as_multimodal_message(msg,
    #                                        add_upload_info=add_upload_info,
    #                                        add_multimodel_upload_info=add_upload_info,
    #                                        add_audio_upload_info=add_upload_info,
    #                                        lang=lang)
    #     text = ''
    #     for item in msg.content:
    #         if item.type == 'text':
    #             text += item.value
    #     msg.content = text
    #     return msg

    # def extract_text_from_message(self,
    #     msg: Message,
    #     add_upload_info: bool,
    #     lang: Literal['auto', 'en', 'zh'] = 'auto',
    # ) -> str:
    #     if isinstance(msg.content, list):
    #         text = self.format_as_text_message(msg, add_upload_info=add_upload_info, lang=lang).content
    #     elif isinstance(msg.content, str):
    #         text = msg.content
    #     else:
    #         raise TypeError(f'List of str or str expected, but received {type(msg.content).__name__}.')
    #     return text.strip()

    def _count_tokens(self, msg: Dict[str, Any]) -> int:
        """Calculate token count for message"""
        content = msg.get("content", "")
        
        if isinstance(content, str):
            return tokenizer.count_tokens(content)
        elif isinstance(content, list):
            # Handle complex content formats, extract text parts
            text_parts = []
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    text_parts.append(item["text"])
                elif isinstance(item, str):
                    text_parts.append(item)
            if text_parts:
                text = '\n'.join(text_parts)
                return tokenizer.count_tokens(text)
            else:
                return 0
        else:
            # Convert other types to string
            return tokenizer.count_tokens(str(content))

    def get_max_tokens(self):
        return self.current_agent_context.context_usage.total_context_length * self.context_rule.optimization_config.max_token_budget_ratio

    

    def is_out_of_context(self, messages: List[Dict[str, Any]],
                          is_last_message_in_memory: bool) -> bool:
        # Calculate based on historical message length to determine if threshold is reached, this is a rough statistic
        current_usage = self.current_agent_context.context_usage
        real_used = current_usage.used_context_length
        if not is_last_message_in_memory:
            real_used += self._count_tokens(messages[-1])
        return real_used > self.get_max_tokens()

    def compress_messages(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not self.context_rule.llm_compression_config.enabled:
            return messages
        # 1. Re-split processed messages
        final_chunk_result = self.chunk_pipeline.split_messages(messages)

        # 2. Process each chunk
        processed_chunks = self.process_message_chunks(final_chunk_result.chunks)
        
        # 3. Re-merge messages
        return self.chunk_pipeline.merge_chunks(processed_chunks)

    def process_context(self, messages: List[Dict[str, Any]], context: Context) -> ContextProcessingResult:
        """Process complete context, return processing results and statistics"""
        start_time = time.time()

        # 1. Content compression
        compressed_messages = self.compress_messages(messages)
        
        # 2. Content length limit
        truncated_result = self.truncate_messages(compressed_messages, context)
        truncated_messages = truncated_result.processed_messages
        
        total_time = time.time() - start_time

        color_log(f"\nContext processing statistics: "
                   f"\nOriginal message count={truncated_result.original_messages_len}"
                   f"\nProcessed message count={truncated_result.processing_messaged_len}"
                   f"\nMax context length max_context_len={self.get_max_tokens()} = {self.current_agent_context.context_usage.total_context_length} * {self.context_rule.optimization_config.max_token_budget_ratio}"
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

