import json
from pathlib import Path

from parsebench_dataset import artifacts
from parsebench_dataset.artifacts import (
    DEFAULT_VLM_MODEL_PROFILE,
    FileXRunRequest,
    SubprocessFileXRunner,
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


def test_parsebench_uses_remote_vlm_without_local_layout_model(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["environment"]
        return b"{}", b"", 0

    monkeypatch.setattr(artifacts, "_run_bounded_process", fake_run)
    runner = SubprocessFileXRunner(
        environment={
            "GATEWAY_VLLM_BASE_URL": "https://antchat.alipay.com/v1",
            "GATEWAY_VLLM_MODEL_NAME": "ai_cloud_Kimi_k26_pgc",
            "GATEWAY_VLLM_HTTP_MODEL_NAME": "kimi_k26_pc",
            "GATEWAY_VLLM_API_KEY": "protected-test-key",
            "FILEX_PADDLE_OCR_VL_REC_API_MODEL_NAME": "aisearch_paaldocr_vl_16",
        }
    )

    result = runner.run(
        FileXRunRequest(
            source_path=Path("/workspace/input.pdf"),
            task_id="remote-vlm-smoke",
            file_type="pdf",
            provider="paddle_ocr",
            page=1,
            no_cache=True,
            timeout_seconds=30,
            workspace_root=Path("/workspace"),
            vlm_model_profile=DEFAULT_VLM_MODEL_PROFILE,
        )
    )

    command = captured["command"]
    env_content = json.loads(command[command.index("--env-content-json") + 1])
    assert env_content["paddle_ocr_use_layout_detection"] is False
    assert "paddle_ocr_layout_detection_model_name" not in env_content
    assert "paddle_ocr_layout_detection_model_dir" not in env_content
    assert env_content["paddle_ocr_vl_rec_backend"] == "vllm-server"
    assert result.resolved_model_name == "aisearch_paaldocr_vl_16"
