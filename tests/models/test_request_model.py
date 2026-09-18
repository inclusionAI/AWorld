from __future__ import annotations

import copy
import json
from types import MethodType, SimpleNamespace

import pytest

from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.ant_provider import AntProvider
from aworld.models.openai_provider import AzureOpenAIProvider, OpenAIProvider
from aworld.models.request_model import effective_request_model_name
from aworld.models.reviewed_custom_provider import ReviewedCustomChatProvider


def _provider(provider_type, params=None):
    # Exact provider types with no client initialization, credentials or I/O.
    provider = object.__new__(provider_type)
    provider.model_name = "bound-model"
    provider.kwargs = {"params": copy.deepcopy(params or {})}
    if isinstance(provider, OpenAIProvider):
        provider.is_http_provider = False
    return provider


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AzureOpenAIProvider])
@pytest.mark.parametrize("params,request_kwargs,expected", [
    ({}, {}, "bound-model"),
    ({"model": "configured-model"}, {}, "configured-model"),
    ({}, {"model_name": "named-model"}, "named-model"),
    ({"model": "configured-model"}, {"model_name": "named-model"}, "configured-model"),
    ({"model": "configured-model"}, {"model": "request-model"}, "request-model"),
    ({}, {"model_name": "named-model", "model": "request-model"}, "request-model"),
    ({"model": "configured-model"}, {"model": None}, "bound-model"),
    ({"model": "configured-model"}, {"model": None, "model_name": "named-model"}, "named-model"),
    ({}, {"model_name": None}, None),
    ({"model": "configured-model"}, {"model_name": None}, "configured-model"),
    ({"model_name": "ignored-configured-name"}, {}, "bound-model"),
    ({"model": None}, {}, "bound-model"),
    ({"model": "configured-model"}, {"model": ""}, ""),
    ({}, {"model_name": ""}, ""),
    ({}, {"model": " OPENAI/Case-Sensitive-Deployment "}, " OPENAI/Case-Sensitive-Deployment "),
])
def test_openai_model_resolution_matches_final_adapter_params(
    provider_type, params, request_kwargs, expected,
):
    provider = _provider(provider_type, params)
    original_config = copy.deepcopy(provider.kwargs)
    original_request = copy.deepcopy(request_kwargs)
    wire = provider.get_openai_params(
        [{"role": "user", "content": "go"}], **request_kwargs,
    )
    assert effective_request_model_name(provider, request_kwargs) == wire["model"] == expected
    assert provider.kwargs == original_config
    assert request_kwargs == original_request


@pytest.mark.parametrize("request_kwargs,expected", [
    ({}, "bound-model"),
    ({"model": "ignored-model"}, "bound-model"),
    ({"model_name": "named-model", "model": "ignored-model"}, "named-model"),
    ({"model_name": None}, None),
    ({"model_name": ""}, ""),
])
def test_anthropic_model_resolution_matches_native_params(request_kwargs, expected):
    provider = _provider(AnthropicProvider, {"model": "ignored-configured-model"})
    wire = provider.get_anthropic_params(
        [{"role": "user", "content": "go"}], **request_kwargs,
    )
    assert effective_request_model_name(provider, request_kwargs) == wire["model"] == expected


def _standard_request():
    return {
        "messages": [{"role": "user", "content": "go"}], "tools": None,
        "params": {"temperature": 0.0, "max_tokens": 100, "stop": None},
    }


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("request_kwargs", [{}, {"model": "ignored-model"}, {"model": None}])
def test_ant_model_resolution_matches_final_lowered_payload(stream, request_kwargs):
    provider = _provider(AntProvider, {"model": "ignored-configured-model"})
    provider.kwargs.update({"ant_visit_biz": "test-biz", "ant_visit_biz_line": "test-line"})
    provider.api_key = "unused-offline-test-key"
    # Keep the real route/parameter lowering and bypass only encryption.
    provider._build_request_data = MethodType(lambda self, params: params, provider)
    projection = provider._lower_context_request(_standard_request(), request_kwargs, stream, None)
    wire = projection.payload if stream else projection.payload["queryConditions"]
    assert effective_request_model_name(provider, request_kwargs) == wire["model"] == "bound-model"


def test_ant_stream_retains_bound_model_despite_named_override():
    provider = _provider(AntProvider)
    kwargs = {"model_name": "ignored-model"}
    wire = provider._lower_context_request(_standard_request(), kwargs, True, None).payload
    assert effective_request_model_name(provider, kwargs) == wire["model"] == "bound-model"


