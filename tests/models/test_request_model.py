from __future__ import annotations

import copy
import json
from types import MethodType, SimpleNamespace

import pytest

from aworld.models.anthropic_provider import AnthropicProvider
from aworld.models.ant_provider import AntProvider
from aworld.models.openai_provider import AzureOpenAIProvider, OpenAIProvider
from aworld.models.request_model import effective_request_model_name, effective_request_output_limits
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


def _sdk_request_body(adapter_params):
    from openai import OpenAI
    from openai._models import FinalRequestOptions

    body = dict(adapter_params)
    extra_body = body.pop("extra_body", None)
    # Exercise the installed SDK's final merge and serialization without send.
    with OpenAI(api_key="unused-offline-test-key", base_url="https://unused.invalid/v1") as client:
        request = client._build_request(FinalRequestOptions.construct(
            method="post", url="/chat/completions",
            json_data=body, extra_json=extra_body,
        ))
    return json.loads(request.content)


def _http_request_body(monkeypatch, provider, request_kwargs, max_tokens=None):
    from aworld.models.llm_http_handler import LLMHTTPHandler

    prepared = provider._prepare_chat_completion_request(
        messages=[{"role": "user", "content": "go"}], temperature=0.0,
        max_tokens=max_tokens, stop=None, kwargs=copy.deepcopy(request_kwargs), stream=False,
    )
    sent = []

    def post(url, **kwargs):
        sent.append(json.loads(kwargs["data"]) if "data" in kwargs else kwargs["json"])
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {})

    monkeypatch.setattr("aworld.models.llm_http_handler.requests.post", post)
    handler = object.__new__(LLMHTTPHandler)
    handler.base_url, handler.headers, handler.timeout = "https://unused.invalid", {}, 1
    handler._make_request("chat/completions", prepared.params, serialized_body=prepared.serialized_body)
    return sent[0]


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AzureOpenAIProvider])
@pytest.mark.parametrize("params,request_kwargs,sdk_model,http_model", _EXTRA_BODY_CASES)
def test_sdk_extra_body_model_matches_locally_built_request(
    provider_type, params, request_kwargs, sdk_model, http_model,
):
    provider = _provider(provider_type, params)
    original_config = copy.deepcopy(provider.kwargs)
    original_request = copy.deepcopy(request_kwargs)
    adapter_params = provider.get_openai_params(
        [{"role": "user", "content": "go"}], **request_kwargs,
    )
    body = _sdk_request_body(adapter_params)
    assert effective_request_model_name(provider, request_kwargs) == body["model"] == sdk_model
    assert provider.kwargs == original_config
    assert request_kwargs == original_request


@pytest.mark.parametrize("params,request_kwargs,sdk_model,http_model", _EXTRA_BODY_CASES)
def test_http_extra_body_stays_nested_in_actual_request_body(
    monkeypatch, params, request_kwargs, sdk_model, http_model,
):
    provider = _provider(OpenAIProvider, params)
    provider.is_http_provider = True
    body = _http_request_body(monkeypatch, provider, request_kwargs)
    assert effective_request_model_name(provider, request_kwargs) == body["model"] == http_model
    extra_body = request_kwargs.get("extra_body", params.get("extra_body"))
    if extra_body is not None:
        assert body["extra_body"] == extra_body


def test_extra_body_model_is_unknown_without_transport_identity():
    provider = _provider(OpenAIProvider)
    del provider.is_http_provider
    assert effective_request_model_name(provider, {"extra_body": {"model": "override"}}) is None


