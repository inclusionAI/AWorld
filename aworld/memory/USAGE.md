# AWorld Memory System - Usage Guide

## Overview

The AWorld Memory System provides long-term memory capabilities for AI agents through automatic file monitoring, vector search, and seamless prompt injection.

## Quick Start

### 1. Basic Usage

```python
import asyncio
from aworld.memory import MemoryManager

async def main():
    # Create memory manager
    manager = MemoryManager(
        workspace_dir="/path/to/workspace",
        agent_id="my_agent"
    )
    
    # Start the manager
    await manager.start()
    
    # Search memory
    results = await manager.search("What did we discuss about the API?")
    for result in results:
        print(f"Score: {result['score']:.2f}")
        print(f"Source: {result['path']}:{result['start_line']}-{result['end_line']}")
        print(f"Text: {result['text'][:200]}...")
        print()
    
    # Stop the manager
    await manager.stop()

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Integration with Agent

```python
from aworld.memory import MemoryManager

class MyAgent:
    def __init__(self, workspace_dir):
        self.memory_manager = MemoryManager(
            workspace_dir=workspace_dir,
            agent_id=self.__class__.__name__
        )
    
    async def start(self):
        await self.memory_manager.start()
    
    async def stop(self):
        await self.memory_manager.stop()
    
    def build_system_prompt(self, base_prompt: str) -> str:
        # Get available tools
        available_tools = {"memory_search", "memory_get", "other_tool"}
        
        # Inject memory section
        return self.memory_manager.inject_prompt(base_prompt, available_tools)
    
    async def handle_query(self, query: str):
        # Search memory if needed
        if "remember" in query.lower() or "recall" in query.lower():
            results = await self.memory_manager.search(query)
            # Use results in response...
```

## Memory Files

### File Structure

```
workspace/
├── MEMORY.md          # Main memory file
├── memory.md          # Alternative main file
└── memory/            # Memory directory
    ├── project.md     # Project-specific memories
    ├── decisions.md   # Decision log
    └── preferences.md # User preferences
```

### Example MEMORY.md

```markdown
# Project Memory

## API Design Decisions

### 2024-03-20: REST vs GraphQL
We decided to use REST API for simplicity and better caching support.
Key stakeholders: Alice, Bob
Rationale: Team familiarity, existing infrastructure

## User Preferences

- Prefers concise responses
- Likes code examples
- Works in PST timezone

## Important Context

The project uses Python 3.11+ and requires async/await patterns.
```

## Configuration

### Default Configuration

The system uses sensible defaults:
- Chunk size: 400 tokens
- Chunk overlap: 80 tokens
- Watch debounce: 1500ms
- Hybrid search: enabled (70% vector, 30% text)
- Min score: 0.35
- Max results: 6

### Custom Configuration

```python
from aworld.memory import MemoryConfig, MemoryManager

config = MemoryConfig(
    enabled=True,
    sources=["memory"],
    provider="openai",
    model="text-embedding-3-small",
    chunking=MemoryChunkingConfig(
        tokens=500,
        overlap=100
    ),
    sync=MemorySyncConfig(
        watch=True,
        watch_debounce_ms=2000,
        on_session_start=True,
        on_search=True
    ),
    query=MemoryQueryConfig(
        max_results=10,
        min_score=0.4,
        hybrid=MemoryHybridConfig(
            enabled=True,
            vector_weight=0.8,
            text_weight=0.2
        )
    )
)

