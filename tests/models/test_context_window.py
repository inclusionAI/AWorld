"""Capacity resolution is configuration, not a provider/network operation."""
import pytest

from aworld.config.conf import AgentConfig, ModelConfig
from aworld.models.context_window import (
    DEFAULT_CONTEXT_WINDOW_TOKENS, MODEL_CONTEXT_WINDOWS,
    register_model_context_window, resolve_model_context_window,
)
from aworld.models.utils import ModelUtils


@pytest.mark.parametrize('model,expected', [
    ('gpt-4.1', 1_047_576),
    ('gemini-2.5-pro', 1_048_576),
    ('gpt-4', 8192),
    ('gpt-4o', 128000),
    ('moonshotai/kimi-k2-instruct-0905', 262144),
    ('OPENAI/GPT-4.1', 1_047_576),
])
def test_known_models_resolve_capacity_independently_of_the_tokenizer(model, expected):
    result = resolve_model_context_window(model)
    assert result.tokens == expected
    assert result.source == 'model_registry' and not result.is_fallback
    assert ModelUtils.get_context_window(model) == expected


@pytest.mark.parametrize('model', [None, '', 'aisearch_dsv4flash_cron_job', 'my-gpt-4.1-proxy', 'llama-3.2-new'])
def test_unknown_aliases_have_an_explicit_operational_fallback(model):
    result = resolve_model_context_window(model)
    assert result.tokens == DEFAULT_CONTEXT_WINDOW_TOKENS
    assert result.source == 'fallback' and result.matched_model is None
    assert ModelUtils.get_context_window(model) == result.tokens


def test_explicit_window_precedence_and_source_do_not_mutate_registry():
    declared = resolve_model_context_window('gpt-4', max_model_len=1_000_000)
    assert declared.tokens == 1_000_000 and declared.source == 'explicit_max_model_len'
    compiled = resolve_model_context_window('gpt-4', max_model_len=1_000_000, context_limit=900_000)
    assert compiled.tokens == 900_000 and compiled.source == 'explicit_context_limit'
    assert resolve_model_context_window('gpt-4').tokens == 8192


@pytest.mark.parametrize('bad', [0, -1, True, 1.25, '1000000'])
@pytest.mark.parametrize('field', ['context_limit', 'max_model_len'])
def test_invalid_explicit_windows_are_rejected(bad, field):
    with pytest.raises(ValueError, match='positive integer'):
        resolve_model_context_window('unknown', **{field:bad})


def test_compatibility_registration_is_exact_case_normalized_and_shared(monkeypatch):
    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, 'custom-capacity', 1)
    ModelUtils.add_model_context_window('CUSTOM-CAPACITY', 800000)
    assert resolve_model_context_window('custom-capacity').tokens == 800000
    assert ModelUtils.get_context_window('custom-capacity') == 800000
    assert resolve_model_context_window('prefix-custom-capacity').is_fallback
    with pytest.raises(ValueError):
        register_model_context_window('invalid', 0)


def test_unset_model_and_agent_limits_remain_unset_through_roundtrip():
    model = ModelConfig(llm_model_name='gemini-2.5-pro')
    assert model.max_model_len is None
    assert 'max_model_len' not in model.model_fields_set
    assert ModelConfig.model_validate(model.model_dump()).max_model_len is None
    assert AgentConfig(llm_config=model).max_input_tokens is None


def test_flat_model_override_preserves_nested_identity_window_and_explicit_field_provenance():
    model = ModelConfig(llm_model_name='deployed-alias', max_model_len=1_000_000,
                        context_compiler={'context_limit':900_000})
    agent = AgentConfig(llm_config=model, llm_temperature=0.25)
    assert agent.llm_config.llm_model_name == 'deployed-alias'
    assert agent.llm_config.max_model_len == 1_000_000
    assert agent.llm_config.context_compiler.context_limit == 900_000
    assert 'reserved_output_tokens' not in agent.llm_config.context_compiler.model_fields_set
    assert agent.llm_config.llm_temperature == 0.25
    replaced = AgentConfig(llm_config=model, max_model_len=700000)
    assert replaced.llm_model_name == 'deployed-alias'
    assert replaced.llm_config.max_model_len == 700000


def test_flat_override_preserves_in_place_nested_settings_and_field_provenance():
    model = ModelConfig(llm_model_name="deployed-alias")
    model.context_compiler.context_limit = 900000
    model.params["max_completion_tokens"] = 32768
    model.ext_config["deployment_option"] = "kept"
    agent = AgentConfig(llm_config=model, llm_temperature=0.25,
                        context_compiler={"mode":"observe"})
    assert agent.llm_config.context_compiler.context_limit == 900000
    assert agent.llm_config.context_compiler.mode == "observe"
    assert agent.llm_config.params["max_completion_tokens"] == 32768
    assert agent.llm_config.ext_config["deployment_option"] == "kept"
    assert "reserved_output_tokens" not in agent.llm_config.context_compiler.model_fields_set
    assert model.context_compiler.mode == "enforce"


def test_exact_registration_precedes_an_existing_alias(monkeypatch):
    monkeypatch.setitem(MODEL_CONTEXT_WINDOWS, "claude-3-5-sonnet-latest", 900000)
    assert resolve_model_context_window("claude-3-5-sonnet-latest").tokens == 900000


@pytest.mark.parametrize("model", ["qwen/gpt-4.1", "openai/gemini-2.5-pro"])
def test_foreign_vendor_namespaces_do_not_resolve_an_unrelated_model(model):
    assert resolve_model_context_window(model).is_fallback


def test_candidate_generation_resolves_unset_small_model_capacity():
    from aworld.self_evolve.candidate_generation import _model_aware_candidate_output_limit
    assert _model_aware_candidate_output_limit(ModelConfig(llm_model_name="gpt-4")) == 1024
    assert _model_aware_candidate_output_limit(ModelConfig(llm_model_name="gpt-4.1")) == 32768


@pytest.mark.parametrize('field', ['max_model_len', 'max_input_tokens'])
@pytest.mark.parametrize('bad', [0, -1, True, 1.5])
def test_typed_config_limits_reject_invalid_declarations(field, bad):
    with pytest.raises(ValueError):
        (ModelConfig if field == 'max_model_len' else AgentConfig)(**{field:bad})