def test_uninterpretable_sdk_extra_body_does_not_borrow_bound_model():
    provider = _provider(OpenAIProvider)
    assert effective_request_model_name(provider, {"extra_body": "opaque-body"}) is None


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AzureOpenAIProvider])
@pytest.mark.parametrize("params,max_tokens,request_kwargs", [
    ({}, None, {}),
    ({"max_tokens": 32768}, None, {}),
    ({"max_tokens": 32768}, 4096, {}),
    ({"max_completion_tokens": 32768}, None, {}),
    ({"max_completion_tokens": 32768}, 8192, {}),
    ({"max_completion_tokens": 32768}, None, {"max_completion_tokens": 4096}),
    ({"max_completion_tokens": 32768}, None, {"max_completion_tokens": None}),
])
def test_output_limits_match_adapter_parameter_precedence(provider_type, params, max_tokens, request_kwargs):
    provider = _provider(provider_type, params)
    wire = provider.get_openai_params(
        [{"role": "user", "content": "go"}], max_tokens=max_tokens, **request_kwargs,
    )
    assert effective_request_output_limits(provider, max_tokens=max_tokens, request_kwargs=request_kwargs) == (
        wire.get("max_tokens"), wire.get("max_completion_tokens"),
    )


_OUTPUT_EXTRA_BODY_CASES = [
    ({"extra_body": {"max_tokens": 32768}}, {}, (32768, 16384)),
    ({"extra_body": {"max_completion_tokens": 32768}}, {}, (8192, 32768)),
    ({"extra_body": {"max_tokens": None, "max_completion_tokens": None}}, {}, (None, None)),
    ({"extra_body": {"max_tokens": 65536}}, {"extra_body": {"max_completion_tokens": 4096}}, (8192, 4096)),
    ({"extra_body": {"max_tokens": 65536}}, {"extra_body": None}, (8192, 16384)),
    ({"extra_body": {"max_tokens": 65536}}, {"extra_body": {}}, (8192, 16384)),
    ({}, {"extra_body": {"max_tokens": 4096, "max_completion_tokens": 2048}}, (4096, 2048)),
    ({}, {"extra_body": {"model": "other-model"}}, (8192, 16384)),
]


@pytest.mark.parametrize("provider_type", [OpenAIProvider, AzureOpenAIProvider])
@pytest.mark.parametrize("params,request_kwargs,expected", _OUTPUT_EXTRA_BODY_CASES)
def test_sdk_output_limits_match_final_serialized_body(provider_type, params, request_kwargs, expected):
    provider = _provider(provider_type, {"max_completion_tokens": 16384, **params})
    original_params, original_kwargs = copy.deepcopy(provider.kwargs), copy.deepcopy(request_kwargs)
    wire = _sdk_request_body(provider.get_openai_params(
        [{"role": "user", "content": "go"}], max_tokens=8192, **request_kwargs,
    ))
    assert effective_request_output_limits(provider, max_tokens=8192, request_kwargs=request_kwargs) == (
        wire.get("max_tokens"), wire.get("max_completion_tokens"),
    ) == expected
    assert provider.kwargs == original_params and request_kwargs == original_kwargs


@pytest.mark.parametrize("params,request_kwargs,expected", _OUTPUT_EXTRA_BODY_CASES)
def test_http_output_limits_do_not_promote_nested_extra_body(monkeypatch, params, request_kwargs, expected):
    provider = _provider(OpenAIProvider, {"max_completion_tokens": 16384, **params})
    provider.is_http_provider = True
    wire = _http_request_body(monkeypatch, provider, request_kwargs, max_tokens=8192)
    assert effective_request_output_limits(provider, max_tokens=8192, request_kwargs=request_kwargs) == (
        wire.get("max_tokens"), wire.get("max_completion_tokens"),
    ) == (8192, 16384)


@pytest.mark.parametrize("max_tokens", [None, 0, False, "", 2048, 32768])
def test_anthropic_output_limits_match_native_default(max_tokens):
    provider = _provider(AnthropicProvider, {"max_tokens": 99999, "max_completion_tokens": 99999})
    kwargs = {"max_completion_tokens": 32768, "extra_body": {"max_tokens": 99999}}
    wire = provider.get_anthropic_params(
        [{"role": "user", "content": "go"}], max_tokens=max_tokens, **kwargs,
    )
    assert effective_request_output_limits(provider, max_tokens=max_tokens, request_kwargs=kwargs) == (wire["max_tokens"],)