manager = MemoryManager(
    workspace_dir="/path/to/workspace",
    agent_id="my_agent",
    config=config
)
```

## Features

### 1. Automatic File Monitoring

The system automatically watches for changes in:
- `MEMORY.md` and `memory.md` in workspace root
- All `.md` files in `memory/` directory
- Additional paths specified in configuration

Changes are detected with a debounce mechanism (default 1.5s) to avoid excessive syncing.

### 2. Hybrid Search

Combines two search methods:
- **Vector Search**: Semantic similarity using embeddings
- **Full-Text Search**: Keyword matching using SQLite FTS5

Results are merged with configurable weights (default: 70% vector, 30% text).

### 3. Prompt Injection

Automatically injects memory instructions into agent system prompts:

```
## Memory Recall
Before answering anything about prior work, decisions, dates, people, preferences, or todos:
run memory_search on MEMORY.md + memory/*.md; then use memory_get to pull only the needed lines.
If low confidence after search, say you checked.

Citations: include Source: <path#line> when it helps the user verify memory snippets.
```

### 4. Memory Tools

Two tools are provided for agent use:

#### memory_search
Search memory files for relevant information.

```python
results = await memory_search_tool.search(
    query="What did we decide about the database?",
    max_results=6,
    min_score=0.35
)
```

#### memory_get
Retrieve specific content from a memory file.

```python
content = await memory_get_tool.get(
    path="memory/decisions.md",
    start_line=10,
    end_line=20
)
```

## Best Practices

### 1. Organize Memory Files

- Use `MEMORY.md` for general, frequently-accessed information
- Create topic-specific files in `memory/` directory
- Use clear headings and structure
- Include dates for time-sensitive information

### 2. Write Clear Memory Content

```markdown
# Good Example
## 2024-03-20: API Rate Limiting Decision
We implemented rate limiting at 100 requests/minute per user.
Reason: Prevent abuse and ensure fair usage.
Implementation: Redis-based token bucket algorithm.

# Bad Example
We did rate limiting stuff.
```

### 3. Use Semantic Queries

```python
# Good queries
"What rate limiting did we implement?"
"Why did we choose Redis?"
"What are the user's timezone preferences?"

# Less effective queries
"rate"
"redis"
"timezone"
```

### 4. Monitor Memory Usage

```python
# Check if sync is needed
if manager.sync_manager.dirty:
    await manager.sync_manager.sync(reason="manual")

# Get storage stats
# (Implementation depends on your needs)
```

## Troubleshooting

### Memory Search Returns No Results

1. Check if files exist and are being monitored
2. Verify OpenAI API key is set: `export OPENAI_API_KEY=your_key`
3. Check if initial sync completed
4. Try lowering `min_score` threshold

### File Changes Not Detected

1. Verify watcher is started: `manager.watcher.start()`
2. Check debounce timing (may need to wait 1.5s)
3. Ensure files are in monitored paths
4. Check logs for errors

### High Memory Usage

1. Reduce chunk size in configuration
2. Limit number of memory files
3. Enable embedding cache cleanup
4. Consider using smaller embedding model

## Advanced Usage

### Custom Embedding Provider

```python
class CustomEmbeddingProvider:
    def __init__(self):
        self.provider = "custom"
        self.model = "my-model"
    
    async def embed(self, text: str) -> List[float]:
        # Your custom embedding logic
        return [0.1, 0.2, ...]  # Return embedding vector

# Use custom provider
manager = MemoryManager(workspace_dir="...")
manager.embedding_provider = CustomEmbeddingProvider()
```

### Manual Sync Control

```python
# Disable automatic sync
config = MemoryConfig(
    sync=MemorySyncConfig(
        watch=False,
        on_session_start=False,
        on_search=False
    )
)

# Trigger sync manually
await manager.sync_manager.sync(reason="manual")
```

### Context Manager Usage

```python
async with MemoryManager(workspace_dir="...") as manager:
    results = await manager.search("query")
    # Manager automatically starts and stops
```

## API Reference

See individual module documentation for detailed API reference:
- `MemoryManager`: Main orchestrator
- `MemoryStorage`: SQLite storage backend
- `MemoryWatcher`: File system monitoring
- `MemorySyncManager`: Synchronization logic
- `MemoryPromptInjector`: Prompt modification
- `MemorySearchTool`: Search tool for agents
- `MemoryGetTool`: Content retrieval tool

## Performance Considerations

- **Embedding Generation**: Can be slow for large files (use caching)
- **Vector Search**: O(n) complexity (consider using specialized vector DB for large datasets)
- **File Watching**: Minimal overhead with debouncing
- **Database Size**: Grows with number of chunks (monitor and clean up old data)

## Security Considerations

- Memory files may contain sensitive information
- Secure your OpenAI API key
- Consider encrypting the SQLite database
- Implement access controls for memory files
- Be cautious with memory content in prompts (token limits)
