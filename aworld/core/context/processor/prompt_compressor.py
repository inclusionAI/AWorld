import asyncio
import logging
import re
from abc import ABC, abstractmethod
import traceback
from typing import Any, Dict, List

from aworld.config.conf import ModelConfig
from aworld.core.context.processor import CompressionResult, CompressionType
from aworld.models.llm import get_llm_model
from aworld.config import ConfigDict
                
logger = logging.getLogger(__name__)

def _create_llm_client(llm_config: ModelConfig):
    config = ConfigDict(llm_config)
    return get_llm_model(config)

def _remove_think_blocks(content: str) -> str:
    """Remove <think>...</think> blocks from content"""
    # Use regex to remove all <think>...</think> blocks (case insensitive, multiline)
    pattern = r'<think>.*?</think>'
    cleaned_content = re.sub(pattern, '', content, flags=re.IGNORECASE | re.DOTALL)
    return cleaned_content
    
class BaseCompressor(ABC):
    """Base compressor class"""
    
    def __init__(self, config: Dict[str, Any] = None, llm_config: ModelConfig = None):
        self.config = config or {}
        self.llm_config = llm_config
    @abstractmethod
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content"""
        pass
    
    def _calculate_compression_ratio(self, original: str, compressed: str) -> float:
        """Calculate compression ratio"""
        if len(original) == 0:
            return 1.0
        return len(compressed) / len(original)

class LLMCompressor(BaseCompressor):
    """LLM-based compressor"""
    
    def __init__(self, config: Dict[str, Any] = None, llm_config: ModelConfig = None):
        super().__init__(config, llm_config)
        self.compression_prompt = self.config.get("compression_prompt", self._default_compression_prompt())
        # Lazy import to avoid circular dependencies
        self._llm_client = _create_llm_client(llm_config)
    
    def _default_compression_prompt(self) -> str:
        """Default compression prompt"""
        return """## Task
You are a text compression expert. Please intelligently compress the following text, retaining core information and key content while removing redundancy and unimportant parts.

## Compression Requirements
1. Keep the position and count of [SYSTEM], [USER], [ASSISTANT], and [TOOL] tags unchanged in the output
2. Maintain the main meaning and logical structure of the original text, retain key information and important details, use more concise expressions
3. Remove repetitive, redundant statements, ensure the compressed text remains coherent and readable

## Original Text:
{content}

Please output the compressed text:"""
    
    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """Compress content using LLM"""
        original_content = content
        
        # Get LLM client
        llm_client = self._llm_client
        if llm_client is None:
            logger.warning("LLM client unavailable, returning original content")
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"error": "LLM client unavailable", "original_metadata": metadata or {}},
                compression_type=CompressionType.LLM_BASED
            )
        
        try:
            # Build prompt
            prompt = self.compression_prompt.format(content=content)
            messages = [{"role": "user", "content": prompt}]
            
            # Call LLM
            response = llm_client.completion(
                messages=messages,
                # max_tokens=self.max_tokens,
                temperature=0.3
            )
            
            # Remove <think>...</think> blocks first, then strip whitespace
            compressed_content = _remove_think_blocks(response.content).strip()
            compression_ratio = self._calculate_compression_ratio(original_content, compressed_content)
            
            return CompressionResult(
                original_content=original_content,
                compressed_content=compressed_content,
                compression_ratio=compression_ratio,
                metadata={
                    "prompt_tokens": getattr(response.usage, 'prompt_tokens', 0),
                    "completion_tokens": getattr(response.usage, 'completion_tokens', 0),
                    "original_metadata": metadata or {}
                },
                compression_type=CompressionType.LLM_BASED
            )
            
        except Exception as e:
            logger.error(f"LLM compression failed: {traceback.format_exc()}")
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"error": str(e), "original_metadata": metadata or {}},
                compression_type=CompressionType.LLM_BASED
            )

class PromptCompressor:
    """Unified Prompt compressor"""
    
    def __init__(self,
                 compression_types: List[CompressionType] = None,
                 configs: Dict[CompressionType, Dict[str, Any]] = None,
                 llm_config: ModelConfig = None):
        self.compression_types = compression_types or [CompressionType.LLM_BASED]
        self.configs = configs or {}
        
        # Initialize LLM compressor only
        self.compressors = {}
        for comp_type in self.compression_types:
            config = self.configs.get(comp_type, {})
            if comp_type == CompressionType.LLM_BASED:
                self.compressors[comp_type] = LLMCompressor(config=config, llm_config=llm_config)
            else:
                logger.warning(f"Unsupported compression type: {comp_type}")
    
    def compress(self, content: str, metadata: Dict[str, Any] = None, compression_type: CompressionType = None) -> CompressionResult:
        if compression_type is None:
            compression_type = CompressionType.LLM_BASED
        
        if compression_type not in self.compressors:
            logger.warning(f"Compression type {compression_type} unavailable, using LLM_BASED compressor")
            compression_type = CompressionType.LLM_BASED
        
        compressor = self.compressors[compression_type]
        return compressor.compress(content, metadata)
    
    def compress_batch(self, contents: List[str], metadata_list: List[Dict[str, Any]] = None, compression_type: CompressionType = None) -> List[CompressionResult]:
        if metadata_list is None:
            metadata_list = [{}] * len(contents)
        
        results = []
        for content, metadata in zip(contents, metadata_list):
            result = self.compress(content, metadata, compression_type)
            results.append(result)
        
        return results
    