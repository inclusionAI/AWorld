from aworld.agents.audio_agent import AudioAgent
from aworld.agents.image_agent import ImageAgent
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


def test_model_config_preserves_context_cache_model_when_initialized_from_dict():
    model_config = ModelConfig(
        llm_provider="openai",
        llm_model_name="fake-model",
        llm_api_key="fake-key",
        context_cache={"enabled": False, "allow_provider_native_cache": False},
    )

    assert isinstance(model_config.context_cache, ContextCacheConfig)
    assert model_config.context_cache.enabled is False
    assert model_config.context_cache.allow_provider_native_cache is False


def test_media_agent_provider_normalization_preserves_context_cache_model():
    base_config = AgentConfig(
        llm_config=ModelConfig(
            llm_provider="openai",
            llm_model_name="fake-model",
            llm_api_key="fake-key",
            llm_base_url="https://example.com/v1",
            context_cache={"enabled": False, "allow_provider_native_cache": False},
        )
    )

    audio_config = AudioAgent._ensure_audio_tts_provider_config(base_config)
    image_config = ImageAgent._ensure_image_config(base_config)

    assert isinstance(audio_config.llm_config.context_cache, ContextCacheConfig)
    assert audio_config.llm_config.context_cache.enabled is False
    assert audio_config.llm_config.context_cache.allow_provider_native_cache is False
    assert isinstance(image_config.llm_config.context_cache, ContextCacheConfig)
    assert image_config.llm_config.context_cache.enabled is False
    assert image_config.llm_config.context_cache.allow_provider_native_cache is False
