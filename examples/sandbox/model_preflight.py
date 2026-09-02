"""Fail-fast provider connectivity probe for local benchmark experiments.

The receipt intentionally contains no prompt, response text, endpoint, or secret.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout-sec", type=float, default=120)
    parser.add_argument("--model-seed", type=int)
    return parser.parse_args()


async def probe(timeout_sec: float, model_seed: int | None) -> dict:
    from aworld.config.conf import ModelConfig
    from aworld.models.llm import LLMModel

    model_name = os.environ.get("LLM_MODEL_NAME")
    api_key = os.environ.get("LLM_API_KEY")
    provider = os.environ.get("LLM_PROVIDER", "openai")
    if not model_name or not api_key:
        raise RuntimeError("LLM_MODEL_NAME and LLM_API_KEY must be set")
    started = time.monotonic()
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
            params={"seed": model_seed} if model_seed is not None else {},
            context_compiler={"mode": "off"},
        )
    )
    response = await asyncio.wait_for(
        model.acompletion(
            messages=[
                {
                    "role": "user",
                    "content": "Reply with exactly READY and no explanation.",
                }
            ],
            temperature=0,
            max_tokens=128,
        ),
        timeout=timeout_sec,
    )
    content = str(getattr(response, "content", "") or "").strip()
    reasoning = str(getattr(response, "reasoning_content", "") or "").strip()
    error = getattr(response, "error", None)
    finish_reason = str(getattr(response, "finish_reason", "") or "")
    if error or not content or finish_reason == "length":
        raise RuntimeError("provider did not complete the readiness response")
    usage = getattr(response, "usage", None) or {}
    return {
        "schema_version": "aworld.model-preflight/v1",
        "status": "passed",
        "provider": provider,
        "model": model_name,
        "model_seed": model_seed,
        "latency_seconds": round(time.monotonic() - started, 3),
        "response_sha256": hashlib.sha256((content or reasoning).encode()).hexdigest(),
        "finish_reason": finish_reason,
        "usage": {
            "prompt_tokens": int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
        },
    }


def main() -> None:
    args = parse_args()
    if args.timeout_sec <= 0:
        raise ValueError("--timeout-sec must be positive")
    try:
        receipt = asyncio.run(probe(args.timeout_sec, args.model_seed))
    except BaseException as exc:
        receipt = {
            "schema_version": "aworld.model-preflight/v1",
            "status": "failed",
            "reason_code": (
                "provider_connectivity_timeout"
                if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                else "provider_connectivity_failed"
            ),
            "exception_type": type(exc).__name__,
        }
        print(json.dumps(receipt, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == "__main__":
    main()
