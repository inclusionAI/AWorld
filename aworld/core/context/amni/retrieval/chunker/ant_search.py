import json
from typing import List

from aworld.logs.util import logger
from .base import ChunkerBase, Chunk, ChunkMetadata
from aworld.output import Artifact


class AntSearchChunker(ChunkerBase):
    """
    A chunker that uses Ant Search text splitting strategies to ensure uniform and semantic chunks.
    """
    def __init__(self):
        super().__init__()

    def _extract_valid_json_from_back(self, content: str) -> str:
        """
        Extract valid JSON content by merging segments from back to front until valid JSON is found.
        
        Args:
            content: Input string that may contain answerBox=xxx|peopleAlsoAsk=yyy|zzz format
            
        Returns:
            Valid JSON string or empty string if no valid JSON found
        """
        segments = content.split("|")
        logger.debug(f"🔍 Processing {len(segments)} segments from content")
        
        # Try from the last segment backwards, merging more segments until we get valid JSON
        for i in range(len(segments)):
            # Start from the last segment, then include more segments from the back
            end_index = len(segments)
            start_index = end_index - i - 1
            
            # Skip segments that start with answerBox= or peopleAlsoAsk=
            filtered_segments = []
            for j in range(start_index, end_index):
                segment = segments[j].strip()
                if not (segment.startswith("answerBox=") or segment.startswith("peopleAlsoAsk=")):
                    filtered_segments.append(segment)
            
            if not filtered_segments:
                continue
                
            candidate_json = "|".join(filtered_segments)
            logger.debug(f"🧪 Testing JSON candidate: {candidate_json[:100]}...")
            
            try:
                # Try to parse as JSON
                json.loads(candidate_json)
                logger.debug(f"✅ Found valid JSON after merging {len(filtered_segments)} segments")
                return candidate_json
            except (json.JSONDecodeError, ValueError):
                # Continue trying with more segments
                continue
        
        logger.warning("⚠️ No valid JSON found in content")
        return ""

    async def chunk(self, artifact: Artifact) -> List[Chunk]:
        """
        Chunk the artifact content using Ant Search strategy.
        
        Args:
            artifact: Input artifact containing search results
            
        Returns:
            List of chunks extracted from the artifact
        """
        if not artifact.content.__contains__("answerBox=") or not artifact.content.__contains__("peopleAlsoAsk="):
            logger.debug("🚫 Artifact content not contains answerBox or peopleAlsoAsk, use default chunking")
            return []
        
        # Extract valid JSON content from the artifact
        result_content = self._extract_valid_json_from_back(artifact.content)
        
        if not result_content:
            logger.warning("🚫 No valid content extracted from artifact")
            return []

        try:
            result = json.loads(result_content)
            logger.info(f"📦 AntSearchChunker successfully parsed JSON with {len(result) if isinstance(result, list) else 'non-list'} items")
            
            if isinstance(result, list):
                chunks = []
                for i, item in enumerate(result):
                    if isinstance(item, dict):
                        chunk = Chunk(
                            chunk_id=f"{artifact.artifact_id}_chunk_{i}",
                            content=f"ChunkTitle: {item.get('title', 'No Title')}\nChunkContent: {item.get('content', 'No Content')}",
                            chunk_metadata=ChunkMetadata(
                                chunk_index=i,
                                chunk_size=len(str(item.get('content', ''))),
                                chunk_overlap=0,
                                artifact_id=artifact.artifact_id,
                                artifact_type=artifact.artifact_type.value,
                            ),
                        )
                        chunks.append(chunk)
                        logger.debug(f"📄 Created chunk {i}: {item.get('title', 'No Title')[:50]}...")
                
                logger.info(f"🎯 Successfully created {len(chunks)} chunks")
                return chunks
            else:
                logger.warning("⚠️ JSON result is not a list, cannot create chunks")
                return []
                
        except Exception as err:
            logger.error(f"❌ AntSearchChunker chunk failed: {err}")
            return []