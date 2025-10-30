import re
import traceback
from typing import List, Optional, Dict, Union

from langchain_text_splitters import RecursiveCharacterTextSplitter, MarkdownTextSplitter, \
    HTMLSemanticPreservingSplitter
from langchain_text_splitters.base import TextSplitter

from aworld.output import Artifact
from .ant_search import AntSearchChunker
from .base import Chunk, ChunkConfig, ChunkerBase
from aworld.logs.util import logger


class SmartChunker(ChunkerBase):
    """
    A smart chunker that uses intelligent text splitting strategies to ensure uniform and semantic chunks.
    
    Features:
    - Adaptive text splitting based on content type
    - Maintains semantic boundaries (sentences, paragraphs)
    - Ensures chunk size uniformity
    - Supports multiple content formats (markdown, HTML, plain text)
    """

    def __init__(self, config: ChunkConfig):
        super().__init__(config=config)
        
        # Initialize different text splitters for different content types
        self._splitters = self._initialize_splitters()
        self._ant_search_chunker = AntSearchChunker()


    def _initialize_splitters(self) -> dict[str, TextSplitter]:
        """
        Initialize different text splitters for different content types.
        
        Returns:
            Dictionary mapping content type to appropriate text splitter
        """
        # Recursive character splitter for general text with better semantic boundaries
        recursive_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            length_function=len,
            separators=["\n\n", "\n", ". ", "! ", "? ", " ", ""],  # Prioritize paragraph breaks, then sentences
            keep_separator=True,
            strip_whitespace=True,
        )
        
        # Markdown splitter for markdown content
        markdown_splitter = MarkdownTextSplitter(
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
        )

        
        # HTML splitter for HTML content
        html_splitter = HTMLSemanticPreservingSplitter(
            max_chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            headers_to_split_on=[("h1", "Header 1"), ("h2", "Header 2")],
            preserve_links=True,
            preserve_images=True
        )

        # Playwright splitter for Playwright content


        return {
            "text": recursive_splitter,
            "markdown": markdown_splitter,
            "html": html_splitter,
        }

    def _detect_content_type(self, content: str) -> str:
        """
        Detect the content type to choose appropriate splitter.
        
        Args:
            content: The text content to analyze
            
        Returns:
            Content type string ("markdown", "html", or "text")
        """
        # Check for markdown patterns
        markdown_patterns = [
            r'^#\s+',  # Headers
            r'\*\*.*?\*\*',  # Bold text
            r'\*.*?\*',  # Italic text
            r'\[.*?\]\(.*?\)',  # Links
            r'```[\s\S]*?```',  # Code blocks
            r'^\s*[-*+]\s+',  # List items
        ]
        
        # Check for HTML patterns
        html_patterns = [
            r'<[^>]+>',  # HTML tags
            r'&[a-zA-Z]+;',  # HTML entities
        ]
        
        # Count pattern matches
        markdown_score = sum(len(re.findall(pattern, content, re.MULTILINE)) for pattern in markdown_patterns)
        html_score = sum(len(re.findall(pattern, content)) for pattern in html_patterns)
        
        if markdown_score > html_score and markdown_score > 0:
            return "markdown"
        elif html_score > 0:
            return "html"
        else:
            return "text"

    def _normalize_chunk_sizes(self, texts: List[Union[str, object]]) -> List[str]:
        """
        Normalize chunk sizes to ensure uniformity.
        
        Args:
            texts: List of text chunks (strings or Document objects)
            
        Returns:
            List of normalized text chunks
        """
        if not texts:
            return texts
            
        normalized_texts = []
        target_size = self.config.chunk_size
        
        for text in texts:
            # Handle both string and Document objects
            if hasattr(text, 'page_content'):
                # This is a Document object
                text_content = text.page_content
            else:
                # This is a string
                text_content = text
                
            if len(text_content) <= target_size:
                normalized_texts.append(text_content)
            else:
                # Split oversized chunks further
                sub_chunks = self._split_oversized_chunk(text_content, target_size)
                normalized_texts.extend(sub_chunks)
        
        return normalized_texts

    def _split_oversized_chunk(self, text: str, target_size: int) -> List[str]:
        """
        Split an oversized chunk into smaller, uniform chunks.
        
        Args:
            text: The text to split
            target_size: Target chunk size
            
        Returns:
            List of smaller text chunks
        """
        chunks = []
        current_pos = 0
        
        while current_pos < len(text):
            # Find the best break point within the target size
            end_pos = min(current_pos + target_size, len(text))
            
            if end_pos == len(text):
                # Last chunk
                chunks.append(text[current_pos:])
                break
            
            # Look for good break points (sentence endings, paragraph breaks)
            break_points = [
                text.rfind('. ', current_pos, end_pos),
                text.rfind('! ', current_pos, end_pos),
                text.rfind('? ', current_pos, end_pos),
                text.rfind('\n\n', current_pos, end_pos),
                text.rfind('\n', current_pos, end_pos),
                text.rfind(' ', current_pos, end_pos),
            ]
            
            # Find the best break point
            best_break = -1
            for point in break_points:
                if point > current_pos:
                    best_break = point
                    break
            
            if best_break > current_pos:
                # Found a good break point
                chunk_text = text[current_pos:best_break + 1].strip()
                if chunk_text:
                    chunks.append(chunk_text)
                current_pos = best_break + 1
            else:
                # No good break point, force split
                chunk_text = text[current_pos:end_pos].strip()
                if chunk_text:
                    chunks.append(chunk_text)
                current_pos = end_pos
        
        return chunks

    async def chunk(self, artifact: Artifact) -> Optional[List[Chunk]]:
        """
        Intelligently chunk the given artifact content.
        
        Args:
            artifact: The artifact to chunk
            
        Returns:
            A list of uniform and semantic Chunk objects
        """
        try:
            if artifact.metadata.get("origin_tool_name") == "aworldsearch-server" and artifact.metadata.get("origin_action_name") == "search":
                chunks = await self._ant_search_chunker.chunk(artifact)
                if chunks:
                    return chunks

            # Detect content type and choose appropriate splitter
            content_type = self._detect_content_type(artifact.content)
            splitter = self._splitters.get(content_type, self._splitters["text"])
            
            logger.debug(f"Using {content_type} splitter for artifact {artifact.artifact_id}")
            
            # Split text using appropriate splitter
            texts = splitter.split_text(artifact.content)
            
            # Normalize chunk sizes for uniformity
            normalized_texts = self._normalize_chunk_sizes(texts)
            
            # Generate contextual information for each chunk
            contextualized_texts = []
            for i, chunk_text in enumerate(normalized_texts):
                # Add context generation if enabled (can be made configurable)
                # context = await self.generate_context(chunk_text, artifact.content[:1000])
                contextualized_texts.append(chunk_text)
            
            # Create chunks with metadata
            chunks = self._create_chunks(contextualized_texts, artifact)
            
            # Log chunking statistics
            chunk_sizes = [len(chunk.content) for chunk in chunks]
            avg_size = sum(chunk_sizes) / len(chunk_sizes) if chunk_sizes else 0
            logger.debug(f"Created {len(chunks)} chunks with average size {avg_size:.1f} characters")
            
            return chunks
            
        except Exception as e:
            logger.error(f"Error during chunking: {str(e)}, traceback is {traceback.format_exc()}")
            return None

    def get_chunking_stats(self, chunks: List[Chunk]) -> dict:
        """
        Get statistics about the chunking process.
        
        Args:
            chunks: List of created chunks
            
        Returns:
            Dictionary containing chunking statistics
        """
        if not chunks:
            return {}
            
        chunk_sizes = [len(chunk.content) for chunk in chunks]
        
        return {
            "total_chunks": len(chunks),
            "min_chunk_size": min(chunk_sizes),
            "max_chunk_size": max(chunk_sizes),
            "avg_chunk_size": sum(chunk_sizes) / len(chunk_sizes),
            "chunk_size_variance": self._calculate_variance(chunk_sizes),
            "total_content_length": sum(chunk_sizes),
        }

    def _calculate_variance(self, values: List[float]) -> float:
        """
        Calculate variance of a list of values.
        
        Args:
            values: List of numeric values
            
        Returns:
            Variance value
        """
        if len(values) <= 1:
            return 0.0
            
        mean = sum(values) / len(values)
        squared_diff_sum = sum((x - mean) ** 2 for x in values)
        return squared_diff_sum / len(values)


