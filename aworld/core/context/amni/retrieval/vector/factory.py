from typing import Optional

from .base import VectorDB, VectorDBConfig


class VectorDBFactory:

    @staticmethod
    def get_vector_db(vector_db_config: VectorDBConfig) -> Optional[VectorDB]:
        if not vector_db_config:
            return None
        if vector_db_config.provider == "chroma":
            from .chroma import ChromaVectorDB
            return ChromaVectorDB(vector_db_config.config)
        if vector_db_config.provider == "elasticsearch":
            from .elasticsearch import ElasticsearchVectorDB
            return ElasticsearchVectorDB(vector_db_config.config)
        else:
            raise ValueError(f"Vector database {vector_db_config.provider} is not supported")