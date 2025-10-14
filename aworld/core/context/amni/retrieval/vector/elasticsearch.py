"""
Elasticsearch vector database implementation for amnicontext.

This implementation is based on the open-webui project's Elasticsearch vector database code.
Special thanks to the open-webui contributors for their excellent work.

Reference: https://github.com/open-webui/open-webui/blob/main/backend/open_webui/retrieval/vector/dbs/elasticsearch.py
"""
import time
from elasticsearch import Elasticsearch
from typing import Optional, Dict, Any
from elasticsearch.helpers import bulk, scan


from ..embeddings import EmbeddingsResults, EmbeddingsResult, EmbeddingsMetadata, SearchResult,SearchResults
from .base import VectorDB


class ElasticsearchVectorDB(VectorDB):
    """
    Important:
    in order to reduce the number of indexes and since the embedding vector length is fixed, we avoid creating
    an index for each file but store it as a text field, while seperating to different index
    baesd on the embedding length.
    """

    def __init__(self, config: Dict[str, Any]):
        self.index_prefix = config.get("elasticsearch_index_prefix")
        self.client = Elasticsearch(
            hosts=[config.get("elasticsearch_url")],
            basic_auth=(
                (config.get("elasticsearch_username"), config.get("elasticsearch_password"))
                if config.get("elasticsearch_username") and config.get("elasticsearch_password")
                else None
            ),
            ssl_assert_fingerprint=config.get("ssl_assert_fingerprint"),
        )


    def _get_index_name(self, dimension: int) -> str:
        return f"{self.index_prefix}_d{str(dimension)}"


    def _scan_result_to_get_result(self, hits) -> Optional[list[EmbeddingsResult]]:
        if not hits:
            return None
        docs = []

        for hit in hits:
            docs.append(
                EmbeddingsResult(
                    id=hit["_id"],
                    content=hit["_source"].get("text"),
                    metadata=EmbeddingsMetadata(**hit["_source"].get("metadata"))
                )
            )
        return docs



    def _result_to_get_result(self, result) -> Optional[EmbeddingsResults]:
        if not result["hits"]["hits"]:
            return None
        docs = self._scan_result_to_get_result(result["hits"]["hits"])

        return EmbeddingsResults(
            **{
                "docs": docs,
                "retrieved_at": int(time.time()),
            }
        )


    def _result_to_search_result(self, result) -> Optional[SearchResults]:
        if not result.body["hits"]["hits"]:
            return None
        hits = result.body["hits"]["hits"]
        docs = []
        for hit in hits:
            docs.append(
                SearchResult(
                    id=hit["_id"],
                    score=hit.get("_score") / 2,
                    content=hit["_source"].get("text"),
                    metadata=EmbeddingsMetadata(**hit["_source"].get("metadata"))
                )
            )
        return SearchResults(
            docs=docs,
            search_at=int(time.time())
        )


    def _create_index(self, dimension: int):
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
                    "vector": {
                        "type": "dense_vector",
                        "dims": dimension,  # Adjust based on your vector dimensions
                        "index": True,
                        "similarity": "cosine",
                    },
                    "text": {"type": "text"},
                    "metadata": {"type": "object"},
                },
            }
        }
        self.client.indices.create(index=self._get_index_name(dimension), body=body)



    def _create_batches(self, items: list[EmbeddingsResult], batch_size=100):
        for i in range(0, len(items), batch_size):
            yield items[i : min(i + batch_size, len(items))]


    def has_collection(self, collection_name) -> bool:
        query_body = {"query": {"bool": {"filter": []}}}
        query_body["query"]["bool"]["filter"].append(
            {"term": {"collection": collection_name}}
        )

        try:
            result = self.client.count(index=f"{self.index_prefix}*", body=query_body)

            return result.body["count"] > 0
        except Exception as e:
            return None

    def delete_collection(self, collection_name: str):
        query = {"query": {"term": {"collection": collection_name}}}
        self.client.delete_by_query(index=f"{self.index_prefix}*", body=query)


    def search(
            self, collection_name: str, vectors: list[list[float | int]], filter: dict, threshold: float, limit: int
    ) -> Optional[SearchResults]:
        """Search for nearest neighbors based on vector similarity.
        
        Args:
            collection_name (str): Name of the collection
            vectors (list[list[float | int]]): Query vectors
            filter (dict): Filter conditions
            threshold (float): Similarity threshold
            limit (int): Maximum number of results to return
            
        Returns:
            Optional[SearchResults]: Search results or None if collection doesn't exist
        """
        # Build filter conditions
        filter_conditions = [{"term": {"collection": collection_name}}]
        
        # Add additional filter conditions if provided
        if filter:
            for field, value in filter.items():
                filter_conditions.append({"term": {f"metadata.{field}": value}})

        query = {
            "size": limit,
            "_source": ["text", "metadata"],
            "query": {
                "script_score": {
                    "query": {
                        "bool": {"filter": filter_conditions}
                    },
                    "script": {
                        "source": "cosineSimilarity(params.vector, 'vector') + 1",
                        "params": {
                            "vector": vectors[0]
                        },  # Assuming single query vector
                    },
                }
            },
        }

        result = self.client.search(
            index=self._get_index_name(len(vectors[0])), body=query
        )

        # Apply threshold filtering to results
        search_results = self._result_to_search_result(result)
        if search_results and threshold:
            # Filter results based on threshold
            filtered_docs = []
            for doc in search_results.docs:
                if doc.score and doc.score >= threshold:
                    filtered_docs.append(doc)
            
            # Update search results with filtered docs
            search_results.docs = filtered_docs

        return search_results

    def query(
        self, collection_name: str, filter: dict, limit: Optional[int] = None
    ) -> Optional[EmbeddingsResults]:
        if not self.has_collection(collection_name):
            return None

        query_body = {
            "query": {"bool": {"filter": []}},
            "_source": ["text", "metadata"],
        }

        for field, value in filter.items():
            query_body["query"]["bool"]["filter"].append({"term": {f"metadata.{field}": value}})
        query_body["query"]["bool"]["filter"].append(
            {"term": {"collection": collection_name}}
        )
        size = limit if limit else 10

        try:
            result = self.client.search(
                index=f"{self.index_prefix}*",
                body=query_body,
                size=size,
            )

            return self._result_to_get_result(result)

        except Exception as e:
            return None


    def _has_index(self, dimension: int):
        return self.client.indices.exists(
            index=self._get_index_name(dimension=dimension)
        )

    def get_or_create_index(self, dimension: int):
        if not self._has_index(dimension=dimension):
            self._create_index(dimension=dimension)


    def get(self, collection_name: str) -> Optional[EmbeddingsResults]:
        # Get all the items in the collection.
        query = {
            "query": {"bool": {"filter": [{"term": {"collection": collection_name}}]}},
            "_source": ["text", "metadata"],
        }
        results = list(scan(self.client, index=f"{self.index_prefix}*", query=query))

        return self._scan_result_to_get_result(results)


    def insert(self, collection_name: str, items: list[EmbeddingsResult]):
        if not self._has_index(dimension=len(items[0].embedding)):
            self._create_index(dimension=len(items[0].embedding))

        for batch in self._create_batches(items):
            actions = [
                {
                    "_index": self._get_index_name(dimension=len(items[0].embedding)),
                    "_id": item.id,
                    "_source": {
                        "collection": collection_name,
                        "vector": item.embedding,
                        "text": item.content,
                        "metadata": item.metadata.model_dump(),
                    },
                }
                for item in batch
            ]
            bulk(self.client, actions)

    # Upsert documents using the update API with doc_as_upsert=True.
    def upsert(self, collection_name: str, items: list[EmbeddingsResult]):
        if not self._has_index(dimension=len(items[0].embedding)):
            self._create_index(dimension=len(items[0].embedding))
        for batch in self._create_batches(items):
            actions = [
                {
                    "_op_type": "update",
                    "_index": self._get_index_name(dimension=len(item.embedding)),
                    "_id": item.id,
                    "doc": {
                        "collection": collection_name,
                        "vector": item.embedding,
                        "text": item.content,
                        "metadata": item.metadata.model_dump(),
                    },
                    "doc_as_upsert": True,
                }
                for item in batch
            ]
            bulk(self.client, actions)

    # Delete specific documents from a collection by filtering on both collection and document IDs.
    def delete(
        self,
        collection_name: str,
        ids: Optional[list[str]] = None,
        filter: Optional[dict] = None,
    ):

        query = {
            "query": {"bool": {"filter": [{"term": {"collection": collection_name}}]}}
        }
        # logic based on chromaDB
        if ids:
            query["query"]["bool"]["filter"].append({"terms": {"_id": ids}})
        elif filter:
            for field, value in filter.items():
                query["query"]["bool"]["filter"].append(
                    {"term": {f"metadata.{field}": value}}
                )

        self.client.delete_by_query(index=f"{self.index_prefix}*", body=query)

    def reset(self):
        indices = self.client.indices.get(index=f"{self.index_prefix}*")
        for index in indices:
            self.client.indices.delete(index=index)