def get_smart_chunker_config(
        chunk_size: int = 512,
        chunk_overlap: int = 64,
        provider: str = "smart"
) -> ChunkConfig:
    """
    Get a configuration for SmartChunker with optimized settings.

    Args:
        chunk_size: Target size for each chunk in characters
        chunk_overlap: Overlap between consecutive chunks
        provider: Provider identifier for the chunker

    Returns:
        Configured ChunkConfig object
    """
    return ChunkConfig(
        provider=provider,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        chunk_separator="\n\n"  # Smart separator for better paragraph breaks
    )


def get_optimized_configs() -> Dict[str, ChunkConfig]:
    """
    Get a collection of optimized configurations for different use cases.

    Returns:
        Dictionary of named configurations
    """
    return {
        "small_chunks": get_smart_chunker_config(
            chunk_size=256,
            chunk_overlap=32
        ),
        "medium_chunks": get_smart_chunker_config(
            chunk_size=512,
            chunk_overlap=64
        ),
        "large_chunks": get_smart_chunker_config(
            chunk_size=1024,
            chunk_overlap=128
        ),
        "code_chunks": get_smart_chunker_config(
            chunk_size=800,
            chunk_overlap=100
        ),
        "document_chunks": get_smart_chunker_config(
            chunk_size=600,
            chunk_overlap=80
        )
    }


def create_smart_chunker(config_name: str = "medium_chunks") -> SmartChunker:
    """
    Create a SmartChunker instance with a predefined configuration.

    Args:
        config_name: Name of the configuration to use

    Returns:
        Configured SmartChunker instance

    Raises:
        ValueError: If the configuration name is not found
    """
    configs = get_optimized_configs()

    if config_name not in configs:
        raise ValueError(f"Configuration '{config_name}' not found. Available: {list(configs.keys())}")

    return SmartChunker(config=configs[config_name])
