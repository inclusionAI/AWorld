from abc import ABC
from typing import Optional, List

from ..embeddings import SearchResults


class RetrievalIndexPlugin(ABC):

    def __init__(self, config: dict):
        self.config = config
        # Default to False - don't wait for insert to complete before returning
        self.wait_insert = config.get("wait_insert", False)

    @property
    def name(self):
        return type(self)

    async def build_index(self, collection: str, doc_id: str, content: str, meta: dict, **kwargs):
        """Async insert document"""
        pass

    async def build_index_batch(self, collection: str, documents: List[dict], **kwargs):
        """Async batch insert documents
        
        Args:
            collection (str): Collection name to organize documents
            documents (List[dict]): List of documents with format [{"id": str, "content": str, "meta": dict}, ...]
            **kwargs: Additional keyword arguments
        """
        pass

    async def async_search(self, collection: str, query: str, search_filter: dict = None, top_k=None, **kwargs) -> Optional[SearchResults]:
        """Async query"""
        pass