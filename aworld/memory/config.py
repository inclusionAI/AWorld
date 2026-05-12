"""
Memory configuration management module.
Handles configuration parsing and normalization for the memory system.
"""

import os
from pathlib import Path
from typing import List, Literal, Optional, Dict, Any
from dataclasses import dataclass, field


@dataclass
class MemoryChunkingConfig:
    """Configuration for text chunking."""
    tokens: int = 400
    overlap: int = 80


@dataclass
class MemorySyncSessionsConfig:
    """Configuration for session synchronization."""
    delta_bytes: int = 100_000
    delta_messages: int = 50
    post_compaction_force: bool = True


@dataclass
class MemorySyncConfig:
    """Configuration for memory synchronization."""
    on_session_start: bool = True
    on_search: bool = True
    watch: bool = True
    watch_debounce_ms: int = 1500
    interval_minutes: int = 0
    sessions: MemorySyncSessionsConfig = field(default_factory=MemorySyncSessionsConfig)


@dataclass
class MemoryHybridConfig:
    """Configuration for hybrid search."""
    enabled: bool = True
    vector_weight: float = 0.7
    text_weight: float = 0.3
    candidate_multiplier: int = 4


@dataclass
class MemoryQueryConfig:
    """Configuration for memory queries."""
    max_results: int = 6
    min_score: float = 0.35
    hybrid: MemoryHybridConfig = field(default_factory=MemoryHybridConfig)


@dataclass
class MemoryVectorConfig:
    """Configuration for vector storage."""
    enabled: bool = True
    extension_path: Optional[str] = None


@dataclass
class MemoryStoreConfig:
    """Configuration for memory storage."""
    driver: Literal["sqlite"] = "sqlite"
    path: str = ""
    vector: MemoryVectorConfig = field(default_factory=MemoryVectorConfig)


@dataclass
class MemoryCacheConfig:
    """Configuration for embedding cache."""
    enabled: bool = True
    max_entries: Optional[int] = None


@dataclass
class MemoryConfig:
    """Main memory configuration."""
    enabled: bool = True
    sources: List[Literal["memory", "sessions"]] = field(default_factory=lambda: ["memory"])
    extra_paths: List[str] = field(default_factory=list)
    provider: Literal["openai", "local", "gemini", "voyage", "mistral", "ollama", "auto"] = "auto"
    model: str = "text-embedding-3-small"
    output_dimensionality: Optional[int] = None
    store: MemoryStoreConfig = field(default_factory=MemoryStoreConfig)
    chunking: MemoryChunkingConfig = field(default_factory=MemoryChunkingConfig)
    sync: MemorySyncConfig = field(default_factory=MemorySyncConfig)
    query: MemoryQueryConfig = field(default_factory=MemoryQueryConfig)
    cache: MemoryCacheConfig = field(default_factory=MemoryCacheConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MemoryConfig":
        """Create configuration from dictionary."""
        # Parse nested configurations
        store_data = data.get("store", {})
        store = MemoryStoreConfig(
            driver=store_data.get("driver", "sqlite"),
            path=store_data.get("path", ""),
            vector=MemoryVectorConfig(
                enabled=store_data.get("vector", {}).get("enabled", True),
                extension_path=store_data.get("vector", {}).get("extension_path"),
            ),
        )

        chunking_data = data.get("chunking", {})
        chunking = MemoryChunkingConfig(
            tokens=chunking_data.get("tokens", 400),
            overlap=chunking_data.get("overlap", 80),
        )

        sync_data = data.get("sync", {})
        sync = MemorySyncConfig(
            on_session_start=sync_data.get("on_session_start", True),
            on_search=sync_data.get("on_search", True),
            watch=sync_data.get("watch", True),
            watch_debounce_ms=sync_data.get("watch_debounce_ms", 1500),
            interval_minutes=sync_data.get("interval_minutes", 0),
            sessions=MemorySyncSessionsConfig(
                delta_bytes=sync_data.get("sessions", {}).get("delta_bytes", 100_000),
                delta_messages=sync_data.get("sessions", {}).get("delta_messages", 50),
                post_compaction_force=sync_data.get("sessions", {}).get("post_compaction_force", True),
            ),
        )

        query_data = data.get("query", {})
        query = MemoryQueryConfig(
            max_results=query_data.get("max_results", 6),
            min_score=query_data.get("min_score", 0.35),
            hybrid=MemoryHybridConfig(
                enabled=query_data.get("hybrid", {}).get("enabled", True),
                vector_weight=query_data.get("hybrid", {}).get("vector_weight", 0.7),
                text_weight=query_data.get("hybrid", {}).get("text_weight", 0.3),
                candidate_multiplier=query_data.get("hybrid", {}).get("candidate_multiplier", 4),
            ),
        )

        cache_data = data.get("cache", {})
        cache = MemoryCacheConfig(
            enabled=cache_data.get("enabled", True),
            max_entries=cache_data.get("max_entries"),
        )

        return cls(
            enabled=data.get("enabled", True),
            sources=data.get("sources", ["memory"]),
            extra_paths=data.get("extra_paths", []),
            provider=data.get("provider", "auto"),
            model=data.get("model", "text-embedding-3-small"),
            output_dimensionality=data.get("output_dimensionality"),
            store=store,
            chunking=chunking,
            sync=sync,
            query=query,
            cache=cache,
        )

    def resolve_store_path(self, agent_id: str) -> str:
        """Resolve the storage path for the given agent."""
        if self.store.path:
            path = self.store.path.replace("{agent_id}", agent_id)
            return str(Path(path).expanduser().resolve())
        
        # Default path: ~/.aworld/memory/{agent_id}.sqlite
        home = Path.home()
        memory_dir = home / ".aworld" / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        return str(memory_dir / f"{agent_id}.sqlite")


def load_memory_config(config_path: Optional[str] = None) -> MemoryConfig:
    """Load memory configuration from file or use defaults."""
    if config_path and os.path.exists(config_path):
        import yaml
        with open(config_path, "r") as f:
            data = yaml.safe_load(f)
            return MemoryConfig.from_dict(data.get("memory", {}))
    
    return MemoryConfig()
