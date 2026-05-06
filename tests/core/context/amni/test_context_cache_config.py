from aworld.config.conf import AgentConfig, ModelConfig
from aworld.core.context.amni.config import AgentContextConfig, AmniConfigFactory, ContextCacheConfig


def test_context_cache_defaults_are_enabled():
    agent_context_config = AgentContextConfig()
    model_config = ModelConfig()

    assert isinstance(agent_context_config.context_cache, ContextCacheConfig)
    assert agent_context_config.context_cache.enabled is True
    assert agent_context_config.context_cache.allow_provider_native_cache is True

    assert isinstance(model_config.context_cache, ContextCacheConfig)
    assert model_config.context_cache.enabled is True
    assert model_config.context_cache.allow_provider_native_cache is True


def test_agent_config_top_level_context_cache_passthrough():
    agent_config = AgentConfig(
        llm_provider="openai",
        llm_model_name="fake-model",
        llm_api_key="fake-key",
        context_cache=ContextCacheConfig(enabled=False, allow_provider_native_cache=False),
    )

    assert agent_config.llm_config.context_cache.enabled is False
    assert agent_config.llm_config.context_cache.allow_provider_native_cache is False


def test_amni_config_factory_preserves_default_context_cache():
    config = AmniConfigFactory.create()

    assert config.agent_config.context_cache.enabled is True
    assert config.agent_config.context_cache.allow_provider_native_cache is True