@pytest.mark.parametrize("provider_type", [AntProvider, ReviewedCustomChatProvider])
@pytest.mark.parametrize("max_tokens", [None, 1024, 32768])
def test_other_chat_output_limits_match_final_lowering(provider_type, max_tokens):
    provider = _provider(provider_type, {"max_tokens": 99999})
    standard = _standard_request()
    standard["params"]["max_tokens"] = max_tokens
    kwargs = {"max_completion_tokens": 99999, "extra_body": {"max_tokens": 99999}}
    if provider_type is AntProvider:
        wire = provider._lower_context_request(standard, kwargs, True, None).payload
    else:
        wire = provider._lower(provider.model_name, standard, kwargs, False).payload
    assert effective_request_output_limits(provider, max_tokens=max_tokens, request_kwargs=kwargs) == (wire["max_tokens"],)


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "4096"])
def test_output_limit_values_are_not_coerced_before_caller_validation(value):
    provider = _provider(OpenAIProvider)
    actual = effective_request_output_limits(provider, max_tokens=value, request_kwargs={"max_completion_tokens": value})
    assert actual == (value, value)
    assert all(type(item) is type(value) for item in actual)


@pytest.mark.parametrize("field", ["max_tokens", "max_completion_tokens"])
def test_output_extra_body_requires_known_transport(field):
    provider = _provider(OpenAIProvider)
    del provider.is_http_provider
    with pytest.raises(ValueError, match="transport identity"):
        effective_request_output_limits(provider, max_tokens=1024, request_kwargs={"extra_body": {field: 32768}})


def test_output_limits_reject_uninterpretable_sdk_body():
    provider = _provider(OpenAIProvider)
    with pytest.raises(ValueError, match="extra_body"):
        effective_request_output_limits(provider, max_tokens=1024, request_kwargs={"extra_body": "opaque-body"})


@pytest.mark.parametrize("is_http", [False, True])
def test_framework_sanitizer_wrapper_preserves_effective_wire_identity_and_output(monkeypatch, is_http):
    from aworld.self_evolve.candidate_generation import _SanitizingProvider

    provider = _provider(OpenAIProvider, {
        "model": "configured-model", "max_completion_tokens": 2048,
        "extra_body": {"model": "extra-model", "max_tokens": 8192, "max_completion_tokens": 16384},
    })
    provider.is_http_provider = is_http
    wrapper = _SanitizingProvider(_SanitizingProvider(provider))
    kwargs = {"model": "call-model", "max_completion_tokens": 4096}
    if is_http:
        wire = _http_request_body(monkeypatch, provider, kwargs, max_tokens=1024)
    else:
        wire = _sdk_request_body(wrapper.get_openai_params(
            [{"role": "user", "content": "go"}], max_tokens=1024, **kwargs,
        ))
    assert effective_request_model_name(wrapper, kwargs) == wire["model"]
    assert effective_request_output_limits(wrapper, max_tokens=1024, request_kwargs=kwargs) == (
        wire.get("max_tokens"), wire.get("max_completion_tokens"),
    )
    assert wrapper._delegate._delegate is provider


@pytest.mark.parametrize("malformation", ["cycle", "missing", "none"])
def test_malformed_framework_wrappers_do_not_recurse_or_guess(malformation):
    from aworld.self_evolve.candidate_generation import _SanitizingProvider

    wrapper = object.__new__(_SanitizingProvider)
    if malformation != "missing":
        wrapper._delegate = wrapper if malformation == "cycle" else None
    assert effective_request_model_name(wrapper) is None
    with pytest.raises(ValueError, match="framework provider wrapper"):
        effective_request_output_limits(wrapper, max_tokens=1024)


def test_arbitrary_delegate_attributes_are_never_unwrapped():
    class UnreviewedWrapper:
        model_name = "unknown-bound-model"

        @property
        def _delegate(self):
            raise AssertionError("arbitrary delegate attribute must not be read")

    provider = UnreviewedWrapper()
    assert effective_request_model_name(provider, {"model": "unproven-override"}) == "unknown-bound-model"
    assert effective_request_output_limits(provider, max_tokens=1024) == (1024,)
