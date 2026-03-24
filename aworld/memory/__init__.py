"""
AWorld Memory System

Provides long-term memory capabilities for agents through:
- Automatic file monitoring and synchronization
- Vector and full-text search
- Prompt injection for seamless integration
- Memory tools for agent use
"""

from .config import MemoryConfig, load_memory_config
from .manager import MemoryManager
from .storage import MemoryStorage
from .watcher import MemoryWatcher
from .sync import MemorySyncManager, SimpleEmbeddingProvider
from .prompt_injector import MemoryPromptInjector, get_memory_prompt_injector
from .tools import MemorySearchTool, MemoryGetTool, create_memory_tools

__all__ = [
    "MemoryConfig",
    "load_memory_config",
    "MemoryManager",
    "MemoryStorage",
    "MemoryWatcher",
    "MemorySyncManager",
    "SimpleEmbeddingProvider",
    "MemoryPromptInjector",
    "get_memory_prompt_injector",
    "MemorySearchTool",
    "MemoryGetTool",
    "create_memory_tools",
]

__version__ = "1.0.0"
