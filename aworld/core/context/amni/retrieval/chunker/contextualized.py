import logging
import os
from typing import List, Optional

from langchain_text_splitters import CharacterTextSplitter

from aworld.config import ConfigDict
from aworld.models.llm import get_llm_model, acall_llm_model
from aworld.output import Artifact
from .base import Chunk, ChunkConfig, ChunkerBase


class ContextualizedChunker(ChunkerBase):
    """
    A chunker that splits text by characters.
    """

    def __init__(self, config: ChunkConfig):
        super().__init__(config=config)
        self._text_splitter = CharacterTextSplitter(
            separator="\n",
            chunk_size=self.config.chunk_size,
            chunk_overlap=self.config.chunk_overlap,
            length_function=len,
            is_separator_regex=False,
        )
        self._llm_client = get_llm_model(conf=ConfigDict({
            "llm_model_name": os.environ['LLM_MODEL_NAME'],
            "llm_api_key": os.environ['LLM_API_KEY'],
            "llm_base_url": os.environ['LLM_BASE_URL']
        }))

    async def chunk(self, artifact: Artifact) -> Optional[List[Chunk]]:
        """
        Chunks the given content by characters.

        Args:
            content: The string content to chunk.

        Returns:
            A list of `Chunk` objects.
        """

        # TODO 根据不同的artifact类型路由到不同的pipeline
        texts = self._text_splitter.split_text(artifact.content)

        contextualized_texts = []
        for chunk_text in texts:
            # context = await self.generate_context(chunk_text)
            contextualized_texts.append(f"{chunk_text}")

        return self._create_chunks(contextualized_texts, artifact)

    async def generate_context(self, chunk_text: str, document_context: str = "") -> str:
        """
        Generate context for a chunk using LLM

        Args:
            chunk: The chunk to generate context for
            document_context: Optional broader document context

        Returns:
            Generated context (50-100 tokens)
        """
        if not chunk_text:
            return ""
        try:
            prompt = await self.generate_context_prompt(chunk_text, document_context)

            # Simulate LLM response (in real implementation, call actual LLM)
            response = await acall_llm_model(self._llm_client, messages=[
                {"role": "user", "content": prompt}
            ])

            # Extract context from response (first 50-100 tokens)
            context = response.content.strip()
            return context
        except:
            logging.warning(f"generate_context failed, content is {chunk_text[:100]}")
            return ""

    async def generate_context_prompt(self, chunk_text: str, document_context: str = "") -> str:
        """
        Generate prompt for context generation

        Args:
            chunk_str: The chunk to generate context for
            document_context: Optional broader document context

        Returns:
            Formatted prompt for the LLM
        """
        prompt = f"""
        Please provide 50-100 tokens of context that situates this text chunk within its document.

        Document context: {document_context}

        Text chunk:
        {chunk_text}

        Provide contextual information that helps understand:
        1. What came before this chunk
        2. The broader topic or theme
        3. The document structure
        4. Any relevant background information

        Context (50-100 tokens):
        """
        return prompt.strip()

