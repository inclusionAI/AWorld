import logging
from dataclasses import dataclass
from enum import Enum
from typing import List, Dict, Any, Union

# Import compressor modules - use conditional import to handle relative import issues
try:
    from .prompt_compressor import PromptCompressor, CompressionType, CompressionResult
except ImportError:
    # If relative import fails, try absolute import
    try:
        from prompt_compressor import PromptCompressor, CompressionType, CompressionResult
    except ImportError:
        # If both fail, define placeholder classes
        logger = logging.getLogger(__name__)
        logger.warning("Unable to import compressor module, compression functionality will be unavailable")
        
        class CompressionType:
            RULE_BASED = "rule_based"
            STATISTICAL = "statistical"
            LLM_BASED = "llm_based"
            TFIDF_BASED = "tfidf_based"  # New TF-IDF compression type

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

class ProcessingStatus(Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"

class SplitMode(Enum):
    RANDOM = "random"
    SEQUENTIAL = "sequential"
    SEMANTIC = "semantic"
    LENGTH_BASED = "length_based"

@dataclass
class ProcessingResult:
    """Single message processing result"""
    status: ProcessingStatus
    original_message: Dict[str, Any]
    processed_message: Dict[str, Any]
    metadata: Dict[str, Any]
    error: str = None
    compression_result: CompressionResult = None

class MapPipeline:
    """
    MapPipeline - Processing pipeline focused on single message compression
    
    No longer performs message splitting, but applies independent compression processing to each message
    """
    
    def __init__(self, 
                 enable_compression: bool = True,
                 compression_types: List[str] = None,
                 compression_configs: Dict[str, Dict[str, Any]] = None,
                 default_compression_type: str = "rule_based"):
        """
        Initialize message compression pipeline
        
        Args:
            enable_compression: Whether to enable compression
            compression_types: List of supported compression types
            compression_configs: Compressor configurations
            default_compression_type: Default compression type
        """
        # Compressor configuration
        self.enable_compression = enable_compression
        self.default_compression_type = CompressionType(default_compression_type)
        
        # Initialize compressor
        if self.enable_compression:
            if compression_types is None:
                compression_types = ["rule_based", "statistical", "llm_based", "tfidf_based"]
            
            comp_types = [CompressionType(ct) for ct in compression_types]
            self.compressor = PromptCompressor(
                compression_types=comp_types,
                configs={CompressionType(k): v for k, v in (compression_configs or {}).items()}
            )
            logger.info(f"Compressor enabled, supported compression types: {compression_types}")
        else:
            self.compressor = None
            logger.info("Compressor disabled")
        
        # Compression statistics
        self.compression_stats = {
            "total_messages_processed": 0,
            "total_messages_compressed": 0,
            "total_original_length": 0,
            "total_compressed_length": 0,
            "compression_results": []
        }

    def process_message(self, 
                       message: Dict[str, Any], 
                       metadata: Dict[str, Any] = None) -> ProcessingResult:
        """
        Process single message
        
        Args:
            message: OpenAI format message
            metadata: Metadata
            
        Returns:
            Processing result
        """
        try:
            # Extract message content
            content = message.get("content", "")
            print('tocompress:', content, self.enable_compression, self.compressor)
            
            if not content:
                # For messages without content (like tool_calls), return directly
                return ProcessingResult(
                    status=ProcessingStatus.COMPLETED,
                    original_message=message,
                    processed_message=message,
                    metadata=metadata or {}
                )
            
            compression_result = None
            processed_content = content
            # Apply compression (if enabled)
            if self.enable_compression and self.compressor and isinstance(content, str):
                try:
                    # Get compression type from metadata, use default if not available
                    compression_type = metadata.get('compression_type', self.default_compression_type) if metadata else self.default_compression_type
                    if isinstance(compression_type, str):
                        compression_type = CompressionType(compression_type)
                    
                    compression_result = self.compressor.compress(
                        content=content,
                        metadata=metadata,
                        compression_type=compression_type
                    )
                    
                    processed_content = compression_result.compressed_content
                    print(f"Content before compression: {content} \nContent after compression: {processed_content}")
                    
                    # Update compression statistics
                    self.compression_stats["total_messages_compressed"] += 1
                    self.compression_stats["total_original_length"] += len(content)
                    self.compression_stats["total_compressed_length"] += len(processed_content)
                    self.compression_stats["compression_results"].append(compression_result)
                    
                    logger.debug(f"Message compression completed, compression ratio: {compression_result.compression_ratio:.3f}")
                    
                except Exception as e:
                    raise e
                    logger.warning(f"Message compression failed: {e}, using original content")
                    processed_content = content
            
            # Create processed message
            processed_message = message.copy()
            processed_message["content"] = processed_content
            
            # Update statistics
            self.compression_stats["total_messages_processed"] += 1
            
            # Build processing result
            result_metadata = metadata.copy() if metadata else {}
            result_metadata.update({
                "message_role": message.get("role", "unknown"),
                "original_length": len(content),
                "processed_length": len(processed_content)
            })
            
            if compression_result:
                result_metadata.update({
                    "compression_applied": True,
                    "compression_type": compression_result.compression_type.value,
                    "compression_ratio": compression_result.compression_ratio
                })
            else:
                result_metadata.update({
                    "compression_applied": False
                })
            
            return ProcessingResult(
                status=ProcessingStatus.COMPLETED,
                original_message=message,
                processed_message=processed_message,
                metadata=result_metadata,
                compression_result=compression_result
            )
            
        except Exception as e:
            logger.error(f"Message processing failed: {e}")
            return ProcessingResult(
                status=ProcessingStatus.FAILED,
                original_message=message,
                processed_message=message,
                metadata=metadata or {},
                error=str(e)
            )

    def process_batch(self, 
                     messages: List[Dict[str, Any]], 
                     metadata_list: List[Dict[str, Any]] = None) -> List[ProcessingResult]:
        """
        Batch process messages
        
        Args:
            messages: Message list
            metadata_list: Metadata list
            
        Returns:
            Processing result list
        """
        if metadata_list is None:
            metadata_list = [{}] * len(messages)
        
        results = []
        for message, metadata in zip(messages, metadata_list):
            result = self.process_message(message, metadata)
            results.append(result)
        
        return results

    def compress_message_content(self, 
                                content: str, 
                                compression_type: str = None,
                                metadata: Dict[str, Any] = None) -> tuple[str, CompressionResult]:
        """
        Directly compress message content
        
        Args:
            content: Content to compress
            compression_type: Compression type
            metadata: Metadata
            
        Returns:
            (compressed_content, compression_result)
        """
        if not self.enable_compression or not self.compressor:
            return content, None
        
        try:
            comp_type = CompressionType(compression_type) if compression_type else self.default_compression_type
            compression_result = self.compressor.compress(
                content=content,
                metadata=metadata,
                compression_type=comp_type
            )
            
            return compression_result.compressed_content, compression_result
            
        except Exception as e:
            logger.warning(f"Content compression failed: {e}")
            return content, None

    def get_compression_statistics(self) -> Dict[str, Any]:
        """
        Get detailed compression statistics
        """
        if not self.enable_compression or not self.compression_stats["compression_results"]:
            return {
                "compression_enabled": self.enable_compression,
                "total_messages_processed": self.compression_stats["total_messages_processed"],
                "total_messages_compressed": 0,
                "overall_compression_ratio": 1.0,
                "space_saved_percentage": 0.0
            }
        
        # Calculate overall compression ratio
        total_original = self.compression_stats["total_original_length"]
        total_compressed = self.compression_stats["total_compressed_length"]
        overall_ratio = total_compressed / total_original if total_original > 0 else 1.0
        space_saved = (1 - overall_ratio) * 100
        
        # Use compressor's statistics functionality
        detailed_stats = {}
        if self.compressor:
            detailed_stats = self.compressor.get_compression_stats(self.compression_stats["compression_results"])
        
        # Add pipeline-specific statistics
        detailed_stats.update({
            "compression_enabled": self.enable_compression,
            "total_messages_processed": self.compression_stats["total_messages_processed"],
            "total_messages_compressed": self.compression_stats["total_messages_compressed"],
            "overall_compression_ratio": overall_ratio,
            "space_saved_percentage": space_saved,
            "compression_success_rate": (self.compression_stats["total_messages_compressed"] / 
                                       max(self.compression_stats["total_messages_processed"], 1))
        })
        
        return detailed_stats

    def reset_statistics(self):
        """Reset statistics"""
        self.compression_stats = {
            "total_messages_processed": 0,
            "total_messages_compressed": 0,
            "total_original_length": 0,
            "total_compressed_length": 0,
            "compression_results": []
        }

    # Backward compatibility methods
    def process_text_prompt(self, 
                           prompt: Union[str, List[Dict[str, str]]], 
                           metadata: Dict[str, Any] = None) -> ProcessingResult:
        """Backward compatibility: process text prompt"""
        if isinstance(prompt, str):
            message = {"role": "user", "content": prompt}
        elif isinstance(prompt, list) and len(prompt) > 0:
            # If it's a message list, process the first message
            message = prompt[0]
        else:
            message = {"role": "user", "content": ""}
        
        return self.process_message(message, metadata)

    def process_tool_prompt(self, 
                           tool_messages: List[Dict[str, str]], 
                           metadata: Dict[str, Any] = None) -> ProcessingResult:
        """Backward compatibility: process tool prompt"""
        if not tool_messages:
            return ProcessingResult(
                status=ProcessingStatus.COMPLETED,
                original_message={},
                processed_message={},
                metadata=metadata or {}
            )
        
        # Process the first tool message
        return self.process_message(tool_messages[0], metadata)


# Backward compatibility alias
HiddenPipeline = MapPipeline