@pytest.mark.parametrize("request_kwargs", [
    {}, {"model": "ignored-model", "model_name": "ignored-name"}, {"model_name": None},
])
def test_reviewed_custom_transport_retains_bound_model(request_kwargs):
    provider = _provider(ReviewedCustomChatProvider, {"model": "ignored-configured-model"})
    wire = provider._lower(provider.model_name, _standard_request(), request_kwargs, False).payload
    assert effective_request_model_name(provider, request_kwargs) == wire["model"] == "bound-model"


@pytest.mark.parametrize("bound", [None, "opaque-model", "", 123])
def test_unknown_adapter_never_infers_routing_from_model_names(bound):
    provider = SimpleNamespace(model_name=bound, kwargs={"params": {"model": "gpt-4.1"}})
    expected = bound if isinstance(bound, str) else None
    assert effective_request_model_name(provider, {"model_name": "gpt-4o", "model": "gpt-4"}) == expected


_EXTRA_BODY_CASES = [
    ({"extra_body": {"model": "configured-extra-model"}}, {}, "configured-extra-model", "bound-model"),
    ({"model": "configured-model", "extra_body": {"model": "configured-extra-model"}},
     {"model": "request-model"}, "configured-extra-model", "request-model"),
    ({"extra_body": {"model": "configured-extra-model"}},
     {"extra_body": {"model": "request-extra-model"}}, "request-extra-model", "bound-model"),
    ({"extra_body": {"model": "configured-extra-model"}}, {"extra_body": None}, "bound-model", "bound-model"),
    ({"extra_body": {"model": "configured-extra-model"}}, {"extra_body": {}}, "bound-model", "bound-model"),
    ({}, {"extra_body": {"model": None}}, None, "bound-model"),
    ({}, {"extra_body": {"model": ""}}, "", "bound-model"),
]


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AzureOpenAIProvider])
@pytest.mark.parametrize("params,request_kwargs,sdk_model,http_model", _EXTRA_BODY_CASES)
def test_sdk_extra_body_model_matches_locally_built_request(
    provider_type, params, request_kwargs, sdk_model, http_model,
):
    from openai import OpenAI
    from openai._models import FinalRequestOptions

    provider = _provider(provider_type, params)
    original_config = copy.deepcopy(provider.kwargs)
    original_request = copy.deepcopy(request_kwargs)
    adapter_params = provider.get_openai_params(
        [{"role": "user", "content": "go"}], **request_kwargs,
    )
    extra_body = adapter_params.pop("extra_body", None)
    # Exercise the installed SDK's final merge and serialization without send.
    with OpenAI(api_key="unused-offline-test-key", base_url="https://unused.invalid/v1") as client:
        request = client._build_request(FinalRequestOptions.construct(
            method="post", url="/chat/completions",
            json_data=adapter_params, extra_json=extra_body,
        ))
    body = json.loads(request.content)
    assert effective_request_model_name(provider, request_kwargs) == body["model"] == sdk_model
    assert provider.kwargs == original_config
    assert request_kwargs == original_request


@pytest.mark.parametrize("params,request_kwargs,sdk_model,http_model", _EXTRA_BODY_CASES)
def test_http_extra_body_stays_nested_in_actual_request_body(
    monkeypatch, params, request_kwargs, sdk_model, http_model,
):
    from aworld.models.llm_http_handler import LLMHTTPHandler

    provider = _provider(OpenAIProvider, params)
    provider.is_http_provider = True
    prepared = provider._prepare_chat_completion_request(
        messages=[{"role": "user", "content": "go"}], temperature=0.0,
        max_tokens=None, stop=None, kwargs=copy.deepcopy(request_kwargs), stream=False,
    )
    sent = []

    def post(url, **kwargs):
        sent.append(json.loads(kwargs["data"]) if "data" in kwargs else kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    monkeypatch.setattr("aworld.models.llm_http_handler.requests.post", post)
    handler = object.__new__(LLMHTTPHandler)
    handler.base_url, handler.headers, handler.timeout = "https://unused.invalid", {}, 1
    handler._make_request("chat/completions", prepared.params, serialized_body=prepared.serialized_body)
    assert effective_request_model_name(provider, request_kwargs) == sent[0]["model"] == http_model
    if prepared.params.get("extra_body") is not None:
        assert sent[0]["extra_body"] == prepared.params["extra_body"]


def test_extra_body_model_is_unknown_without_transport_identity():
    provider = _provider(OpenAIProvider)
    del provider.is_http_provider
    assert effective_request_model_name(provider, {"extra_body": {"model": "override"}}) is None


def test_uninterpretable_sdk_extra_body_does_not_borrow_bound_model():
    provider = _provider(OpenAIProvider)
    assert effective_request_model_name(provider, {"extra_body": "opaque-body"}) is None
