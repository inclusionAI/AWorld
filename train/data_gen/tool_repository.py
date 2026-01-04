# coding: utf-8
# Copyright (c) 2025 inclusionAI.
import asyncio
import random
from typing import Dict, List, Optional, Any
from dataclasses import asdict

from jsonlines import jsonlines

from aworld.core.storage.base import Storage
from aworld.core.storage.data import Data
from aworld.logs.util import logger
from train.data_gen.schema import Specification, GeneratedTool


class ToolRepository:
    """Tool Repository for storing, managing and retrieving generated tools with advanced caching and persistence mechanisms."""

    def __init__(self, buffer_size: int = 1000, storage: Storage = None):
        self.name = "generated_tools"
        self.stored_buffer_size = buffer_size
        self.stored_buffer = []
        self.mem_buffer_size = buffer_size * 100
        self.tools: Dict[str, GeneratedTool] = {}
        # Tool distribution for eviction
        self.distribution: Dict[str, float] = {}
        # Tool access count for sampling
        self.access_count: Dict[str, int] = {}
        self._lock = asyncio.Lock()
        self.storage = storage

    async def add_tools(self, tools: List[GeneratedTool]) -> bool:
        for tool in tools:
            await self.add_tool(tool)
        return True

    async def add_tool(self, tool: GeneratedTool) -> bool:
        """Add a tool to the repository.
        
        Args:
            tool: Tool to be added.
            
        Returns:
            Whether the addition was successful.
        """
        async with self._lock:
            if len(self.tools) >= self.mem_buffer_size:
                await self._evict_tool()

            self.tools[tool.spec.name] = tool
            self.access_count[tool.spec.name] = 0
            self.distribution[tool.spec.category] = self.distribution.get(tool.spec.category, 0) + 1

            self.stored_buffer.append(tool)
            if len(self.stored_buffer) >= self.stored_buffer_size:
                await self.save_to_storage()
                self.stored_buffer.clear()

            logger.debug(f"{tool.spec.name} add success.")
            return True

    async def get_tool(self, tool_id: str) -> Optional[GeneratedTool]:
        """Get a tool by tool ID.
        
        Args:
            tool_id: ID of the tool to retrieve.
            
        Returns:
            Tool instance or None if not found.
        """
        async with self._lock:
            tool = self.tools.get(tool_id)
            if tool:
                self.access_count[tool_id] = self.access_count.get(tool_id, 0) + 1
            return tool

    async def get_by_category(self, category: str, count: int = 5) -> List[GeneratedTool]:
        """Get tools by category.
        
        Args:
            category: Tool category.
            count: Number of tools to retrieve.
            
        Returns:
            List of tools matching the category.
        """
        async with self._lock:
            filtered_tools = []
            for tool in self.tools.values():
                if tool.spec.category == category:
                    filtered_tools.append(tool)
                if len(filtered_tools) >= count:
                    break
            return filtered_tools

    async def get_by_random(self, base_count: int = 5, use_random_count: bool = False) -> List[GeneratedTool]:
        """Get tools randomly.
        
        Args:
            base_count: Number of tools to retrieve.
            use_random_count: Whether to use a random number.
            
        Returns:
            Randomly selected tools.
        """
        async with self._lock:
            tool_list = list(self.tools.values())
            if use_random_count:
                base_count = random.choice(range(1, base_count + 1))
            return random.choices(tool_list, k=base_count)

    async def get_by_capability(self, capability: str, count: int = 5) -> List[GeneratedTool]:
        """Get tools by capability.
        
        Args:
            capability: Tool capability.
            count: Number of tools to retrieve.
            
        Returns:
            List of tools with the specified capability.
        """
        async with self._lock:
            filtered_tools = []
            for tool in self.tools.values():
                if capability in tool.spec.capabilities:
                    filtered_tools.append(tool)

                if len(filtered_tools) >= count:
                    break
            return filtered_tools

    async def get_by_complexity(self,
                                count: int = 5,
                                min_score: float = 0.3,
                                max_score: float = 0.85) -> List[GeneratedTool]:
        """Get tools by complexity.
        
        Args:
            count: Number of tools to retrieve.
            min_score: Minimum complexity score.
            max_score: Maximum complexity score.
            
        Returns:
            List of tools with complexity scores in the specified range.
        """
        async with self._lock:
            filtered_tools = []
            for tool in self.tools.values():
                if min_score <= tool.complexity_score <= max_score:
                    filtered_tools.append(tool)

                if len(filtered_tools) >= count:
                    break

            return filtered_tools

    async def get_by_diversity(self,
                               count: int = 5,
                               min_score: float = 0.3,
                               max_score: float = 0.85) -> List[GeneratedTool]:
        """Get tools by diversity.
        
        Args:
            count: Number of tools to retrieve.
            min_score: Minimum diversity score.
            max_score: Maximum diversity score.
            
        Returns:
            List of tools with diversity scores in the specified range.
        """
        async with self._lock:
            filtered_tools = []
            for tool in self.tools.values():
                if min_score <= tool.diversity_score <= max_score:
                    filtered_tools.append(tool)

                if len(filtered_tools) >= count:
                    break

            return filtered_tools

    async def remove_tool(self, tool_id: str) -> bool:
        """ Remove a tool from the repository.
        
        Args:
            tool_id: ID of the tool to remove.
            
        Returns:
            Whether the removal was successful.
        """
        async with self._lock:
            if tool_id in self.tools:
                del self.tools[tool_id]
                if tool_id in self.access_count:
                    del self.access_count[tool_id]

                return True
            return False

    async def list_tools(self) -> List[GeneratedTool]:
        """List all tools in the repository.
        
        Returns:
            List of all tools.
        """
        async with self._lock:
            return list(self.tools.values())

    async def _evict_tool(self) -> bool:
        """Evict a tool when the buffer is full.
        
        Returns:
            Whether a tool was successfully evicted
        """
        if not self.tools:
            return False

        cate = max(self.distribution.items(), key=lambda x: x[1])[0]
        tool_list = await self.get_by_category(cate)
        # Randomly select one
        tool = random.choice(tool_list)
        await self.remove_tool(tool.spec.name)

        return True

    async def save_to_json(self, file_path: str) -> bool:
        try:
            serializable_tools = []
            for tid, tool in self.tools.items():
                tool_dict = {
                    'id': tool.id,
                    'spec': asdict(tool.spec),
                    'examples': tool.examples,
                    'complexity_score': tool.complexity_score,
                    'diversity_score': tool.diversity_score,
                    'active': tool.active,
                    'success_rate': tool.success_rate,
                    'timeout_rate': tool.timeout_rate,
                    'error_rate': tool.error_rate,
                    'metadata': tool.metadata
                }
                serializable_tools.append(tool_dict)

            with jsonlines.open(file_path, "w") as f:
                f.write_all(serializable_tools)
            logger.info(f"{len(self.tools)} tools saved to {file_path}")
            return True
        except Exception as e:
            logger.error(f"Tool persistence failed, error: {str(e)}")
            return False

    async def save_to_storage(self) -> bool:
        """Save tools to persistent storage.
        
        Returns:
            Whether the save operation was successful.
        """
        if not self.storage:
            return False

        try:
            serializable_tools = {}
            for tid, tool in self.tools.items():
                tool_dict = {
                    'id': tool.id,
                    'spec': asdict(tool.spec),
                    'examples': tool.examples,
                    'complexity_score': tool.complexity_score,
                    'diversity_score': tool.diversity_score,
                    'active': tool.active,
                    'success_rate': tool.success_rate,
                    'timeout_rate': tool.timeout_rate,
                    'error_rate': tool.error_rate,
                    'metadata': tool.metadata
                }
                serializable_tools[tid] = tool_dict

            data = Data(value=serializable_tools, block_id=self.name)
            await self.storage.create_data(data)

            logger.info(f"{len(self.tools)} tools saved to {self.storage.name()} in {self.name}/{data.id}")
            return True
        except Exception as e:
            logger.error(f"Tool persistence failed, error: {str(e)}")
            return False

    async def load_from_file(self, file_path: str) -> bool:
        """Load tools from special file.

        Returns:
            Whether the load operation was successful.
        """
        try:
            with open(file_path, "r+", encoding="utf8") as f:
                for data in jsonlines.Reader(f):
                    self.tools[data['id']] = GeneratedTool(
                        id=data['id'],
                        spec=Specification(**data['spec']),
                        examples=data['examples'],
                        complexity_score=data['complexity_score'],
                        diversity_score=data['diversity_score'],
                        active=data['active'],
                        success_rate=data['success_rate'],
                        timeout_rate=data['timeout_rate'],
                        error_rate=data['error_rate'],
                        metadata=data['metadata']
                    )
                    self.distribution[data['spec']['category']] = self.distribution.get(data['spec']['category'], 0) + 1

            logger.info(f"Loaded {len(self.tools)} tools from {self.storage.name()} {self.name}")
            return True
        except Exception as e:
            logger.error(f"Loading tools from {self.storage.name()} failed, error: {str(e)}")
            return False

    async def load_from_storage(self) -> bool:
        """Load tools from persistent storage.
        
        Returns:
            Whether the load operation was successful.
        """
        if not self.storage:
            return True
        try:
            results = await self.storage.get_data_items(self.name)
            for result in results:
                tools = result.value
                for data in tools.values():
                    self.tools[data['id']] = GeneratedTool(
                        id=data['id'],
                        spec=Specification(**data['spec']),
                        examples=data['examples'],
                        complexity_score=data['complexity_score'],
                        diversity_score=data['diversity_score'],
                        active=data['active'],
                        success_rate=data['success_rate'],
                        timeout_rate=data['timeout_rate'],
                        error_rate=data['error_rate'],
                        metadata=data['metadata']
                    )
                    self.distribution[data['spec']['category']] = self.distribution.get(data['spec']['category'], 0) + 1

            logger.info(f"Loaded {len(self.tools)} tools from {self.storage.name()} {self.name}")
            return True
        except Exception as e:
            logger.error(f"Loading tools from {self.storage.name()} failed, error: {str(e)}")
            return False

    async def clear(self):
        # Clear in-memory data
        async with self._lock:
            self.tools.clear()
            self.access_count.clear()
            self.stored_buffer.clear()
            self.distribution.clear()

    async def size(self) -> int:
        """Get the number of tools in the repository.
        
        Returns:
            Number of tools.
        """
        async with self._lock:
            return len(self.tools)

    async def stats(self) -> Dict[str, Any]:
        """Get repository statistics.
        
        Returns:
            Dictionary containing statistical information.
        """
        async with self._lock:
            complexity_counts = {}
            diversity_counts = {}

            for tool in self.tools.values():
                complexity = tool.spec.complexity
                complexity_counts[complexity] = complexity_counts.get(complexity, 0) + 1

                diversity = tool.spec.diversity
                diversity_counts[diversity] = diversity_counts.get(diversity, 0) + 1

            return {
                'total_count': len(self.tools),
                'category_dist': self.distribution,
                'complexity_dist': complexity_counts,
                'diversity_dist': diversity_counts,
                'sample_dist': self.access_count
            }
