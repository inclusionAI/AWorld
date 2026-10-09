"""Adapt the existing LLM provider to the Session/Run model protocol."""

from __future__ import annotations

import importlib.util
import inspect
import json
import math
from dataclasses import replace
from hashlib import sha256
from urllib.parse import urlsplit

from aworld.core.agent.messages import AssistantMessage, ModelRequest, ToolCall, ToolResultMessage


def request_payload(request: ModelRequest, model: str, reasoning_effort: str | None = None, *,
                    reasoning_identity: str | None = None, requires_reasoning_content: bool = False) -> dict:
    messages = []
    if request.system_prompt:
        messages.append({"role": "system", "content": request.system_prompt})
    for message in request.messages:
        if isinstance(message, AssistantMessage):
            value = {"role": "assistant", "content": message.content or None}
            if request.tools and reasoning_effort != "none":
                if message.reasoning_content is not None and message.reasoning_identity in (None, reasoning_identity):
                    value["reasoning_content"] = message.reasoning_content
                elif requires_reasoning_content:
                    # History predating reasoning capture can have no replay field.
                    value["reasoning_content"] = ""
            if message.tool_calls:
                value["tool_calls"] = [{"id": call.id, "type": "function", "function": {
                    "name": call.name, "arguments": json.dumps(dict(call.arguments), ensure_ascii=False, allow_nan=False),
                }} for call in message.tool_calls]
        elif isinstance(message, ToolResultMessage):
            value = {"role": "tool", "tool_call_id": message.tool_call_id,
                     "content": json.dumps({"result": message.content, "is_error": message.is_error}, ensure_ascii=False, allow_nan=False)}
        else:
            value = {"role": "user", "content": message.content}
        messages.append(value)
    payload = {"model": model, "messages": messages, "stream": False}
    if request.max_output_tokens is not None:
        payload["max_tokens"] = request.max_output_tokens
    if request.tools:
        payload["tools"] = [{"type": "function", "function": {"name": tool.name,
            "description": tool.description, "parameters": dict(tool.parameters)}} for tool in request.tools]
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort
    return payload


def parse_response(payload: dict) -> AssistantMessage:
    from aworld.core.agent.usage import ModelResponseError
    from aworld.models.token_accounting import parse_usage
    usage = parse_usage(payload.get("usage")) if isinstance(payload, dict) else None
    try:
        message = _parse_message(payload)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        choices = payload.get("choices") if isinstance(payload, dict) else None
        choice = choices[0] if isinstance(choices, list) and len(choices) == 1 and isinstance(choices[0], dict) else {}
        body = choice.get("message")
        body = body if isinstance(body, dict) else {}
        reason = choice.get("finish_reason")
        diagnostics = {"choices_count": len(choices) if isinstance(choices, list) else None,
            "finish_reason": reason if reason in ("stop", "tool_calls", "length", "content_filter", None) else "unsupported",
            "content_chars": len(body["content"]) if isinstance(body.get("content"), str) else None,
            "tool_call_count": len(body["tool_calls"]) if isinstance(body.get("tool_calls"), list) else 0,
            "reasoning_chars": len(body["reasoning_content"]) if isinstance(body.get("reasoning_content"), str) else None,
            "refusal": bool(body.get("refusal"))}
        if getattr(exc, "code", None) == "empty_final_response":
            diagnostics["response_kind"] = "reasoning_only" if body.get("reasoning_content") else "empty"
        raise ModelResponseError(str(exc), usage=usage, code=getattr(exc, "code", "invalid_response"),
                                 diagnostics=diagnostics) from exc
    return replace(message, usage=usage)


def _parse_message(payload: dict) -> AssistantMessage:
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise ValueError("Expected one complete model choice")
    choice = choices[0]
    reason, message = choice.get("finish_reason"), choice.get("message")
    if reason not in ("stop", "tool_calls") or not isinstance(message, dict):
        raise ValueError("Model response is incomplete or unsupported")
    if message.get("role") != "assistant" or message.get("refusal"):
        raise ValueError("Model did not return an executable assistant response")
    calls = []
    for item in message.get("tool_calls") or []:
        if item.get("type") != "function":
            raise ValueError("Only function tools are supported")
        function = item.get("function", {})
        arguments = json.loads(function.get("arguments", ""))
        if not isinstance(arguments, dict):
            raise ValueError("Tool arguments must be a JSON object")
        calls.append(ToolCall(item.get("id"), function.get("name"), arguments))
    if (reason == "tool_calls") != bool(calls):
        raise ValueError("Model finish reason does not match its tool calls")
    content = message.get("content")
    if content is None:
        content = ""
    if isinstance(content, str) and not content.strip() and not calls:
        from aworld.core.agent.usage import ModelResponseError
        raise ModelResponseError("Model returned an empty final response", code="empty_final_response")
    return AssistantMessage(content, tuple(calls), reasoning_content=message.get("reasoning_content"))


