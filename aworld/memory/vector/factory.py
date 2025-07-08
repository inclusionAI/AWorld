
from pydantic import BaseModel

from aworld.core.memory import VectorDBConfig
from aworld.memory.vector.dbs.base import VectorDB





class VectorDBFactory:

    @staticmethod
    def get_vector_db(vector_db_config: VectorDBConfig) -> VectorDB:
        if vector_db_config.provider == "chroma":
            from aworld.memory.vector.dbs.chroma import ChromaVectorDB
            return ChromaVectorDB(vector_db_config.config)
        else:
            raise ValueError(f"Vector database {vector_db_config.provider} is not supported")