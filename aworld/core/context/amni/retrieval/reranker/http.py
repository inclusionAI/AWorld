import traceback
from typing import List, Optional

import aiohttp

from aworld.logs.util import logger
from .base import Reranker, RerankResult
from .factory import RerankConfig

PREFIX = ("<|im_start|>system\n"
          "Judge whether the Document meets the requirements based on the "
          "Query and the Instruct provided. Note that the answer can only be "
          "\"yes\" or \"no\".<|im_end|>\n"
          "<|im_start|>user\n")
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
RERANKER_INSTRUCTION = " Given a task, retrieve relevant passages that to solve the task\nCurrent Task:"

# Constants for batch size management
MAX_BATCH_SIZE = 32
QWEN3_RERANKER_8B = "Qwen3_Reranker_8B"
MAX_DOC_LENGTH_32K = 32 * 1024  # 32K characters

class HttpReranker(Reranker):
    """
    HTTP-based Reranker supporting async operations.
    Uses aiohttp for non-blocking AmHTTP requests.
    """

    def __init__(self, config: RerankConfig) -> None:
        """
        Initialize HttpReranker.
        
        Args:
            config (RerankConfig): Configuration for rerank model and API.
        """
        self.config = config

    async def run(
            self,
            query: str,
            documents: List[str],
            top_k: Optional[int] = 10,
            **kwargs
    ) -> Optional[List[RerankResult]]:
        """
        Run rerank model using HTTP API asynchronously.
        
        Args:
            query (str): Search query.
            documents (List[str]): List of documents to rerank.
            top_k (Optional[int]): Number of top results to return.
            **kwargs: Additional keyword arguments.
            
        Returns:
            List[RerankResult]: List of rerank results sorted by score.
        """
        if not documents:
            return None

        try:
            return await self._run_http(query, documents, top_k)
        except Exception as e:
            logger.error(f"❌ HTTP rerank 调用失败: {str(e)}, traceback is {traceback.format_exc()}")
            return None

    def _calculate_batch_size(self, documents: List[str]) -> int:
        """
        Calculate appropriate batch size based on model and document length.
        
        Args:
            documents (List[str]): List of documents to process.
            
        Returns:
            int: Calculated batch size.
        """
        model_name: str = self.config.get_value("model_name")
        
        # For Qwen3_Reranker_8B, check if any document exceeds 32K
        if model_name == QWEN3_RERANKER_8B:
            max_doc_length = max(len(doc) for doc in documents) if documents else 0
            if max_doc_length > MAX_DOC_LENGTH_32K:
                logger.warning(f"⚠️ Qwen3_Reranker_8B 检测到文档长度超过32K ({max_doc_length} chars), 限制batch数量为1")
                return 1
        
        # Default batch size limit
        return min(len(documents), MAX_BATCH_SIZE)

    async def _run_http(
            self,
            query: str,
            documents: List[str],
            top_k: Optional[int] = 10,
    ) -> List[RerankResult]:
        """
        Run rerank using HTTP API asynchronously.
        
        Args:
            query (str): Search query.
            documents (List[str]): List of documents to rerank.
            top_k (Optional[int]): Number of top results to return.
            
        Returns:
            List[RerankResult]: List of rerank results.
        """
        url = f"{self.config.get_value('base_url')}/rerank"
        headers = {
            "Authorization": f"Bearer {self.config.get_value('api_key')}",
            "Content-Type": "application/json"
        }

        # Calculate appropriate batch size
        batch_size = self._calculate_batch_size(documents)

        parsed_documents = documents
        model_name: str = self.config.get_value("model_name")
        if model_name.startswith("Qwen3_Reranker"):
            query = f"{PREFIX}<Instruct>: {RERANKER_INSTRUCTION} {query}"
            parsed_documents = [f"<Document>: {doc}{SUFFIX}" for doc in documents]

        all_results = []
        
        # Process documents in batches
        for i in range(0, len(documents), batch_size):
            batch_docs = documents[i:i + batch_size]
            batch_parsed_docs = parsed_documents[i:i + batch_size]
            
            payload = {
                "query": query,
                "texts": [doc[:1000] for doc in batch_parsed_docs],
                "model": self.config.get_value("model_name"),
            }
            if top_k is not None:
                payload["top_n"] = top_k

            logger.debug(f"🔄 处理batch {i//batch_size + 1}: {len(batch_docs)} 文档")
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, headers=headers, verify_ssl=False) as response:
                    response.raise_for_status()
                    data = await response.json()
                    
            # Process batch results
            for item in data.get('data', []):
                # Adjust index to global document index
                global_idx = i + item['index']
                text = documents[global_idx]
                all_results.append(RerankResult(idx=global_idx, doc=text, score=item['score']))
        
        # Sort all results by score and apply top_k if specified
        if top_k is not None:
            all_results = sorted(all_results, key=lambda x: x.score, reverse=True)[:top_k]
        else:
            all_results = sorted(all_results, key=lambda x: x.score, reverse=True)
            
        return all_results
