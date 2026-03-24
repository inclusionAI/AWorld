"""
Memory manager - orchestrates all memory components.
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Set

from .config import MemoryConfig, load_memory_config
from .storage import MemoryStorage
from .watcher import MemoryWatcher
from .sync import MemorySyncManager, SimpleEmbeddingProvider
from .prompt_injector import MemoryPromptInjector
from .tools import create_memory_tools

logger = logging.getLogger(__name__)


class MemoryManager:
    """Main memory manager coordinating all components."""

    def __init__(
        self,
        workspace_dir: str,
        agent_id: str = "default",
        config: Optional[MemoryConfig] = None,
    ):
        """Initialize memory manager."""
        self.workspace_dir = Path(workspace_dir)
        self.agent_id = agent_id
        self.config = config or load_memory_config()
        
        # Resolve storage path
        db_path = self.config.resolve_store_path(agent_id)
        
        # Initialize components
        self.storage = MemoryStorage(
            db_path=db_path,
            cache_enabled=self.config.cache.enabled,
            fts_enabled=self.config.query.hybrid.enabled,
        )
        
        # Initialize embedding provider
        self.embedding_provider = None
        if self.config.provider == "openai" or self.config.provider == "auto":
            self.embedding_provider = SimpleEmbeddingProvider(model=self.config.model)
        
        # Initialize sync manager
        self.sync_manager = MemorySyncManager(
            storage=self.storage,
            workspace_dir=str(self.workspace_dir),
            chunk_tokens=self.config.chunking.tokens,
            chunk_overlap=self.config.chunking.overlap,
            embedding_provider=self.embedding_provider,
        )
        
        # Initialize watcher
        self.watcher = None
        if self.config.sync.watch:
            self.watcher = MemoryWatcher(
                workspace_dir=str(self.workspace_dir),
                on_change=self._on_file_change,
                debounce_ms=self.config.sync.watch_debounce_ms,
                extra_paths=self.config.extra_paths,
            )
        
        # Initialize prompt injector
        self.prompt_injector = MemoryPromptInjector(citations_mode="on")
        
        # Create tools
        self.tools = create_memory_tools(self, str(self.workspace_dir))
        
        # State
        self._started = False
        self._sync_task = None

    def _on_file_change(self):
        """Handle file change event."""
        logger.info("Memory files changed, marking dirty")
        self.sync_manager.mark_dirty()
        
        # Schedule sync
        if self._started:
            asyncio.create_task(self.sync_manager.sync(reason="file-change"))

    async def start(self):
        """Start memory manager."""
        if self._started:
            logger.warning("Memory manager already started")
            return
        
        logger.info("Starting memory manager")
        
        # Start watcher
        if self.watcher:
            self.watcher.start()
        
        # Initial sync if configured
        if self.config.sync.on_session_start:
            await self.sync_manager.sync(reason="session-start")
        
        # Start interval sync if configured
        if self.config.sync.interval_minutes > 0:
            self._sync_task = asyncio.create_task(self._interval_sync())
        
        self._started = True
        logger.info("Memory manager started")

    async def stop(self):
        """Stop memory manager."""
        if not self._started:
            return
        
        logger.info("Stopping memory manager")
        
        # Stop watcher
        if self.watcher:
            self.watcher.stop()
        
        # Cancel interval sync
        if self._sync_task:
            self._sync_task.cancel()
            try:
                await self._sync_task
            except asyncio.CancelledError:
                pass
        
        # Close storage
        self.storage.close()
        
        self._started = False
        logger.info("Memory manager stopped")

    async def _interval_sync(self):
        """Run periodic sync."""
        interval_seconds = self.config.sync.interval_minutes * 60
        
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.sync_manager.sync(reason="interval")
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Interval sync failed: {e}", exc_info=True)

    async def search(
        self,
        query: str,
        max_results: Optional[int] = None,
        min_score: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Search memory for relevant information.
        
        Args:
            query: Search query
            max_results: Maximum results to return
            min_score: Minimum similarity score
        
        Returns:
            List of search results
        """
        # Sync if configured
        if self.config.sync.on_search and self.sync_manager.dirty:
            await self.sync_manager.sync(reason="search")
        
        # Use config defaults if not specified
        max_results = max_results or self.config.query.max_results
        min_score = min_score or self.config.query.min_score
        
        # Generate query embedding
        if not self.embedding_provider:
            logger.warning("No embedding provider, returning empty results")
            return []
        
        query_embedding = await self.embedding_provider.embed(query)
        
        # Perform hybrid search
        vector_results = self.storage.search_vector(
            query_embedding=query_embedding,
            limit=max_results * self.config.query.hybrid.candidate_multiplier,
        )
        
        keyword_results = []
        if self.config.query.hybrid.enabled:
            keyword_results = self.storage.search_keyword(
                query=query,
                limit=max_results * self.config.query.hybrid.candidate_multiplier,
            )
        
        # Merge results
        results = self._merge_results(
            vector_results=vector_results,
            keyword_results=keyword_results,
            vector_weight=self.config.query.hybrid.vector_weight,
            text_weight=self.config.query.hybrid.text_weight,
        )
        
        # Filter by score and limit
        filtered = [r for r in results if r['score'] >= min_score]
        return filtered[:max_results]

    def _merge_results(
        self,
        vector_results: List[Dict[str, Any]],
        keyword_results: List[Dict[str, Any]],
        vector_weight: float,
        text_weight: float,
    ) -> List[Dict[str, Any]]:
        """Merge vector and keyword search results."""
        # Create a map of results by ID
        results_map = {}
        
        # Add vector results
        for result in vector_results:
            result_id = result['id']
            results_map[result_id] = {
                **result,
                'score': result['score'] * vector_weight,
            }
        
        # Add/merge keyword results
        for result in keyword_results:
            result_id = result['id']
            if result_id in results_map:
                # Merge scores
                results_map[result_id]['score'] += result.get('text_score', result['score']) * text_weight
            else:
                results_map[result_id] = {
                    **result,
                    'score': result.get('text_score', result['score']) * text_weight,
                }
        
        # Sort by score
        merged = list(results_map.values())
        merged.sort(key=lambda x: x['score'], reverse=True)
        
        return merged

    def get_prompt_injector(self) -> MemoryPromptInjector:
        """Get prompt injector."""
        return self.prompt_injector

    def get_tools(self) -> Dict[str, Any]:
        """Get memory tools."""
        return self.tools

    def inject_prompt(self, system_prompt: str, available_tools: Set[str]) -> str:
        """Inject memory section into system prompt."""
        return self.prompt_injector.inject_into_prompt(system_prompt, available_tools)
    
    def get_memory_prompt(self, available_tools: Optional[Set[str]] = None) -> str:
        """Get memory prompt section as string."""
        if available_tools is None:
            available_tools = {"memory_search", "memory_get"}
        lines = self.prompt_injector.build_memory_section(available_tools)
        return "\n".join(lines)

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.stop()
