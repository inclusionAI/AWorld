"""
Processor Module - Operation Pipeline Framework

Provides operation processing framework for AMNI context system with factory-based registry.

Core Components:
- BaseOp: Abstract base class for all operations
- OpFactory: Factory pattern for operation registration via @memory_op decorator
- MemoryCommand: Command wrapper for memory operations (ADD/DELETE/KEEP/ADD_NODE/ADD_EDGE)

Operation Categories:

1. Memory Persistence: SaveMemoryOp, SaveGraphMemoryOp
   - Persist general memory items and graph structures

2. Prompt Enhancement: SystemPromptAugmentOp
   - Augment system prompts with memory context using rerank strategy

3. Tool Result Processing: ToolResultOffloadOp
   - Offload large tool results (>30k tokens) to workspace

4. Extraction Operations:
   - Base Classes: LlmExtractOp (OpenAI), LangExtractOp (langextract with few-shot)
   - Memory Content: ExtractToolFactOp, ExtractAgentExperienceOp, ExtractUserProfileOp
   - Graph Memory: ExtractToolMemoryNodeOp, ExtractToolMemoryLinkOp
   - Entity & Keywords: ExtractEntityOp (NLTK-based NER and TF-IDF)

Design: Factory, Template Method, Command, and Pipeline patterns
"""

from .base import BaseArtifactProcessor
from .memory_processor import PipelineMemoryProcessor
from .processor_factory import BaseContextProcessor, ProcessorFactory

__all__ = [
    "ProcessorFactory",
    "BaseContextProcessor",
    "BaseArtifactProcessor",
    "PipelineMemoryProcessor",
    "memory_processor",
]
