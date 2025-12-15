"""
Qdrant vector search implementation for aworld.
"""

import time
import traceback
import uuid
from typing import Optional, Dict, Any

from aworld.logs.util import logger
from aworld.memory.embeddings.base import (
    EmbeddingsResults,
    EmbeddingsMetadata,
    EmbeddingsResult,
)
from aworld.memory.vector.dbs.base import VectorDB


class QdrantVectorDB(VectorDB):
    """Qdrant implementation of the VectorDB interface."""

    CONTENT_KEY = "content"
    METADATA_KEY = "metadata"
    DEFAULT_DISTANCE = "cosine"

    def _parse_distance(self, distance_config):
        from qdrant_client.models import Distance

        distance_map = {
            "cosine": Distance.COSINE,
            "euclid": Distance.EUCLID,
            "euclidean": Distance.EUCLID,
            "dot": Distance.DOT,
            "manhattan": Distance.MANHATTAN,
        }

        distance_config_lower = distance_config.lower()
        if distance_config_lower in distance_map:
            return distance_map[distance_config_lower]
        else:
            raise ValueError(
                f"Unsupported distance: {distance_config}. Supported: {list(distance_map.keys())}"
            )

    def __init__(self, config: Dict[str, Any]):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance

        self.distance = self._parse_distance(
            config.get("qdrant_distance", self.DEFAULT_DISTANCE)
        )
        self.vector_size = config.get("qdrant_vector_size", 1536)

        client_config = {
            "url": config.get("qdrant_url"),
            "host": config.get("qdrant_host"),
            "port": config.get("qdrant_port"),
            "grpc_port": config.get("qdrant_grpc_port"),
            "prefer_grpc": config.get("qdrant_prefer_grpc"),
            "path": config.get("qdrant_path"),
            "api_key": config.get("qdrant_api_key"),
            "timeout": config.get("qdrant_timeout"),
        }
        client_config = {k: v for k, v in client_config.items() if v is not None}

        self.client = QdrantClient(**client_config)

    def has_collection(self, collection_name: str) -> bool:
        return self.client.collection_exists(collection_name=collection_name)

    def delete_collection(self, collection_name: str):
        if self.has_collection(collection_name):
            self.client.delete_collection(collection_name=collection_name)

    def _get_or_create_collection(self, collection_name: str):
        from qdrant_client.models import VectorParams

        if not self.has_collection(collection_name):
            self.client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=self.vector_size, distance=self.distance
                ),
            )

    def _convert_id_to_uuid(self, id_str: str):
        """Qdrant requires point IDs to be either +ve integers or UUIDs."""
        try:
            return uuid.UUID(id_str)
        except (ValueError, AttributeError):
            return uuid.uuid5(uuid.NAMESPACE_DNS, str(id_str))

    def _build_filter(self, filter: Optional[dict]) -> Optional[Any]:
        if not filter:
            return None

        from qdrant_client.models import Filter, FieldCondition, MatchValue

        conditions = []
        for key, value in filter.items():
            if value is not None:
                conditions.append(
                    FieldCondition(
                        key=f"metadata.{key}"
                        if not key.startswith("metadata.")
                        else key,
                        match=MatchValue(value=value),
                    )
                )

        if not conditions:
            return None

        return Filter(must=conditions)

    def search(
        self,
        collection_name: str,
        vectors: list[list[float | int]],
        filter: dict,
        threshold: float,
        limit: int,
    ) -> Optional[EmbeddingsResults]:
        try:
            if not self.has_collection(collection_name):
                return None

            qdrant_filter = self._build_filter(filter)

            results = self.client.query_points(
                collection_name=collection_name,
                query=vectors[0],
                query_filter=qdrant_filter,
                limit=limit,
                score_threshold=threshold if threshold else None,
            )

            docs = self._convert_results(results.points)

            return EmbeddingsResults(docs=docs, retrieved_at=int(time.time()))
        except Exception as e:
            traceback.print_exc()
            logger.error(f"Error in search: {e}")
            return None

    def _convert_results(self, results, with_score=True):
        docs = []
        for result in results:
            payload = result.payload or {}
            metadata_dict = payload.get(self.METADATA_KEY, {})
            metadata_obj = EmbeddingsMetadata.model_validate(metadata_dict)

            docs.append(
                EmbeddingsResult(
                    id=str(result.id),
                    embedding=None,
                    content=payload.get(self.CONTENT_KEY, ""),
                    metadata=metadata_obj,
                    score=result.score if with_score else None,
                )
            )
        return docs

    def query(
        self, collection_name: str, filter: dict, limit: Optional[int] = None
    ) -> Optional[EmbeddingsResults]:
        try:
            if not self.has_collection(collection_name):
                return None

            qdrant_filter = self._build_filter(filter)

            results, _ = self.client.scroll(
                collection_name=collection_name,
                scroll_filter=qdrant_filter,
                limit=limit if limit else 100,
                with_payload=True,
                with_vectors=False,
            )

            docs = self._convert_scroll_results(results)

            return EmbeddingsResults(docs=docs, retrieved_at=int(time.time()))
        except:
            return None

    def _convert_scroll_results(self, results):
        docs = []
        for result in results:
            payload = result.payload or {}
            metadata_dict = payload.get(self.METADATA_KEY, {})
            metadata_obj = EmbeddingsMetadata.model_validate(metadata_dict)

            docs.append(
                EmbeddingsResult(
                    id=str(result.id),
                    embedding=result.vector,
                    content=payload.get(self.CONTENT_KEY, ""),
                    metadata=metadata_obj,
                    score=None,
                )
            )
        return docs

    def get(self, collection_name: str) -> Optional[EmbeddingsResults]:
        if not self.has_collection(collection_name):
            return None

        all_results = []
        offset = None
        while True:
            batch, offset = self.client.scroll(
                collection_name=collection_name,
                limit=100,
                offset=offset,
                with_payload=True,
                with_vectors=True,
            )
            if not batch:
                break
            all_results.extend(batch)
            if offset is None:
                break

        docs = self._convert_scroll_results(all_results)
        return EmbeddingsResults(docs=docs, retrieved_at=int(time.time()))

    def insert(self, collection_name: str, items: list[EmbeddingsResult]):
        from qdrant_client.models import PointStruct

        if not items:
            return

        self._get_or_create_collection(collection_name)

        points = []
        for item in items:
            metadata_dict = item.metadata.model_dump() if item.metadata else {}
            payload = {self.CONTENT_KEY: item.content, self.METADATA_KEY: metadata_dict}

            points.append(
                PointStruct(
                    id=self._convert_id_to_uuid(item.id),
                    vector=item.embedding,
                    payload=payload,
                )
            )

        batch_size = 100
        for i in range(0, len(points), batch_size):
            batch = points[i : i + batch_size]
            self.client.upsert(collection_name=collection_name, points=batch)

    def upsert(self, collection_name: str, items: list[EmbeddingsResult]):
        from qdrant_client.models import PointStruct

        if not items:
            return

        self._get_or_create_collection(collection_name)

        points = []
        for item in items:
            metadata_dict = item.metadata.model_dump() if item.metadata else {}
            payload = {self.CONTENT_KEY: item.content, self.METADATA_KEY: metadata_dict}

            points.append(
                PointStruct(
                    id=self._convert_id_to_uuid(item.id),
                    vector=item.embedding,
                    payload=payload,
                )
            )

        self.client.upsert(collection_name=collection_name, points=points)

    def delete(
        self,
        collection_name: str,
        ids: Optional[list[str]] = None,
        filter: Optional[dict] = None,
    ):
        try:
            if not self.has_collection(collection_name):
                return

            if ids:
                self.client.delete(
                    collection_name=collection_name,
                    points_selector=[self._convert_id_to_uuid(i) for i in ids],
                )
            elif filter:
                qdrant_filter = self._build_filter(filter)
                if qdrant_filter:
                    self.client.delete(
                        collection_name=collection_name, points_selector=qdrant_filter
                    )
            else:
                self.delete_collection(collection_name)
        except Exception as e:
            logger.debug(
                f"Attempted to delete from non-existent collection {collection_name}. Ignoring."
            )

    def reset(self):
        collections = self.client.get_collections().collections
        for collection in collections:
            self.client.delete_collection(collection_name=collection.name)
