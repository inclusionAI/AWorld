import asyncio
import os
from typing import Optional, List

from aworld.logs.util import logger
from ..embeddings import EmbeddingFactory, EmbeddingsMetadata, EmbeddingsResult, SearchResults, \
    Embeddings, AmniEmbeddingsConfig
from ..vector import VectorDB, VectorDBConfig, VectorDBFactory
from .base import RetrievalIndexPlugin


class SemanticIndexPlugin(RetrievalIndexPlugin):
    embedder: Optional[Embeddings]
    vector_db = Optional[VectorDB]

    def __init__(self, config: dict):
        super().__init__(config)

        # Initialize embedder
        self.embedder = EmbeddingFactory.get_embedder(embedding_config=AmniEmbeddingsConfig(
                provider=os.getenv("EMBEDDING_PROVIDER", "openai"),
                base_url=os.getenv('EMBEDDING_BASE_URL'),
                api_key=os.getenv('EMBEDDING_API_KEY'),
                model_name=os.getenv('EMBEDDING_MODEL_NAME'),
                dimensions=int(os.getenv('EMBEDDING_MODEL_DIMENSIONS', '1024'))
            ) if not config.get("embedding_config") else config.get("embedding_config"))
        if self.embedder is None:
            raise RuntimeError("Failed to initialize embedder")
        logger.debug(f"✅ Embedder initialized: {type(self.embedder).__name__}")

        # Initialize vector database
        self.vector_db = VectorDBFactory.get_vector_db(vector_db_config=VectorDBConfig(
            provider=os.getenv("VECTOR_STORE_PROVIDER", "chroma"),
            config={
                "chroma_data_path": os.getenv("CHROMA_PATH", "./data/chroma_db"),
                "elasticsearch_url": os.getenv("ELASTICSEARCH_URL"),
                "elasticsearch_index_prefix": os.getenv("ELASTICSEARCH_INDEX_PREFIX"),
                "elasticsearch_username": os.getenv("ELASTICSEARCH_USERNAME"),
                "elasticsearch_password": os.getenv("ELASTICSEARCH_PASSWORD"),
                "ssl_assert_fingerprint": os.getenv("ELASTICSEARCH__SSL_ASSERT_FINGERPRINT"),
            }
        ) if not config.get("vector_db_config") else config.get("vector_db_config"))
        if self.vector_db is None:
            raise RuntimeError("Failed to initialize vector database")
        logger.debug(f"✅ Vector DB initialized: {type(self.vector_db).__name__}")

    async def build_index(self, collection: str, doc_id: str, content: str, metadata: dict, **kwargs) -> None:
        """Build index for a single document.
        
        Args:
            collection (str): Collection name to organize documents
            doc_id (str): Document identifier
            content (str): Document content to index
            metadata (dict): Document metadata
            **kwargs: Additional keyword arguments
        """
        return await self._add_content_to_vector(collection, doc_id, content, metadata, **kwargs)

    async def build_index_batch(self, collection: str, documents: List[dict], **kwargs) -> None:
        """Batch insert multiple documents into the vector database.
        
        Args:
            collection (str): Collection name to organize documents
            documents (List[dict]): List of documents with format [{"doc_id": str, "content": str, "meta": dict}, ...]
            **kwargs: Additional keyword arguments
        """
        return await self._add_content_batch_to_vector(collection, documents, **kwargs)

    async def _create_embedding_result(self, doc_id: str, content: str, metadata: dict) -> EmbeddingsResult:
        """Create an EmbeddingsResult object for the given document.
        
        Args:
            doc_id (str): Document identifier
            content (str): Document content
            metadata (dict): Document metadata
            
        Returns:
            EmbeddingsResult: Created embedding result object
            
        Raises:
            RuntimeError: If embedder is not initialized
        """
        if self.embedder is None:
            logger.error("❌ Embedder is not initialized")
            raise RuntimeError("Embedder is not initialized")

        logger.debug(f"🔍 Generating embedding for content: {content}")
        embedding = await self.embedder.async_embed_query(content)

        # Create metadata with embedding model info
        doc_metadata = EmbeddingsMetadata(
            embedding_model=self.embedder.config.model_name,
            **metadata
        )

        return EmbeddingsResult(
            id=doc_id,
            embedding=embedding,
            content=content,
            metadata=doc_metadata
        )

    async def _process_document_concurrently(self, doc: dict) -> Optional[EmbeddingsResult]:
        """Process a single document concurrently.
        
        Args:
            doc (dict): Document to process
            
        Returns:
            Optional[EmbeddingsResult]: Processed result or None if failed
        """
        try:
            # Extract document information
            doc_id = doc.get("doc_id")
            content = doc.get("content")
            metadata = doc.get("meta", {})

            # Validate document data
            if not doc_id or not content:
                logger.warning(f"⚠️ Skipping document with missing id or content: {doc}")
                return None

            # Create embedding result using common method
            result = await self._create_embedding_result(doc_id, content, metadata)
            return result

        except Exception as e:
            error_msg = f"Failed to process document {doc.get('doc_id', 'unknown')}: {str(e)}"
            logger.error(f"❌ {error_msg}")
            return None

    def _validate_components(self) -> None:
        """Validate that required components are properly initialized.
        
        Raises:
            RuntimeError: If embedder or vector_db is not initialized
        """
        if self.embedder is None:
            logger.error("❌ Embedder is not initialized")
            raise RuntimeError("Embedder is not initialized")

        if self.vector_db is None:
            logger.error("❌ Vector database is not initialized")
            raise RuntimeError("Vector database is not initialized")

    async def _add_content_batch_to_vector(self, collection: str, documents: List[dict], **kwargs) -> None:
        """Add multiple documents to the vector database in batch.
        
        Args:
            collection (str): Collection name to organize documents
            documents (List[dict]): List of documents to process
            **kwargs: Additional keyword arguments
        """
        try:
            # Validate components
            self._validate_components()

            if not documents:
                logger.warning("⚠️ No documents provided for batch indexing")
                return

            logger.info(f"🚀 [SEMANTIC]Starting batch indexing of {len(documents)} documents to collection {collection}")

            # Process documents in batches to avoid memory issues
            batch_size = kwargs.get("batch_size", 50)
            max_concurrency = kwargs.get("max_concurrency", 10)  # Limit concurrent embedding requests
            total_batches = (len(documents) + batch_size - 1) // batch_size
            results = []
            failed_docs = []

            for batch_idx in range(0, len(documents), batch_size):
                batch = documents[batch_idx:batch_idx + batch_size]
                current_batch_num = batch_idx // batch_size + 1

                logger.debug(f"📦 Processing batch {current_batch_num}/{total_batches} ({len(batch)} documents)")

                # Process documents concurrently within the batch
                semaphore = asyncio.Semaphore(max_concurrency)

                async def process_with_semaphore(doc: dict) -> Optional[EmbeddingsResult]:
                    async with semaphore:
                        return await self._process_document_concurrently(doc)

                # Create concurrent tasks for the batch
                tasks = [process_with_semaphore(doc) for doc in batch]
                batch_results_raw = await asyncio.gather(*tasks, return_exceptions=True)

                # Process results and handle exceptions
                batch_results = []
                for i, result in enumerate(batch_results_raw):
                    doc = batch[i]
                    if isinstance(result, Exception):
                        error_msg = f"Exception during processing: {str(result)}"
                        logger.error(f"❌ {error_msg}")
                        failed_docs.append({"doc": doc, "reason": error_msg})
                    elif result is None:
                        # Document was skipped due to validation
                        failed_docs.append({"doc": doc, "reason": "Validation failed or processing error"})
                    else:
                        batch_results.append(result)

                # Process successful batch results
                if batch_results:
                    try:
                        # Upsert batch to vector database
                        self.vector_db.upsert(collection_name=collection, items=batch_results)
                        results.extend(batch_results)
                        logger.debug(f"✅ Successfully processed batch {current_batch_num} with {len(batch_results)} documents")
                    except Exception as e:
                        logger.error(f"❌ Failed to upsert batch {current_batch_num} to vector DB: {str(e)}")
                        # Add failed documents to tracking
                        for result in batch_results:
                            failed_docs.append({"doc": {"doc_id": result.id, "content": result.content}, "reason": f"Upsert failed: {str(e)}"})

            # Log final results
            success_count = len(results)
            failed_count = len(failed_docs)
            total_count = len(documents)

            logger.debug(f"🎉 [SEMANTIC]Batch indexing completed:")
            logger.debug(f"   📊 Total documents: {total_count}")
            logger.debug(f"   ✅ Successfully indexed: {success_count}")
            logger.debug(f"   ❌ Failed: {failed_count}")
            logger.debug(f"   📍 Collection: {collection}")
            logger.debug(f"   ⚡ Concurrency level: {max_concurrency}")

            # Log failed documents for debugging if any
            if failed_docs:
                logger.warning(f"⚠️ Failed documents summary:")
                for failed in failed_docs[:10]:  # Only show first 10 failures
                    logger.warning(f"   - {failed['doc'].get('doc_id', 'unknown')}: {failed['reason']}")
                if len(failed_docs) > 10:
                    logger.warning(f"   ... and {len(failed_docs) - 10} more failures")

        except Exception as e:
            logger.error(f"❌ Failed to batch index documents to collection {collection}: {str(e)}")
            raise

    async def _add_content_to_vector(self, collection: str, doc_id: str, content: str, metadata: dict, **kwargs) -> None:
        """Add content to the vector database.

        Args:
            collection (str): Collection name to organize documents
            doc_id (str): Document identifier
            content (str): Content to be added
            metadata (dict): Metadata of the content
            **kwargs: Additional keyword arguments
        """
        try:
            # Validate components
            self._validate_components()

            # Create embedding result using common method
            result = await self._create_embedding_result(doc_id, content, metadata)

            self.vector_db.upsert(collection_name=collection, items=[result])
            logger.debug(f"💾 Content {doc_id} successfully upserted to vector DB collection {collection}")

        except Exception as e:
            logger.error(f"❌ Failed to add content {doc_id} to vector DB: {str(e)}")
            raise

    async def async_search(self, collection: str, query: str, search_filter: dict = None, top_k: int = 50, **kwargs) -> Optional[SearchResults]:
        """Search for similar documents in the vector database.
        
        Args:
            collection (str): Collection name to search in
            query (str): Search query text
            search_filter (dict, optional): Search filters. Defaults to None.
            top_k (int, optional): Number of top results to return. Defaults to 50.
            **kwargs: Additional keyword arguments
            
        Returns:
            Optional[SearchResults]: Search results or None if search fails
        """
        if search_filter is None:
            search_filter = {}

        threshold = search_filter.get("threshold", 0.7)
        top_k = search_filter.get("top_k", 20)
        rewritten_query = (
            f"Given a task, retrieve relevant passages that to solve the task\n"
            f"Current Task: {query}"
        )
        logger.info(f"retrival origin_task_input -> {query};\n rewrite_task_input is -> {rewritten_query}")
        query_embedding = await self.embedder.async_embed_query(rewritten_query)
        return self.vector_db.search(collection, [query_embedding], search_filter, threshold=threshold, limit=top_k)
