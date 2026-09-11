"""Redacted provider-neutral prompt-cache conformance preflight.

The probe discovers behavior from provider usage instead of assuming a vendor.
Its receipt contains no prompt, response text, endpoint, or credential.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PROBE_SCHEMA_VERSION = "aworld.cache-conformance-preflight/v1"
PROBE_LABELS = ("cold", "repeat", "suffix_change", "prefix_change")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-sec", type=float, default=180)
    parser.add_argument("--model-seed", type=int)
    parser.add_argument("--prefix-line-count", type=int, default=900)
    parser.add_argument("--skip-streaming", action="store_true")
    return parser.parse_args()


def _value_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _probe_prefix(run_nonce: str, mode: str, line_count: int) -> str:
    header = f"aworld-cache-probe:{run_nonce}:{mode}"
    lines = [header]
    lines.extend(
        f"stable-prefix-line-{index:04d}: invariant {index % 17}."
        for index in range(line_count)
    )
    return "\n".join(lines)


def _messages(prefix: str, suffix: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": prefix},
        {"role": "user", "content": f"Return exactly OK. suffix={suffix}"},
    ]


async def _call_model(
    *,
    model: Any,
    mode: str,
    label: str,
    messages: list[dict[str, str]],
    timeout_sec: float,
) -> dict[str, Any]:
    from aworld.models.usage import build_cache_usage_receipt

    started = time.monotonic()
    try:
        responses = []
        if mode == "stream":

            async def consume() -> None:
                async for response in model.astream_completion(
                    messages=messages,
                    temperature=0,
                    max_tokens=32,
                ):
                    responses.append(response)

            await asyncio.wait_for(consume(), timeout=timeout_sec)
        else:
            responses.append(
                await asyncio.wait_for(
                    model.acompletion(
                        messages=messages,
                        temperature=0,
                        max_tokens=32,
                    ),
                    timeout=timeout_sec,
                )
            )

        selected_response = None
        selected_receipt = None
        for response in responses:
            receipt = build_cache_usage_receipt(
                raw_usage=getattr(response, "raw_usage", None),
                normalized_usage=getattr(response, "usage", None),
            )
            if selected_response is None or receipt.fidelity.value == "exact":
                selected_response = response
                selected_receipt = receipt
            if receipt.fidelity.value == "exact":
                break
        if selected_receipt is None:
            selected_receipt = build_cache_usage_receipt(
                raw_usage=None, normalized_usage=None
            )

        return {
            "label": label,
            "mode": mode,
            "status": "success",
            "latency_seconds": round(time.monotonic() - started, 3),
            "request_fingerprint": _value_hash(messages),
            "provider_request_id_present": bool(
                getattr(selected_response, "provider_request_id", None)
            ),
            "stream_chunk_count": len(responses) if mode == "stream" else None,
            "cache_usage_receipt": selected_receipt.to_dict(),
        }
    except Exception as exc:
        return {
            "label": label,
            "mode": mode,
            "status": "failed",
            "latency_seconds": round(time.monotonic() - started, 3),
            "request_fingerprint": _value_hash(messages),
            "exception_type": type(exc).__name__,
            "error_fingerprint": _value_hash(
                {"type": type(exc).__name__, "detail": str(exc)}
            ),
            "cache_usage_receipt": {
                "schema_version": "aworld.cache-usage-receipt.v1",
                "fidelity": "unavailable",
                "reason_code": "provider_call_failed",
                "cache_read_tokens": None,
            },
        }


def evaluate_cache_conformance(
    observations: list[dict[str, Any]],
) -> dict[str, Any]:
    modes = list(dict.fromkeys(str(item.get("mode")) for item in observations))
    exact_count = sum(
        item.get("status") == "success"
        and (item.get("cache_usage_receipt") or {}).get("fidelity") == "exact"
        for item in observations
    )
    exact_coverage = exact_count / len(observations) if observations else 0.0
    failures: list[str] = []
    if exact_coverage != 1.0:
        failures.append("cache_usage_not_exact")

    capability_observed = bool(modes)
    for mode in modes:
        by_label = {
            str(item.get("label")): item
            for item in observations
            if item.get("mode") == mode
        }
        if set(by_label) != set(PROBE_LABELS):
            failures.append("probe_sequence_incomplete")
            capability_observed = False
            continue
        values: dict[str, int] = {}
        for label in PROBE_LABELS:
            item = by_label[label]
            receipt = item.get("cache_usage_receipt") or {}
            value = receipt.get("cache_read_tokens")
            if (
                item.get("status") != "success"
                or receipt.get("fidelity") != "exact"
                or isinstance(value, bool)
                or not isinstance(value, int)
                or value < 0
            ):
                capability_observed = False
                continue
            values[label] = value
        if len(values) != len(PROBE_LABELS):
            capability_observed = False
            continue
        if values["repeat"] <= values["cold"]:
            failures.append("repeat_cache_hit_not_observed")
            capability_observed = False
        if values["suffix_change"] <= values["cold"]:
            failures.append("suffix_prefix_reuse_not_observed")
            capability_observed = False
        if values["prefix_change"] >= values["suffix_change"]:
            failures.append("prefix_invalidation_not_observed")
            capability_observed = False

    deduped_failures = list(dict.fromkeys(failures))
    return {
        "status": "passed" if not deduped_failures else "failed",
        "cache_capability_observed": capability_observed,
        "exact_usage_coverage": exact_coverage,
        "exact_observation_count": exact_count,
        "observation_count": len(observations),
        "validated_modes": modes,
        "failure_codes": deduped_failures,
    }


async def probe_model_cache(
    *,
    model: Any,
    provider: str,
    model_name: str,
    timeout_sec: float,
    include_streaming: bool,
    run_nonce: str,
    prefix_line_count: int,
) -> dict[str, Any]:
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    if prefix_line_count <= 0:
        raise ValueError("prefix_line_count must be positive")
    if not run_nonce:
        raise ValueError("run_nonce must be non-empty")

    observations: list[dict[str, Any]] = []
    modes = ["nonstream"] + (["stream"] if include_streaming else [])
    for mode in modes:
        stable_prefix = _probe_prefix(run_nonce, mode, prefix_line_count)
        changed_prefix = "X" + stable_prefix[1:]
        cases = (
            ("cold", stable_prefix, "A"),
            ("repeat", stable_prefix, "A"),
            ("suffix_change", stable_prefix, "B"),
            ("prefix_change", changed_prefix, "B"),
        )
        for label, prefix, suffix in cases:
            observations.append(
                await _call_model(
                    model=model,
                    mode=mode,
                    label=label,
                    messages=_messages(prefix, suffix),
                    timeout_sec=timeout_sec,
                )
            )

    decision = evaluate_cache_conformance(observations)
    return {
        "schema_version": PROBE_SCHEMA_VERSION,
        "provider": provider,
        "model": model_name,
        "run_nonce_hash": _value_hash(run_nonce),
        "prefix_line_count": prefix_line_count,
        "observations": observations,
        **decision,
    }


async def probe_from_environment(args: argparse.Namespace) -> dict[str, Any]:
    from aworld.config.conf import ModelConfig
    from aworld.models.llm import LLMModel

    model_name = os.environ.get("LLM_MODEL_NAME")
    api_key = os.environ.get("LLM_API_KEY")
    provider = os.environ.get("LLM_PROVIDER", "openai")
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")
    model = LLMModel(
        conf=ModelConfig(
            llm_provider=provider,
            llm_model_name=model_name,
            llm_api_key=api_key,
            llm_base_url=os.environ.get("LLM_BASE_URL"),
            llm_temperature=0,
            llm_sync_enabled=False,
            llm_async_enabled=True,
            llm_stream_call=False,
            max_retries=0,
            params={"seed": args.model_seed} if args.model_seed is not None else {},
            context_compiler={"mode": "off"},
        )
    )
    return await probe_model_cache(
        model=model,
        provider=provider,
        model_name=model_name,
        timeout_sec=args.timeout_sec,
        include_streaming=not args.skip_streaming,
        run_nonce=secrets.token_hex(16),
        prefix_line_count=args.prefix_line_count,
    )


def main() -> None:
    args = parse_args()
    try:
        receipt = asyncio.run(probe_from_environment(args))
    except Exception as exc:
        receipt = {
            "schema_version": PROBE_SCHEMA_VERSION,
            "status": "failed",
            "reason_code": "cache_preflight_execution_failed",
            "exception_type": type(exc).__name__,
            "error_fingerprint": _value_hash(
                {"type": type(exc).__name__, "detail": str(exc)}
            ),
        }
        print(json.dumps(receipt, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(receipt, ensure_ascii=False))
    if receipt["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
