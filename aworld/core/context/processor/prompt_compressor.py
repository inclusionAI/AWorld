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
        self.max_tokens = self.config.get("max_tokens", 1000)
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
                max_tokens=self.max_tokens,
                temperature=0.3
            )
            
            compressed_content = response.content.strip()
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

class MapReduceCompressor(BaseCompressor):
    def __init__(self, config: Dict[str, Any] = None, llm_config: ModelConfig = None):
        super().__init__(config)
        self.chunk_size = self.config.get("chunk_size", 2000)
        self.overlap = self.config.get("overlap", 200)
        self.task_type = self.config.get("task_type", "summarize")
        self.llm_client = _create_llm_client(llm_config)
        
        # Processing statistics
        self.processing_stats = {
            "total_messages_processed": 0,
            "total_chunks_created": 0,
            "total_processing_time": 0,
            "average_chunk_size": 0
        }
        
        if not self.llm_client:
            logger.warning("No LLM service provided, MapReduce functionality will be limited")

    def compress(self, content: str, metadata: Dict[str, Any] = None) -> CompressionResult:
        """
        Compress content using MapReduce approach
        
        Args:
            content: Long text content to compress
            metadata: Additional metadata
            
        Returns:
            CompressionResult with compressed content
        """
        original_content = content
        
        try:
            # Process long content using MapReduce
            compressed_content = self.process_long_content(content, self.task_type)
            compression_ratio = self._calculate_compression_ratio(original_content, compressed_content)
            
            return CompressionResult(
                original_content=original_content,
                compressed_content=compressed_content,
                compression_ratio=compression_ratio,
                metadata={
                    "chunk_size": self.chunk_size,
                    "overlap": self.overlap,
                    "task_type": self.task_type,
                    "chunks_created": self.processing_stats.get("last_chunk_count", 0),
                    "original_metadata": metadata or {}
                },
                compression_type=CompressionType.MAP_REDUCE
            )
            
        except Exception as e:
            logger.error(f"MapReduce compression failed: {traceback.format_exc()}")
            return CompressionResult(
                original_content=original_content,
                compressed_content=content,
                compression_ratio=1.0,
                metadata={"error": str(e), "original_metadata": metadata or {}},
                compression_type=CompressionType.MAP_REDUCE
            )

    def process_long_content(self, content: str, task_type: str = "summarize", **kwargs) -> str:
        """
        Process long text content core method
        
        Args:
            content: Long text content
            task_type: Processing task type
            **kwargs: Additional parameters
            
        Returns:
            Processed text content
        """
        try:
            # 1. Smart chunking
            chunks = self.smart_split_document(content)
            self.processing_stats["total_chunks_created"] += len(chunks)
            self.processing_stats["last_chunk_count"] = len(chunks)
            
            # 2. Map phase - process each chunk
            # TODO async
            map_results = self.sequential_map(chunks, task_type, **kwargs)
            
            # 3. Reduce phase - aggregate results
            final_result = self.reduce_results(map_results, task_type)
            
            return final_result
            
        except Exception as e:
            logger.error(f"Long text processing failed: {traceback.format_exc()}")
            return content  # Return original content on failure

    def smart_split_document(self, document: str) -> List[str]:
        """Smart document chunking"""
        # Priority: split by semantic boundaries
        if self.has_clear_sections(document):
            return self.split_by_sections(document)

        # Split by paragraphs
        elif self.has_paragraphs(document):
            return self.split_by_paragraphs(document)

        # Fixed length split (with overlap)
        else:
            return self.split_with_overlap(document)

    def has_clear_sections(self, text: str) -> bool:
        """Check if text has clear section structure"""
        section_patterns = [
            r'第[一二三四五六七八九十\d]+章',
            r'第[一二三四五六七八九十\d]+节',
            r'^\d+\.',
            r'^#+ ',
            r'===',
            r'---'
        ]
        for pattern in section_patterns:
            if len(re.findall(pattern, text, re.MULTILINE)) >= 2:
                return True
        return False

    def has_paragraphs(self, text: str) -> bool:
        """Check if text has paragraph structure"""
        return text.count('\n\n') >= 2

    def split_by_sections(self, text: str) -> List[str]:
        """Split by sections"""
        # Simple section splitting implementation
        sections = re.split(r'(?=第[一二三四五六七八九十\d]+[章节]|^#+ |^\d+\.)', text, flags=re.MULTILINE)
        return [section.strip() for section in sections if section.strip()]

    def split_by_paragraphs(self, text: str) -> List[str]:
        """Split by paragraphs, but keep within chunk_size limit"""
        paragraphs = text.split('\n\n')
        chunks = []
        current_chunk = ""
        
        for para in paragraphs:
            if len(current_chunk) + len(para) <= self.chunk_size:
                current_chunk += para + '\n\n'
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = para + '\n\n'
        
        if current_chunk:
            chunks.append(current_chunk.strip())
        
        return chunks

    def split_with_overlap(self, text: str) -> List[str]:
        """Fixed length split with overlap"""
        chunks = []
        start = 0

        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            chunk = text[start:end]
            chunks.append(chunk)

            if end == len(text):
                break

            # Overlap handling, avoid sentence truncation
            start = end - self.overlap

        return chunks

    async def parallel_map(self, chunks: List[str], task_type: str, **kwargs) -> List[str]:
        """Process document chunks in parallel"""
        if not self.llm_client:
            return chunks  # Return original chunks when no LLM service
        
        tasks = []
        for i, chunk in enumerate(chunks):
            prompt = self.build_map_prompt(chunk, task_type, i, len(chunks), **kwargs)
            task = self.process_chunk_async(prompt)
            tasks.append(task)

        # Concurrent execution
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Handle exception results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.warning(f"Chunk {i} processing failed: {result}, using original content")
                processed_results.append(chunks[i])
            else:
                processed_results.append(result)
        
        return processed_results

    def sequential_map(self, chunks: List[str], task_type: str, **kwargs) -> List[str]:
        """Process document chunks sequentially"""
        if not self.llm_client:
            return chunks
        
        results = []
        for i, chunk in enumerate(chunks):
            try:
                prompt = self.build_map_prompt(chunk, task_type, i, len(chunks), **kwargs)
                result = self.llm_client.completion(prompt)
                results.append(result)
            except Exception as e:
                logger.warning(f"Chunk {i} processing failed: {traceback.format_exc()}, using original content")
                results.append(chunk)
        
        return results

    async def process_chunk_async(self, prompt: str) -> str:
        """Process single chunk asynchronously"""
        try:
            if hasattr(self.llm_client, 'agenerate'):
                return await self.llm_client.agenerate(prompt)
            else:
                # If no async method, use thread pool
                loop = asyncio.get_event_loop()
                return await loop.run_in_executor(None, self.llm_client.completion, prompt)
        except Exception as e:
            logger.error(f"Async chunk processing failed: {traceback.format_exc()}")
            raise

    def build_map_prompt(self, chunk: str, task_type: str, chunk_id: int, total_chunks: int, **kwargs) -> str:
        """Build Map phase prompt"""
        question = kwargs.get('question', '')
        
        base_prompts = {
            "summarize": f"""
You are processing part {chunk_id+1} of a long document (total {total_chunks} parts).
Please summarize the key information from the following text:

{chunk}

Summary requirements:
1. Retain important details and key information
2. Note this is part {chunk_id+1} of the document
3. If it involves content from other parts, please mark it
""",

            "qa": f"""
You are processing part {chunk_id+1} of a long document (total {total_chunks} parts).
Answer the question based on the following text:

Text: {chunk}

Question: {question}

If this part of the text cannot fully answer the question, please indicate that information from other parts is needed.
""",

            "analyze": f"""
You are analyzing part {chunk_id+1} of a long document (total {total_chunks} parts).
Please analyze the following text:

{chunk}

Analysis requirements:
1. Extract key viewpoints and arguments
2. Identify important concepts and terms
3. Note this is analysis of part {chunk_id+1}
"""
        }

        return base_prompts.get(task_type, base_prompts["summarize"])
    
    def reduce_results(self, map_results: List[str], task_type: str) -> str:
        """Smart result aggregation"""
        if not map_results:
            return ""
        
        if len(map_results) == 1:
            return map_results[0]
        
        # If too many results, need hierarchical aggregation
        if len(map_results) > 10:
            return self.hierarchical_reduce(map_results, task_type)
        else:
            return self.direct_reduce(map_results, task_type)

    def hierarchical_reduce(self, results: List[str], task_type: str) -> str:
        """Hierarchical aggregation"""
        current_level = results

        while len(current_level) > 1:
            next_level = []

            # Group every 3-5 results for aggregation
            for i in range(0, len(current_level), 4):
                group = current_level[i:i+4]
                reduced = self.reduce_group(group, task_type)
                next_level.append(reduced)

            current_level = next_level

        return current_level[0]

    def direct_reduce(self, results: List[str], task_type: str) -> str:
        """Direct aggregation"""
        if not self.llm_client:
            # Simple concatenation when no LLM service
            return '\n\n'.join(results)
        
        prompt = self.build_reduce_prompt(results, task_type)
        try:
            return self.llm_client.generate(prompt)
        except Exception as e:
            logger.error(f"Aggregation failed: {traceback.format_exc()}")
            return '\n\n'.join(results)

    def reduce_group(self, group: List[str], task_type: str) -> str:
        """Aggregate a group of results"""
        return self.direct_reduce(group, task_type)

    def build_reduce_prompt(self, partial_results: List[str], task_type: str) -> str:
        """Build Reduce phase prompt"""
        results_text = "\n\n".join([
            f"Part {i+1}: {result}" 
            for i, result in enumerate(partial_results)
        ])

        prompts = {
            "summarize": f"""
The following are summaries of various parts of a long document:

{results_text}

Please merge these part summaries into a complete, coherent summary, requirements:
1. Maintain logical coherence
2. Remove duplicate information
3. Highlight main viewpoints
4. Maintain appropriate level of detail
""",

            "qa": f"""
The following are answer fragments obtained from various parts of the document:

{results_text}

Please synthesize this information to give a complete, accurate final answer.
""",

            "analyze": f"""
The following are analyses of various parts of the document:

{results_text}

Please synthesize these analyses to give complete analytical conclusions:
1. Integrate key viewpoints from all parts
2. Identify overall patterns and trends
3. Provide comprehensive insights
"""
        }

        return prompts.get(task_type, prompts["summarize"])

class PromptCompressor:
    """Unified Prompt compressor"""
    
    def __init__(self,
                 compression_types: List[CompressionType] = None,
                 configs: Dict[CompressionType, Dict[str, Any]] = None,
                 llm_config: ModelConfig = None):
        self.compression_types = compression_types or [CompressionType.LLM_BASED]
        self.configs = configs or {}
        
        # Initialize various compressors
        self.compressors = {}
        for comp_type in self.compression_types:
            config = self.configs.get(comp_type, {})
            if comp_type == CompressionType.LLM_BASED:
                self.compressors[comp_type] = LLMCompressor(config=config, llm_config=llm_config)
            elif comp_type == CompressionType.MAP_REDUCE:
                self.compressors[comp_type] = MapReduceCompressor(config=config, llm_config=llm_config)
    
    def compress(self, content: str, metadata: Dict[str, Any] = None, compression_type: CompressionType = None) -> CompressionResult:
        if compression_type is None:
            compression_type = self.compression_types[0]
        
        if compression_type not in self.compressors:
            logger.warning(f"Compression type {compression_type} unavailable, using default compressor")
            compression_type = self.compression_types[0]
        
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
    