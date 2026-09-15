from copy import deepcopy
import importlib.util
import json
from pathlib import Path
from types import MethodType, SimpleNamespace

from aworld.config import ModelConfig
from aworld.core.context.base import Context
from aworld.core.context.compiler import canonical_json_hash
from aworld.core.context.compiler import LifecycleAction
from aworld.evaluations.cache_replay import evaluate_cache_replay
from aworld.models.llm import LLMModel
from aworld.models.model_response import ModelResponse
from aworld.models.openai_provider import OpenAIProvider


def _call(
    ordinal: int,
    *,
    stable_hash: str,
    epoch: int,
    dynamic_value: str,
    cache_read_tokens: int,
    break_reasons=(),
):
    request_id = f"request-{ordinal}"
    candidate_payload = {
        "messages": [
            {"role": "system", "content": "redacted stable payload"},
            {"role": "user", "content": dynamic_value},
        ],
        "tools": None,
        "params": {"temperature": 0, "max_tokens": 8, "stop": None},
    }
    candidate_hash = canonical_json_hash(candidate_payload)
    provider_payload = {
        "model": "provider-model",
        "messages": candidate_payload["messages"],
        "temperature": 0,
    }
    provider_hash = canonical_json_hash(provider_payload)
    plan_base = {
        "schema_version": "aworld.context.cache-plan.v1",
        "candidate_content_hash": candidate_hash,
        "inference_profile": {
            "provider": "provider-under-test",
            "model": "provider-model",
            "reasoning_effort": None,
            "execution_mode": "chat.sync",
            "context_limit": 128_000,
            "response_format_hash": None,
        },
        "policy_version": "policy-v1",
        "tool_catalog_hash": canonical_json_hash([]),
        "skill_set_hash": canonical_json_hash([]),
        "logical_stable_prefix_hash": stable_hash,
        "stable_message_count": 1,
        "cache_epoch": epoch,
        "break_reasons": list(break_reasons),
        "native_cache_requested": True,
        "provider_cache_namespace_hash": None,
    }
    plan_fingerprint = canonical_json_hash(plan_base)
    candidate_contract_hash = canonical_json_hash(
        {
            "candidate_content_hash": candidate_hash,
            "cache_plan_fingerprint": plan_fingerprint,
        }
    )
    plan = {
        **plan_base,
        "fingerprint": plan_fingerprint,
        "candidate_contract_hash": candidate_contract_hash,
    }
    usage = {
        "prompt_tokens": 100,
        "completion_tokens": 1,
        "total_tokens": 101,
        "prompt_tokens_details": {"cached_tokens": cache_read_tokens},
    }
    return {
        "request_id": request_id,
        "status": "success",
        "provider_invoked": True,
        "provider_attempt_status": "attempted",
        "request_trace_match": True,
        "provider_request": {
            "request_id": request_id,
            "provider_name": "provider-under-test",
            "payload": provider_payload,
            "capture_stage": "provider_prepared",
            "fidelity": "provider_prepared",
            "content_hash": provider_hash,
        },
        "context_rollout": {
            "candidate_snapshot": {
                "content_hash": candidate_hash,
                "cache_plan_fingerprint": plan_fingerprint,
                "candidate_contract_hash": candidate_contract_hash,
            },
            "final_compile": {
                "partition": {"stable_prefix_hash": stable_hash},
                "cache_plan": plan,
            },
            "provider_lowering": {
                "candidate_content_hash": candidate_hash,
                "cache_plan_fingerprint": plan_fingerprint,
                "candidate_contract_hash": candidate_contract_hash,
                "provider_request": {"content_hash": provider_hash},
            },
        },
        "usage_raw": usage,
        "usage_normalized": usage,
    }


def _valid_trace():
    stable_a = canonical_json_hash({"stable": "a"})
    stable_b = canonical_json_hash({"stable": "b"})
    return [
        _call(
            0,
            stable_hash=stable_a,
            epoch=0,
            dynamic_value="cold",
            cache_read_tokens=0,
        ),
        _call(
            1,
            stable_hash=stable_a,
            epoch=0,
            dynamic_value="repeat",
            cache_read_tokens=80,
        ),
        _call(
            2,
            stable_hash=stable_a,
            epoch=0,
            dynamic_value="suffix changed",
            cache_read_tokens=80,
        ),
        _call(
            3,
            stable_hash=stable_b,
            epoch=0,
            dynamic_value="prefix changed",
            cache_read_tokens=0,
        ),
        _call(
            4,
            stable_hash=stable_b,
            epoch=1,
            dynamic_value="compacted",
            cache_read_tokens=0,
            break_reasons=("history_compaction",),
        ),
        _call(
            5,
            stable_hash=stable_b,
            epoch=1,
            dynamic_value="after compaction",
            cache_read_tokens=70,
        ),
    ]


