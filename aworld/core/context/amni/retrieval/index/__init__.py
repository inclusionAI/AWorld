from typing import Optional

from pydantic import Field, BaseModel

from .base import RetrievalIndexPlugin

class RetrievalPluginConfig(BaseModel):
    type: str
    config: Optional[dict] = Field(default_factory=dict)

class RetrievalIndexPluginFactory:

    @staticmethod
    def get_index_plugin(index_plugin_config: RetrievalPluginConfig) -> RetrievalIndexPlugin:
        if index_plugin_config.type == "semantic":
            from .semantic import SemanticIndexPlugin
            return SemanticIndexPlugin(config=index_plugin_config.config)
        elif index_plugin_config.type == "full_text":
            from .fulltext import FullTextIndexPlugin
            return FullTextIndexPlugin(config=index_plugin_config.config)
        else:
            raise ValueError(f"Invalid index plugin type: {index_plugin_config.type}")
