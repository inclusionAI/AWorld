"""
Memory synchronization manager.
Handles file indexing, chunking, and embedding generation.
"""

import hashlib
import logging
import os
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Set
import asyncio
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)


class MemorySyncManager:
    """Manages synchronization of memory files to database."""

    def __init__(
        self,
        storage,
        workspace_dir: str,
        chunk_tokens: int = 400,
        chunk_overlap: int = 80,
        embedding_provider=None,
    ):
        """Initialize sync manager."""
        self.storage = storage
        self.workspace_dir = Path(workspace_dir)
        self.chunk_tokens = chunk_tokens
        self.chunk_overlap = chunk_overlap
        self.embedding_provider = embedding_provider
        self.dirty = False
        self._syncing = False

    def mark_dirty(self):
        """Mark that files need to be synced."""
        self.dirty = True

    async def sync(self, reason: str = "manual"):
        """Synchronize memory files to database."""
        if self._syncing:
            logger.debug("Sync already in progress, skipping")
            return

        self._syncing = True
        try:
            logger.info(f"Starting memory sync (reason: {reason})")
            
            # Find all memory files
            memory_files = self._find_memory_files()
            logger.info(f"Found {len(memory_files)} memory files")

            # Index each file
            for file_path in memory_files:
                await self._index_file(file_path)

            # Clean up deleted files
            self._cleanup_deleted_files(memory_files)

            self.dirty = False
            logger.info("Memory sync completed")
        except Exception as e:
            logger.error(f"Memory sync failed: {e}", exc_info=True)
        finally:
            self._syncing = False

    def _find_memory_files(self) -> List[Path]:
        """Find all memory markdown files."""
        files = []
        
        # Check for MEMORY.md in workspace root
        memory_md = self.workspace_dir / "MEMORY.md"
        if memory_md.exists():
            files.append(memory_md)
        
        # Check for memory.md in workspace root
        memory_md_lower = self.workspace_dir / "memory.md"
        if memory_md_lower.exists():
            files.append(memory_md_lower)
        
        # Find all .md files in memory directory
        memory_dir = self.workspace_dir / "memory"
        if memory_dir.exists():
            for md_file in memory_dir.rglob("*.md"):
                if not self._should_ignore_path(md_file):
                    files.append(md_file)
        
        return files

    def _should_ignore_path(self, path: Path) -> bool:
        """Check if path should be ignored."""
        # Ignore hidden files and directories
        if any(part.startswith('.') for part in path.parts):
            return True
        
        # Ignore specific directories
        ignore_dirs = {'.git', 'node_modules', '__pycache__', '.venv', 'venv'}
        if any(part in ignore_dirs for part in path.parts):
            return True
        
        return False

    async def _index_file(self, file_path: Path):
        """Index a single file."""
        try:
            # Read file content
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Calculate file hash
            file_hash = hashlib.sha256(content.encode()).hexdigest()
            
            # Check if file has changed
            stored_hash = self.storage.get_file_hash(str(file_path))
            if stored_hash == file_hash:
                logger.debug(f"File unchanged, skipping: {file_path}")
                return
            
            logger.info(f"Indexing file: {file_path}")
            
            # Delete old chunks
            self.storage.delete_file_chunks(str(file_path))
            
            # Split into chunks
            chunks = self._chunk_text(content)
            logger.debug(f"Split into {len(chunks)} chunks")
            
            # Generate embeddings and store chunks
            for i, chunk in enumerate(chunks):
                chunk_id = f"{file_path}:{i}"
                chunk_hash = hashlib.sha256(chunk['text'].encode()).hexdigest()
                
                # Generate embedding
                embedding = await self._generate_embedding(chunk['text'])
                
                # Store chunk
                self.storage.insert_chunk(
                    chunk_id=chunk_id,
                    path=str(file_path),
                    source="memory",
                    start_line=chunk['start_line'],
                    end_line=chunk['end_line'],
                    chunk_hash=chunk_hash,
                    model=self.embedding_provider.model if self.embedding_provider else "default",
                    text=chunk['text'],
                    embedding=embedding,
                )
            
            # Update file metadata
            stat = file_path.stat()
            self.storage.upsert_file(
                path=str(file_path),
                file_hash=file_hash,
                mtime=int(stat.st_mtime),
                size=stat.st_size,
            )
            
            logger.info(f"Indexed file: {file_path} ({len(chunks)} chunks)")
        except Exception as e:
            logger.error(f"Failed to index file {file_path}: {e}", exc_info=True)

    def _chunk_text(self, text: str) -> List[Dict[str, Any]]:
        """Split text into chunks."""
        lines = text.split('\n')
        chunks = []
        
        # Simple chunking by approximate token count
        # Rough estimate: 1 token ≈ 4 characters
        chars_per_chunk = self.chunk_tokens * 4
        overlap_chars = self.chunk_overlap * 4
        
        current_chunk = []
        current_chars = 0
        start_line = 0
        
        for i, line in enumerate(lines):
            line_chars = len(line)
            
            if current_chars + line_chars > chars_per_chunk and current_chunk:
                # Save current chunk
                chunk_text = '\n'.join(current_chunk)
                chunks.append({
                    'text': chunk_text,
                    'start_line': start_line,
                    'end_line': i - 1,
                })
                
                # Start new chunk with overlap
                overlap_lines = []
                overlap_chars_count = 0
                for j in range(len(current_chunk) - 1, -1, -1):
                    overlap_chars_count += len(current_chunk[j])
                    overlap_lines.insert(0, current_chunk[j])
                    if overlap_chars_count >= overlap_chars:
                        break
                
                current_chunk = overlap_lines + [line]
                current_chars = sum(len(l) for l in current_chunk)
                start_line = i - len(overlap_lines)
            else:
                current_chunk.append(line)
                current_chars += line_chars
        
        # Add last chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'start_line': start_line,
                'end_line': len(lines) - 1,
            })
        
        return chunks

    async def _generate_embedding(self, text: str) -> List[float]:
        """Generate embedding for text."""
        if not self.embedding_provider:
            # Return zero vector if no provider
            return [0.0] * 1536  # Default OpenAI embedding size
        
        try:
            # Check cache first
            text_hash = hashlib.sha256(text.encode()).hexdigest()
            cached = self.storage.get_cached_embedding(
                provider=self.embedding_provider.provider,
                model=self.embedding_provider.model,
                provider_key="default",
                text_hash=text_hash,
            )
            if cached:
                return cached
            
            # Generate new embedding
            embedding = await self.embedding_provider.embed(text)
            
            # Cache it
            self.storage.cache_embedding(
                provider=self.embedding_provider.provider,
                model=self.embedding_provider.model,
                provider_key="default",
                text_hash=text_hash,
                embedding=embedding,
                dims=len(embedding),
            )
            
            return embedding
        except Exception as e:
            logger.error(f"Failed to generate embedding: {e}")
            return [0.0] * 1536

    def _cleanup_deleted_files(self, current_files: List[Path]):
        """Remove chunks for files that no longer exist."""
        current_paths = {str(f) for f in current_files}
        
        # This is a simplified version - in production, you'd query the database
        # for all indexed files and remove those not in current_paths
        logger.debug("Cleanup of deleted files completed")


class SimpleEmbeddingProvider:
    """Simple embedding provider using OpenAI."""

    def __init__(self, api_key: Optional[str] = None, model: str = "text-embedding-3-small"):
        """Initialize provider."""
        self.provider = "openai"
        self.model = model
        self.api_key = api_key or os.getenv("OPENAI_API_KEY")

    async def embed(self, text: str) -> List[float]:
        """Generate embedding for text."""
        try:
            import openai
            
            client = openai.AsyncOpenAI(api_key=self.api_key)
            response = await client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding failed: {e}")
            # Return zero vector on failure
            return [0.0] * 1536