class ProviderModel:
    """Convert messages only; provider owns transport, parsing and retries."""

    def __init__(self, provider, *, model: str, reasoning_effort: str | None = None,
                 owns_provider: bool = False, default_parameters: dict | None = None,
                 requires_reasoning_content: bool | None = None):
        self._provider = provider
        self._model, self._reasoning_effort = model, reasoning_effort
        self._owns_provider = owns_provider
        self._default_parameters = dict(default_parameters or {})
        if requires_reasoning_content is not None and type(requires_reasoning_content) is not bool:
            raise TypeError("requires_reasoning_content must be bool or None")
        route = (model.lower(), str(getattr(provider, "base_url", "")).lower())
        self._requires_reasoning_content = (any("deepseek" in part or "dsv4" in part for part in route)
            if requires_reasoning_content is None else requires_reasoning_content)

    @property
    def reasoning_identity(self):
        # Replay is bound to the originating model/endpoint, including custom gateways.
        route = (self._model, str(getattr(self._provider, "base_url", "")))
        return sha256(json.dumps(route).encode()).hexdigest()

    @property
    def context_identity(self):
        return (self._model, str(getattr(self._provider, "base_url", "")), self._reasoning_effort,
                json.dumps(self._default_parameters, sort_keys=True), self._requires_reasoning_content)

    async def complete(self, request: ModelRequest) -> AssistantMessage:
        effort = self._reasoning_effort or self._default_parameters.get("reasoning_effort")
        identity = self.reasoning_identity
        payload = {**self._default_parameters, **request_payload(request, self._model, effort,
            reasoning_identity=identity,
            requires_reasoning_content=self._requires_reasoning_content and effort != "none")}
        payload.pop("model")
        payload.pop("stream")
        messages = payload.pop("messages")
        replayed_chars = sum(len(message.get("reasoning_content") or "") for message in messages)
        try:
            response = await self._provider.acompletion(messages=messages, **payload)
        except Exception as exc:
            # Legacy errors may contain endpoint/response data. Expose only the
            # status or deepest exception type in RunResult/ATIF.
            current, seen, status, timed_out = exc, set(), None, False
            timeout_types = {
                "APITimeoutError", "ConnectTimeout", "ReadTimeout",
                "TimeoutError", "TimeoutException",
            }
            while current is not None and id(current) not in seen:
                seen.add(id(current))
                status = status or getattr(current, "status_code", None)
                timed_out = timed_out or any(
                    cls.__name__ in timeout_types for cls in type(current).__mro__
                )
                cause = current.__cause__ or current.__context__
                if cause is None:
                    break
                current = cause
            detail = (f"HTTP {status}" if status is not None else
                      "timeout" if timed_out else type(current).__name__)
            raise RuntimeError(f"Model provider failed: {detail}") from None
        raw = response.raw_response
        if not isinstance(raw, dict):
            raw = raw.model_dump(mode="json")
        # Validate the original response: legacy normalization may fabricate
        # missing tool ids/names. Incomplete output must not dispatch tools.
        # Use original provider evidence, never normalized missing-as-zero usage.
        original_usage = getattr(response, "raw_usage", None)
        if isinstance(original_usage, dict) and getattr(response, "usage_reported", True):
            raw = {**raw, "usage": original_usage}
        from aworld.core.agent.usage import ModelResponseError
        try:
            parsed = parse_response(raw)
        except ModelResponseError as exc:
            exc.diagnostics["replayed_reasoning_chars"] = replayed_chars
            raise
        return replace(parsed, reasoning_identity=identity, replayed_reasoning_chars=replayed_chars)

    async def aclose(self):
        if not self._owns_provider:
            return
        closed = set()
        for client in (getattr(self._provider, "async_provider", None),
                       getattr(self._provider, "provider", None)):
            if client is None or id(client) in closed:
                continue
            closed.add(id(client))
            close = getattr(client, "aclose", None) or getattr(client, "close", None)
            if close:
                result = close()
                if inspect.isawaitable(result):
                    await result


class ChatCompletionsModel(ProviderModel):
    """Construct the original OpenAIProvider lazily using its async SDK path."""

    def __init__(self, *, model: str, base_url: str = "https://api.openai.com/v1",
                 api_key: str | None = None, timeout: float = 60,
                 reasoning_effort: str | None = None, max_retries: int = 3,
                 default_parameters: dict | None = None, requires_reasoning_content: bool | None = None):
        if not isinstance(model, str) or not model.strip():
            raise ValueError("Specify --model or AWORLD_MODEL")
        parsed = urlsplit(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("base_url must be an HTTP(S) base URL without credentials/query")
        if parsed.hostname == "api.openai.com" and not api_key:
            raise ValueError("Set AWORLD_API_KEY or OPENAI_API_KEY in the environment")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("request timeout must be positive finite seconds")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be an integer between 0 and 10")
        # Check before importing legacy modules: tokenizer helpers can install
        # missing packages. All dependencies must be installed explicitly.
        modules = ("openai", "httpx", "pydantic", "yaml", "loguru", "numpy",
                   "tiktoken", "requests", "wrapt", "executing", "packaging",
                   "fastapi", "importlib_metadata", "opentelemetry.sdk")
        try:
            missing = [name for name in modules if importlib.util.find_spec(name) is None]
        except ModuleNotFoundError:
            missing = ["provider dependencies"]
        if missing:
            raise RuntimeError('Live models need the optional dependencies: pip install "aworld[llm]"')
        from aworld.models.openai_provider import OpenAIProvider
        provider = OpenAIProvider(model_name=model, base_url=base_url,
            api_key=api_key or "local-no-key", sync_enabled=False,
            async_enabled=True, timeout=timeout, max_retries=max_retries)
        super().__init__(provider, model=model, reasoning_effort=reasoning_effort, owns_provider=True,
                         default_parameters=default_parameters, requires_reasoning_content=requires_reasoning_content)
