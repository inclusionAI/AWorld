import os
from typing import Optional, Any

from pydantic import BaseModel, Field

from .base import Reranker


class RerankConfig(BaseModel):
    """
    Configuration for reranker providers.
    
    Attributes:
        provider (str): Reranker provider type (e.g., "http").
        config (Optional[dict[str, Any]]): Provider-specific configuration.
    """
    provider: str = Field(default="http", description="Provider")
    config: Optional[dict[str, Any]] = Field(default_factory=dict, description="config")

    @staticmethod
    def from_config(config: dict) -> Optional["RerankConfig"]:
        """
        Create RerankConfig from dictionary.
        
        Args:
            config (dict): Configuration dictionary.
            
        Returns:
            Optional[RerankConfig]: RerankConfig instance or None if config is empty.
        """
        if not config:
            return None
        return RerankConfig(**config)

    def get_value(self, key: str) -> Any:
        """
        Get configuration value by key.
        
        Args:
            key (str): Configuration key.
            
        Returns:
            Any: Configuration value or None if key doesn't exist.
        """
        return self.config.get(key)



class RerankerFactory:
    """
    Factory class for creating reranker instances.
    """

    @staticmethod
    def get_reranker(reranker_config: RerankConfig) -> Reranker:
        """
        Create reranker instance based on configuration.
        
        Args:
            reranker_config (RerankConfig): Reranker configuration.
            
        Returns:
            Reranker: Reranker instance.
            
        Raises:
            ValueError: If provider is not supported.
            
        Example:
            ```python
            config = RerankConfig(provider="http", config={
                "base_url": "http://localhost:8000",
                "api_key": "your-api-key",
                "model_name": "bge-reranker-v2-m3"
            })
            reranker = RerankerFactory.get_reranker(config)
            ```
        """
        if reranker_config.provider == "http":
            from .http import HttpReranker
            return HttpReranker(reranker_config)
        else:
            raise ValueError(f"Invalid reranker provider: {reranker_config.provider}")

    @staticmethod
    def get_default_reranker() -> Reranker:
        """
        Create default reranker instance using environment variables.
        
        Returns:
            Reranker: Default reranker instance.
            
        Environment Variables:
            RERANKER_BASE_URL: Base URL for reranker API.
            RERANKER_API_KEY: API key for authentication.
            RERANKER_MODEL_NAME: Model name to use.
            
        Example:
            ```python
            # Ensure environment variables are set
            # RERANKER_BASE_URL=http://localhost:8000
            # RERANKER_API_KEY=your-api-key
            # RERANKER_MODEL_NAME=bge-reranker-v2-m3
            reranker = RerankerFactory.get_default_reranker()
            ```
        """
        return RerankerFactory.get_reranker(RerankConfig(provider="http", config={
            "base_url": os.getenv("RERANKER_BASE_URL"),
            "api_key": os.getenv("RERANKER_API_KEY"),
            "model_name": os.getenv("RERANKER_MODEL_NAME"),
        }))