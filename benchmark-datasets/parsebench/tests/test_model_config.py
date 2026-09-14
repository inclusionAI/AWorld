from parsebench_dataset.artifacts import (
    DEFAULT_VLM_MODEL_PROFILE,
    _resolved_gateway_vllm,
)


def test_parsebench_uses_distinct_logical_and_http_vlm_names() -> None:
    assert DEFAULT_VLM_MODEL_PROFILE == "ai_cloud_Kimi_k26_pgc"
    assert _resolved_gateway_vllm(
        {
            "GATEWAY_VLLM_BASE_URL": "https://antchat.alipay.com/v1",
            "GATEWAY_VLLM_MODEL_NAME": "ai_cloud_Kimi_k26_pgc",
            "GATEWAY_VLLM_HTTP_MODEL_NAME": "kimi_k26_pc",
            "GATEWAY_VLLM_API_KEY": "protected-test-key",
            "LLM_BASE_URL": "https://fallback.invalid/v1",
            "LLM_MODEL_NAME": "fallback-model",
            "LLM_API_KEY": "fallback-key",
        }
    ) == (
        "https://antchat.alipay.com/v1",
        "ai_cloud_Kimi_k26_pgc",
        "kimi_k26_pc",
        "protected-test-key",
    )


def test_parsebench_vlm_config_falls_back_to_legacy_runtime_contract() -> None:
    assert _resolved_gateway_vllm(
        {
            "LLM_BASE_URL": "https://legacy.example/v1",
            "LLM_MODEL_NAME": "legacy-model",
            "LLM_API_KEY": "legacy-key",
        }
    ) == (
        "https://legacy.example/v1",
        "legacy-model",
        "legacy-model",
        "legacy-key",
    )