def test_cache_replay_proves_exact_trace_usage_and_explained_breaks():
    report = evaluate_cache_replay(_valid_trace())
    payload = report.to_dict()

    assert report.status == "passed"
    assert report.request_trace_match_rate == 1.0
    assert report.exact_usage_coverage == 1.0
    assert report.break_event_count == 3
    assert report.explained_break_count == 3
    assert report.unexplained_break_count == 0
    assert [call.expected_cache_reuse for call in report.calls] == [
        False,
        True,
        True,
        False,
        False,
        True,
    ]
    assert "redacted stable payload" not in repr(payload)
    assert "suffix changed" not in repr(payload)


def test_cache_replay_fails_closed_on_tamper_missing_usage_and_stale_break():
    calls = _valid_trace()
    calls[1]["provider_request"]["content_hash"] = "sha256:" + "f" * 64
    calls[2].pop("usage_raw")
    calls[2].pop("usage_normalized")
    calls[5]["context_rollout"]["final_compile"]["cache_plan"][
        "break_reasons"
    ] = ["history_compaction"]

    report = evaluate_cache_replay(calls)

    assert report.status == "failed"
    assert report.request_trace_match_rate == 1.0
    assert report.exact_usage_coverage < 1.0
    assert "provider_request_hash_mismatch" in report.failure_codes
    assert "cache_usage_not_exact" in report.failure_codes
    assert (
        "unexplained_declared_break:history_compaction" in report.failure_codes
    )


def test_cache_replay_rejects_epoch_regression():
    calls = _valid_trace()
    regressed = deepcopy(calls[-1])
    regressed["request_id"] = "request-regressed"
    regressed["provider_request"]["request_id"] = "request-regressed"
    regressed["context_rollout"]["final_compile"]["cache_plan"][
        "cache_epoch"
    ] = 0

    report = evaluate_cache_replay([*calls, regressed])

    assert report.status == "failed"
    assert "cache_epoch_regressed" in report.failure_codes


def test_cache_replay_requires_calls():
    report = evaluate_cache_replay([])

    assert report.status == "failed"
    assert report.failure_codes == ("cache_replay_calls_missing",)


def test_cache_replay_portfolio_keeps_runs_isolated_and_redacted(tmp_path: Path):
    script_path = (
        Path(__file__).resolve().parents[2]
        / "examples"
        / "evaluations"
        / "cache_replay_report.py"
    )
    spec = importlib.util.spec_from_file_location("cache_replay_report", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    paths = []
    for index in range(2):
        path = tmp_path / f"calls-{index}.json"
        path.write_text(json.dumps(_valid_trace()), encoding="utf-8")
        paths.append(path)

    report = module.build_portfolio(paths)

    assert report["status"] == "passed"
    assert report["run_count"] == 2
    assert report["call_count"] == 12
    assert report["request_trace_match_rate"] == 1.0
    assert report["exact_usage_coverage"] == 1.0
    assert report["unexplained_break_count"] == 0
    assert all(set(run) == {"source_hash", "call_count", "report"} for run in report["runs"])
    assert str(tmp_path) not in repr(report)


def test_cache_replay_accepts_real_runtime_provider_records():
    calls = []

    class _Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return object()

    provider = object.__new__(OpenAIProvider)
    provider.model_name = "provider-model"
    provider.kwargs = {}
    provider.provider = SimpleNamespace(
        chat=SimpleNamespace(completions=_Completions())
    )
    provider.async_provider = None
    provider.is_http_provider = False
    provider.stream_tool_buffer = []

    def response(_self, _raw):
        cache_read = (0, 80, 0, 80)[len(calls) - 1]
        usage = {
            "prompt_tokens": 100,
            "completion_tokens": 1,
            "total_tokens": 101,
            "prompt_tokens_details": {"cached_tokens": cache_read},
        }
        return ModelResponse(
            id=f"response-{len(calls)}",
            model="provider-model",
            content="OK",
            message={"role": "assistant", "content": "OK"},
            finish_reason="stop",
            usage=usage,
            raw_usage=usage,
        )

    provider.postprocess_response = MethodType(response, provider)
    model = LLMModel(
        conf=ModelConfig(
            context_compiler={"mode": "enforce", "universal_final": True}
        ),
        custom_provider=provider,
    )
    model.provider_name = "openai"
    context = Context(task_id="runtime-cache-replay")
    context.trace_id = ""

    def invoke(dynamic: str):
        model.completion(
            [
                {"role": "system", "content": "stable rules"},
                {"role": "user", "content": dynamic},
            ],
            context=context,
        )

    invoke("cold")
    invoke("suffix change")
    context.advance_context_lifecycle(LifecycleAction.CHECKPOINT)
    invoke("compacted")
    invoke("after compaction")

    report = evaluate_cache_replay(context.get_llm_calls())

    assert report.status == "passed"
    assert report.request_trace_match_rate == 1.0
    assert report.exact_usage_coverage == 1.0
    assert report.unexplained_break_count == 0
    assert [call.expected_cache_reuse for call in report.calls] == [
        False,
        True,
        False,
        True,
    ]
