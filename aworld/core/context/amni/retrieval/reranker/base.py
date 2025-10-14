from abc import ABC, abstractmethod
from typing import Optional

from pydantic import BaseModel, Field


class RerankResult(BaseModel):
    """
    Rerank result model.
    
    Attributes:
        idx (int): Document index in the original list.
        doc (str): Document content.
        score (float): Relevance score.
    """
    idx: int = Field(..., description="text idx")
    doc: str = Field(..., description="text")
    score: float = Field(..., description="Score")


class Reranker(ABC):
    """
    Abstract base class for reranker implementations.
    
    Rerankers are used to reorder documents based on their relevance to a query.
    """
    
    @abstractmethod
    async def run(
            self,
            query: str,
            documents: list[str],
            top_n: Optional[int] = 5,
            **kwargs
    ) -> list[RerankResult]:
        """
        Run rerank model to reorder documents by relevance.
        
        Args:
            query (str): Search query to compare documents against.
            documents (list[str]): List of documents for reranking.
            top_n (Optional[int]): Number of top results to return. Defaults to 5.
            **kwargs: Additional keyword arguments for specific implementations.
            
        Returns:
            list[RerankResult]: List of reranked results sorted by relevance score (descending).
            
        Example:
            ```python
            reranker = HttpReranker(config)
            results = await reranker.run("你好", documents=[
                "你好",
                "我很好",
                "谢谢你",
                "很高兴遇到你",
            ])
            for result in results:
                print(f"{result.doc}: {result.score}")
            # Output:
            # 你好: 0.989096999168396
            # 很高兴遇到你: 0.30921047925949097
            # 谢谢你: 0.10078048706054688
            # 我很好: 0.06917418539524078
            ```
        """
        raise NotImplementedError

