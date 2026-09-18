import os
from unittest.mock import patch

import pytest

from aworld_cli.core.model_profiles import resolve_context_window_env, resolve_model_profile
from aworld_cli.builtin_agents.smllc.agents.aworld_agent import build_aworld_agent


@pytest.fixture(autouse=True)
def isolated_process_environment():
    with patch.dict(os.environ):
        yield


@pytest.mark.parametrize('raw,expected', [(None,None), ('',None), ('  ',None), ('\n\t',None), (' 1000000 ',1000000)])
def test_context_window_environment_is_optional_positive_integer(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv('AWORLD_CONTEXT_WINDOW_TOKENS', raising=False)
    else:
        monkeypatch.setenv('AWORLD_CONTEXT_WINDOW_TOKENS', raw)
    assert resolve_context_window_env() == expected


@pytest.mark.parametrize('raw', ['0', '-1', '1.5', 'true', '1M', '+1000', '1_000', '\u0661\u0660\u0660\u0660'])
def test_invalid_context_window_environment_fails_before_model_creation(monkeypatch, raw):
    monkeypatch.setenv('AWORLD_CONTEXT_WINDOW_TOKENS', raw)
    with pytest.raises(ValueError, match='AWORLD_CONTEXT_WINDOW_TOKENS'):
        resolve_context_window_env()


def test_builtin_agent_receives_explicit_deployment_capacity_without_extra_input_cap(monkeypatch):
    monkeypatch.setenv('AWORLD_CONTEXT_WINDOW_TOKENS', '1000000')
    monkeypatch.setenv('AWORLD_BUILTIN_SUBAGENTS', 'none')
    monkeypatch.setenv('LLM_MODEL_NAME', 'aisearch_dsv4flash_cron_job')
    monkeypatch.setenv('LLM_API_KEY', 'offline')
    swarm = build_aworld_agent()
    agent = next(iter(swarm.agents.values()))
    assert agent.conf.llm_config.max_model_len == 1000000
    assert agent.conf.max_input_tokens is None


def test_named_profile_retains_window_and_compiler_override(monkeypatch):
    monkeypatch.setenv('AWORLD_CONTEXT_WINDOW_TOKENS', '123000')
    config = resolve_model_profile('local-window-test', config_dict={'models':{
        'local-window-test': {'model':'deployed-alias', 'context_window':1_000_000,
                             'context_compiler':{'context_limit':900_000}},
    }})
    assert config.max_model_len == 1_000_000
    assert config.context_compiler.context_limit == 900_000


def test_default_global_profile_reaches_builtin_and_model_change_drops_only_profile_owned_window(monkeypatch):
    import aworld_cli.core.config as config
    monkeypatch.setenv("AWORLD_BUILTIN_SUBAGENTS", "none")
    monkeypatch.delenv("AWORLD_CONTEXT_WINDOW_TOKENS", raising=False)
    monkeypatch.setattr(config, "_profile_context_window_value", None)
    config._apply_models_config_to_env({"default":{
        "model":"profile-deployment", "api_key":"offline", "context_window":1000000,
    }})
    first = next(iter(build_aworld_agent().agents.values()))
    assert first.conf.llm_config.llm_model_name == "profile-deployment"
    assert first.conf.llm_config.max_model_len == 1000000
    config._apply_models_config_to_env({"default":{"model":"gpt-4", "api_key":"offline"}})
    second = next(iter(build_aworld_agent().agents.values()))
    assert second.conf.llm_config.max_model_len is None
    monkeypatch.setenv("AWORLD_CONTEXT_WINDOW_TOKENS", "900000")
    config._apply_models_config_to_env({"default":{"model":"external-deployment", "api_key":"offline"}})
    third = next(iter(build_aworld_agent().agents.values()))
    assert third.conf.llm_config.max_model_len == 900000
