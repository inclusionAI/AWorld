import os
import time
from typing import Optional, List

from elasticsearch import Elasticsearch

from ..embeddings import SearchResults,SearchResult, EmbeddingsMetadata
from .base import RetrievalIndexPlugin
from aworld.logs.util import logger


class FullTextIndexPlugin(RetrievalIndexPlugin):

    """
    Full-text search index plugin.

    default use elasticsearch
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.index = config.get("elasticsearch_index") if config.get("elasticsearch_index") else os.getenv("ELASTICSEARCH_INDEX")
        elasticsearch_username = config.get("elasticsearch_username") if config.get("elasticsearch_username") else os.getenv("ELASTICSEARCH_USERNAME")
        elasticsearch_password = config.get("elasticsearch_password") if config.get("elasticsearch_password") else os.getenv("ELASTICSEARCH_PASSWORD")
        self.client = Elasticsearch(
            hosts=[config.get("elasticsearch_url") if config.get("elasticsearch_url") else os.getenv("ELASTICSEARCH_URL")],
            basic_auth=(
                (elasticsearch_username, elasticsearch_password)
                if elasticsearch_username and elasticsearch_password
                else None
            ),
            ssl_assert_fingerprint=config.get("elasticsearch_ssl_assert_fingerprint") if config.get("elasticsearch_ssl_assert_fingerprint") else os.getenv("ELASTICSEARCH_SSL_ASSERT_FINGERPRINT"),
        )
        if config.get("created_index", False):
            self.create_index()
            

    def _create_index(self):
        """Create Elasticsearch index with appropriate mappings for full-text search.
        
        This method creates an index with optimized mappings for full-text search,
        including text analysis and metadata storage capabilities.
        """
        body = {
            "mappings": {
                "dynamic_templates": [
                    {
                        "strings": {
                            "match_mapping_type": "string",
                            "mapping": {"type": "keyword"},
                        }
                    }
                ],
                "properties": {
                    "collection": {"type": "keyword"},
                    "id": {"type": "keyword"},
                    "text": {"type": "text"},
                    "metadata": {"type": "object"},
                },
            }
        }
        self.client.indices.create(index=self.index, body=body)


    def _has_index(self):
        """Check if the Elasticsearch index exists.
        
        Returns:
            bool: True if index exists, False otherwise
        """
        return self.client.indices.exists(
            index=self.index
        )

    def create_index(self):
        """Create the Elasticsearch index if it doesn't exist.
        
        This method ensures the index is available before performing operations.
        """
        if not self._has_index():
            self._create_index()

    async def build_index(self, collection: str, doc_id: str, content: str, meta: dict, **kwargs) -> None:
        """Build full-text search index by inserting document into Elasticsearch.
        
        This method indexes a document for full-text search capabilities, storing
        the content and metadata in Elasticsearch for later retrieval.
        
        Args:
            collection (str): Collection name to organize documents
            doc_id (str): Unique document identifier
            content (str): Document content for full-text search
            meta (dict): Document metadata for filtering and organization
            **kwargs: Additional keyword arguments
            
        Returns:
            None: Document is indexed successfully
        """
        try:
            # Ensure index exists before indexing
            self.create_index()
            
            # Prepare document for indexing
            doc = {
                "collection": collection,
                "id": doc_id,
                "text": content,
                "metadata": meta
            }
            
            # Index the document
            self.client.index(
                index=self.index,
                id=doc_id,
                body=doc
            )
            
            logger.info(f"✅ Successfully indexed document {doc_id} in collection {collection}")
            
        except Exception as e:
            logger.error(f"❌ Failed to index document {doc_id} in collection {collection}: {str(e)}")
            raise

    async def build_index_batch(self, collection: str, documents: List[dict], **kwargs) -> None:
        """Build full-text search index by batch inserting documents into Elasticsearch.
        
        This method indexes multiple documents for full-text search capabilities,
        storing the content and metadata in Elasticsearch for later retrieval.
        
        Args:
            collection (str): Collection name to organize documents
            documents (List[dict]): List of documents with format [{"id": str, "content": str, "meta": dict}, ...]
            **kwargs: Additional keyword arguments
            
        Returns:
            None: Documents are indexed successfully
        """
        try:
            if not documents:
                logger.warning("⚠️ No documents provided for batch indexing")
                return
            
            # Ensure index exists before indexing
            self.create_index()
            
            logger.info(f"🚀 [FULL_TEXT]Starting batch indexing of {len(documents)} documents to collection {collection}")
            start_time = time.time()
            # Process documents in batches to optimize Elasticsearch operations
            batch_size = kwargs.get("batch_size", 20)
            total_indexed = 0
            
            for i in range(0, len(documents), batch_size):
                batch = documents[i:i + batch_size]
                logger.debug(f"📦 Processing batch {i//batch_size + 1}/{(len(documents) + batch_size - 1)//batch_size}")
                
                # Prepare batch actions for bulk indexing
                actions = []
                for doc in batch:
                    try:
                        doc_id = doc.get("doc_id")
                        content = doc.get("content")
                        meta = doc.get("meta", {})
                        
                        if not doc_id or not content:
                            logger.warning(f"⚠️ Skipping document with missing id or content: {doc}")
                            continue
                        
                        # Prepare document for indexing
                        action = {
                            "_index": self.index,
                            "_id": doc_id,
                            "_source": {
                                "collection": collection,
                                "id": doc_id,
                                "text": content,
                                "metadata": meta
                            }
                        }
                        actions.append(action)
                        
                    except Exception as e:
                        logger.error(f"❌ Failed to prepare document {doc.get('id', 'unknown')}: {str(e)}")
                        continue
                
                if actions:
                    try:
                        # Use bulk API for efficient batch indexing
                        from elasticsearch.helpers import bulk
                        success, errors = bulk(self.client, actions, raise_on_error=False)
                        
                        if errors:
                            logger.warning(f"⚠️ Some documents failed to index in batch: {len(errors)} errors")
                            for error in errors[:5]:  # Log first 5 errors
                                logger.info(f"⚠️ Indexing error: {error}")
                        
                        total_indexed += success
                        logger.debug(f"✅ Successfully indexed batch with {success} documents")
                        
                    except Exception as e:
                        logger.error(f"❌ Failed to index batch: {str(e)}")
                        # Fallback to individual indexing for this batch
                        for action in actions:
                            try:
                                self.client.index(
                                    index=action["_index"],
                                    id=action["_id"],
                                    body=action["_source"]
                                )
                                total_indexed += 1
                            except Exception as individual_error:
                                logger.error(f"❌ Failed to index document {action['_id']}: {str(individual_error)}")
            
            logger.info(f"🎉 [FULL_TEXT] Batch indexing completed: {total_indexed} documents indexed to collection {collection}, use time {time.time() - start_time} seconds")
            
        except Exception as e:
            logger.error(f"❌ [FULL_TEXT] Failed to batch index documents to collection {collection}: {str(e)}")
            raise

    async def async_search(self, collection: str, query: str, search_filter: dict = None, top_k: int = None, **kwargs) -> Optional[SearchResults]:
        """Perform full-text search using Elasticsearch.
        
        This method searches for documents matching the query text within the specified
        collection, applying filters and limiting results as specified.
        
        Args:
            collection (str): Collection name to search within
            query (str): Search query text
            search_filter (dict, optional): Additional filter conditions. Defaults to None.
            top_k (int, optional): Maximum number of results to return. Defaults to None.
            **kwargs: Additional keyword arguments
            
        Returns:
            Optional[SearchResults]: Search results or None if no results found
        """
        try:
            if search_filter is None:
                search_filter = {}
            
            # Set default top_k if not specified
            if top_k is None:
                top_k = 20
            
            # Build search query with collection filter
            search_body = {
                "size": top_k,
                "_source": ["text", "metadata", "id"],
                "query": {
                    "bool": {
                        "must": [
                            {
                                "match": {
                                    "text": {
                                        "query": query,
                                        "operator": "or",
                                        "fuzziness": "AUTO"
                                    }
                                }
                            }
                        ],
                        "filter": [
                            {"term": {"collection": collection}}
                        ]
                    }
                },
                "highlight": {
                    "fields": {
                        "text": {
                            "pre_tags": ["<em>"],
                            "post_tags": ["</em>"],
                            "fragment_size": 150,
                            "number_of_fragments": 3
                        }
                    }
                }
            }
            
            # Add additional filters if provided
            if search_filter:
                for field, value in search_filter.items():
                    if field != "threshold":  # Skip threshold as it's not a document field
                        search_body["query"]["bool"]["filter"].append(
                            {"term": {f"metadata.{field}": value}}
                        )
            
            # Execute search
            result = self.client.search(
                index=self.index,
                body=search_body
            )
            
            # Process search results
            search_results = self._process_search_results(result, query)
            
            if search_results:
                logger.debug(f"🔍 Found {len(search_results.docs)} results for query: {query}")
            else:
                logger.debug(f"🔍 No results found for query: {query}")
            
            return search_results
            
        except Exception as e:
            logger.error(f"❌ Search failed for query '{query}' in collection {collection}: {str(e)}")
            return None

    def _process_search_results(self, result: dict, query: str) -> Optional[SearchResults]:
        """Process Elasticsearch search results into SearchResults format.
        
        Args:
            result (dict): Raw Elasticsearch search result
            query (str): Original search query for logging
            
        Returns:
            Optional[SearchResults]: Processed search results or None if no hits
        """
        if not result.get("hits", {}).get("hits"):
            return None
        
        hits = result["hits"]["hits"]
        docs = []
        
        for hit in hits:
            # Extract metadata and convert to EmbeddingsMetadata if possible
            metadata = hit["_source"].get("metadata", {})
            try:
                metadata_obj = EmbeddingsMetadata(**metadata)
            except Exception:
                # If metadata doesn't match EmbeddingsMetadata schema, use as-is
                metadata_obj = metadata
            
            # Create SearchResult with score normalization
            # Elasticsearch scores can be very high, so we normalize them
            score = hit.get("_score", 0)
            if score > 0:
                # Normalize score to 0-1 range for consistency with other search methods
                normalized_score = min(score / 10.0, 1.0)
            else:
                normalized_score = 0.0
            
            doc = SearchResult(
                id=hit["_id"],
                score=normalized_score,
                content=hit["_source"].get("text", ""),
                metadata=metadata_obj
            )
            docs.append(doc)
        
        return SearchResults(
            docs=docs,
            search_at=int(time.time())
        )

    async def delete_document(self, collection: str, doc_id: str) -> bool:
        """Delete a document from the full-text search index.
        
        Args:
            collection (str): Collection name where the document is stored
            doc_id (str): Document ID to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            # Check if document exists and belongs to the collection
            doc = self.client.get(index=self.index, id=doc_id)
            if doc["_source"].get("collection") != collection:
                logger.warning(f"⚠️ Document {doc_id} does not belong to collection {collection}")
                return False
            
            # Delete the document
            self.client.delete(index=self.index, id=doc_id)
            logger.debug(f"✅ Successfully deleted document {doc_id} from collection {collection}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to delete document {doc_id} from collection {collection}: {str(e)}")
            return False

    async def delete_collection(self, collection: str) -> bool:
        """Delete all documents from a specific collection.
        
        Args:
            collection (str): Collection name to delete
            
        Returns:
            bool: True if deletion was successful, False otherwise
        """
        try:
            # Delete all documents in the collection
            query = {
                "query": {
                    "term": {"collection": collection}
                }
            }
            
            result = self.client.delete_by_query(index=self.index, body=query)
            deleted_count = result.get("deleted", 0)
            
            logger.info(f"🗑️ Deleted {deleted_count} documents from collection {collection}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Failed to delete collection {collection}: {str(e)}")
            return False

    def get_collection_stats(self, collection: str) -> Optional[dict]:
        """Get statistics about a collection.
        
        Args:
            collection (str): Collection name to get stats for
            
        Returns:
            Optional[dict]: Collection statistics or None if error occurs
        """
        try:
            query = {
                "query": {
                    "term": {"collection": collection}
                },
                "size": 0,
                "aggs": {
                    "total_docs": {"value_count": {"field": "_id"}},
                    "avg_content_length": {"avg": {"field": "text.length"}}
                }
            }
            
            result = self.client.search(index=self.index, body=query)
            
            stats = {
                "collection": collection,
                "total_documents": result["aggregations"]["total_docs"]["value"],
                "average_content_length": result["aggregations"]["avg_content_length"]["value"],
                "index_name": self.index
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"❌ Failed to get stats for collection {collection}: {str(e)}")
            return None


